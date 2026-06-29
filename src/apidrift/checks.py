"""The checks. Kept cleanly separable from resolution and reporting.

v0 ships Check A (symbol existence) and Check B (keyword-arg validity). Both share one
fail-safe walk of the resolved path against the *installed* package; the per-check
logic stays independent on top of it. Every uncertainty — an import that fails, a
dynamic ``__getattr__`` parent, a C-extension object, a callable with no introspectable
signature — resolves to silence, never a flag. That asymmetry is the product (tenet #1):
a missed drift is tolerable, a false alarm is not.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum

from apidrift.introspect import (
    SubmoduleStatus,
    did_you_mean,
    import_package,
    is_introspectable_parent,
    package_version,
    public_members,
    safe_signature,
    try_import_submodule,
)
from apidrift.resolver import ResolvedCall


class Severity(Enum):
    """How a diagnostic gates CI. Errors fail the run; notices do not (by default)."""

    ERROR = "error"
    NOTICE = "notice"


@dataclass(frozen=True)
class Violation:
    """A single diagnostic. Shared shape across all checks; ``check`` discriminates.

    ``symbol`` is the fully-qualified thing the diagnostic is about (the missing path,
    the called callable, or the deprecated symbol). ``token`` is the offending leaf
    (the absent attribute or the bad keyword); empty when not applicable.
    """

    check: str  # "existence" | "keyword" | "deprecation"
    severity: Severity
    lineno: int
    col_offset: int
    symbol: str
    token: str
    package: str
    version: str | None
    suggestions: tuple[str, ...] = ()
    note: str | None = None  # extra free-form text (e.g. a deprecation message)


# --------------------------------------------------------------------------- #
# Shared walk
# --------------------------------------------------------------------------- #
class _WalkStatus(Enum):
    RESOLVED = "resolved"  # whole path exists; .obj is the target object
    ABSENT = "absent"  # a segment is genuinely absent (Check A territory)
    UNVERIFIABLE = "unverifiable"  # import failed / dynamic / C-ext / broken -> silent


@dataclass(frozen=True)
class _WalkResult:
    status: _WalkStatus
    obj: object = None
    missing_index: int = -1
    parent: object = None


def _walk(call: ResolvedCall) -> _WalkResult:
    """Resolve ``call.fqname`` against the installed package, fail-safe.

    Extends the module boundary along importable submodules first, then resolves the
    remaining segments via ``getattr``. Returns RESOLVED with the target object,
    ABSENT at the first genuinely-absent segment, or UNVERIFIABLE for anything we
    cannot trust.
    """
    module = import_package(call.root_package)
    if module is None:
        return _WalkResult(_WalkStatus.UNVERIFIABLE)

    segments = call.fqname.split(".")
    obj: object = module
    index = 1  # segments[0] is the root we just imported

    while index < len(segments):
        candidate = ".".join(segments[: index + 1])
        result = try_import_submodule(candidate)
        if result.status is SubmoduleStatus.OK:
            obj = result.module
            index += 1
        elif result.status is SubmoduleStatus.NOT_A_MODULE:
            break
        else:  # BROKEN -> a real module that won't import cleanly
            return _WalkResult(_WalkStatus.UNVERIFIABLE)

    while index < len(segments):
        segment = segments[index]
        parent = obj
        try:
            obj = getattr(parent, segment)
        except AttributeError:
            if not is_introspectable_parent(parent):
                return _WalkResult(_WalkStatus.UNVERIFIABLE)
            return _WalkResult(_WalkStatus.ABSENT, missing_index=index, parent=parent)
        except Exception:  # a descriptor/property that raises -> unverifiable, stay silent
            return _WalkResult(_WalkStatus.UNVERIFIABLE)
        index += 1

    return _WalkResult(_WalkStatus.RESOLVED, obj=obj)


# --------------------------------------------------------------------------- #
# Check A — symbol existence
# --------------------------------------------------------------------------- #
def check_existence(call: ResolvedCall) -> Violation | None:
    """Flag a resolved call whose dotted target is absent in the installed package."""
    return _existence(call, _walk(call))


def _existence(call: ResolvedCall, walk: _WalkResult) -> Violation | None:
    if walk.status is not _WalkStatus.ABSENT:
        return None
    segments = call.fqname.split(".")
    index = walk.missing_index
    token = segments[index]
    return Violation(
        check="existence",
        severity=Severity.ERROR,
        lineno=call.lineno,
        col_offset=call.col_offset,
        symbol=".".join(segments[: index + 1]),
        token=token,
        package=call.root_package,
        version=package_version(call.root_package),
        suggestions=did_you_mean(token, public_members(walk.parent)),
    )


# --------------------------------------------------------------------------- #
# Check B — keyword-arg validity
# --------------------------------------------------------------------------- #
def check_keywords(call: ResolvedCall) -> list[Violation]:
    """Flag keyword arguments not accepted by the resolved callable's signature."""
    return _keywords(call, _walk(call))


def _keywords(call: ResolvedCall, walk: _WalkResult) -> list[Violation]:
    if walk.status is not _WalkStatus.RESOLVED:
        return []  # absent (Check A's job) or unverifiable -> silent

    signature = safe_signature(walk.obj)
    if signature is None:
        return []  # no introspectable signature -> unverifiable -> silent

    parameters = signature.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return []  # **kwargs declared -> any keyword could be valid -> silent (mandatory guard)

    acceptable = {
        name
        for name, param in parameters.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }

    violations: list[Violation] = []
    for keyword in call.node.keywords:
        if keyword.arg is None:
            continue  # `**mapping` unpacking -> keys unknown, cannot judge -> skip
        if keyword.arg not in acceptable:
            violations.append(
                Violation(
                    check="keyword",
                    severity=Severity.ERROR,
                    lineno=keyword.lineno,
                    col_offset=keyword.col_offset,
                    symbol=call.fqname,
                    token=keyword.arg,
                    package=call.root_package,
                    version=package_version(call.root_package),
                    suggestions=did_you_mean(keyword.arg, sorted(acceptable)),
                )
            )
    return violations


# --------------------------------------------------------------------------- #
# Aggregate — one walk, all checks
# --------------------------------------------------------------------------- #
def check_call(call: ResolvedCall) -> list[Violation]:
    """Run every check against one call with a single walk; return all violations."""
    walk = _walk(call)
    existence = _existence(call, walk)
    if existence is not None:
        return [existence]  # an absent symbol short-circuits: no point checking its kwargs
    return _keywords(call, walk)
