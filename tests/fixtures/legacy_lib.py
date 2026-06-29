"""An importable stand-in for a third-party library, used by the check tests.

It lives on ``sys.path`` (see ``tests/conftest.py``) so apidrift's resolver treats it
as an installed third-party package. Each callable pins one signature/marker shape the
checks must handle correctly.
"""

from __future__ import annotations


def normal(x: int, y: int) -> tuple[int, int]:
    """Ordinary positional-or-keyword parameters."""
    return (x, y)


def positional_only(a: int, b: int, /, c: int) -> tuple[int, int, int]:
    """``a`` and ``b`` are positional-only; only ``c`` is acceptable by keyword."""
    return (a, b, c)
