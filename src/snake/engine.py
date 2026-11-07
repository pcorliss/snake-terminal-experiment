"""Pure game logic. No rendering, no audio, no terminal - and no wall clock.

Everything here is deterministic for a given ``Rules(seed=...)``: the same seed
plus the same sequence of ``push_dir`` calls replays identically.  That is what
makes the engine unit-testable, what powers the ``--demo`` attract mode, and
what lets a race condition show up as a failing test instead of a shrug.

The engine reports what happened through :class:`Event` objects; the
presentation layer turns those into sound, particles and floating score text.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterator, List, Optional, Sequence, Set, Tuple

Pos = Tuple[int, int]
Vec = Tuple[int, int]

UP: Vec = (0, -1)
DOWN: Vec = (0, 1)
LEFT: Vec = (-1, 0)
RIGHT: Vec = (1, 0)
DIRS: Dict[str, Vec] = {"up": UP, "down": DOWN, "left": LEFT, "right": RIGHT}
OPPOSITE: Dict[Vec, Vec] = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}

_VEC_TO_BIT: Dict[Vec, int] = {UP: 1, RIGHT: 2, DOWN: 4, LEFT: 8}

KIND_APPLE = "apple"
KIND_GOLDEN = "golden"


@dataclass(frozen=True)
class Food:
    kind: str
    x: int
    y: int
    ttl: int = 0  # ticks remaining (0 = never expires)
    uid: int = 0


@dataclass(frozen=True)
class Event:
    """Something that just happened, for the presentation layer to react to."""

    type: str
    x: int = 0
    y: int = 0
    value: int = 0
    combo: int = 1
    level: int = 1
    text: str = ""
    cause: str = ""
    kind: str = ""


@dataclass(frozen=True)
class Rules:
    """All the numbers that define a ruleset; see ``snake.config`` presets."""

    width: int
    height: int
    wrap: bool = False
    obstacles: bool = True
    obstacle_from_level: int = 3
    obstacles_per_level: int = 3
    max_obstacles: int = 46
    start_length: int = 3
    food_per_level: int = 5
    max_level: int = 12
    apple_points: int = 10
    golden_points: int = 50
    golden_chance: float = 0.30
    golden_ttl: int = 36
    combo_window: int = 16  # ticks between eats to keep a chain alive
    max_combo: int = 8
    speed_base: float = 0.145
    speed_decay: float = 0.90
    speed_floor: float = 0.055
    seed: Optional[int] = None

    def tick_interval(self, level: int) -> float:
        """Seconds per logic step at ``level`` (1-based) - the difficulty curve."""
        if not self.speed_base or not self.speed_decay:
            raise ValueError("speed_base/speed_decay must be non-zero")
        k = max(0, level - 1)
        return max(self.speed_floor, self.speed_base * (self.speed_decay ** k))


class SnakeGame:
    """A single snake on a single board."""

    def __init__(self, rules: Rules) -> None:
        if rules.width < 5 or rules.height < 5:
            raise ValueError("board must be at least 5x5")
        self.rules = rules
        self.rng = random.Random(rules.seed)
        self._bot_rng = random.Random((rules.seed or 0) + 7919)
        self._next_uid = 1

        self.snake: List[Pos] = []
        self.obstacles: Set[Pos] = set()
        self.foods: Dict[Pos, Food] = {}
        self.pending: Deque[Vec] = deque()

        self.dir: Vec = RIGHT
        self.score = 0
        self.eaten = 0
        self.level = 1
        self.combo = 0
        self.ticks = 0
        self.last_eat_tick = -999
        self.grow = 0
        self.alive = True
        self.won = False
        self.death_cause = ""

        self._reset_board()

    # ------------------------------------------------------------------ setup

    def _reset_board(self) -> None:
        r = self.rules
        cy = r.height // 2
        start_x = r.width // 3
        self.snake = [(start_x - i, cy) for i in range(r.start_length)]
        self.dir = RIGHT
        self._spawn_apple()

    def new_uid(self) -> int:
        uid = self._next_uid
        self._next_uid += 1
        return uid

    # ------------------------------------------------------------- geometry

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.rules.width and 0 <= y < self.rules.height

    def wrap_point(self, x: int, y: int) -> Pos:
        return x % self.rules.width, y % self.rules.height

    def free_cells(self) -> List[Pos]:
        blocked = set(self.snake) | self.obstacles | set(self.foods)
        return [
            (x, y)
            for y in range(self.rules.height)
            for x in range(self.rules.width)
            if (x, y) not in blocked
        ]

    def is_occupied(self, x: int, y: int) -> bool:
        return (x, y) in self.snake or (x, y) in self.obstacles or (x, y) in self.foods

    def length(self) -> int:
        return len(self.snake)

    def food_left_to_level(self) -> int:
        r = self.rules
        return r.food_per_level - (self.eaten % r.food_per_level)

    # ----------------------------------------------------------------- input

    def push_dir(self, direction: Vec | str) -> bool:
        """Queue a turn. Returns ``False`` if it was a no-op/reversal."""
        vec = DIRS[direction] if isinstance(direction, str) else direction
        if vec not in OPPOSITE:
            return False
        reference = self.pending[-1] if self.pending else self.dir
        if vec == reference or vec == OPPOSITE[reference]:
            return False
        if len(self.pending) >= 3:
            return False
        self.pending.append(vec)
        return True

    # ----------------------------------------------------------------- step

    def step(self) -> List[Event]:
        """Advance one logic tick; returns the events that occurred."""
        if not self.alive:
            return []
        events: List[Event] = []
        self.ticks += 1

        direction = self.dir
        while self.pending:
            candidate = self.pending.popleft()
            if candidate != OPPOSITE[self.dir]:
                direction = candidate
                break
        if direction != self.dir:
            self.dir = direction
            events.append(Event("turn", x=self.snake[0][0], y=self.snake[0][1]))

        hx, hy = self.snake[0]
        nx, ny = hx + self.dir[0], hy + self.dir[1]

        if self.rules.wrap:
            nx, ny = self.wrap_point(nx, ny)
        elif not self.in_bounds(nx, ny):
            return [self._die("wall", nx, ny)]

        if (nx, ny) in self.obstacles:
            return [self._die("obstacle", nx, ny)]

        food = self.foods.get((nx, ny))
        grows = food is not None or self.grow > 0
        body = self.snake if grows else self.snake[:-1]
        if (nx, ny) in body:
            return [self._die("self", nx, ny)]

        self.snake.insert(0, (nx, ny))
        if food is not None:
            self._eat(food, nx, ny, events)

        if self.grow > 0:
            self.grow -= 1
        else:
            self.snake.pop()

        self._tick_food(events)
        self._maybe_win(events)
        return events

    def _die(self, cause: str, x: int, y: int) -> Event:
        self.alive = False
        self.death_cause = cause
        hx, hy = self.snake[0]
        return Event("die", x=hx, y=hy, cause=cause, value=self.score)

    def _eat(self, food: Food, x: int, y: int, events: List[Event]) -> None:
        r = self.rules
        del self.foods[(x, y)]

        chained = (self.ticks - self.last_eat_tick) <= r.combo_window
        self.combo = min(self.combo + 1, r.max_combo) if chained else 1
        self.last_eat_tick = self.ticks

        base = r.apple_points if food.kind == KIND_APPLE else r.golden_points
        points = base * self.combo
        self.score += points

        events.append(
            Event(
                "eat" if food.kind == KIND_APPLE else "eat_golden",
                x=x,
                y=y,
                value=points,
                combo=self.combo,
                level=self.level,
                kind=food.kind,
            )
        )

        if food.kind == KIND_APPLE:
            self.eaten += 1
            self.grow += 1
            self._spawn_apple()
            if self.eaten % r.food_per_level == 0:
                self._level_up(events)
        else:
            events.append(Event("bonus_cleared", x=x, y=y, value=points, combo=self.combo))

        self._maybe_golden()

    def _level_up(self, events: List[Event]) -> None:
        r = self.rules
        if self.level >= r.max_level:
            return
        self.level += 1
        events.append(Event("level_up", x=self.snake[0][0], y=self.snake[0][1], level=self.level))
        if r.obstacles and self.level >= r.obstacle_from_level:
            self._add_obstacles()

    # ----------------------------------------------------------------- spawn

    def _candidates(self) -> List[Pos]:
        return self.free_cells()

    def _spawn_apple(self) -> Optional[Food]:
        options = self._candidates()
        if not options:
            return None
        x, y = self.rng.choice(sorted(options))
        food = Food(KIND_APPLE, x, y, ttl=0, uid=self.new_uid())
        self.foods[(x, y)] = food
        return food

    def _maybe_golden(self) -> None:
        r = self.rules
        if any(f.kind == KIND_GOLDEN for f in self.foods.values()):
            return
        if self.rng.random() >= r.golden_chance:
            return
        options = self._candidates()
        if not options:
            return
        x, y = self.rng.choice(sorted(options))
        self.foods[(x, y)] = Food(KIND_GOLDEN, x, y, ttl=r.golden_ttl, uid=self.new_uid())

    def _tick_food(self, events: List[Event]) -> None:
        for pos, food in list(self.foods.items()):
            if food.ttl <= 0:
                continue
            ttl = food.ttl - 1
            if ttl <= 0:
                del self.foods[pos]
                events.append(Event("bonus_expired", x=pos[0], y=pos[1], kind=food.kind))
            else:
                self.foods[pos] = Food(food.kind, pos[0], pos[1], ttl=ttl, uid=food.uid)

    def _maybe_win(self, events: List[Event]) -> None:
        board = self.rules.width * self.rules.height
        if len(self.snake) + len(self.obstacles) >= board and not self.foods:
            self.alive = False
            self.won = True
            events.append(Event("win", value=self.score, x=self.snake[0][0], y=self.snake[0][1]))

    # ------------------------------------------------------------- obstacles

    def _add_obstacles(self) -> int:
        r = self.rules
        wanted = r.obstacles_per_level
        placed = 0
        attempts = 0
        while placed < wanted and attempts < 60:
            attempts += 1
            cells = self._candidate_obstacle()
            if cells is None:
                continue
            prospective = self.obstacles | set(cells)
            if not self._still_connected(prospective):
                continue
            self.obstacles = prospective
            placed += 1
            if len(self.obstacles) >= r.max_obstacles:
                break
        return placed

    def _candidate_obstacle(self) -> Optional[List[Pos]]:
        r = self.rules
        horizontal = self.rng.random() < 0.5
        span = self.rng.randint(1, 3)
        if horizontal:
            x = self.rng.randrange(0, r.width - span + 1)
            y = self.rng.randrange(0, r.height)
            cells = [(x + i, y) for i in range(span)]
        else:
            x = self.rng.randrange(0, r.width)
            y = self.rng.randrange(0, r.height - span + 1)
            cells = [(x, y + i) for i in range(span)]

        blocked = set(self.snake) | set(self.foods)
        hx, hy = self.snake[0]
        for x, y in cells:
            if (x, y) in blocked or (x, y) in self.obstacles:
                return None
            if max(abs(x - hx), abs(y - hy)) < 3:  # never wall in the head
                return None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (x + dx, y + dy) in blocked:
                        return None
        return cells

    def _still_connected(self, obstacles: Set[Pos]) -> bool:
        """Flood fill the board: obstacles may never seal off a region."""
        r = self.rules
        free_total = r.width * r.height - len(obstacles)
        if free_total <= 0:
            return False
        start = next(
            ((x, y) for y in range(r.height) for x in range(r.width) if (x, y) not in obstacles),
            None,
        )
        if start is None:
            return False
        seen = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not self.in_bounds(nx, ny):
                    continue
                if (nx, ny) in obstacles or (nx, ny) in seen:
                    continue
                seen.add((nx, ny))
                stack.append((nx, ny))
        return len(seen) == free_total

    # ------------------------------------------------------- render support

    def step_interval(self) -> float:
        """Seconds per logic step at the current level."""
        return self.rules.tick_interval(self.level)

    def cell_in_frame(self, x: int, y: int, w: int, h: int) -> Pos:
        """Map a cell that may be off-board (wrap mode) back into frame space."""
        if self.rules.wrap:
            return x % w, y % h
        return x, y

    def pipes(self) -> List[int]:
        """Per-segment neighbour bitmasks, wrap-aware (drives the pipe glyphs)."""
        masks: List[int] = []
        n = len(self.snake)
        for i, (x, y) in enumerate(self.snake):
            mask = 0
            for j in (i - 1, i + 1):
                if j < 0 or j >= n:
                    continue
                mask |= self._bit_between((x, y), self.snake[j])
            masks.append(mask)
        return masks

    def _bit_between(self, a: Pos, b: Pos) -> int:
        dx, dy = b[0] - a[0], b[1] - a[1]
        if self.rules.wrap:
            if abs(dx) > 1:
                dx = -1 if dx > 0 else 1
            if abs(dy) > 1:
                dy = -1 if dy > 0 else 1
        return _VEC_TO_BIT.get((dx, dy), 0)

    def segments(self) -> Iterator[Tuple[int, Pos, int]]:
        for index, pos in enumerate(self.snake):
            yield index, pos, index / max(1, len(self.snake) - 1)

    def foods_sorted(self) -> Sequence[Food]:
        return [self.foods[k] for k in sorted(self.foods)]

    # ---------------------------------------------------------- attract mode

    def autopilot(self) -> Optional[Vec]:
        """One steering decision for ``--demo``: chase food, avoid dying.

        Lives in the engine (not the app) because it is pure decision logic and
        uses its own RNG stream, so the bot's choices never perturb the board's
        deterministic spawns.
        """
        if not self.alive or not self.foods:
            return None
        dist = self._distance_field()
        head = self.snake[0]
        best: Optional[Vec] = None
        best_key = None
        for vec in (UP, RIGHT, DOWN, LEFT):
            if vec == OPPOSITE[self.dir]:
                continue
            nx, ny = head[0] + vec[0], head[1] + vec[1]
            if self.rules.wrap:
                nx, ny = self.wrap_point(nx, ny)
            elif not self.in_bounds(nx, ny):
                continue
            if (nx, ny) in self.obstacles or (nx, ny) in set(self.snake[:-1]):
                continue
            escape = sum(1 for nb in self._neighbours((nx, ny)) if nb not in self._blocked_for_path())
            key = (-dist.get((nx, ny), 10_000), escape, self._bot_rng.random())
            if best_key is None or key > best_key:
                best, best_key = vec, key
        return best

    def _neighbours(self, pos: Pos) -> List[Pos]:
        x, y = pos
        out: List[Pos] = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if self.rules.wrap:
                nx, ny = self.wrap_point(nx, ny)
            elif not self.in_bounds(nx, ny):
                continue
            out.append((nx, ny))
        return out

    def _blocked_for_path(self) -> Set[Pos]:
        # the tail cell is about to vacate, so it is not a wall
        return set(self.obstacles) | set(self.snake[:-1])

    def _distance_field(self) -> Dict[Pos, int]:
        """Multi-source BFS: distance from every reachable cell to the nearest food."""
        blocked = self._blocked_for_path()
        head = self.snake[0]
        dist: Dict[Pos, int] = {}
        queue: Deque[Pos] = deque()
        for pos in self.foods:
            if pos not in blocked:
                dist[pos] = 0
                queue.append(pos)
        while queue:
            cur = queue.popleft()
            d = dist[cur] + 1
            for nb in self._neighbours(cur):
                if nb in dist or nb in blocked or nb == head:
                    continue
                dist[nb] = d
                queue.append(nb)
        return dist
