"""The renderer: a cell buffer plus a diffing ANSI presenter.

Why not curses?  I want decisions curses makes for you - and the ones it
refuses to make: 24-bit colour, DEC synchronized updates (zero tearing), a
hand-tuned diff that only rewrites cells that actually changed, and full
control over the glyph alphabet.  It is ~250 lines and depends on nothing.

The pipeline per frame is::

    cached background layer  ->  copy into the live frame
    entities / HUD / overlays ->  draw into the live frame
    post pass                 ->  crt scanlines + vignette (cheap, cached)
    diff vs previous frame    ->  only the changed cells hit the wire
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from .color import RGB, scale

BOLD = 1
DIM = 2
ITALIC = 4
UNDERLINE = 8
REVERSE = 16
BLINK = 32  # rendered as bold+bright; terminals that ignore it lose nothing

_ATTR_CODES = {
    BOLD: "1",
    DIM: "2",
    ITALIC: "3",
    UNDERLINE: "4",
    REVERSE: "7",
    BLINK: "5",
}


class Frame:
    """A rectangular grid of styled cells, addressed by (x, y)."""

    __slots__ = ("cols", "rows", "chars", "fg", "bg", "attr")

    def __init__(self, cols: int, rows: int, fill: str = " ") -> None:
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        n = self.cols * self.rows
        self.chars: List[str] = [fill] * n
        self.fg: List[Optional[RGB]] = [None] * n
        self.bg: List[Optional[RGB]] = [None] * n
        self.attr: List[int] = [0] * n

    # ------------------------------------------------------------ plumbing

    def idx(self, x: int, y: int) -> int:
        return y * self.cols + x

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.cols and 0 <= y < self.rows

    def clear(self, fill: str = " ") -> None:
        n = self.cols * self.rows
        self.chars = [fill] * n
        self.fg = [None] * n
        self.bg = [None] * n
        self.attr = [0] * n

    def copy_layer(self, layer: "Frame") -> None:
        """Blit a cached layer (the background) into this frame."""
        self.chars = layer.chars[:]
        self.fg = layer.fg[:]
        self.bg = layer.bg[:]
        self.attr = layer.attr[:]

    # ---------------------------------------------------------------- draw

    def set(self, x: int, y: int, ch: str, fg: Optional[RGB] = None, bg: Optional[RGB] = None, attr: int = 0) -> None:
        if not self.inside(x, y):
            return
        i = self.idx(x, y)
        self.chars[i] = ch
        self.fg[i] = fg
        self.bg[i] = bg
        self.attr[i] = attr

    def get(self, x: int, y: int) -> Tuple[str, Optional[RGB], Optional[RGB], int]:
        i = self.idx(x, y)
        return self.chars[i], self.fg[i], self.bg[i], self.attr[i]

    def put(
        self,
        x: int,
        y: int,
        text: str,
        fg: Optional[RGB] = None,
        bg: Optional[RGB] = None,
        attr: int = 0,
    ) -> int:
        """Write a string; returns the x just past the last cell written."""
        for offset, ch in enumerate(text):
            self.set(x + offset, y, ch, fg, bg, attr)
        return x + len(text)

    def cell_key(self, i: int) -> Tuple[str, Optional[RGB], Optional[RGB], int]:
        return (self.chars[i], self.fg[i], self.bg[i], self.attr[i])


# ----------------------------------------------------------------- helpers


def hline(f: Frame, x: int, y: int, n: int, ch: str, fg: Optional[RGB] = None, bg: Optional[RGB] = None, attr: int = 0) -> None:
    for i in range(n):
        f.set(x + i, y, ch, fg, bg, attr)


def vline(f: Frame, x: int, y: int, n: int, ch: str, fg: Optional[RGB] = None, bg: Optional[RGB] = None, attr: int = 0) -> None:
    for i in range(n):
        f.set(x, y + i, ch, fg, bg, attr)


def fill_rect(
    f: Frame,
    x: int,
    y: int,
    w: int,
    h: int,
    ch: str = " ",
    fg: Optional[RGB] = None,
    bg: Optional[RGB] = None,
    attr: int = 0,
) -> None:
    for row in range(y, y + h):
        for col in range(x, x + w):
            f.set(col, row, ch, fg, bg, attr)


def border(
    f: Frame,
    x: int,
    y: int,
    w: int,
    h: int,
    glyphs: Sequence[str],
    fg: Optional[RGB] = None,
    bg: Optional[RGB] = None,
    attr: int = 0,
) -> None:
    """Draw a box. ``glyphs`` is the theme's Glyphs object (tl,tr,bl,br,h,v)."""
    tl, tr, bl, br, hs, vs = glyphs[0], glyphs[1], glyphs[2], glyphs[3], glyphs[4], glyphs[5]
    if w >= 2 and h >= 1:
        hline(f, x + 1, y, w - 2, hs, fg, bg, attr)
        hline(f, x + 1, y + h - 1, w - 2, hs, fg, bg, attr)
    if h >= 2:
        vline(f, x, y + 1, h - 2, vs, fg, bg, attr)
        vline(f, x + w - 1, y + 1, h - 2, vs, fg, bg, attr)
    f.set(x, y, tl, fg, bg, attr)
    f.set(x + w - 1, y, tr, fg, bg, attr)
    f.set(x, y + h - 1, bl, fg, bg, attr)
    f.set(x + w - 1, y + h - 1, br, fg, bg, attr)


