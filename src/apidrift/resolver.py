"""AST resolution of third-party API call targets — apidrift's engineering core.

This module does *resolution only*: it turns a Python source file into the set of
fully-qualified call targets that root at an imported, installed, third-party
package. It performs no existence or signature checks — those live in ``checks.py``.

Resolution is deliberately conservative (sound-by-default, design tenet #1). A call
that cannot be traced with confidence to an imported third-party package is skipped,
never guessed:

* Receivers that are not an imported name (``df.merge(...)`` where ``df`` is a local)
  need type inference — out of scope, skipped.
* Names that may originate from ``from x import *`` are refused.
* Names that are reassigned or ambiguously imported are dropped.
* stdlib and relative/local imports are out of scope.

The two halves are :func:`build_import_table` (which names map to which packages) and
:func:`resolve_calls` (which calls root at one of those names).
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from pathlib import Path

_STDLIB_MODULES = sys.stdlib_module_names


class RootKind(Enum):
    """Classification of a call's top-level root package."""

    THIRD_PARTY = "third-party"
    STDLIB = "stdlib"
    NOT_INSTALLED = "not-installed"


class SkipReason(Enum):
    """Why a call was not resolved to a checkable third-party target."""

    NOT_RESOLVABLE = "receiver is not an imported name (local / inferred type)"
    RELATIVE_IMPORT = "relative or local import (no installed version to check)"
    WILDCARD = "bare name may originate from a wildcard import"
    STDLIB = "stdlib module (out of scope for v0)"
    NOT_INSTALLED = "package not installed in this environment"


@dataclass(frozen=True)
class ImportedName:
    """A name bound in the module namespace by an import statement.

    ``base_fqname`` is the fully-qualified dotted prefix the name refers to. For
    ``import pandas as pd`` it is ``pandas``; for ``from pandas import read_csv`` it
    is ``pandas.read_csv``.
    """

    local_name: str
    base_fqname: str
    root_package: str
    lineno: int


@dataclass(frozen=True)
class ImportTable:
    """The resolved import environment of a single module."""

    names: dict[str, ImportedName]
    wildcard_modules: tuple[str, ...]
    relative_names: frozenset[str]


@dataclass(frozen=True)
class ResolvedCall:
    """A call whose target was resolved to an installed third-party FQ name."""

    fqname: str
    root_package: str
    attr_path: tuple[str, ...]
    lineno: int
    col_offset: int
    node: ast.Call = field(compare=False, repr=False)


@dataclass(frozen=True)
class SkippedCall:
    """A call apidrift declined to resolve, with the reason (for ``--verbose``)."""

    display: str
    reason: SkipReason
    lineno: int
    col_offset: int


@dataclass(frozen=True)
class FileResolution:
    """The full resolution result for one source file."""

    path: str
    resolved: tuple[ResolvedCall, ...]
    skipped: tuple[SkippedCall, ...]
    import_table: ImportTable
    syntax_error: SyntaxError | None = None


