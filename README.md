# SNAKE // NEON ABYSS

The following is an entirely generated with a local LLM. This is not my code. Enjoy!

## Introduction

A snake game for the terminal: truecolour, a custom cell renderer, a procedurally
synthesized soundtrack, and an engine that contains no I/O and no wall clock.
Python 3.13, standard library only — no `curses`, no pip installs, no binary
assets in the repo.

```
    █   █ █████  ███  █   █        ███  ████  █   █  ████  ████
    ██  █ █     █   █ ██  █       █   █ █   █ █   █ █     █
    █ █ █ ████  █   █ █ █ █       █████  ████  █ █  ███   ███
    █  ██ █     █   █ █  ██       █   █ █   █   █       █     █
    █   █ █████  ███  █   █       █   █ ████    █   ████  ████
```

---

## Playing it

```sh
mise run play          # or: mise exec -- python snake.py
```

No install step, no virtualenv to remember. `snake.py` puts `src/` on `sys.path`
and runs. If you'd rather not use mise, any Python 3.13 will do:
`python3.13 snake.py`.

**Requirements:** a real terminal (stdin/stdout must be a TTY), 24-bit colour,
and ideally Unicode. macOS or Linux. Audio needs a system player — `afplay`,
`aplay`, `paplay` or `play`; if none exists the game says so and stays silent
rather than pretending.

A window of about **80×24** is the comfortable target: the board defaults to
30×18 and gets a status column beside it. Smaller terminals still work — the
board shrinks to fit and the HUD drops away — but a live run keeps the board it
started with and re-centres it, so resizing mid-game doesn't teleport your snake.

### Keys

| key | what it does |
|---|---|
| arrows / `wasd` / `hjkl` | steer |
| `space` | turbo — hold it to skip the wait between steps |
| `p` | pause / resume |
| `q` or `esc` | back to menu (ends the run) |
| `r` | restart instantly, same settings |
| `n` | new board, even if a `--seed` is pinned |
| `1` `2` `3` `4` | difficulty: garden / classic / velocity / void |
| `m` / `M` | sound effects on/off · music on/off |
| `t` | cycle colour theme |
| `u` | unicode glyphs / plain ascii glyphs |
| `f` | particles, shake, vignette, scanlines on/off |
| `?` | help screen |
| `f5` or `ctrl+c` | hard quit — the terminal is always restored |

Double-tap a corner inside one tick and both turns register: steering goes
straight into the engine's own input queue, so the last two keystrokes before a
step are what count, not the last one.

### Attract mode

Leave the menu alone for 60 seconds and it plays itself: an autopilot snake
picks a difficulty and starts, holding a countdown in the status bar so you know
how long you have before the cabinet starts showing off. Any key takes it back.
`mise run demo` (or `--demo`) starts in that mode deliberately.

---

## Rules

Eating an apple is **10 points × combo**. Chained eats (within 16 ticks of each
other) build the combo up to ×8, so a fast run scores far more than a careful
one — the point is to keep the chain, not just the snake, alive.

- **Golden fruit** spawns alongside the apple some of the time (30–40% by
  difficulty), is worth **50 × combo**, and lives 36 ticks. It's a decision: it's
  the best value on the board and it's usually in the worst place on the board.
- **Levels** advance every 4–6 apples depending on difficulty, to a cap of 12.
  Each level speeds the snake up (×0.90 by default, with a floor so it stays
  playable) and adds shards to the field.
- **Shards** are placed so that they can never seal off a region — the generator
  checks connectivity rather than trusting luck.
- **Winning** means filling the board with snake and shards. On a default 30×18
  field that is 540 cells, and the game will hand you the table entry with your
  name on it if you get there.

High scores persist in `.snake/scores.json`, one table overall plus per-difficulty
tables, top 8 kept. A zero-score run (you met a wall immediately) is not recorded,
so the table doesn't fill with noise.

### Difficulties

| key | edges | shards | pace | golden |
|---|---|---|---|---|
| `garden` | wrap | never | 0.155 s/step, ×0.94/level | 35% |
| `classic` | solid | from level 3, +3/level | 0.140 s/step, ×0.90/level | 30% |
| `velocity` | solid | from level 2, +5/level | 0.115 s/step, ×0.92/level | 38% |
| `void` | wrap | from level 2, +4/level | 0.128 s/step, ×0.91/level | 40% |

Wrapping makes the board bigger than it looks. Solid walls make the corners the
safest place on the field, until you've used them all.

