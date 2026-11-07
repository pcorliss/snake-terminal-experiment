"""NEON ABYSS — a terminal snake game.

Package layout (all standard library, no third-party runtime deps):

* :mod:`snake.config`  - CLI parsing, difficulty presets, speed curve
* :mod:`snake.color`   - RGB math (lerp / hsv / blending / scaling)
* :mod:`snake.theme`   - palettes + glyph sets (unicode & ascii fallback)
* :mod:`snake.engine`  - pure, deterministic game logic (no I/O at all)
* :mod:`snake.render`  - cell buffer + diffed ANSI presenters
* :mod:`snake.keys`    - raw-mode keyboard decoding
* :mod:`snake.audio`   - procedural WAV synthesis + async playback
* :mod:`snake.effects` - particles, popups, shake, flash (presentation only)
* :mod:`snake.screens` - menus, HUD, overlays
* :mod:`snake.app`     - the game loop that stitches everything together
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
