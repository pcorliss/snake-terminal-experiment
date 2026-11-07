"""The playfield itself: snake, food, shards, particles.

Two choices here define the look:

* The body is drawn as *connected pipe glyphs* (`╭ ╮ ╰ ╯ ─ │ ╰╴`) rather than
  solid blocks.  A body of `████` turns into an unreadable blob the first time
  it bends; a body of pipes stays legible as one continuous creature, and it
  still reads correctly in plain ASCII mode (`+` corners, `-`/`|` runs).
* Bright things get a background *halo*.  Terminals cannot blend, so "glow"
  has to be faked by tinting the cell under an object.  Cheap, and it makes the
  head read as a lamp travelling through the abyss.

Juice that is not game state (eat flash, the death wave, turbo) lives in
:class:`Visuals` here, never in the engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .color import RGB, lerp, mix3
from .effects import Effects
from .engine import Food, SnakeGame
from .render import BOLD, Frame
from .screens import Layout
from .theme import Theme

XY = Tuple[int, int]


@dataclass
class Visuals:
    """Presentation-only animation state for one run."""

    eat_flash: float = 0.0  # seconds since the last bite
    death_wave: float = 0.0  # cells of the body that have "burned out"
    boost: bool = False

    def tick(self, dt: float) -> None:
        self.eat_flash = max(0.0, self.eat_flash - dt)

    def bite(self) -> None:
        self.eat_flash = 0.35

    def begin_death(self) -> None:
        self.death_wave = 0.0

    def advance_death(self, dt: float, total: int) -> None:
        self.death_wave = min(float(total), self.death_wave + dt * 26.0)


def body_color(theme: Theme, index: int, total: int) -> RGB:
    """Head-to-tail gradient through three stops, so the hue travels."""
    if total <= 1:
        return theme.snake_head
    t = index / (total - 1)
    return mix3(theme.snake_head, theme.snake_mid, theme.snake_tail, t)


def _halo(f: Frame, x: int, y: int, color: RGB, amount: float) -> None:
    """Tint the background under a cell so bright things appear to glow."""
    if not f.inside(x, y) or amount <= 0:
        return
    i = f.idx(x, y)
    base = f.bg[i]
    if base is None:
        return
    f.bg[i] = lerp(base, color, min(1.0, amount))


def draw_shards(f: Frame, layout: Layout, game: SnakeGame, theme: Theme) -> None:
    for (x, y) in game.obstacles:
        px, py = layout.field_x + 1 + x, layout.field_y + 1 + y
        f.set(px, py, theme.glyphs.obstacle, theme.obstacle, None, BOLD)
        _halo(f, px, py, theme.obstacle, 0.25)


def draw_food(f: Frame, layout: Layout, food: Food, theme: Theme, clock: float, golden_ttl: int) -> None:
    px, py = layout.field_x + 1 + food.x, layout.field_y + 1 + food.y
    if food.kind == "golden":
        pulse = 0.5 + 0.5 * math.sin(clock * 9.0)
        color = lerp(theme.golden, theme.accent2, 0.20 + 0.25 * pulse)
        ttl_frac = (food.ttl / golden_ttl) if golden_ttl else 1.0
        if ttl_frac < 0.3 and int(clock * 9) % 2 == 0:
            color = lerp(color, theme.text_dim, 0.75)
        f.set(px, py, theme.glyphs.golden, color, None, BOLD)
        _halo(f, px, py, theme.golden_glow, 0.18 + 0.24 * pulse * ttl_frac)
    else:
        pulse = 0.5 + 0.5 * math.sin(clock * 3.1 + 0.7 * (food.x + food.y))
        f.set(px, py, theme.glyphs.apple, lerp(theme.apple, theme.accent, 0.12 * pulse), None, BOLD)
        _halo(f, px, py, theme.apple_glow, 0.13 + 0.12 * pulse)


def draw_snake(f: Frame, layout: Layout, game: SnakeGame, theme: Theme, vis: Visuals, clock: float, fx_enabled: bool) -> None:
    body: List[XY] = game.snake
    pipes = game.pipes()
    total = len(body)
    dead = not game.alive
    glyphs = theme.glyphs

    # tail first, so the head always wins where things overlap
    for i in range(total - 1, -1, -1):
        x, y = body[i]
        px, py = layout.field_x + 1 + x, layout.field_y + 1 + y
        color = body_color(theme, i, total)
        mask = pipes[i]
        burning = dead and i >= total - int(vis.death_wave)
        if burning:
            color = lerp(color, theme.danger, 0.85)
        if i == 0:
            glyph = glyphs.dead if dead else glyphs.head
            if dead:
                color = theme.danger
            elif vis.eat_flash > 0:
                color = lerp(color, (255, 255, 255), min(0.75, vis.eat_flash * 2.2))
            f.set(px, py, glyph, color, None, BOLD)
            if fx_enabled and not dead:
                glow = 0.18 + 0.10 * math.sin(clock * 6.5) + (0.2 if vis.boost else 0.0)
                _halo(f, px, py, color, glow)
                for nx, ny in ((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)):
                    _halo(f, nx, ny, color, glow * 0.45)
        else:
            glyph = glyphs.pipes.get(mask, glyphs.h)
            # straight middle runs stay unbold: the bold cells then trace the
            # shape of the snake (turns, junctions, the neck) instead of a tube
            bold = i <= 1 or mask not in (5, 10)
            f.set(px, py, glyph, color, None, BOLD if bold else 0)


def draw_playfield(
    f: Frame,
    layout: Layout,
    game: SnakeGame,
    theme: Theme,
    effects: Effects,
    vis: Visuals,
    clock: float,
    fx_enabled: bool,
) -> None:
    draw_shards(f, layout, game, theme)
    golden_ttl = game.rules.golden_ttl
    for food in game.foods_sorted():
        draw_food(f, layout, food, theme, clock, golden_ttl)
    draw_snake(f, layout, game, theme, vis, clock, fx_enabled)
    if fx_enabled:
        effects.draw(f, layout.field_x + 1, layout.field_y + 1, layout.gw, layout.gh, theme)
