"""A frame sink that prints plain text frames.

Used by ``--headless``: it lets you eyeball the layout, record a GIF, or write
snapshot tests without a TTY, a cursor or a single escape sequence.
"""

from __future__ import annotations

import sys
from typing import List, Optional, TextIO, Tuple

from .render import Frame, PostFX


class HeadlessPresenter:
    def __init__(self, out: Optional[TextIO] = None, postfx: Optional[PostFX] = None, separator: str = "--") -> None:
        self.out = out or sys.stdout
        self.postfx = postfx  # None -> the caller already applied post-processing
        self.separator = separator
        self.frames: List[List[str]] = []

    def start(self) -> None:
        pass

    def end(self) -> None:
        self.out.flush()

    def render(self, frame: Frame) -> None:
        if self.postfx is not None:
            self.postfx.apply(frame)
        rows = ["".join(frame.chars[row * frame.cols : row * frame.cols + frame.cols]).rstrip() for row in range(frame.rows)]
        self.frames.append(rows)
        self.out.write("\n".join(rows))
        if self.separator:
            self.out.write(f"\n{self.separator} ({frame.cols}x{frame.rows})\n")
        self.out.flush()