### Themes

`neon` (NEON ABYSS — deep space blue, magenta fruit), `amber` (AMBER CRT),
`phosphor` (GREEN PHOSPHOR, P1 monochrome), `graphite` (cool grey, no theatrics).
`-T` at the command line, `t` while playing. `--ascii` swaps every glyph for a
7-bit equivalent for terminals that mishandle box-drawing and block characters.

---

## Command line

```
--size WxH          playfield size (default: fit the terminal)
-d, --difficulty    garden | classic | velocity | void
-T, --theme         neon | amber | phosphor | graphite
--ascii             7-bit glyphs only
--fps 10-60         frame rate (default 30)
--seed N            reproducible board; determinism is real, not decorative
--demo              attract mode: an autopilot plays
--frames N          exit after N frames
--headless          no terminal I/O; render plain-text frames to stdout
--no-alt-screen     draw inline instead of switching to the alternate buffer
--mute              no sound effects at start
--no-music          no ambient score at start
--no-fx             no particles, shake or CRT post-processing
--version
```

`--headless` is a fixed 100×30 virtual terminal on a fixed clock, so the same
seed and frame count give byte-identical output. That is what makes it possible
to test a game about reflexes:

```sh
mise run frames                       # 30 frames of a seeded game, as text
mise exec -- python snake.py --headless --frames 200 --seed 7 --demo > board.txt
```

---

## How it's put together

| file | role |
|---|---|
| `src/snake/engine.py` | the rules. Pure, seeded, deterministic, event-emitting. No rendering, no audio, no time. |
| `src/snake/config.py` | settings, CLI, difficulty presets, key help |
| `src/snake/render.py` | cell buffer, diffed ANSI presenter, CRT post-processing |
| `src/snake/screens.py` | menu, HUD, help, pause, game over, layout maths, bitmap font |
| `src/snake/view.py` | the playfield: snake, fruit, shards, glow |
| `src/snake/effects.py` | particles, popups, rings, shake, flashes |
| `src/snake/audio.py` | synthesis, WAV cache, async playback, generative score |
| `src/snake/theme.py` | palettes, glyphs, music specs |
| `src/snake/keys.py` | raw-mode input, escape-sequence decoding, resize |
| `src/snake/app.py` | the loop and the mode machine (menu/play/pause/help/over) |
| `src/snake/scores.py` | high-score book, atomic JSON |
| `src/snake/present.py` | headless text presenter |

State that belongs to the *game* lives in `SnakeGame`. State that belongs to the
*feel* — eat flashes, the death wave, the turbo glow — lives in `view.Visuals` and
can be switched off with `f` without changing a single simulation tick. Same
reason the engine has no clock in it: `--headless` and the tests are just the
same game with a different notion of time.

Terminal hygiene is deliberate. Alternate screen on, cursor hidden, DEC
synchronized-update markers around each frame so nothing tears, only changed
cells rewritten, and every exit path — key, EOF, Ctrl-C, SIGTERM, an exception —
passes through the same teardown that puts the terminal back. Two presenters
implement one protocol (`start` / `render` / `end`), which is why headless mode
costs about forty lines instead of a rewrite.

---

## Notes from the author — how this got built

The brief I was given was deliberately short: build a snake game in this
directory, and *make all the choices myself* — sound, video, interface, theme,
graphics. Use git, use mise for tooling, don't wander outside the project, don't
ask questions. That is a harder kind of brief than a long one, because it removes
the excuses rather than the ambiguity. Everything below is a decision I made
because I thought it was right, and the reasoning is here so you can disagree
with it an informed way.

**I refused `curses`, and that set the shape of everything.** `curses` is the
obvious answer and it is the wrong one for what I wanted to draw. It has no
concept of 24-bit colour as a first-class thing, no concept of animation state,
and it makes a diffed, synchronized update hard to reason about. So I wrote a
cell buffer — parallel arrays of chars, fg, bg, attributes — and a presenter that
compares each cell against the last frame and emits only the deltas, wrapped in
`\x1b[?2026h` / `l` so a whole frame appears at once. The cost is a few hundred
lines of my own code. The benefit is that vignettes, scanlines, screen shake and
background halos are ordinary loops over an array rather than fights with a
library. The same buffer is what makes `--headless` almost free, which in turn is
what let me look at the game's output without a terminal at all — the single most
useful feature I added and the one I did not plan for.

