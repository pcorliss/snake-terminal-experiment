"""Layout, chrome and every screen: menu, HUD, help, pause, game over.

Two design rules drive this file:

1. The playfield never moves during a run.  It is centred once, and the HUD
   lives in a gutter that appears or disappears only between runs, so the
   board never jumps under your hands mid-game.
2. Everything is clipped.  A cramped terminal degrades (shorter blurbs, HUD
   moves below the board, overlays shrink) instead of scattering glyphs over
   the status line.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .color import RGB, lerp, scale
from .config import KEY_HELP, TITLE
from .render import BOLD, DIM, ITALIC, Frame, bar, border, fill_rect, gradient_text, text_centered
from .theme import Theme

# ------------------------------------------------------------------- banner
# 5x5 bitmap font.  Anything the game prints in display type goes through
# this table, so it ships complete: a letter that isn't here simply vanishes
# from the word it belongs to (a past version of this file lost the O, B and Y
# of "NEON ABYSS" exactly that way).  Rows top to bottom, '#' = ink.
_FONT5_ROWS = {
    " ": ".....|.....|.....|.....|.....",
    "!": "..#..|..#..|..#..|.....|..#..",
    "?": ".###.|#...#|..#..|.....|..#..",
    ".": ".....|.....|.....|.....|..#..",
    ",": ".....|.....|.....|..#..|.#...",
    ":": ".....|..#..|.....|..#..|.....",
    "-": ".....|.....|#####|.....|.....",
    "+": ".....|..#..|#####|..#..|.....",
    "=": ".....|#####|.....|#####|.....",
    "/": "#...#|...#.|..#..|.#...|#....",
    "%": "##..#|##.#.|..#..|.#.##|#..##",
    "*": "..#..|#.#..|.#.#.|#.#..|..#..",
    "A": ".###.|#...#|#####|#...#|#...#",
    "B": "####.|#...#|.####|#...#|####.",
    "C": ".####|#....|#....|#....|.####",
    "D": "####.|#...#|#...#|#...#|####.",
    "E": "#####|#....|####.|#....|#####",
    "F": "#####|#....|####.|#....|#....",
    "G": ".####|#....|#..##|#...#|.####",
    "H": "#...#|#...#|#####|#...#|#...#",
    "I": "#####|..#..|..#..|..#..|#####",
    "J": "####.|....#|....#|#...#|.###.",
    "K": "#...#|#..#.|###..|#..#.|#...#",
    "L": "#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#...#|#...#",
    "N": "#...#|##..#|#.#.#|#..##|#...#",
    "O": ".###.|#...#|#...#|#...#|.###.",
    "P": "####.|#...#|####.|#....|#....",
    "Q": ".###.|#...#|#.#.#|#..#.|.##.#",
    "R": "####.|#...#|####.|#..#.|#...#",
    "S": ".####|#....|###..|....#|####.",
    "T": "#####|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|.###.",
    "V": "#...#|#...#|#...#|#...#|.#.#.",
    "W": "#...#|#...#|#.#.#|##.##|#...#",
    "X": "#...#|#...#|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..",
    "Z": "#####|....#|..#..|#....|#####",
    "0": ".###.|#..##|#.#.#|##..#|.###.",
    "1": "..#..|.##..|..#..|..#..|#####",
    "2": "####.|....#|..#..|.#...|#####",
    "3": "####.|....#|.###.|....#|####.",
    "4": "#..#.|#..#.|#####|...#.|...#.",
    "5": "#####|#....|####.|....#|####.",
    "6": ".###.|#....|####.|#...#|.###.",
    "7": "#####|....#|...#.|..#..|..#..",
    "8": ".###.|#...#|.###.|#...#|.###.",
    "9": ".###.|#...#|.####|....#|.###.",
}

FONT5 = {
    ch: tuple(rows.split("|")) for ch, rows in _FONT5_ROWS.items()
}


def bitmap_text(f: Frame, x: int, y: int, text: str, colors: Sequence[RGB], theme: Theme, shadow: Optional[RGB] = None) -> int:
    """Draw blocky display type. Returns the width consumed."""
    pixel = theme.glyphs.bar_full if theme.glyphs.bar_full != "#" else "#"
    blank = " "
    cx = x
    for ch in text.upper():
        glyph = FONT5.get(ch)
        if glyph is None:
            cx += 5
            continue
        for row, bits in enumerate(glyph):
            t = (cx - x) / max(1, len(text) * 6)
            idx = min(len(colors) - 1, int(t * (len(colors) - 1)))
            color = colors[idx]
            for col, bit in enumerate(bits):
                if bit == "#":
                    f.set(cx + col, y + row, pixel, color, None, BOLD)
        cx += 6
    return cx - x - 1


def fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def fmt_num(value: int) -> str:
    """12345 -> 12,345 (cheap, locale-free grouping)."""
    text = str(int(value))
    out = []
    while len(text) > 3:
        out.append(text[-3:])
        text = text[:-3]
    out.append(text)
    return ",".join(reversed(out))


# ------------------------------------------------------------------ layout


@dataclass
class Layout:
    cols: int
    rows: int
    gw: int  # game grid width (cells)
    gh: int
    field_x: int
    field_y: int
    hud_x: int
    hud_w: int
    hud_y: int
    hud_h: int
    show_hud: bool

    @property
    def board_w(self) -> int:
        return self.gw + 2

    @property
    def board_h(self) -> int:
        return self.gh + 2


def compute_layout(cols: int, rows: int, gw: int, gh: int) -> Layout:
    show_hud = cols >= (gw + 2) + 30
    hud_w = min(30, max(22, cols - gw - 10)) if show_hud else 0
    total_w = gw + 2 + ((hud_w + 3) if show_hud else 0)
    field_x = max(1, (cols - total_w) // 2 + 1)
    avail_h = rows - 2  # title bar + status bar
    field_y = max(1, 1 + (avail_h - (gh + 2)) // 2)
    hud_x = field_x + gw + 4 if show_hud else 0
    return Layout(
        cols=cols,
        rows=rows,
        gw=gw,
        gh=gh,
        field_x=field_x,
        field_y=field_y,
        hud_x=hud_x,
        hud_w=hud_w,
        hud_y=field_y,
        hud_h=gh + 2,
        show_hud=show_hud,
    )


# -------------------------------------------------------------- background


class Starfield:
    """Slow-drifting background motes: the 'abyss' in NEON ABYSS."""

    def __init__(self, seed: int = 99) -> None:
        self.rng = random.Random(seed)
        self.stars: List[Tuple[float, float, float, str, float]] = []
        self.cols = 0
        self.rows = 0

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        count = max(12, min(70, (cols * rows) // 260))
        self.stars = [
            (
                self.rng.uniform(0, cols),
                self.rng.uniform(0, rows),
                self.rng.uniform(0.05, 0.5),
                self.rng.choice((". ", " .", "·", "⋆", " ")),
                self.rng.uniform(0.3, 1.0),
            )
            for _ in range(count)
        ]

    def update(self, dt: float) -> None:
        if not self.stars:
            return
        for i, (x, y, v, ch, phase) in enumerate(self.stars):
            x -= v * dt
            if x < -1:
                x = self.cols + 1
                y = self.rng.uniform(0, self.rows)
            self.stars[i] = (x, y, v, ch, phase)

    def draw(self, f: Frame, theme: Theme) -> None:
        if not self.stars:
            return
        for x, y, v, ch, phase in self.stars:
            if ch == " ":
                continue
            brightness = 0.35 + 0.35 * math.sin(phase * 6.0 + self.cols * 0.01)
            color = scale(theme.text_dim, max(0.15, brightness))
            f.set(int(x), int(y), ch, color, None, 0)


def build_background(f: Frame, theme: Theme, layout: Layout) -> None:
    """Paint the static layer: background gradient + playfield floor."""
    for y in range(f.rows):
        t = y / max(1, f.rows - 1)
        bg = lerp(theme.bg_top, theme.bg_bottom, t)
        for x in range(f.cols):
            f.set(x, y, " ", None, bg, 0)

    fx, fy, gw, gh = layout.field_x, layout.field_y, layout.gw, layout.gh
    for y in range(gh):
        for x in range(gw):
            checker = theme.floor_a if (x + y) % 2 == 0 else theme.floor_b
            f.set(fx + 1 + x, fy + 1 + y, theme.glyphs.empty, None, checker, 0)


# ------------------------------------------------------------------ panels


def panel(f: Frame, x: int, y: int, w: int, h: int, theme: Theme, title: str = "", accent: Optional[RGB] = None, dim: float = 0.35) -> None:
    """A bordered card that also darkens what is behind it."""
    accent = accent or theme.accent
    dim_region(f, x, y, w, h, dim)
    bg = lerp(theme.bg_bottom, accent, 0.08)
    fill_rect(f, x + 1, y + 1, w - 2, h - 2, " ", None, bg, 0)
    border(f, x, y, w, h, (theme.glyphs.tl, theme.glyphs.tr, theme.glyphs.bl, theme.glyphs.br, theme.glyphs.h, theme.glyphs.v), accent, bg, 0)
    if title and w >= len(title) + 4:
        tx = x + (w - len(title)) // 2
        f.put(tx - 1, y, f" {title} ", accent, bg, BOLD)


def dim_region(f: Frame, x: int, y: int, w: int, h: int, factor: float = 0.35) -> None:
    for row in range(y, y + h):
        for col in range(x, x + w):
            if not f.inside(col, row):
                continue
            i = f.idx(col, row)
            if f.fg[i] is not None:
                f.fg[i] = scale(f.fg[i], factor)
            if f.bg[i] is not None:
                f.bg[i] = scale(f.bg[i], factor)


def key_label(f: Frame, x: int, y: int, key: str, desc: str, theme: Theme, width: int) -> None:
    """One help row: key names in accent, description in dim."""
    f.put(x, y, key, theme.accent, None, BOLD)
    offset = min(width - x, len(key) + 2)
    f.put(x + offset, y, desc[: max(0, f.cols - (x + offset))], theme.text_dim, None, 0)


# --------------------------------------------------------------- chrome


def draw_title_bar(
    f: Frame,
    theme: Theme,
    difficulty: str,
    best: int,
    extra: Optional[Tuple[str, RGB]] = None,
) -> None:
    colors = (theme.snake_mid, theme.accent, theme.accent2)
    gradient_text(f, 2, 0, TITLE, colors, None, BOLD)
    right = f.cols - 3
    # right to left: BEST, then an optional attention grabber, then the difficulty tag
    if best > 0:
        label = f"BEST {fmt_num(best)}"
        right -= len(label)
        f.put(right, 0, label, theme.golden, None, BOLD)
        right -= 2
    if extra:
        label, col = extra
        right -= len(label)
        f.put(right, 0, label, col, None, BOLD)
        right -= 2
    tag = difficulty.upper()
    right -= len(tag)
    f.put(right, 0, tag, theme.accent2, None, BOLD)


def draw_status_bar(f: Frame, theme: Theme, hints: Sequence[str], note: str) -> None:
    y = f.rows - 1
    # The note is right-aligned, so reserve its width first: hints must stop
    # short of it instead of writing over it (that looked like a rendering bug
    # for about ten seconds of my life).
    note_x = f.cols - 2 if not note else max(2, f.cols - len(note) - 2)
    if note:
        f.put(note_x, y, note, theme.text_dim, None, DIM)
    x = 2
    for hint in hints:
        if x + len(hint) > note_x - 3:
            break
        head, _, tail = hint.partition(" ")
        f.put(x, y, head, theme.accent, None, BOLD)
        if tail:
            f.put(x + len(head) + 1, y, tail, theme.text_dim, None, 0)
        x += len(hint) + 3


def draw_board_frame(f: Frame, layout: Layout, theme: Theme, level: int, danger: float = 0.0) -> None:
    """The playfield border. Its glow pulses faster at higher levels."""
    glyphs = theme.glyphs
    g = (glyphs.tl, glyphs.tr, glyphs.bl, glyphs.br, glyphs.h, glyphs.v)
    base = lerp(theme.wall, theme.wall_dim, 0.25)
    if danger > 0:
        base = lerp(base, theme.danger, min(1.0, danger))
    x = layout.field_x
    y = layout.field_y
    w = layout.gw + 2
    h = layout.gh + 2
    border(f, x, y, w, h, g, base, None, BOLD)


# -------------------------------------------------------------------- HUD


@dataclass
class HudData:
    score: int = 0
    best: int = 0
    level: int = 1
    to_next_level: int = 0
    per_level: int = 5
    length: int = 3
    speed: float = 0.0
    combo: int = 0
    combo_frac: float = 0.0
    bonus_frac: Optional[float] = None  # None -> no bonus on the board
    bonus_left: float = 0.0
    difficulty: str = ""
    theme_name: str = ""
    sound: bool = True
    music: bool = True
    fx: bool = True
    ascii_glyphs: bool = False
    elapsed: float = 0.0
    deaths: int = 0
    fps: float = 0.0
    seed: Optional[int] = None
    player: str = "none"
    demo: bool = False


def draw_hud(f: Frame, layout: Layout, theme: Theme, d: HudData) -> None:
    if not layout.show_hud:
        return
    x = layout.hud_x
    y = layout.hud_y
    w = layout.hud_w
    g = theme.glyphs
    inner = w - 4
    cx = x + 2

    border(f, x, y, w, layout.hud_h, (g.tl, g.tr, g.bl, g.br, "─", g.v), theme.wall_dim, None, 0)
    f.put(x + 2, y, " STATUS ", theme.accent2, None, BOLD)

    row = y + 2
    f.put(cx, row, "SCORE", theme.text_dim, None, 0)
    row += 1
    digits = fmt_num(d.score)
    gradient_text(f, cx, row, digits, (theme.snake_mid, theme.golden), None, BOLD)
    row += 2

    f.put(cx, row, f"BEST   {fmt_num(d.best)}", theme.text_dim, None, 0)
    row += 1
    f.put(cx, row, f"TIME   {fmt_time(d.elapsed)}", theme.text_dim, None, 0)
    row += 2

    if row + 1 < y + layout.hud_h - 1:
        f.put(cx, row, f"LEVEL  {d.level}", theme.text, None, BOLD)
        row += 1
        frac = 1.0 - (d.to_next_level / max(1, d.per_level))
        bar(f, cx, row, min(inner, 16), frac, g.bar_full, g.bar_empty, theme.accent, theme.wall_dim)
        row += 2

        f.put(cx, row, f"LENGTH {d.length}", theme.text, None, 0)
        row += 1
        f.put(cx, row, f"SPEED  {d.speed:.1f}/s", theme.text, None, 0)
        row += 2

    if d.combo > 1:
        f.put(cx, row, f"COMBO x{d.combo}", theme.golden, None, BOLD)
        row += 1
        bar(f, cx, row, min(inner, 12), d.combo_frac, g.bar_full, g.bar_empty, theme.golden, theme.wall_dim)
        row += 2

    if d.bonus_frac is not None:
        fading = d.bonus_frac < 0.25
        f.put(cx, row, "BONUS", theme.golden if not fading else theme.danger, None, BOLD)
        row += 1
        bar(f, cx, row, min(inner, 16), d.bonus_frac, g.bar_full, g.bar_empty, theme.golden, theme.wall_dim)
        row += 2

    row = max(row, y + layout.hud_h - 6)
    if row + 4 < y + layout.hud_h:
        f.put(cx, row, d.difficulty, theme.accent2, None, BOLD)
        row += 1
        sound = "on " if d.sound else "off"
        music = "on " if d.music else "off"
        fx = "on" if d.fx else "off"
        f.put(cx, row, f"snd {sound} mus {music} fx {fx}", theme.text_dim, None, 0)
        row += 1
        extra = " · ascii" if d.ascii_glyphs else ""
        f.put(cx, row, f"{d.theme_name}{extra}"[:inner], theme.text_dim, None, ITALIC)
        row += 1
        if d.demo:
            f.put(cx, row, "DEMO - autopilot", theme.golden, None, DIM)
        else:
            f.put(cx, row, f"{d.player} audio"[:inner], theme.text_dim, None, DIM)


# ------------------------------------------------------------------ menu


@dataclass
class MenuItem:
    key: str
    label: str
    blurb: str


class DecorSnake:
    """An ornamental snake that sweeps the menu; pure decoration."""

    def __init__(self, seed: int = 5) -> None:
        self.rng = random.Random(seed)
        self.trail: List[Tuple[int, int]] = []
        self.t = 0.0
        self.step = 0.09
        self.food: Optional[Tuple[int, int]] = None
        self.x = 0
        self.y = 0
        self.dir = 1
        self.row = 0
        self.length = 9
        self.bounds = (0, 0, 1, 1)

    def reset(self, x0: int, y0: int, w: int, h: int) -> None:
        bounds = (x0, y0, max(1, w), max(1, h))
        if bounds == self.bounds:
            # draw_menu() calls this every frame; re-seeding the same bounds
            # would park the snake on its starting cell forever
            return
        self.bounds = bounds
        self.x = x0
        self.y = y0
        self.row = 0
        self.dir = 1
        self.trail = []
        self.food = None

    def update(self, dt: float) -> None:
        self.t += dt
        while self.t >= self.step:
            self.t -= self.step
            self._advance()

    def _advance(self) -> None:
        x0, y0, w, h = self.bounds
        max_row = max(0, h - 1)
        self.x += self.dir
        if self.x >= x0 + w:
            self.x = x0 + w - 1
            self.dir = -1
            self.row = (self.row + 1) % (max_row + 1)
            self.y = y0 + self.row
        elif self.x < x0:
            self.x = x0
            self.dir = 1
            self.row = (self.row + 1) % (max_row + 1)
            self.y = y0 + self.row
        self.trail.insert(0, (self.x, self.y))
        while len(self.trail) > self.length:
            self.trail.pop()
        if self.food is None and self.rng.random() < 0.10:
            self.food = (self.rng.randrange(x0, x0 + w), self.rng.randrange(y0, y0 + max(1, h)))
        elif self.food and self.food[0] == self.x and self.food[1] == self.y:
            self.food = None

    def draw(self, f: Frame, theme: Theme) -> None:
        if self.food:
            f.set(self.food[0], self.food[1], theme.glyphs.apple, theme.apple, None, BOLD)
        n = len(self.trail)
        for i, (x, y) in enumerate(self.trail):
            t = i / max(1, n - 1)
            color = lerp(theme.snake_head, theme.snake_tail, t)
            if i == 0:
                f.set(x, y, theme.glyphs.head, color, None, BOLD)
            else:
                prev = self.trail[i - 1]
                nxt = self.trail[i + 1] if i + 1 < n else None
                mask = _mask_between((x, y), prev) | (_mask_between((x, y), nxt) if nxt else 0)
                f.set(x, y, theme.glyphs.pipes.get(mask, theme.glyphs.h), color, None, 0)


def _mask_between(a: Tuple[int, int], b: Optional[Tuple[int, int]]) -> int:
    if b is None:
        return 0
    dx, dy = b[0] - a[0], b[1] - a[1]
    if (dx, dy) == (0, -1):
        return 1
    if (dx, dy) == (1, 0):
        return 2
    if (dx, dy) == (0, 1):
        return 4
    if (dx, dy) == (-1, 0):
        return 8
    return 0


def draw_menu(
    f: Frame,
    theme: Theme,
    layout: Layout,
    difficulties: Sequence[MenuItem],
    cursor: int,
    scores: Sequence[str],
    decor: DecorSnake,
    sound: bool,
    music: bool,
    theme_name: str,
    ascii_glyphs: bool,
    compact: bool,
    version: str,
) -> None:
    cols, rows = f.cols, f.rows
    banner_colors = (theme.snake_head, theme.snake_mid, theme.accent, theme.accent2)

    if compact:
        text_centered(f, 0, 1, cols, TITLE, theme.accent, None, BOLD)
        text_centered(f, 0, 2, cols, "terminal snake", theme.text_dim, None, ITALIC)
        start_y = 4
        panel_w = min(cols - 4, 44)
        panel_x = (cols - panel_w) // 2
        panel(f, panel_x, start_y, panel_w, len(difficulties) + 4, theme, "DIFFICULTY", theme.accent2)
        y = start_y + 2
        for i, item in enumerate(difficulties):
            active = i == cursor
            mark = theme.glyphs.cursor if active else theme.glyphs.bullet
            f.put(panel_x + 2, y, f"{mark} {item.label}", theme.golden if active else theme.text, None, BOLD if active else 0)
            y += 1
        decor.reset(panel_x + 2, start_y + len(difficulties) + 2, panel_w - 4, 1)
        text_centered(f, 2, rows - 2, cols, "[enter] start    [?] help    [q] quit", theme.text_dim, None, 0)
        return

    # --- title block
    text = TITLE
    tw = len(text) * 6 - 1
    bx = max(2, (cols - tw) // 2)
    by = 2
    bitmap_text(f, bx, by, text, banner_colors, theme)
    tagline = "a snake game for terminals · graphics in truecolour · sound synthesized live · " + version
    text_centered(f, bx, by + 6, tw, tagline, theme.text_dim, None, ITALIC)

    # --- left card: difficulty picker
    lw = min(44, max(30, cols // 2 - 4))
    lh = len(difficulties) * 2 + 5
    lx = max(2, (cols - (lw + 2 + 34)) // 2)
    ly = by + 9
    panel(f, lx, ly, lw, lh, theme, "DIFFICULTY", theme.accent2)
    y = ly + 2
    for i, item in enumerate(difficulties):
        active = i == cursor
        mark = theme.glyphs.cursor if active else theme.glyphs.bullet
        f.put(lx + 2, y, f"{mark} {item.label}", theme.golden if active else theme.text, None, BOLD if active else 0)
        text_dim = theme.text if active else theme.text_dim
        f.put(lx + 4, y + 1, item.blurb[: lw - 6], text_dim, None, ITALIC)
        y += 2
    decor.reset(lx + 2, ly + lh - 3, lw - 4, 1)
    text_centered(f, lx, ly + lh - 2, lw, "1 2 3 4 to choose · enter to start", theme.text_dim, None, 0)

    # --- right card: high scores
    rw = 34
    rx = lx + lw + 3
    if rx + rw <= cols - 2:
        rh = max(8, min(rows - ly - 3, 12))
        panel(f, rx, ly, rw, rh, theme, "HIGH SCORES", theme.golden)
        yy = ly + 2
        for line in scores[: rh - 4]:
            f.put(rx + 2, yy, line[: rw - 4], theme.text, None, 0)
            yy += 1
        if not scores:
            f.put(rx + 2, yy, "no scores yet - play!", theme.text_dim, None, ITALIC)

    # --- footer
    flags = f"theme {theme_name}{' ascii' if ascii_glyphs else ''}  ·  snd {'on' if sound else 'off'}  ·  mus {'on' if music else 'off'}"
    text_centered(f, 2, rows - 2, cols - 4, flags, theme.text_dim, None, 0)
    text_centered(f, 2, rows - 3, cols - 4, "[?] keys   [t] theme   [m] sound   [M] music   [u] glyphs", theme.accent, None, 0)


# --------------------------------------------------------------- overlays


def draw_help(f: Frame, theme: Theme, page_note: str = "") -> None:
    cols, rows = f.cols, f.rows
    key_width = max(len(keys) for keys, _ in KEY_HELP)
    desc_width = max(len(desc) for _, desc in KEY_HELP)
    # wide enough that nothing gets elided, but never wider than the terminal
    w = min(cols - 4, key_width + desc_width + 10)
    h = min(rows - 4, len(KEY_HELP) + 5)
    x = (cols - w) // 2
    y = (rows - h) // 2
    panel(f, x, y, w, h, theme, "CONTROLS", theme.accent)
    yy = y + 2
    for keys, desc in KEY_HELP:
        if yy >= y + h - 2:
            f.put(x + 2, yy, "...", theme.text_dim, None, 0)
            break
        f.put(x + 2, yy, keys, theme.accent, None, BOLD)
        f.put(x + 2 + key_width + 2, yy, desc[: max(0, w - key_width - 6)], theme.text_dim, None, 0)
        yy += 1
    text_centered(f, x, y + h - 2, w, page_note or "any key to go back", theme.text_dim, None, 0)


def draw_pause(f: Frame, layout: Layout, theme: Theme, d: HudData) -> None:
    w = min(layout.gw, 34)
    h = 7
    x = layout.field_x + (layout.gw - w) // 2 + 1
    y = layout.field_y + (layout.gh - h) // 2 + 1
    panel(f, x, y, w, h, theme, "PAUSED", theme.accent2, dim=0.25)
    text_centered(f, x + 1, y + 2, w - 2, f"{fmt_num(d.score)} pts · level {d.level} · len {d.length}", theme.text, None, 0)
    text_centered(f, x + 1, y + 4, w - 2, "[p] resume   [r] restart   [q] menu", theme.text_dim, None, 0)


def draw_game_over(
    f: Frame,
    layout: Layout,
    theme: Theme,
    d: HudData,
    cause: str,
    won: bool,
    rank: int,
    record: bool,
) -> None:
    w = min(max(24, layout.gw), 44)
    h = 11 if layout.gh >= 11 else min(layout.gh, 11)
    x = layout.field_x + (layout.gw - w) // 2 + 1
    y = layout.field_y + (layout.gh - h) // 2 + 1
    accent = theme.golden if won else theme.danger
    title = "BOARD CLEARED" if won else "GAME OVER"
    panel(f, x, y, w, h, theme, title, accent, dim=0.30)
    cy = y + 2
    reasons = {
        "self": "you ran into yourself",
        "wall": "you hit the wall",
        "obstacle": "you hit a shard",
        "win": "nothing left to bite",
    }
    text_centered(f, x + 1, cy, w - 2, reasons.get(cause, ""), theme.text_dim, None, ITALIC)
    cy += 2
    score_text = fmt_num(d.score)
    gradient_text(f, x + 1 + (w - 2 - len(score_text)) // 2, cy, score_text, (theme.snake_mid, theme.golden), None, BOLD)
    cy += 2
    stats = f"level {d.level} · length {d.length} · {fmt_time(d.elapsed)}"
    text_centered(f, x + 1, cy, w - 2, stats, theme.text_dim, None, 0)
    cy += 1
    if record and rank:
        text_centered(f, x + 1, cy, w - 2, f"NEW RECORD #{rank} on {d.difficulty}", theme.golden, None, BOLD)
    else:
        text_centered(f, x + 1, cy, w - 2, f"best {fmt_num(d.best)}", theme.text_dim, None, 0)
    cy += 2
    text_centered(f, x + 1, cy, w - 2, "[r] again   [enter] menu", theme.accent, None, BOLD)
