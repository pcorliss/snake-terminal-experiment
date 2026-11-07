"""Visual identity: palettes ("themes") and glyph sets.

Two glyph sets exist because terminals disagree about how much room an
ambiguous-width character needs.  The unicode set is gorgeous but uses box
drawing / geometric shapes; the ascii set is a bulletproof fallback (and a
fun retro look in its own right) selectable with ``--ascii`` or the ``a`` key.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Sequence, Tuple

from .color import RGB, hex_rgb

# Direction bitmasks used to pick a "pipe" glyph for a snake segment.
UP, RIGHT, DOWN, LEFT = 1, 2, 4, 8


@dataclass(frozen=True)
class Glyphs:
    """Every character the renderer can draw, so themes can swap the alphabet."""

    # snake pipes, keyed by the direction bitmask of the segment's neighbours
    pipes: Dict[int, str]
    head: str  # the head cell
    tail: str  # a dead-end cap when the tail is a straight run
    single: str  # a length-1 snake

    # playfield border
    tl: str
    tr: str
    bl: str
    br: str
    h: str
    v: str

    # entities
    apple: str
    golden: str
    obstacle: str

    # defaults below are the bits that are the same for every alphabet
    dead: str = "X"  # drawn on the head instead, at the moment of death
    empty: str = " "
    particles: Tuple[str, ...] = (".", "'", "`", "*", ",")
    sparkle: str = "+"
    arrow_up: str = "^"
    arrow_down: str = "v"
    bullet: str = "-"
    bar_full: str = "#"
    bar_empty: str = "."
    cursor: str = ">"


UNICODE_GLYPHS = Glyphs(
    pipes={
        UP | DOWN: "│",
        LEFT | RIGHT: "─",
        UP | RIGHT: "╰",
        UP | LEFT: "╯",
        DOWN | RIGHT: "╭",
        DOWN | LEFT: "╮",
        # T-junctions only happen after a wrap; keep something sane on screen.
        UP | DOWN | LEFT: "┤",
        UP | DOWN | RIGHT: "├",
        UP | LEFT | RIGHT: "┴",
        DOWN | LEFT | RIGHT: "┬",
        UP | DOWN | LEFT | RIGHT: "┼",
        # dead-end caps
        UP: "╵",
        DOWN: "╷",
        LEFT: "╴",
        RIGHT: "╶",
    },
    head="█",
    tail="╺",
    single="█",
    dead="✕",
    tl="╔",
    tr="╗",
    bl="╚",
    br="╝",
    h="═",
    v="║",
    apple="◆",
    golden="✦",
    obstacle="▨",
    particles=("·", "˙", "⋆", "'", ","),
    sparkle="✧",
    arrow_up="▲",
    arrow_down="▼",
    bullet="·",
    bar_full="█",
    bar_empty="░",
    cursor="▶",
)

ASCII_GLYPHS = Glyphs(
    pipes={
        UP | DOWN: "|",
        LEFT | RIGHT: "-",
        UP | RIGHT: "+",
        UP | LEFT: "+",
        DOWN | RIGHT: "+",
        DOWN | LEFT: "+",
        UP | DOWN | LEFT: "+",
        UP | DOWN | RIGHT: "+",
        UP | LEFT | RIGHT: "+",
        DOWN | LEFT | RIGHT: "+",
        UP | DOWN | LEFT | RIGHT: "+",
        UP: "'",
        DOWN: ",",
        LEFT: "-",
        RIGHT: "-",
    },
    head="O",
    tail=".",
    single="O",
    dead="x",
    tl="+",
    tr="+",
    bl="+",
    br="+",
    h="-",
    v="|",
    apple="@",
    golden="$",
    obstacle="#",
    particles=(".", "'", "`", ",", ";"),
    sparkle="*",
    arrow_up="^",
    arrow_down="v",
    bullet="-",
    bar_full="#",
    bar_empty=".",
    cursor=">",
)


@dataclass(frozen=True)
class MusicSpec:
    """The score that accompanies a theme - sound design is part of the look."""

    bpm: float
    root: float  # Hz of the tonic
    mode: Tuple[int, ...]  # semitone offsets from the root
    pad_wave: str = "triangle"
    arp_wave: str = "square"
    arp_gain: float = 0.5
    steps_per_bar: int = 16


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    blurb: str
    glyphs: Glyphs

    # screen background (vertical gradient)
    bg_top: RGB
    bg_bottom: RGB

    # playfield floor: a very subtle checkerboard
    floor_a: RGB
    floor_b: RGB

    wall: RGB
    wall_dim: RGB
    obstacle: RGB
    snake_head: RGB
    snake_mid: RGB
    snake_tail: RGB
    apple: RGB
    apple_glow: RGB
    golden: RGB
    golden_glow: RGB
    text: RGB
    text_dim: RGB
    accent: RGB
    accent2: RGB
    danger: RGB
    popup: RGB
    particle_colors: Tuple[RGB, ...] = ()

    scanline: float = 0.90  # brightness multiplier applied to every other row
    vignette: float = 0.22  # 0 = off, 1 = edges go black
    music: MusicSpec | None = None

    def with_glyphs(self, glyphs: Glyphs) -> "Theme":
        return replace(self, glyphs=glyphs)


def _t(key: str, label: str, blurb: str, **kw) -> Theme:
    """Shorthand builder: hex strings -> RGB tuples."""
    for name, value in list(kw.items()):
        if isinstance(value, str):
            kw[name] = hex_rgb(value)
    if "particle_colors" in kw:
        pc = []
        for c in kw["particle_colors"]:
            pc.append(hex_rgb(c) if isinstance(c, str) else c)
        kw["particle_colors"] = tuple(pc)
    return Theme(key=key, label=label, blurb=blurb, **kw)


NEON = _t(
    "neon",
    "NEON ABYSS",
    "deep space blue, spring-green serpent, magenta fruit",
    glyphs=UNICODE_GLYPHS,
    bg_top="#05060c",
    bg_bottom="#0c1024",
    floor_a="#0a0d1b",
    floor_b="#0c1021",
    wall="#3b5bb0",
    wall_dim="#1d2a52",
    obstacle="#414f73",
    snake_head="#eaffd8",
    snake_mid="#45f0a8",
    snake_tail="#1f8fd6",
    apple="#ff2f74",
    apple_glow="#ff86ad",
    golden="#ffd166",
    golden_glow="#fff0b8",
    text="#cfe9ff",
    text_dim="#5f7ba6",
    accent="#54e0ff",
    accent2="#b06bff",
    danger="#ff3355",
    popup="#ffffff",
    particle_colors=("#ff2f74", "#ffd166", "#54e0ff", "#45f0a8", "#b06bff"),
    scanline=0.92,
    vignette=0.24,
    music=MusicSpec(bpm=112.0, root=110.0, mode=(0, 2, 3, 5, 7, 8, 10), arp_gain=0.45),
)

AMBER = _t(
    "amber",
    "AMBER CRT",
    "warm phosphor terminal, no colour at all",
    glyphs=UNICODE_GLYPHS,
    bg_top="#0a0602",
    bg_bottom="#120b03",
    floor_a="#150d04",
    floor_b="#180f05",
    wall="#8a5a1f",
    wall_dim="#4a3011",
    obstacle="#5c3f17",
    snake_head="#fff4d6",
    snake_mid="#ffb000",
    snake_tail="#8f6200",
    apple="#ffe259",
    apple_glow="#fff2a8",
    golden="#fff9e6",
    golden_glow="#ffffff",
    text="#ffcf8a",
    text_dim="#94714a",
    accent="#ffb000",
    accent2="#ff8c42",
    danger="#ff6a2b",
    popup="#fff4d6",
    particle_colors=("#ffb000", "#ffe259", "#ff8c42", "#fff4d6"),
    scanline=0.82,
    vignette=0.34,
    music=MusicSpec(bpm=96.0, root=98.0, mode=(0, 2, 3, 5, 7, 9, 10), arp_wave="triangle", arp_gain=0.35),
)

PHOSPHOR = _t(
    "phosphor",
    "GREEN PHOSPHOR",
    "P1 monochrome, hard contrast, arcade cabinet",
    glyphs=UNICODE_GLYPHS,
    bg_top="#01060a",
    bg_bottom="#031014",
    floor_a="#03181a",
    floor_b="#041c1e",
    wall="#0f8f4f",
    wall_dim="#08532f",
    obstacle="#125c39",
    snake_head="#e8ffe6",
    snake_mid="#43ff8f",
    snake_tail="#0f9a5a",
    apple="#b6ff3d",
    apple_glow="#e0ff9a",
    golden="#ffffff",
    golden_glow="#dfffe0",
    text="#9dffc0",
    text_dim="#2f7a52",
    accent="#43ff8f",
    accent2="#b6ff3d",
    danger="#ff5c5c",
    popup="#e8ffe6",
    particle_colors=("#43ff8f", "#b6ff3d", "#e8ffe6", "#0f9a5a"),
    scanline=0.80,
    vignette=0.30,
    music=MusicSpec(bpm=128.0, root=130.8, mode=(0, 2, 4, 5, 7, 9, 11), arp_gain=0.5),
)

GRAPHITE = _t(
    "graphite",
    "GRAPHITE",
    "cool minimal grey, easy on the eyes",
    glyphs=UNICODE_GLYPHS,
    bg_top="#101216",
    bg_bottom="#171a20",
    floor_a="#191d24",
    floor_b="#1c2028",
    wall="#5b6674",
    wall_dim="#39414c",
    obstacle="#3f4753",
    snake_head="#f4f7fb",
    snake_mid="#9fb4c9",
    snake_tail="#5f7286",
    apple="#e05f7a",
    apple_glow="#f296aa",
    golden="#e8c37a",
    golden_glow="#f6e3bd",
    text="#dbe3ec",
    text_dim="#7c8794",
    accent="#7fb2d9",
    accent2="#a68fd0",
    danger="#e0525f",
    popup="#ffffff",
    particle_colors=("#dbe3ec", "#7fb2d9", "#e05f7a", "#a68fd0"),
    scanline=1.0,
    vignette=0.10,
    music=MusicSpec(bpm=84.0, root=87.3, mode=(0, 2, 3, 5, 7, 9, 10), pad_wave="sine", arp_wave="triangle", arp_gain=0.3),
)

THEMES: Dict[str, Theme] = {
    t.key: t for t in (NEON, AMBER, PHOSPHOR, GRAPHITE)
}
THEME_ORDER: Tuple[str, ...] = tuple(THEMES)
