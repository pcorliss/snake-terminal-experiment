"""Tiny RGB colour toolkit.

The renderer talks to the terminal in 24-bit true colour, so every colour in
the game is an ``(r, g, b)`` tuple of ints in ``0..255``.  All of the visual
polish (snake body gradients, pulsing food, CRT vignette, particle fades) is
built out of the handful of pure functions below.
"""

from __future__ import annotations

import colorsys
from typing import Tuple

RGB = Tuple[int, int, int]

BLACK: RGB = (0, 0, 0)
WHITE: RGB = (255, 255, 255)


def clamp8(v: float) -> int:
    """Clamp a float channel into the 0..255 integer range."""
    if v <= 0.0:
        return 0
    if v >= 255.0:
        return 255
    return int(v + 0.5)


def hex_rgb(text: str) -> RGB:
    """Parse ``#rrggbb`` (or ``rrggbb``) into an RGB tuple."""
    text = text.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"bad hex colour: {text!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    """Linear interpolation between two colours."""
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    return (
        clamp8(a[0] + (b[0] - a[0]) * t),
        clamp8(a[1] + (b[1] - a[1]) * t),
        clamp8(a[2] + (b[2] - a[2]) * t),
    )


def scale(c: RGB, factor: float) -> RGB:
    """Multiply brightness (used for vignette / scanlines / fades)."""
    if factor == 1.0:
        return c
    return (clamp8(c[0] * factor), clamp8(c[1] * factor), clamp8(c[2] * factor))


def add(c: RGB, amount: float) -> RGB:
    """Add flat light to every channel (soft glow, never saturating to white)."""
    if amount == 0.0:
        return c
    return (clamp8(c[0] + amount), clamp8(c[1] + amount), clamp8(c[2] + amount))


def hsv(h: float, s: float, v: float) -> RGB:
    """HSV -> RGB with hue wrapping (h in turns, i.e. ``1.0 == 360deg``)."""
    h = h % 1.0
    s = 0.0 if s < 0.0 else (1.0 if s > 1.0 else s)
    v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (clamp8(r * 255), clamp8(g * 255), clamp8(b * 255))


def mix3(c0: RGB, c1: RGB, c2: RGB, t: float) -> RGB:
    """Three-stop gradient: ``c0 -> c1 -> c2`` as ``t`` walks 0..1."""
    if t < 0.5:
        return lerp(c0, c1, t * 2.0)
    return lerp(c1, c2, (t - 0.5) * 2.0)


def relative_luma(c: RGB) -> float:
    """Perceptual luminance, 0..1 (ITU-R BT.601)."""
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0


def readable(fg: RGB, bg: RGB, min_delta: float = 0.28) -> RGB:
    """Nudge ``fg`` brighter until it is legible against ``bg``.

    Handy for popups/particles drawn over a brightened background so text never
    disappears into a glow.
    """
    if relative_luma(fg) - relative_luma(bg) >= min_delta:
        return fg
    delta = relative_luma(bg) + min_delta - relative_luma(fg)
    return add(fg, delta * 255.0)
