"""An importable stand-in for a third-party library, used by the check tests.

It lives on ``sys.path`` (see ``tests/conftest.py``) so apidrift's resolver treats it
as an installed third-party package. Each callable pins one signature/marker shape the
checks must handle correctly.
"""

from __future__ import annotations

import functools
import warnings

from typing_extensions import deprecated


def normal(x: int, y: int) -> tuple[int, int]:
    """Ordinary positional-or-keyword parameters."""
    return (x, y)


def _wrapped_base(a: int, b: int) -> tuple[int, ...]:
    """The wrapped target — note it has no ``new_kw`` parameter."""
    return (a, b)


@functools.wraps(_wrapped_base)
def wrapped_adds_kwarg(a: int, b: int, new_kw: int = 0) -> tuple[int, ...]:
    """A ``functools.wraps`` wrapper that ADDS a keyword the wrapped function lacks.

    ``inspect.signature`` follows ``__wrapped__`` to ``_wrapped_base(a, b)`` and would
    falsely reject ``new_kw``. Check B must also consult the wrapper's own (unwrapped)
    signature and stay silent on ``new_kw`` — while still flagging a keyword *neither*
    signature accepts.
    """
    return (a, b, new_kw)


def positional_only(a: int, b: int, /, c: int) -> tuple[int, int, int]:
    """``a`` and ``b`` are positional-only; only ``c`` is acceptable by keyword."""
    return (a, b, c)


@deprecated("use renamed_fn() instead")
def deprecated_fn(x: int) -> int:
    """Carries a PEP 702 ``__deprecated__`` marker -> Check C must flag it (NOTICE)."""
    return x


@deprecated("Old is replaced by New")
class Old:
    """Deprecated class carrying the marker in its own ``__dict__``."""


# Not itself deprecated: it inherits ``Old.__deprecated__`` via attribute lookup, but the
# marker is absent from its own ``__dict__`` -> Check C must stay silent (read vars, not
# getattr). The catch_warnings suppresses the definition-time DeprecationWarning that
# subclassing a deprecated class raises.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class NotDeprecatedChild(Old):
        """A live (non-deprecated) subclass of a deprecated base."""


def warns_at_call(x: int) -> int:
    """Deprecated by a *runtime warning only* — NO ``__deprecated__`` marker.

    Check C must stay silent here: this mechanism is not statically detectable.
    """
    warnings.warn("warns_at_call is going away", DeprecationWarning, stacklevel=2)
    return x


def documented_legacy(x: int) -> int:
    """Deprecated: use normal() instead.

    The word "deprecated" appears only in this docstring -> no marker -> Check C silent.
    """
    return x