**The engine has no I/O, no threads and no clock.** `SnakeGame.step()` advances
one tick; `step_interval()` reports how long a tick should take. Wall time
belongs to the app loop, not the simulation. Once you accept that split, a lot of
things stop being special cases: a reproducible run is `--seed N --frames M`; the
autopilot is a method that returns one steering vector; a test is three lines;
and the attract mode cannot perturb the board's RNG because it draws from a
separate stream (`seed + 7919`). I've built enough games where animation state
leaked into the rules to find I'd rather be sick on the other side of that
particular line.

**Audio is synthesized at startup, not shipped as files.** The brief asked for
sound in a repo with no binary assets, and I take that seriously: a 400 KB WAV of
a chomp is an obscenity in a project that otherwise fits in a text file. So sound
design became data — oscillators, noise, ADSR envelopes — rendered to 16-bit PCM,
cached under `.cache/audio`, and dispatched from a worker thread so the game loop
never waits on a player process. The ambient score is generated in chunks on a
second thread and driven by an `intensity` value the loop updates as you chain
food and level up. Same trick an arcade cabinet uses, minus the cabinet.

**The snake is drawn as pipe glyphs, not blocks.** This is a small thing that
carries the whole look. Solid blocks make a turn ambiguous in a character grid —
you cannot see which way it was going. `╫ ╭ ╮ ╰ ╯` can, and they degrade to `| -
+` sensibly under `--ascii`. Terminals cannot alpha-blend, so the "glow" around
fruit is a lie told by tinting the background of neighbouring cells — a halo
instead of a composite. It reads as light anyway, and it costs nothing.

Two decisions were about honesty rather than aesthetics. **Difficulty is expressed
as a table of numbers a player can read** (this table is the README's table), not
as vibes called "Easy/Normal/Hard" — because the difference between `classic` and
`velocity` is specific and I'd rather tell you. And **every key binding the game
advertises is the one the game implements**; the first pass had the menu saying
`[a] ascii` while the code listened for `u`. That kind of inconsistency teaches
people to distrust the interface, so the help text, the menu footer and the key
handler were reconciled rather than one of them patched.

**What went wrong, because it's the interesting part.** After committing what I
believed was a working game, the first real playtest came back with two bugs, and
both were in things I had declared verified.

The game made no sound at all. My player detection ran `which afplay`, found it,
and *guessed* at its flags — `-a {gain}`, which modern `afplay` rejects outright.
Every sound effect and every music chunk was spawning a process that exited
non-zero into `/dev/null`. The WAV files were perfect; the invocation was a
fiction. The fix that matters is not the correct flag (`-v`, it always was) but
the fact that the engine now *tests* the player before using it — a 40 ms WAV of
digital silence through the real command line — and falls back down the list if
it's refused. Guessing that a program's arguments are right is the bug;
unverified capability detection always looks exactly like "this machine can't do
it".

The menu title was missing its `O`, `B` and `Y`. I had written a 5×5 bitmap font
containing exactly the letters I happened to test with — `S N A K E` — and
`bitmap_text()` silently skips glyphs it doesn't have *while still advancing their
width*, so "NEON ABYSS" rendered with holes that read as odd kerning until someone
pointed at them. A silent fallback in a display path is a trap I set for myself;
the font is now the whole alphabet, digits and punctuation.

Driving the real terminal path under a `pty` — which I should have done before the
first commit — turned up two more. Ctrl-C only quit if the game was the
terminal's foreground process group, because raw mode left `ISIG` switched on; the
fix is to take the byte away from the line discipline so the game can quit through
its own teardown and restore the screen itself. And `snake.py | head` printed two
tracebacks, because a closed output stream is the reader's privilege and not a
crash.

The narrative point I'd make about the whole thing: the parts that were hard were
not the game logic. Twenty years of snake clones have that solved. The hard parts
were the seams — *what the terminal promises and what the code must therefore
keep*, and *what a program should check rather than assume*. A game that restores
your terminal on every exit path, that proves its audio player works instead of
hoping, and that renders identically with and without a TTY, is a game where the
next feature is cheap. That's the argument for all of it.

---

## Repository

```
mise run play      # play
mise run demo      # attract mode
mise run check     # compile everything
mise run frames    # 30 deterministic headless frames
```

Generated, and gitignored: `.cache/audio/` (synthesized WAVs, version-stamped) and
`.snake/scores.json` (high scores). Delete either at any time; the game rebuilds
them.
