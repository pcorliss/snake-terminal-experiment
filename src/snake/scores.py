"""High score persistence (plain JSON next to the game, no database)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Score:
    score: int
    length: int
    level: int
    difficulty: str
    theme: str
    seed: Optional[int]
    won: bool
    at: float

    def row(self) -> str:
        flag = " CLEARED" if self.won else ""
        return f"{self.score:>7}  len {self.length:>3}  lvl {self.level:>2}  {self.difficulty:<8}{flag}"


class ScoreBook:
    """Top-N scores per difficulty, kept in ``.snake/scores.json``."""

    def __init__(self, path: str, limit: int = 8) -> None:
        self.path = path
        self.limit = limit
        self.entries: List[Score] = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            self.entries = []
            return
        entries: List[Score] = []
        for item in raw.get("entries", []):
            try:
                entries.append(
                    Score(
                        score=int(item["score"]),
                        length=int(item["length"]),
                        level=int(item["level"]),
                        difficulty=str(item.get("difficulty", "?")),
                        theme=str(item.get("theme", "?")),
                        seed=item.get("seed"),
                        won=bool(item.get("won", False)),
                        at=float(item.get("at", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue  # tolerate a hand-edited file rather than crash the game
        entries.sort(key=lambda s: s.score, reverse=True)
        self.entries = entries[: self.limit]

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload: Dict[str, Any] = {
            "version": 1,
            "entries": [asdict(e) for e in self.entries],
        }
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)

    def _pool(self, difficulty: Optional[str] = None) -> List[Score]:
        return [e for e in self.entries if difficulty is None or e.difficulty == difficulty]

    def add(self, score: int, length: int, level: int, difficulty: str, theme: str, seed: Optional[int], won: bool) -> int:
        """Record a run; returns its 1-based rank on the table (0 if it missed)."""
        entry = Score(
            score=score,
            length=length,
            level=level,
            difficulty=difficulty,
            theme=theme,
            seed=seed,
            won=won,
            at=time.time(),
        )
        self.entries.append(entry)
        self.entries.sort(key=lambda s: (-s.score, s.at))
        self.entries = self.entries[: self.limit]
        self.save()
        if entry in self.entries:
            return self.entries.index(entry) + 1
        return 0

    def ranked(self, difficulty: Optional[str] = None, limit: int = 5) -> List[Score]:
        """Top entries, optionally filtered to one difficulty."""
        return self._pool(difficulty)[:limit]

    def best(self, difficulty: Optional[str] = None) -> int:
        pool = self._pool(difficulty)
        return pool[0].score if pool else 0

    def qualifies(self, score: int, difficulty: Optional[str] = None) -> bool:
        """Would a run with ``score`` land on the table? Call before :meth:`add`."""
        if score <= 0:
            return False
        pool = self._pool(difficulty)
        if len(pool) < self.limit:
            return True
        return score > min(e.score for e in pool)
