"""Presentation-only juice: particles, shockwave rings, floating score, shake.

Nothing here can affect the game - it consumes events from the engine and
produces pixels.  Every effect is time-based (not frame-based) so it looks the
same at 15fps as at 60fps.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .color import RGB, lerp, scale
from .render import BOLD, Frame
from .theme import Theme

MAX_PARTICLES = 420


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    ch: str
    color: RGB


@dataclass
class Popup:
    x: float
    y: float
    text: str
    life: float
    max_life: float
    color: RGB


@dataclass
class Ring:
    x: float
    y: float
    radius: float
    speed: float
    life: float
    max_life: float
    color: RGB
    ch: str


class Effects:
    """All transient visuals, updated once per rendered frame."""

    def __init__(self, enabled: bool = True, seed: int = 1234) -> None:
        self.enabled = enabled
        self.particles: List[Particle] = []
        self.popups: List[Popup] = []
        self.rings: List[Ring] = []
        self.shake = 0.0
        self.flash = 0.0
        self.flash_color: RGB = (255, 255, 255)
        self.rng = random.Random(seed)
        self._shake_clock = 0.0

    # --------------------------------------------------------------- spawns

    def spawn_burst(self, x: int, y: int, colors: Sequence[RGB], glyphs: Sequence[str], count: int = 12, power: float = 6.0) -> None:
        if not self.enabled:
            return
        for _ in range(count):
            if len(self.particles) >= MAX_PARTICLES:
                break
            angle = self.rng.uniform(0.0, math.tau)
            speed = self.rng.uniform(0.4, 1.0) * power
            life = self.rng.uniform(0.25, 0.75)
            self.particles.append(
                Particle(
                    x + 0.5,
                    y + 0.5,
                    math.cos(angle) * speed,
                    math.sin(angle) * speed * 0.55,  # cells are tall: squash vertically
                    life,
                    life,
                    self.rng.choice(tuple(glyphs)),
                    self.rng.choice(tuple(colors)) if colors else (255, 255, 255),
                )
            )

    def spawn_ring(self, x: int, y: int, color: RGB, ch: str, speed: float = 9.0, life: float = 0.4) -> None:
        if not self.enabled:
            return
        self.rings.append(Ring(x + 0.5, y + 0.5, 0.2, speed, life, life, color, ch))

    def spawn_popup(self, x: int, y: int, text: str, color: RGB, life: float = 0.85) -> None:
        self.popups.append(Popup(float(x), float(y), text, life, life, color))

    def kick(self, amount: float, color: Optional[RGB] = None, flash: float = 0.0) -> None:
        if self.enabled:
            self.shake = min(1.0, self.shake + amount)
            if color is not None and flash > 0:
                self.flash = max(self.flash, flash)
                self.flash_color = color

    # ------------------------------------------------------------- lifecycle

    def eat(self, x: int, y: int, theme: Theme, points: int, combo: int, golden: bool = False) -> None:
        colors = (theme.golden, theme.golden_glow) if golden else (theme.apple, theme.apple_glow, theme.accent)
        glyphs = theme.glyphs.particles
        count = 10 + min(14, combo * 2) + (8 if golden else 0)
        self.spawn_burst(x, y, colors, glyphs, count=count, power=6.5 if not golden else 8.5)
        self.spawn_ring(x, y, theme.golden if golden else theme.apple_glow, theme.glyphs.sparkle, speed=11.0 if golden else 8.0)
        label = f"+{points}"
        if combo > 1:
            label += f" x{combo}"
        self.spawn_popup(x, y, label, theme.golden if golden else theme.popup)
        self.kick(0.10 + 0.05 * combo + (0.15 if golden else 0.0), flash=0.10 if golden else 0.0)

    def death(self, x: int, y: int, theme: Theme) -> None:
        self.spawn_burst(x, y, (theme.danger, theme.snake_head, theme.accent), theme.glyphs.particles, count=34, power=10.0)
        self.spawn_ring(x, y, theme.danger, theme.glyphs.sparkle, speed=16.0, life=0.6)
        self.spawn_ring(x, y, theme.snake_mid, theme.glyphs.particles[0], speed=9.0, life=0.45)
        self.kick(1.0, color=theme.danger, flash=0.5)

    def level_up(self, x: int, y: int, theme: Theme, level: int) -> None:
        self.spawn_popup(x, y - 1, f"LEVEL {level}", theme.accent2, life=1.1)
        self.spawn_ring(x, y, theme.accent2, theme.glyphs.sparkle, speed=13.0, life=0.5)
        self.kick(0.22)

    def expire(self, x: int, y: int, theme: Theme) -> None:
        self.spawn_burst(x, y, (theme.text_dim,), theme.glyphs.particles, count=6, power=3.0)
        self.spawn_popup(x, y, "gone", theme.text_dim, life=0.7)

    def win(self, x: int, y: int, theme: Theme) -> None:
        self.spawn_burst(x, y, (theme.golden, theme.accent, theme.accent2, theme.snake_head), theme.glyphs.particles, count=90, power=13.0)
        self.spawn_ring(x, y, theme.golden, theme.glyphs.sparkle, speed=18.0, life=0.9)
        self.kick(0.6, color=theme.golden, flash=0.35)

    def update(self, dt: float) -> None:
        damp = math.exp(-3.0 * dt)
        for p in self.particles:
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vx *= damp
            p.vy = p.vy * damp + 2.2 * dt  # gravity: crumbs fall
            p.life -= dt
        self.particles = [p for p in self.particles if p.life > 0]

        for p in self.popups:
            p.y -= 1.4 * dt
            p.life -= dt
        self.popups = [p for p in self.popups if p.life > 0]

        for r in self.rings:
            r.radius += r.speed * dt
            r.life -= dt
        self.rings = [r for r in self.rings if r.life > 0]

        if self.shake > 0:
            self.shake = max(0.0, self.shake - 2.6 * dt)
            self._shake_clock += dt
        if self.flash > 0:
            self.flash = max(0.0, self.flash - 3.2 * dt)

    def shake_offset(self) -> Tuple[int, int]:
        """Jitter the playfield origin; the field is clamped by the caller."""
        if self.shake <= 0.01:
            return (0, 0)
        amp = self.shake
        dx = int(round(math.sin(self._shake_clock * 47.0) * amp * 1.6))
        dy = int(round(math.cos(self._shake_clock * 61.0) * amp * 0.9))
        return (max(-1, min(1, dx)), max(-1, min(1, dy)))

    def clear(self) -> None:
        self.particles.clear()
        self.popups.clear()
        self.rings.clear()
        self.popups.clear()
        self.shake = 0.0
        self.flash = 0.0

    # ----------------------------------------------------------------- draw

    def draw(self, f: Frame, ox: int, oy: int, w: int, h: int, theme: Theme) -> None:
        g = theme.glyphs
        # rings first, so particles sit on top
        for r in self.rings:
            frac = r.life / r.max_life
            color = scale(r.color, 0.35 + 0.65 * frac)
            steps = max(8, int(r.radius * 6))
            for i in range(steps):
                angle = (i / steps) * math.tau
                x = int(round(r.x + math.cos(angle) * r.radius))
                y = int(round(r.y + math.sin(angle) * r.radius * 0.6))
                if 0 <= x < w and 0 <= y < h:
                    f.set(ox + x, oy + y, r.ch, color, None, BOLD if frac > 0.6 else 0)

        for p in self.particles:
            frac = p.life / p.max_life
            x = int(p.x)
            y = int(p.y)
            if 0 <= x < w and 0 <= y < h:
                f.set(ox + x, oy + y, p.ch, scale(p.color, 0.25 + 0.75 * frac), None, BOLD if frac > 0.55 else 0)

        for p in self.popups:
            frac = p.life / p.max_life
            y = int(round(p.y))
            x = int(round(p.x - len(p.text) / 2.0))
            color = scale(p.color, 0.3 + 0.7 * frac)
            for i, ch in enumerate(p.text):
                f.set(ox + x + i, oy + y, ch, color, None, BOLD)