# --------------------------------------------------------------------------- #
# Import table
# --------------------------------------------------------------------------- #
def build_import_table(tree: ast.Module) -> ImportTable:
    """Build the name -> target map for a parsed module.

    Handles ``import x``, ``import x as y``, ``import x.y``, ``import x.y as z`` and
    ``from x import a, b as c``. ``from x import *`` marks the module wildcard.
    Relative imports are recorded as local-only. For soundness, names that are
    imported ambiguously (to two different targets) or reassigned anywhere in the
    module are dropped from the table.
    """
    names: dict[str, ImportedName] = {}
    conflicts: set[str] = set()
    wildcard: list[str] = []
    relative: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                root = module.split(".", 1)[0]
                if alias.asname:
                    # `import a.b.c as x` -> x refers to the full a.b.c
                    local, base = alias.asname, module
                else:
                    # `import a.b.c` binds only the top name `a`
                    local, base = root, root
                _register(names, conflicts, ImportedName(local, base, root, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # relative import: from . / from ..pkg import x
                for alias in node.names:
                    if alias.name != "*":
                        relative.add(alias.asname or alias.name)
                continue
            module = node.module or ""
            if not module:
                continue
            root = module.split(".", 1)[0]
            for alias in node.names:
                if alias.name == "*":
                    wildcard.append(module)
                    continue
                local = alias.asname or alias.name
                base = f"{module}.{alias.name}"
                _register(names, conflicts, ImportedName(local, base, root, node.lineno))

    # Soundness: a name that is ambiguous or reassigned cannot be trusted.
    for name in conflicts | _reassigned_names(tree):
        names.pop(name, None)

    return ImportTable(
        names=names,
        wildcard_modules=tuple(wildcard),
        relative_names=frozenset(relative),
    )


def _register(
    names: dict[str, ImportedName],
    conflicts: set[str],
    entry: ImportedName,
) -> None:
    existing = names.get(entry.local_name)
    if existing is not None and existing.base_fqname != entry.base_fqname:
        conflicts.add(entry.local_name)
    names[entry.local_name] = entry


def _reassigned_names(tree: ast.Module) -> set[str]:
    """Names that are assigned, looped, bound as a parameter, or defined anywhere.

    Import statements do not produce ``Store`` ``Name`` nodes, so an imported alias
    only appears here if the code rebinds it — in which case its target is no longer
    trustworthy and we drop it. ``def``, ``async def`` and ``class`` bind their name
    via the node's ``.name`` string (no ``Store`` ``Name`` node either), so they must
    be collected explicitly — otherwise a local ``def read_csv(...)`` would shadow
    ``from pandas import read_csv`` and the resolver would wrongly target
    ``pandas.read_csv`` on the user's own function. Whole-module scope is fine:
    over-dropping only costs recall, never soundness.
    """
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            assigned.add(node.id)
        elif isinstance(node, ast.arg):
            assigned.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned.add(node.name)
    return assigned


# --------------------------------------------------------------------------- #
# Call resolution
# --------------------------------------------------------------------------- #
def resolve_calls(
    tree: ast.Module,
    table: ImportTable,
) -> tuple[list[ResolvedCall], list[SkippedCall]]:
    """Resolve every call in the tree against the import table.

    Returns ``(resolved, skipped)``. Only calls rooting at an imported, installed,
    third-party package land in ``resolved``; everything else is skipped (with a
    reason recorded when it is informative enough to show under ``--verbose``).
    """
    resolved: list[ResolvedCall] = []
    skipped: list[SkippedCall] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        unwound = _unwind_call_func(node.func)
        if unwound is None:
            # Receiver is itself a call, subscript, etc. — nothing to resolve.
            continue
        root_name, attrs = unwound

        imported = table.names.get(root_name)
        if imported is None:
            reason = _skip_reason_for_unimported(root_name, attrs, table)
            if reason is not None:
                display = ".".join((root_name, *attrs))
                skipped.append(SkippedCall(display, reason, node.lineno, node.col_offset))
            continue

        fqname = imported.base_fqname
        if attrs:
            fqname = f"{fqname}." + ".".join(attrs)

        kind = classify_root(imported.root_package)
        if kind is RootKind.STDLIB:
            skipped.append(SkippedCall(fqname, SkipReason.STDLIB, node.lineno, node.col_offset))
        elif kind is RootKind.NOT_INSTALLED:
            skipped.append(
                SkippedCall(fqname, SkipReason.NOT_INSTALLED, node.lineno, node.col_offset)
            )
        else:
            resolved.append(
                ResolvedCall(
                    fqname=fqname,
                    root_package=imported.root_package,
                    attr_path=attrs,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node=node,
                )
            )

    return resolved, skipped


def _unwind_call_func(func: ast.expr) -> tuple[str, tuple[str, ...]] | None:
    """Unwind a Name/Attribute chain to ``(root_name, attr_path)``.

    ``pd.read_csv``                  -> ``("pd", ("read_csv",))``
    ``openai.chat.completions.create`` -> ``("openai", ("chat", "completions", "create"))``
    ``read_csv``                     -> ``("read_csv", ())``
    ``foo().bar`` / ``obj[0].bar``   -> ``None`` (receiver is not a plain name)
    """
    attrs: list[str] = []
    cur: ast.expr = func
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    attrs.reverse()
    return cur.id, tuple(attrs)


def _skip_reason_for_unimported(
    root_name: str,
    attrs: tuple[str, ...],
    table: ImportTable,
) -> SkipReason | None:
    """Reason to record for a call whose root is not an imported name.

    Returns ``None`` for bare builtin/local function calls (``print()``, ``foo()``):
    recording every such call would drown ``--verbose`` in noise.
    """
    if root_name in table.relative_names:
        return SkipReason.RELATIVE_IMPORT
    if attrs:
        # e.g. df.merge(...) — receiver is a local or inferred type, out of scope.
        return SkipReason.NOT_RESOLVABLE
    if table.wildcard_modules:
        return SkipReason.WILDCARD
    return None


@cache
def classify_root(root_package: str) -> RootKind:
    """Classify a top-level package as stdlib, installed third-party, or absent.

    Uses ``importlib.util.find_spec`` on the top-level name only, which inspects the
    import finders *without importing the module* (no side effects). Cached because
    the same roots recur across many calls and files.
    """
    if root_package in _STDLIB_MODULES:
        return RootKind.STDLIB
    try:
        spec = importlib.util.find_spec(root_package)
    except (ImportError, ValueError, AttributeError):
        spec = None
    if spec is None:
        return RootKind.NOT_INSTALLED
    return RootKind.THIRD_PARTY


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def resolve_source(source: str, path: str = "<unknown>") -> FileResolution:
    """Parse and resolve a source string. Syntax errors are captured, not raised."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        empty = ImportTable(names={}, wildcard_modules=(), relative_names=frozenset())
        return FileResolution(
            path=path, resolved=(), skipped=(), import_table=empty, syntax_error=exc
        )
    table = build_import_table(tree)
    resolved, skipped = resolve_calls(tree, table)
    return FileResolution(
        path=path,
        resolved=tuple(resolved),
        skipped=tuple(skipped),
        import_table=table,
    )


def resolve_file(path: Path) -> FileResolution:
    """Read and resolve a file from disk."""
    source = path.read_text(encoding="utf-8")
    return resolve_source(source, str(path))
