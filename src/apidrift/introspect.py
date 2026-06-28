"""Safe environment introspection — the fail-safe boundary for Check A.

The resolver only ever used ``find_spec`` and never imported anything. Check A must
go further: to ask whether a symbol *exists*, it imports the package and walks it.
Importing arbitrary third-party code can do anything — raise, hang on a missing
transitive dependency, or print — so every entry point here degrades to a safe,
silent result (``None`` / empty) instead of propagating. Soundness (tenet #1) lives
or dies on this module never turning an environment quirk into a false alarm.

This module is pure environment access — *capability*, not *policy*. The decision of
when an absence is trustworthy enough to flag lives in ``checks.py``.
"""

from __future__ import annotations

import difflib
import importlib
import importlib.metadata
from dataclasses import dataclass
from enum import Enum
from functools import cache
from types import ModuleType


@cache
def import_package(root: str) -> ModuleType | None:
    """Import a top-level package, failing safe.

    Returns the module, or ``None`` if it cannot be imported cleanly — whether the
    package is absent, raises ``ImportError``, or its top-level code throws anything
    else. A ``None`` here means *unverifiable*, never *missing*.
    """
    try:
        return importlib.import_module(root)
    except Exception:  # deliberate fail-safe: any import error or top-level raise => silent
        return None


class SubmoduleStatus(Enum):
    """Outcome of trying to import a dotted path *as a submodule*."""

    OK = "ok"
    NOT_A_MODULE = "not-a-module"  # the name is an attribute, not an importable module
    BROKEN = "broken"  # it *is* a module but importing it failed -> unverifiable


@dataclass(frozen=True)
class SubmoduleImport:
    status: SubmoduleStatus
    module: ModuleType | None


def try_import_submodule(name: str) -> SubmoduleImport:
    """Try to import ``name`` as a submodule, distinguishing the three outcomes.

    The distinction matters for soundness. ``import x.y`` failing because ``y`` is a
    plain attribute (a function/class) is normal — fall back to ``getattr``. But it
    failing because ``y``'s own import is broken (a missing transitive dependency)
    means we cannot trust a later ``getattr`` absence, so we must stay silent.
    """
    try:
        return SubmoduleImport(SubmoduleStatus.OK, importlib.import_module(name))
    except ModuleNotFoundError as exc:
        # Only "the candidate itself isn't a module" is safe to treat as not-a-module.
        # A *different* missing module (a broken dependency) is unverifiable.
        if exc.name is None or exc.name == name or name.startswith(f"{exc.name}."):
            return SubmoduleImport(SubmoduleStatus.NOT_A_MODULE, None)
        return SubmoduleImport(SubmoduleStatus.BROKEN, None)
    except Exception:  # a module that errors on import is unverifiable, not absent
        return SubmoduleImport(SubmoduleStatus.BROKEN, None)


def has_dynamic_getattr(obj: object) -> bool:
    """True if attribute access on ``obj`` is dynamic (PEP 562 / custom ``__getattr__``).

    When a module or type resolves attributes dynamically, ``getattr`` succeeds (or
    fails) for reasons ``dir()`` cannot see, so absence cannot be proven — the caller
    must stay silent on such parents.
    """
    if isinstance(obj, ModuleType):
        return "__getattr__" in getattr(obj, "__dict__", {})
    return hasattr(type(obj), "__getattr__")


def is_introspectable_parent(obj: object) -> bool:
    """True only if a missing attribute on ``obj`` can be trusted as genuinely absent.

    Restricted to modules and classes with static attribute access. Instances,
    C-extension callables (ufuncs, builtins) and dynamic-``__getattr__`` objects do
    not give a reliable "this name does not exist" answer, so they are excluded.
    """
    return isinstance(obj, (ModuleType, type)) and not has_dynamic_getattr(obj)


def public_members(obj: object) -> list[str]:
    """Public (non-underscore) attribute names of ``obj``, fail-safe to ``[]``."""
    try:
        return [name for name in dir(obj) if not name.startswith("_")]
    except Exception:  # a broken __dir__ just yields no suggestions
        return []


def did_you_mean(name: str, candidates: list[str], *, limit: int = 3) -> tuple[str, ...]:
    """Closest ``candidates`` to ``name`` via difflib (the "did you mean" suggestion)."""
    return tuple(difflib.get_close_matches(name, candidates, n=limit, cutoff=0.6))


@cache
def package_version(root: str) -> str | None:
    """Installed version of the distribution providing import package ``root``.

    Maps the import name to its distribution (e.g. ``sklearn`` -> ``scikit-learn``)
    before querying. Returns ``None`` if it cannot be determined.
    """
    try:
        distributions = importlib.metadata.packages_distributions().get(root)
        dist = distributions[0] if distributions else root
        return importlib.metadata.version(dist)
    except Exception:  # version is cosmetic; never fail the check over it
        return None