def text_centered(f: Frame, x: int, y: int, w: int, text: str, fg: Optional[RGB] = None, bg: Optional[RGB] = None, attr: int = 0) -> None:
    pad = (w - len(text)) // 2
    f.put(x + max(0, pad), y, text, fg, bg, attr)


def gradient_text(f: Frame, x: int, y: int, text: str, colors: Sequence[RGB], bg: Optional[RGB] = None, attr: int = 0) -> None:
    """Colour a string across a list of colours - used for titles and numbers."""
    n = len(text)
    if n == 0:
        return
    for i, ch in enumerate(text):
        t = i / max(1, n - 1)
        idx = min(len(colors) - 1, int(t * (len(colors) - 1) + 0.5))
        f.set(x + i, y, ch, colors[idx], bg, attr)


def bar(f: Frame, x: int, y: int, w: int, frac: float, filled: str, empty: str, fg: RGB, dim: RGB, bg: Optional[RGB] = None) -> None:
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    lit = int(round(frac * w))
    for i in range(w):
        f.set(x + i, y, filled if i < lit else empty, fg if i < lit else dim, bg, BOLD if i < lit else 0)


# --------------------------------------------------------------- post pass


def vignette_factors(cols: int, rows: int, strength: float) -> List[float]:
    """Per-cell brightness multiplier: scanline-free vignette, precomputed."""
    factors: List[float] = []
    if strength <= 0:
        return [1.0] * (cols * rows)
    cx = (cols - 1) / 2.0
    cy = (rows - 1) / 2.0
    max_d = ((cx**2 + cy**2) ** 0.5) or 1.0
    for y in range(rows):
        for x in range(cols):
            dx = (x - cx) / max(1.0, cx)
            dy = (y - cy) / max(1.0, cy)
            d = (dx * dx + dy * dy) ** 0.5
            f = 1.0 - strength * min(1.0, max(0.0, d - 0.35)) / 1.0
            factors.append(f if f > 0.05 else 0.05)
    return factors


class PostFX:
    """Applies vignette + scanlines to a frame, with a quantised colour cache."""

    def __init__(self, cols: int, rows: int, vignette: float, scanline: float) -> None:
        self.vignette = vignette
        self.scanline = scanline
        base = vignette_factors(cols, rows, vignette)
        if scanline < 1.0:
            self.factors = [
                (b if (y % 2 == 0) else b * scanline)
                for y in range(rows)
                for b in base[y * cols : (y + 1) * cols]
            ]
        else:
            self.factors = base
        self._cache: Dict[Tuple[RGB, int], RGB] = {}

    def _scaled(self, c: RGB, factor: float) -> RGB:
        bucket = int(factor * 128)
        key = (c, bucket)
        out = self._cache.get(key)
        if out is None:
            out = scale(c, bucket / 128.0)
            self._cache[key] = out
        return out

    def apply(self, f: Frame) -> None:
        if self.vignette <= 0 and self.scanline >= 1.0:
            return
        for i in range(len(f.chars)):
            factor = self.factors[i]
            if factor >= 0.999:
                continue
            fg = f.fg[i]
            if fg is not None:
                f.fg[i] = self._scaled(fg, factor)
            bg = f.bg[i]
            if bg is not None:
                f.bg[i] = self._scaled(bg, factor)


# ------------------------------------------------------------ presenters


class MemoryPresenter:
    """Headless sink: keeps the last frame as text. Used by tests/``--headless``."""

    def __init__(self) -> None:
        self.last_frame: Optional[Frame] = None
        self.text: str = ""
        self.frames: int = 0

    def start(self) -> None:
        pass

    def end(self) -> None:
        pass

    def set_size(self, cols: int, rows: int) -> None:
        pass

    def render(self, frame: Frame) -> None:
        self.present(frame)

    def present(self, frame: Frame) -> None:
        self.last_frame = frame
        self.frames += 1
        rows = []
        for y in range(frame.rows):
            rows.append("".join(frame.chars[y * frame.cols : (y + 1) * frame.cols]))
        self.text = "\n".join(rows)


