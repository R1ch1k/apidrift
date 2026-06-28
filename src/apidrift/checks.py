"""The checks. Kept cleanly separable from resolution and reporting.

v0 ships Check A (symbol existence). Check B (keyword-arg validity) lands at M2.

Check A walks the resolved dotted path against the *installed* package and flags a
segment only when it is genuinely absent from a parent that could be introspected
cleanly. Every uncertainty — an import that fails, a dynamic ``__getattr__`` parent,
a C-extension object, a broken submodule — resolves to silence, never a flag. That
asymmetry is the product (tenet #1): a missed drift is tolerable, a false alarm is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from apidrift.introspect import (
    SubmoduleStatus,
    did_you_mean,
    import_package,
    is_introspectable_parent,
    package_version,
    public_members,
    try_import_submodule,
)
from apidrift.resolver import ResolvedCall


@dataclass(frozen=True)
class Violation:
    """A confirmed drift: a resolved call whose target is absent in the installed pkg."""

    check: str  # "existence"
    lineno: int
    col_offset: int
    call_fqname: str  # full resolved call, e.g. "openai.ChatCompletion.create"
    missing_path: str  # path up to and including the absent segment, e.g. "pandas.read_exel"
    missing_symbol: str  # the absent segment itself, e.g. "read_exel"
    parent_fqname: str  # the last valid parent, e.g. "pandas"
    package: str  # root package, e.g. "pandas"
    version: str | None  # installed version of that package
    suggestions: tuple[str, ...]  # "did you mean" from the real dir() of the parent


def check_existence(call: ResolvedCall) -> Violation | None:
    """Run Check A against one resolved call. ``None`` means clean *or* unverifiable.

    Walks ``call.fqname`` from the root: first extends the module boundary as far as
    the path imports as a submodule, then resolves the remaining segments via
    ``getattr``. A violation is returned only at the first segment that is genuinely
    absent from a cleanly introspectable parent.
    """
    module = import_package(call.root_package)
    if module is None:
        return None  # import failed/raised -> unverifiable

    segments = call.fqname.split(".")
    obj: object = module
    index = 1  # segments[0] is the root we just imported

    # Phase 1: extend the module boundary along importable submodules.
    while index < len(segments):
        candidate = ".".join(segments[: index + 1])
        result = try_import_submodule(candidate)
        if result.status is SubmoduleStatus.OK:
            obj = result.module
            index += 1
        elif result.status is SubmoduleStatus.NOT_A_MODULE:
            break  # the rest are attribute accesses, not submodules
        else:  # BROKEN -> a real module that won't import cleanly
            return None  # unverifiable

    # Phase 2: resolve the remaining segments as attributes.
    while index < len(segments):
        segment = segments[index]
        parent = obj
        try:
            obj = getattr(parent, segment)
        except AttributeError:
            if not is_introspectable_parent(parent):
                return None  # C-extension / instance / dynamic parent -> unverifiable
            return _violation(call, segments, index, parent)
        except Exception:  # a descriptor/property that raises -> unverifiable, stay silent
            return None
        index += 1

    return None  # whole path exists -> no drift


def _violation(
    call: ResolvedCall,
    segments: list[str],
    index: int,
    parent: object,
) -> Violation:
    segment = segments[index]
    return Violation(
        check="existence",
        lineno=call.lineno,
        col_offset=call.col_offset,
        call_fqname=call.fqname,
        missing_path=".".join(segments[: index + 1]),
        missing_symbol=segment,
        parent_fqname=".".join(segments[:index]),
        package=call.root_package,
        version=package_version(call.root_package),
        suggestions=did_you_mean(segment, public_members(parent)),
    )
