"""Shared pytest setup.

Put ``tests/fixtures`` on ``sys.path`` so the small modules there are importable as
top-level names. apidrift's resolver then classifies them as installed third-party
packages (``find_spec`` succeeds, they are not stdlib), which lets the check tests run
against real, importable callables with controlled signatures and markers — without
shipping or installing a separate package.
"""

from __future__ import annotations

import sys
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))