class AnsiPresenter:
    """Writes a frame to a byte stream (the real terminal), rewriting only deltas."""

    BEGIN_SYNC = b"\x1b[?2026h"
    END_SYNC = b"\x1b[?2026l"

    def __init__(self, writer, cols: int, rows: int, alt_screen: bool = True, title: str = "SNAKE // NEON ABYSS") -> None:
        self.writer = writer
        self.cols = cols
        self.rows = rows
        self.alt_screen = alt_screen
        self.title = title
        self.prev: Optional[List[Tuple]] = None
        self._style_cache: Dict[Tuple, bytes] = {}

    # ------------------------------------------------------- terminal state

    def start(self) -> None:
        self.enter(self.alt_screen)

    def end(self) -> None:
        self.leave(self.alt_screen)

    def render(self, frame: Frame) -> None:
        self.present(frame)

    def enter(self, alt_screen: bool = True) -> None:
        w = self.writer
        w.write(b"\x1b[?25l")  # hide cursor
        if alt_screen:
            w.write(b"\x1b[?1049h")
        w.write(b"\x1b[0m")
        w.write(b"\x1b[H\x1b[2J")
        w.write(b"\x1b]0;" + self.title.encode("utf-8", "replace") + b"\x07")  # window title
        w.flush()

    def leave(self, alt_screen: bool = True) -> None:
        w = self.writer
        w.write(b"\x1b[0m")
        w.write(b"\x1b[?25h")
        w.write(b"\x1b[?2026l")
        if alt_screen:
            w.write(b"\x1b[?1049l")
        w.write(b"\x1b]0;snake\x07")
        w.flush()

    def set_size(self, cols: int, rows: int) -> None:
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.prev = None  # force a full repaint

    def clear(self) -> None:
        self.prev = None
        self.writer.write(b"\x1b[0m\x1b[H\x1b[2J")
        self.writer.flush()

    # -------------------------------------------------------------- styles

    def _style(self, fg: Optional[RGB], bg: Optional[RGB], attr: int) -> bytes:
        key = (fg, bg, attr)
        cached = self._style_cache.get(key)
        if cached is not None:
            return cached
        parts = ["0"]
        for bit, code in _ATTR_CODES.items():
            if attr & bit:
                parts.append(code)
        if fg is None:
            parts.append("39")
        else:
            parts.append(f"38;2;{fg[0]};{fg[1]};{fg[2]}")
        if bg is None:
            parts.append("49")
        else:
            parts.append(f"48;2;{bg[0]};{bg[1]};{bg[2]}")
        out = b"\x1b[" + ";".join(parts).encode("ascii") + b"m"
        self._style_cache[key] = out
        return out

    # --------------------------------------------------------------- frame

    def present(self, frame: Frame) -> None:
        if frame.cols != self.cols or frame.rows != self.rows:
            self.set_size(frame.cols, frame.rows)

        total = frame.cols * frame.rows
        prev = self.prev
        if prev is None or len(prev) != total:
            changed_all = True
            prev = [None] * total  # type: ignore[assignment]
        else:
            changed_all = False

        out: List[bytes] = [self.BEGIN_SYNC]
        cursor = -1  # cell index the cursor currently sits on (unknown -> -1)

        i = 0
        while i < total:
            key = frame.cell_key(i)
            if not changed_all and prev[i] == key:
                i += 1
                continue

            style = self._style(key[1], key[2], key[3])
            row_end = ((i // self.cols) + 1) * self.cols
            stop = min(row_end, total)
            chunk: List[str] = []
            j = i
            while j < stop:
                k = frame.cell_key(j)
                if not changed_all and prev[j] == k:
                    break
                if chunk and (k[1], k[2], k[3]) != (key[1], key[2], key[3]):
                    break
                chunk.append(frame.chars[j])
                j += 1

            if cursor != i:
                out.append(f"\x1b[{i // self.cols + 1};{i % self.cols + 1}H".encode("ascii"))
            out.append(style)
            out.append("".join(chunk).encode("utf-8", "replace"))
            cursor = j - 1
            i = j

        out.append(self.END_SYNC)
        payload = b"".join(out)
        self.writer.write(payload)
        self.writer.flush()

        # snapshot for the next diff
        self.prev = [frame.cell_key(i) for i in range(total)]
