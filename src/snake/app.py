"""The application: one loop, five modes, every subsystem wired together.

    menu  <-> help
      |
      v
    play  <-> pause          help is an overlay, so the board freezes
      |
      v
    over  -> play again / menu

Design notes
------------
The engine advances on the **step** clock (`game.step_interval()`, 7-18 Hz)
while the frame loop runs at `--fps` (10-60). The two are decoupled on purpose:
particles, the pulsing food and key echo want 60 Hz, the snake does not, and
keeping them separate is what makes a step countable and therefore testable.

Input is split the same way. **Steering goes to the engine immediately**
(`push_dir` is an engine-side queue, so two turns inside one tick both
register - the double-tap corner that every snake player expects).
Everything else - pause, restart, quit - is applied to the *app* immediately,
because those are menu actions, not simulation actions.

The background (gradient + floor checkers) is painted once into its own
`Frame` and blitted with `Frame.copy_layer`, so a full redraw costs one list
copy per channel plus the few dozen cells that actually changed.
"""

from __future__ import annotations

import dataclasses
import signal
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from .audio import AudioEngine
from .config import DIFFICULTIES, DIFFICULTY_ORDER, Settings
from .effects import Effects
from .engine import DIRS, Event, SnakeGame
from .keys import KeyReader, install_resize_handler, terminal_size
from .present import HeadlessPresenter
from .render import AnsiPresenter, Frame, PostFX
from .scores import ScoreBook
from .screens import (
    DecorSnake,
    HudData,
    MenuItem,
    Starfield,
    build_background,
    compute_layout,
    draw_board_frame,
    draw_game_over,
    draw_help,
    draw_hud,
    draw_menu,
    draw_pause,
    draw_status_bar,
    draw_title_bar,
    fmt_num,
)
from .theme import ASCII_GLYPHS, THEMES, THEME_ORDER, UNICODE_GLYPHS
from .view import Visuals, draw_playfield

IDLE_DEMO_SECONDS = 60.0  # the menu plays itself after a minute; any key returns
DEMO_HOLD_SECONDS = 2.8  # how long attract mode admires its own corpse
TURBO_STEP = 0.062
MAX_STEPS_PER_FRAME = 4  # a scheduler hiccup must never fast-forward the game

MENU_HINTS = ["enter start", "1-4 difficulty", "t theme", "? keys", "q quit"]
PLAY_HINTS = ["arrows steer", "space turbo", "p pause", "r restart", "n new board", "? keys"]
PAUSE_HINTS = ["p resume", "r restart", "q menu", "? keys"]
OVER_HINTS = ["enter again", "1-4 difficulty", "t theme", "m menu", "q quit"]

HEADLESS_COLS, HEADLESS_ROWS = 100, 30
NOTE_SECONDS = 3.0  # how long a transient status note stays on screen


@dataclass
class Run:
    """Everything that belongs to one playthrough."""

    game: SnakeGame
    demo: bool = False
    acc: float = 0.0
    elapsed: float = 0.0  # simulated seconds spent playing (pausing does not count)
    cause: str = ""
    rank: int = 0
    record: bool = False
    demo_hold: float = 0.0
    won: bool = False


