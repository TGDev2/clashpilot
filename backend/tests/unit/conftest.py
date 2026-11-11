"""Unit tests import the shared helpers of `tests/support` as `support`.

The pytest configuration stays untouched: only the unit tests put `tests/` on the import path.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = str(Path(__file__).resolve().parents[1])
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)
