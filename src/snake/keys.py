"""Raw-mode keyboard input with a hand-written escape-sequence decoder.

No ``curses``, so no ``getch()`` half-second latency and no blocking on arrow
keys: ``poll()`` is non-blocking and returns every key pressed since last
time, in order.  Fast double-taps (right then up inside one tick) therefore
register as two turns instead of being swallowed - the single biggest
feel-it-right detail in a snake game.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import sys
import termios
import time
import tty
from typing import List, Optional, Tuple

_CTRL_NAMES = {
    0x03: "ctrl+c",
    0x04: "ctrl+d",
    0x09: "tab",
    0x0a: "enter",
    0x0d: "enter",
    0x08: "backspace",
    0x7f: "backspace",
    0x1c: "ctrl+\\",
}

_CSI_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}
_CSI_TILDE = {"1": "home", "2": "insert", "3": "delete", "4": "end", "5": "pgup", "6": "pgdn", "7": "home", "8": "end"}
_SS3 = {"A": "up", "B": "down", "C": "right", "D": "left", "P": "f1", "Q": "f2", "R": "f3", "S": "f4", "H": "home", "F": "end"}

# How long a bare Escape waits for an arrow key to show up before we call it Escape.
_ESC_GRACE = 0.06


def _utf8_len(b: int) -> int:
    """Expected byte length of a UTF-8 sequence given its lead byte."""
    if b < 0x80:
        return 1
    if b >= 0xF0:
        return 4
    if b >= 0xE0:
        return 3
    if b >= 0xC0:
        return 2
    return 1  # stray continuation byte: skip it


def decode_escape(buf: bytes, i: int) -> Tuple[Optional[str], int]:
    """Decode one escape sequence starting at ``i``.

    Returns ``(key_or_None, bytes_consumed)``.  ``(None, 0)`` means "the buffer
    ends in the middle of a sequence, give me the rest before deciding".
    """
    n = len(buf)
    if i + 1 >= n:
        return None, 0  # a bare ESC: wait briefly for the rest of the sequence

    if buf[i + 1] == 0x5B:  # ESC [ : CSI
        j = i + 2
        params = bytearray()
        while j < n and not (0x40 <= buf[j] <= 0x7E):
            params.append(buf[j])
            j += 1
        if j >= n:
            return None, 0
        final = chr(buf[j])
        text = params.decode("ascii", "replace")
        if final in _CSI_ARROWS:
            return _CSI_ARROWS[final], j - i
        if final == "H":
            return "home", j - i
        if final == "F":
            return "end", j - i
        if final == "Z":
            return "shift_tab", j - i
        if final == "~":
            head = text.split(";")[0]
            return _CSI_TILDE.get(head, "unknown"), j - i
        if final in ("M", "m"):  # mouse report: consume it, produce nothing
            return None, j - i
        return "unknown", j - i

    if buf[i + 1] == 0x4F:  # ESC O : SS3 (application cursor keys mode)
        if i + 2 >= n:
            return None, 0
        final = chr(buf[i + 2])
        return _SS3.get(final, "unknown"), 3

    # ESC followed by a printable: report ESC as its own key and re-process the
    # rest normally.  This is what makes "esc then q" work.
    return "esc", 1


def decode(buf: bytes) -> Tuple[List[str], bytes]:
    """Turn raw terminal bytes into a list of key names + unconsumed tail."""
    keys: List[str] = []
    i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b == 0x1B:
            key, consumed = decode_escape(buf, i)
            if consumed == 0:
                return keys, buf[i:]
            if key is not None and key != "unknown":
                keys.append(key)
            i += consumed
            continue
        if b in _CTRL_NAMES:
            keys.append(_CTRL_NAMES[b])
            i += 1
            continue
        if b < 0x20:
            i += 1
            continue
        length = _utf8_len(b)
        if i + length > n:
            return keys, buf[i:]
        try:
            text = buf[i : i + length].decode("utf-8")
        except UnicodeDecodeError:
            i += 1
            continue
        for ch in text:
            keys.append(ch)
        i += length
    return keys, b""


class KeyReader:
    """Non-blocking keyboard in cbreak mode.  Restore-on-exit is guaranteed."""

    def __init__(self, fd: Optional[int] = None) -> None:
        self.fd = fd if fd is not None else sys.stdin.fileno()
        self._saved = None
        self._sel = selectors.DefaultSelector()
        self._leftover = b""
        self._pending_since = 0.0
        self._open = False

    # ------------------------------------------------------------- raw mode

    def open_raw(self) -> None:
        if self._open or not os.isatty(self.fd):
            return
        self._saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        attrs = termios.tcgetattr(self.fd)
        attrs[6][termios.VMIN] = 0  # read() returns immediately...
        attrs[6][termios.VTIME] = 0  # ...and never blocks for a timeout
        attrs[0] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        try:
            self._sel.register(self.fd, selectors.EVENT_READ)
        except KeyError:
            pass
        self._open = True

    def close_raw(self) -> None:
        if not self._open:
            return
        try:
            self._sel.unregister(self.fd)
        except (KeyError, ValueError):
            pass
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
            self._saved = None
        self._open = False

    def __enter__(self) -> "KeyReader":
        self.open_raw()
        return self

    def __exit__(self, *exc) -> bool:
        self.close_raw()
        return False

    # ---------------------------------------------------------------- reads

    def poll(self, timeout: float = 0.0) -> List[str]:
        """Return pressed keys without blocking longer than ``timeout``."""
        try:
            events = self._sel.select(timeout=timeout)
        except (OSError, ValueError):
            events = []
        if not events:
            return self._flush_stale()
        try:
            chunk = os.read(self.fd, 4096)
        except BlockingIOError:
            return []
        except OSError:
            return []
        if not chunk:  # stdin hit EOF (detached/closed): ask the caller to quit
            return ["eof"]
        data = self._leftover + chunk
        keys, self._leftover = decode(data)
        if self._leftover:
            self._pending_since = time.monotonic()
        return keys

    def _flush_stale(self) -> List[str]:
        """A lone ESC is indistinguishable from the start of an arrow key, so we
        wait a hair for the rest; when nothing arrives, it really was Escape."""
        if not self._leftover:
            return []
        if time.monotonic() - self._pending_since < _ESC_GRACE:
            return []
        pending, self._leftover = self._leftover, b""
        if pending == b"\x1b":
            return ["esc"]
        return []  # a truncated CSI / multibyte sequence: drop it

    def drain(self) -> List[str]:
        keys: List[str] = []
        while True:
            batch = self.poll(0.0)
            if not batch:
                return keys
            keys.extend(batch)


def terminal_size() -> Tuple[int, int]:
    cols, rows = shutil.get_terminal_size(fallback=(80, 24))
    return cols, rows


def install_resize_handler(on_resize) -> None:
    """SIGWINCH -> set a flag (handled by the game loop, not the signal frame)."""

    def _handler(signum, frame):  # pragma: no cover - signal context
        on_resize()

    try:
        signal.signal(signal.SIGWINCH, _handler)
    except (ValueError, AttributeError, OSError):
        pass