class App:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.running = True
        self.mode = "menu"
        self.help_from = "menu"

        # --- presentation state (never engine state)
        self.theme_key = settings.theme
        self.ascii = settings.ascii_glyphs
        self.diff_key = settings.difficulty
        self.sound_on = settings.sound
        self.music_on = settings.music
        self.fx_on = settings.fx
        self.turbo = False
        self.clock = 0.0
        self.idle = 0.0
        self.deaths = 0
        self.frame_dt = 1.0 / settings.fps
        self.fps = float(settings.fps)

        # --- sizes
        if settings.headless:
            # A fixed virtual terminal, so captured frames and snapshot-style
            # tests do not change when you run them from a different window.
            self.cols, self.rows = HEADLESS_COLS, HEADLESS_ROWS
        else:
            self.cols, self.rows = terminal_size()
        self.gw, self.gh = self._fit_board(self.cols, self.rows)
        self.layout = compute_layout(self.cols, self.rows, self.gw, self.gh)

        self.theme = self._theme_for(self.theme_key, self.ascii)
        self.postfx = self._postfx_for(self.cols, self.rows)
        self.frame = Frame(self.cols, self.rows)
        self.bg_layer = Frame(self.cols, self.rows)
        self.effects = Effects(enabled=self.fx_on, seed=settings.seed or 1234)
        self.vis = Visuals()
        self.star = Starfield(seed=(settings.seed or 4242) + 7)
        self.star.resize(self.cols, self.rows)
        self.decor = DecorSnake(seed=(settings.seed or 11) + 3)

        self.book = ScoreBook(settings.score_file)
        self.audio = AudioEngine(settings.audio_cache, enabled=self.sound_on, music=self.music_on)

        self.run: Optional[Run] = None
        self.menu_scores: List[str] = []
        self.note = ""
        self.note_until = 0.0
        self.keys: Optional[KeyReader] = None
        self.resized = False
        self._presenter = None
        self._audio_live = False

    # ------------------------------------------------------------- theme/sizes

    @staticmethod
    def _theme_for(key: str, ascii_glyphs: bool):
        return THEMES[key].with_glyphs(ASCII_GLYPHS if ascii_glyphs else UNICODE_GLYPHS)

    def _postfx_for(self, cols: int, rows: int) -> PostFX:
        if not self.fx_on:
            return PostFX(cols, rows, 0.0, 1.0)
        return PostFX(cols, rows, self.theme.vignette, self.theme.scanline)

    def _fit_board(self, cols: int, rows: int):
        """Board size in cells: what the user asked for, clamped to this terminal.

        The HUD needs ~32 columns beside the board (`compute_layout` shows it
        when cols >= gw + 32), so the default board leaves room for it instead
        of pushing it off screen.
        """
        want_w = self.s.width or 30
        want_h = self.s.height or 18
        hud_reserve = 32 if cols >= want_w + 32 else 0
        max_w = max(8, cols - 4 - hud_reserve)
        max_h = max(6, rows - 4)
        return min(want_w, max_w), min(want_h, max_h)

    def _rebuild_chrome(self) -> None:
        """Re-paint the cached background after a resize or theme change."""
        self.postfx = self._postfx_for(self.cols, self.rows)
        self.bg_layer = Frame(self.cols, self.rows)
        build_background(self.bg_layer, self.theme, self.layout)

    def _resize(self) -> None:
        cols, rows = terminal_size()
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        self.frame = Frame(cols, rows)
        if self.run is None:
            # the board has not been committed yet, so it may still resize
            self.gw, self.gh = self._fit_board(cols, rows)
        # a live run keeps its board; it just gets re-centred (and clipped if
        # the terminal became genuinely too small)
        self.layout = compute_layout(cols, rows, self.gw, self.gh)
        self.star.resize(cols, rows)
        set_size = getattr(self._presenter, "set_size", None)
        if set_size is not None:
            set_size(cols, rows)
        self._rebuild_chrome()

    # ------------------------------------------------------------- lifecycle

    def _make_presenter(self):
        if self.s.headless:
            return HeadlessPresenter(out=sys.stdout, postfx=None)
        return AnsiPresenter(_ByteWriter(sys.stdout.buffer), self.cols, self.rows)

    def run_forever(self) -> int:
        if not self.s.headless and not (sys.stdin.isatty() and sys.stdout.isatty()):
            sys.stderr.write(
                "snake: this is not a terminal.\n"
                "       run it from a real terminal, or use --headless to render frames to stdout.\n"
            )
            return 2

        if self.s.demo:
            self.mode = "play"
            self._start_run(self._attract_seed(), demo=True)
        elif self.s.frames is not None and not self.s.headless:
            # frames + a real terminal still needs a terminal
            pass

        self._presenter = self._make_presenter()
        if not self.s.headless:
            self.keys = KeyReader()
            self.keys.open_raw()
            install_resize_handler(self._flag_resize)
            signal.signal(signal.SIGINT, self._flag_signal)
            signal.signal(signal.SIGTERM, self._flag_signal)
            if self.sound_on:
                # Synthesizing ~20 effects up front (a few hundred ms of CPU)
                # keeps the first keypress from sounding like a stutter.
                self.audio.warm()
            self._start_audio()
            if self.music_on:
                self._sync_music()

        try:
            self._presenter.start()
            self._rebuild_chrome()
            self.refresh_menu_scores()
            self._loop()
        finally:
            # every exit path (keys, EOF, Ctrl-C, SIGTERM, an exception) comes
            # through here, and only through here
            self._presenter.end()
            if self.keys is not None:
                self.keys.close_raw()
            self.audio.stop()
        return 0

    def _start_audio(self) -> None:
        if self._audio_live:
            return
        self.audio.start()
        self._audio_live = True

    def _flag_resize(self, *_a) -> None:
        self.resized = True  # signal handlers must not touch game state

    def _flag_signal(self, *_a) -> None:
        self.running = False

    def _loop(self) -> None:
        rendered = 0
        slow = 0
        last = time.perf_counter()
        while self.running:
            start = time.perf_counter()
            if self.s.headless:
                # A headless run steps on a fixed clock: same seed + same frame
                # count = byte-identical output, which is what tests need.
                dt = self.frame_dt
            else:
                dt = min(0.1, start - last)  # 0.1: a slept-on terminal must not fast-forward
            last = start

            self._tick(dt)
            if self.resized:
                self.resized = False
                self._resize()
            self._draw()
            self._presenter.render(self.frame)
            rendered += 1
            if self.s.frames is not None and rendered >= self.s.frames:
                self.running = False
                break

            spent = time.perf_counter() - start
            budget = max(0.0, self.frame_dt - spent)
            if self.s.headless:
                self._drive_headless()
            elif self.keys is not None:
                for key in self.keys.poll(budget):
                    self._key(key)
                for key in self.keys.drain():  # whatever arrived while drawing
                    self._key(key)
            if not self.s.headless and self.keys is None:
                time.sleep(budget)

            # Adaptive frame rate: a 200x60 board with scanlines can outrun a
            # laptop. Notice it, back off, and say so, rather than stutter.
            if spent > self.frame_dt * 1.6:
                slow += 1
            else:
                slow = 0
            if slow >= 8 and self.fps > 20.0:
                self.fps -= 4.0
                self.frame_dt = 1.0 / self.fps
                slow = 0

    def _drive_headless(self) -> None:
        """Feed scripted keys in headless mode, one per frame, so tests are deterministic."""
        keys = self.s.input_keys
        if not keys:
            return
        i = getattr(self, "_key_i", 0)
        if i < len(keys):
            self._key_i = i + 1
            self._key(keys[i])

    # ------------------------------------------------------------------- tick

    def _tick(self, dt: float) -> None:
        self.clock += dt
        self.idle += dt
        self.effects.update(dt)
        self.vis.tick(dt)
        self.decor.update(dt)
        if not self.s.headless:
            self.star.update(dt)

        if self.mode == "over":
            run = self.run
            if run is not None:
                self.vis.advance_death(dt, run.game.length())
                if run.demo:
                    run.demo_hold += dt
                    if run.demo_hold >= DEMO_HOLD_SECONDS:
                        self._start_run(self._attract_seed(), demo=True)
            return

        if self.mode == "menu":
            if self.idle >= IDLE_DEMO_SECONDS:
                self._start_run(self._attract_seed(), demo=True)
            return

        if self.mode != "play":  # pause/help: the simulation is frozen
            return

        run = self.run
        if run is None or not run.game.alive:
            return
        run.elapsed += dt
        if run.demo:
            self.turbo = False
            move = run.game.autopilot()
            if move is not None:
                run.game.push_dir(move)

        interval = self._step_interval(run)
        run.acc += dt
        self.audio.set_intensity(self._intensity())
        steps = 0
        while run.acc >= interval and steps < MAX_STEPS_PER_FRAME:
            run.acc -= interval
            steps += 1
            events = run.game.step()
            self._react(events)
            if not run.game.alive:
                break
            interval = self._step_interval(run)

        if not run.game.alive and self.mode == "play":
            if run.game.won:
                self._on_win(run)
            else:
                self._on_death(run, run.game.death_cause or "wall")

    def _step_interval(self, run: Run) -> float:
        interval = run.game.step_interval()
        if self.turbo and not run.demo:
            return min(interval, TURBO_STEP)
        return interval

    def _react(self, events: List[Event]) -> None:
        """Turn engine events into sound and particles. Never touches game state."""
        theme = self.theme
        for ev in events:
            if ev.type == "eat":
                self.audio.play("eat", combo=ev.combo)
                self.effects.eat(ev.x, ev.y, theme, ev.value, ev.combo)
                self.vis.bite()
            elif ev.type == "eat_golden":
                self.audio.play("golden")
                self.effects.eat(ev.x, ev.y, theme, ev.value, ev.combo, golden=True)
                self.vis.bite()
            elif ev.type == "level_up":
                self.audio.play("levelup")
                self.effects.level_up(ev.x, ev.y, theme, ev.level)
            elif ev.type == "bonus_expired":
                self.audio.play("expire", gain=0.5)
                self.effects.expire(ev.x, ev.y, theme)
            elif ev.type == "bonus_cleared":
                pass
            elif ev.type == "die":
                self.audio.play("die")
                self.effects.death(ev.x, ev.y, theme)
                self.vis.begin_death()
            elif ev.type == "win":
                self.audio.play("win")
                self.effects.win(ev.x, ev.y, theme)

    # ---------------------------------------------------------- run lifecycle

    def _attract_seed(self) -> int:
        """Attract mode should not show the same board twice in a row."""
        if self.s.seed is not None:
            return self.s.seed
        return (0x9E37_79B9 ^ (int(time.time() * 1000) & 0xFFFF_FFFF)) & 0x7FFF_FFFF

    def _next_seed(self, force_random: bool = False) -> int:
        if self.s.seed is not None and not force_random:
            return self.s.seed  # a pinned seed replays identically; that is the point
        self._seed_chain = getattr(self, "_seed_chain", 0)
        self._seed_chain = (self._seed_chain * 1103515245 + 12345) & 0x7FFF_FFFF
        return self._seed_chain

    def _start_run(self, seed: int, demo: bool = False) -> None:
        if demo:
            self.diff_key = DIFFICULTY_ORDER[seed % len(DIFFICULTY_ORDER)]
        diff = DIFFICULTIES[self.diff_key]
        rules = diff.rules(self.gw, self.gh, seed)
        game = SnakeGame(rules)
        self.run = Run(game=game, demo=demo)
        self.vis = Visuals()
        self.effects.clear()
        self.turbo = False
        self.mode = "play"
        self.idle = 0.0
        self.deaths = 0
        self._sync_music()
        self._say(f"seed {seed}")

    def _to_menu(self) -> None:
        self.run = None
        self.mode = "menu"
        self.idle = 0.0
        self.effects.clear()
        self.vis = Visuals()
        # the board is up for grabs again, so it may re-fit the terminal
        self.gw, self.gh = self._fit_board(self.cols, self.rows)
        self.layout = compute_layout(self.cols, self.rows, self.gw, self.gh)
        self._rebuild_chrome()
        self.refresh_menu_scores()
        self._sync_music()

    def _on_death(self, run: Run, cause: str) -> None:
        run.cause = cause
        self.deaths += 1
        self.turbo = False
        self._commit(run, won=False)
        self.mode = "over"
        self.idle = 0.0
        self._sync_music()

    def _on_win(self, run: Run) -> None:
        run.cause = "won"
        run.won = True
        self.turbo = False
        self._commit(run, won=True)
        self.mode = "over"
        self.idle = 0.0
        self._sync_music()

    def _commit(self, run: Run, won: bool) -> None:
        if run.demo:
            return  # attract mode scores are not anybody's high score
        game = run.game
        if game.score <= 0 and not won:
            return  # a walk into the first wall is not a score worth keeping
        run.rank = self.book.add(
            score=game.score,
            length=game.length(),
            level=game.level,
            difficulty=self.diff_key,
            theme=self.theme_key,
            seed=game.rules.seed,
            won=won,
        )
        run.record = run.rank == 1 and game.score > 0
        if run.record:
            self.audio.play("record")

    def refresh_menu_scores(self) -> None:
        rows = []
        for entry in self.book.ranked(None, 6):
            flag = " *" if entry.won else ""
            rows.append(f"{fmt_num(entry.score):>8}  {entry.difficulty[:6]:<6} lv{entry.level:<2} len{entry.length}{flag}")
        self.menu_scores = rows

    # ---------------------------------------------------------------- keys

    def _key(self, key: str) -> None:
        if key == "eof":
            self.running = False
            return
        self.idle = 0.0
        if key in ("f5", "ctrl+c", "ctrl+\\"):
            self.running = False
            return
        if self._key_global(key):
            return
        handler = {
            "menu": self._key_menu,
            "play": self._key_play,
            "pause": self._key_pause,
            "over": self._key_over,
            "help": self._key_help,
        }.get(self.mode)
        if handler is not None:
            handler(key)

    def _key_global(self, key: str) -> bool:
        """Keys that mean the same thing on every screen."""
        if key == "t":
            i = THEME_ORDER.index(self.theme_key)
            self.theme_key = THEME_ORDER[(i + 1) % len(THEME_ORDER)]
            self.theme = self._theme_for(self.theme_key, self.ascii)
            self._rebuild_chrome()
            self._sync_music()
            self.audio.play("select")
            self._say(f"theme {self.theme.label}")
            return True
        if key == "u":
            self.ascii = not self.ascii
            self.theme = self._theme_for(self.theme_key, self.ascii)
            self._rebuild_chrome()
            self.audio.play("select")
            self._say("ascii glyphs" if self.ascii else "unicode glyphs")
            return True
        if key == "f":
            self.fx_on = not self.fx_on
            self.effects.enabled = self.fx_on
            self._rebuild_chrome()
            self.audio.play("select")
            self._say("effects on" if self.fx_on else "effects off")
            return True
        if key == "m":
            self.sound_on = not self.sound_on
            self.audio.enabled = self.sound_on
            if self.sound_on:
                self.audio.warm()
                self._start_audio()
                self.audio.play("select")
            self._sync_music()
            return True
        if key == "M":
            self.music_on = not self.music_on
            self.audio.set_music_enabled(self.music_on)
            self.audio.play("select")
            return True
        if key in ("?", "/"):
            if self.mode != "help":
                self.help_from = self.mode
                self.mode = "help"
                self.audio.play("select")
            return True
        return False

    def _key_menu(self, key: str) -> None:
        n = len(DIFFICULTY_ORDER)
        if key in ("up", "k", "w", "left"):
            self._move_cursor(-1)
        elif key in ("down", "j", "s", "right"):
            self._move_cursor(1)
        elif key in ("1", "2", "3", "4"):
            self.diff_key = DIFFICULTY_ORDER[min(n - 1, int(key) - 1)]
            self.audio.play("select")
            self._say(DIFFICULTIES[self.diff_key].label)
        elif key in ("enter", " "):
            self.audio.play("start")
            self._start_run(self.s.seed if self.s.seed is not None else self._next_seed())
        elif key == "q":
            self.running = False
        elif key == "tab":
            self._move_cursor(1)
        else:
            self.audio.play("back", gain=0.35)

    def _move_cursor(self, delta: int) -> None:
        i = DIFFICULTY_ORDER.index(self.diff_key)
        self.diff_key = DIFFICULTY_ORDER[(i + delta) % len(DIFFICULTY_ORDER)]
        self.audio.play("move", gain=0.5)
        self._say(DIFFICULTIES[self.diff_key].label)

    def _key_play(self, key: str) -> None:
        run = self.run
        if run is None:
            return
        direction = DIRS.get(key)
        if direction is None and len(key) == 1:
            direction = {"w": DIRS["up"], "k": DIRS["up"], "s": DIRS["down"], "j": DIRS["down"],
                         "a": DIRS["left"], "h": DIRS["left"], "d": DIRS["right"], "l": DIRS["right"]}.get(key)
        if direction is not None:
            # steering is a simulation action: hand it straight to the engine's
            # own input queue, which is what makes double-taps work
            if run.game.push_dir(direction):
                self.audio.play("turn", gain=0.16)
            return
        if key == " ":
            self.turbo = not self.turbo
            self.vis.boost = self.turbo
            self.audio.play("select", gain=0.4)
            self._say("turbo on" if self.turbo else "turbo off")
        elif key == "p":
            self.mode = "pause"
            self.audio.play("pause")
        elif key in ("q", "esc"):
            self.audio.play("back")
            self._to_menu()
        elif key == "r":
            self.audio.play("start")
            self._start_run(self.s.seed if self.s.seed is not None else self._next_seed(), demo=run.demo)
        elif key in ("pgup", "pgdn", "n"):
            self.audio.play("start")
            self._start_run(self._next_seed(force_random=True), demo=run.demo)  # a different board
        else:
            self.audio.play("back", gain=0.3)

    def _key_pause(self, key: str) -> None:
        run = self.run
        if key in ("p", " ", "enter", "esc"):
            self.mode = "play"
            if run is not None:
                run.acc = 0.0  # never resume into a burst of catch-up steps
            self.audio.play("resume")
        elif key == "r":
            self.audio.play("start")
            self._start_run(self.s.seed if self.s.seed is not None else self._next_seed())
        elif key == "q":
            self.audio.play("back")
            self._to_menu()

    def _key_over(self, key: str) -> None:
        run = self.run
        if key in ("enter", " ", "r"):
            self.audio.play("start")
            self._start_run(self.s.seed if self.s.seed is not None else self._next_seed(),
                            demo=bool(run and run.demo))
        elif key in ("1", "2", "3", "4"):
            self.diff_key = DIFFICULTY_ORDER[min(len(DIFFICULTY_ORDER) - 1, int(key) - 1)]
            self.audio.play("start")
            self._start_run(self.s.seed if self.s.seed is not None else self._next_seed())
        elif key in ("m", "menu", "q", "esc"):
            self.audio.play("back")
            self._to_menu()

    def _key_help(self, key: str) -> None:
        if key in ("f5", "ctrl+c"):
            self.running = False
            return
        self.mode = self.help_from
        if self.mode == "play" and self.run is not None:
            self.run.acc = 0.0
        if key in ("esc", "q", "?", "/"):
            return  # these mean "close", nothing more
        # The key that closed the overlay also does its normal job, which is
        # what makes "any key to go back" true instead of a polite lie.
        handler = {
            "menu": self._key_menu,
            "play": self._key_play,
            "pause": self._key_pause,
            "over": self._key_over,
        }.get(self.mode)
        if handler is not None:
            handler(key)

    def _say(self, text: str) -> None:
        """Show a status note for a few seconds (never for ever)."""
        self.note = text
        self.note_until = self.clock + NOTE_SECONDS

    def _visible_note(self) -> str:
        if self.note and self.clock < self.note_until:
            return self.note
        self.note = ""
        return ""

    # ----------------------------------------------------------------- music

    def _sync_music(self) -> None:
        if not self.music_on or not self.sound_on:
            self.audio.set_music(None)
            return
        spec = self.theme.music
        self.audio.set_music(spec)
        if self.mode == "play" and self.run is not None:
            self.audio.set_intensity(self._intensity())

    def _intensity(self) -> float:
        run = self.run
        if run is None:
            return 0.0
        game = run.game
        rules = game.rules
        speed = 1.0 + (rules.speed_base - game.step_interval()) / max(1e-6, rules.speed_base - rules.speed_floor)
        level = (game.level - 1) / max(1, rules.max_level - 1)
        return max(0.0, min(1.0, 0.55 * level + 0.45 * speed))

    # ----------------------------------------------------------------- draw

    def _draw(self) -> None:
        f = self.frame
        f.copy_layer(self.bg_layer)
        if not self.s.headless and self.fx_on:
            self.star.draw(f, self.theme)

        if self.mode == "menu":
            self._draw_menu(f)
        else:
            self._draw_board(f)

        if self.mode == "help":
            draw_help(f, self.theme, "esc · enter · any key  back")

        if self.fx_on:
            self.postfx.apply(f)
            if self.effects.flash > 0.0:
                self._screen_flash(f, self.effects.flash_color, min(0.45, self.effects.flash * 0.6))

    def _draw_menu(self, f: Frame) -> None:
        items = [MenuItem(key=d.key, label=d.label, blurb=d.blurb) for d in DIFFICULTIES.values()]
        draw_menu(
            f,
            self.theme,
            self.layout,
            items,
            DIFFICULTY_ORDER.index(self.diff_key),
            self.menu_scores,
            self.decor,
            self.sound_on,
            self.music_on and self.sound_on,
            self.theme.label,
            self.ascii,
            self.rows < 22 or self.cols < 96,
            _version(),
        )
        self.decor.draw(f, self.theme)
        note = self._visible_note()
        if not note and not self.sound_on:
            note = "sound off - m turns it on"
        if not note and self.sound_on and self.audio.player is None:
            note = self.audio.last_error or "no audio player found"
        if not note and self.idle > IDLE_DEMO_SECONDS - 15:
            left = int(IDLE_DEMO_SECONDS - self.idle)
            note = f"attract mode in {max(0, left)}s"
        draw_status_bar(f, self.theme, MENU_HINTS, note)

    def _draw_board(self, f: Frame) -> None:
        run = self.run
        if run is None:
            return
        lay = self.layout
        if self.fx_on:
            dx, dy = self.effects.shake_offset()
            if dx or dy:
                lay = dataclasses.replace(lay, field_x=lay.field_x + dx, field_y=lay.field_y + dy)
        danger = min(1.0, self.effects.shake) if self.fx_on else 0.0
        draw_board_frame(f, lay, self.theme, run.game.level, danger)
        draw_playfield(f, lay, run.game, self.theme, self.effects, self.vis, self.clock, self.fx_on)
        draw_title_bar(
            f,
            self.theme,
            DIFFICULTIES[self.diff_key].label,
            0 if run.demo else max(self.book.best(self.diff_key), run.game.score),
            extra=("DEMO", self.theme.golden) if run.demo else None,
        )
        if self.layout.show_hud:
            draw_hud(f, self.layout, self.theme, self._hud(run))

        if self.mode == "pause":
            draw_pause(f, self.layout, self.theme, self._hud(run))
        elif self.mode == "over":
            draw_game_over(
                f, self.layout, self.theme, self._hud(run), run.cause, run.game.won, run.rank, run.record
            )

        hints = {"over": OVER_HINTS, "pause": PAUSE_HINTS}.get(self.mode, PLAY_HINTS)
        draw_status_bar(f, self.theme, hints, self._note(run))

    def _note(self, run: Run) -> str:
        note = self._visible_note()
        if note:
            return note
        if run.demo:
            return "attract mode - any key returns to the menu"
        if self.s.seed is not None:
            return f"seed {self.s.seed} (pinned)"
        if self.fps < self.s.fps - 0.5:
            return f"framerate lowered to {int(self.fps)}fps to keep up"
        return ""

    def _hud(self, run: Run) -> HudData:
        game = run.game
        rules = game.rules
        golden = next((fd for fd in game.foods.values() if fd.kind == "golden"), None)
        combo_left = 0.0
        if game.combo > 0:
            combo_left = max(0.0, 1.0 - (game.ticks - game.last_eat_tick) / max(1, rules.combo_window))
        return HudData(
            score=game.score,
            best=0 if run.demo else max(self.book.best(self.diff_key), game.score),
            level=game.level,
            to_next_level=game.food_left_to_level(),
            per_level=rules.food_per_level,
            length=game.length(),
            speed=1.0 / game.step_interval(),
            combo=game.combo,
            combo_frac=combo_left if game.combo > 1 else 0.0,
            bonus_frac=(golden.ttl / rules.golden_ttl) if golden else None,
            bonus_left=golden.ttl if golden else 0.0,
            difficulty=DIFFICULTIES[self.diff_key].label,
            theme_name=self.theme.label,
            sound=self.sound_on,
            music=self.music_on,
            fx=self.fx_on,
            ascii_glyphs=self.ascii,
            elapsed=run.elapsed,
            deaths=self.deaths,
            fps=self.fps,
            seed=rules.seed,
            player=self.audio.player_kind,
            demo=run.demo,
        )

    def _screen_flash(self, f: Frame, color, amount: float) -> None:
        """Bleach the whole screen for a few frames: the visual comma after a death."""
        for i in range(len(f.chars)):
            bg = f.bg[i]
            if bg is not None:
                f.bg[i] = (
                    int(bg[0] + (color[0] - bg[0]) * amount),
                    int(bg[1] + (color[1] - bg[1]) * amount),
                    int(bg[2] + (color[2] - bg[2]) * amount),
                )


class _ByteWriter:
    """Minimal byte sink with the interface AnsiPresenter expects."""

    def __init__(self, stream) -> None:
        self.stream = stream

    def write(self, data: bytes) -> None:
        try:
            self.stream.write(data)
        except (BrokenPipeError, ValueError):
            pass

    def flush(self) -> None:
        try:
            self.stream.flush()
        except (BrokenPipeError, ValueError):
            pass


def _version() -> str:
    from . import __version__

    return f"v{__version__}"


def main(argv: Optional[List[str]] = None) -> int:
    from .config import parse_args

    settings = parse_args(argv)
    try:
        return App(settings).run_forever()
    except KeyboardInterrupt:
        return 130
