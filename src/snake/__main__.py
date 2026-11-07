"""``python -m snake`` entry point.

Kept deliberately thin: all of the interesting decisions live in
:func:`snake.app.main`.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from .app import main

    sys.exit(main())
