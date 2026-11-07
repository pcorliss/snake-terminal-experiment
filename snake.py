#!/usr/bin/env python3
"""Launcher so the game runs with no install step and no PYTHONPATH.

    python snake.py            # play
    python snake.py --help     # all the flags

`python -m snake` also works if ``src`` is on the path (see the mise tasks).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


if __name__ == "__main__":
    from snake.app import main

    sys.exit(main())
