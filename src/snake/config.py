"""Configuration: difficulty presets, key bindings, command line parsing."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from .engine import Rules
from .theme import GRAPHITE, THEME_ORDER, THEMES, Theme, ASCII_GLYPHS, UNICODE_GLYPHS

APP_DIR = ".snake"
CACHE_DIR = ".cache"
SCORE_FILE = os.path.join(APP_DIR, "scores.json")
TITLE = "NEON ABYSS"


@dataclass(frozen=True)
class Difficulty:
    key: str
    label: str
    blurb: str
    wrap: bool
    obstacles: bool
    obstacle_from_level: int
    obstacles_per_level: int
    speed_base: float
    speed_decay: float
    speed_floor: float
    food_per_level: int
    golden_chance: float
    lives: int = 1

    def rules(self, width: int, height: int, seed: Optional[int]) -> Rules:
        return Rules(
            width=width,
            height=height,
            wrap=self.wrap,
            obstacles=self.obstacles,
            obstacle_from_level=self.obstacle_from_level,
            obstacles_per_level=self.obstacles_per_level,
            speed_base=self.speed_base,
            speed_decay=self.speed_decay,
            speed_floor=self.speed_floor,
            food_per_level=self.food_per_level,
            golden_chance=self.golden_chance,
            seed=seed,
        )


DIFFICULTIES: Dict[str, Difficulty] = {
    d.key: d
    for d in (
        Difficulty(
            key="garden",
            label="GARDEN",
            blurb="wrap edges, no shards, forgiving pace",
            wrap=True,
            obstacles=False,
            obstacle_from_level=99,
            obstacles_per_level=0,
            speed_base=0.155,
            speed_decay=0.94,
            speed_floor=0.085,
            food_per_level=6,
            golden_chance=0.35,
        ),
        Difficulty(
            key="classic",
            label="CLASSIC",
            blurb="solid walls, shards from level 3",
            wrap=False,
            obstacles=True,
            obstacle_from_level=3,
            obstacles_per_level=3,
            speed_base=0.140,
            speed_decay=0.90,
            speed_floor=0.058,
            food_per_level=5,
            golden_chance=0.30,
        ),
        Difficulty(
            key="velocity",
            label="VELOCITY",
            blurb="fast out of the gate, shards at 2",
            wrap=False,
            obstacles=True,
            obstacle_from_level=2,
            obstacles_per_level=5,
            speed_base=0.115,
            speed_decay=0.92,
            speed_floor=0.045,
            food_per_level=4,
            golden_chance=0.38,
        ),
        Difficulty(
            key="void",
            label="VOID",
            blurb="wrap edges, dense shards, no mercy",
            wrap=True,
            obstacles=True,
            obstacle_from_level=2,
            obstacles_per_level=4,
            speed_base=0.128,
            speed_decay=0.91,
            speed_floor=0.05,
            food_per_level=4,
            golden_chance=0.40,
        ),
    )
}
DIFFICULTY_ORDER: Tuple[str, ...] = tuple(DIFFICULTIES)


# key -> (label, description) for the in-game help overlay.
KEY_HELP: List[Tuple[str, str]] = [
    ("arrows / wasd / hjkl", "steer the snake"),
    ("space", "jump ahead one step (skip the wait)"),
    ("p", "pause / resume"),
    ("q  or  esc", "back to menu (ends the run)"),
    ("r", "restart instantly, same settings"),
    ("n", "new board (ignores a pinned --seed)"),
    ("1 2 3 4", "difficulty: garden / classic / velocity / void"),
    ("m  /  M", "sound effects on/off  /  music on/off"),
    ("t", "cycle colour theme"),
    ("u", "unicode glyphs / plain ascii glyphs"),
    ("f", "particles, shake, vignette and scanlines on/off"),
    ("?", "this help screen"),
    ("f5  or  ctrl+c", "hard quit (the terminal is always restored)"),
]


@dataclass
class Settings:
    width: Optional[int] = None
    height: Optional[int] = None
    fps: int = 30
    difficulty: str = "classic"
    theme: str = "neon"
    ascii_glyphs: bool = False
    sound: bool = True
    music: bool = True
    fx: bool = True
    seed: Optional[int] = None
    demo: bool = False
    frames: Optional[int] = None
    headless: bool = False
    no_alt_screen: bool = False
    audio_cache: str = os.path.join(CACHE_DIR, "audio")
    score_file: str = SCORE_FILE
    # Scripted key names for headless runs/tests (``["right", " ", "q"]``).
    # Not reachable from the command line on purpose: tests set it directly.
    input_keys: Optional[List[str]] = None

    def theme_obj(self) -> Theme:
        theme = THEMES[self.theme]
        return theme.with_glyphs(ASCII_GLYPHS if self.ascii_glyphs else UNICODE_GLYPHS)

    def difficulty_obj(self) -> Difficulty:
        return DIFFICULTIES[self.difficulty]


def _size_spec(text: str) -> Tuple[int, int]:
    try:
        w, h = text.lower().replace("*", "x").split("x")
        w, h = int(w), int(h)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must look like 40x20") from exc
    if not (5 <= w <= 200 and 5 <= h <= 200):
        raise argparse.ArgumentTypeError("size must be between 5x5 and 200x200")
    return w, h


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="snake",
        description=(
            f"{TITLE} - a terminal snake game with synthesized sound, "
            "true-colour graphics and a deterministic engine."
        ),
        epilog="keys are listed in-game on the help overlay (?); menu shows everything else.",
    )
    p.add_argument("--size", type=_size_spec, default=None, metavar="WxH", help="playfield size (default: fit the terminal)")
    p.add_argument(
        "--difficulty",
        "-d",
        choices=DIFFICULTY_ORDER,
        default="classic",
        help="ruleset preset (default: classic)",
    )
    p.add_argument("--theme", "-T", choices=THEME_ORDER, default="neon", help="colour theme (default: neon)")
    p.add_argument("--ascii", action="store_true", help="use only 7-bit glyphs (for finicky terminals)")
    p.add_argument("--fps", type=int, default=30, choices=range(10, 61), metavar="10-60", help="frame rate (default: 30)")
    p.add_argument("--seed", type=int, default=None, help="fixed seed for a reproducible board")
    p.add_argument("--demo", action="store_true", help="attract mode: an autopilot snake plays")
    p.add_argument("--frames", type=int, default=None, help="exit after N frames (headless runs/tests)")
    p.add_argument("--headless", action="store_true", help="no terminal I/O at all; render to a buffer (testing)")
    p.add_argument("--no-alt-screen", action="store_true", help="draw inline instead of switching to the alt screen")
    p.add_argument("--mute", action="store_true", help="start without sound effects")
    p.add_argument("--no-music", action="store_true", help="start without the ambient score")
    p.add_argument("--no-fx", action="store_true", help="disable particles, shake and crt post-processing")
    from . import __version__

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def parse_args(argv: Optional[List[str]] = None) -> Settings:
    parser = build_parser()
    ns = parser.parse_args(argv)
    s = Settings(
        fps=ns.fps,
        difficulty=ns.difficulty,
        theme=ns.theme,
        ascii_glyphs=ns.ascii,
        sound=not ns.mute,
        music=not ns.no_music,
        fx=not ns.no_fx,
        seed=ns.seed,
        demo=ns.demo,
        frames=ns.frames,
        headless=ns.headless,
        no_alt_screen=ns.no_alt_screen,
    )
    if ns.size:
        s.width, s.height = ns.size
    return s
