"""The checks. Kept cleanly separable from resolution, introspection, and reporting.

v0 ships three checks: A (symbol existence), B (keyword-arg validity), C (PEP 702
deprecation). Each is pure logic over an :class:`IntrospectionRecord` — the record does
all the importing/introspecting (and may come from the on-disk cache), the checks just
read it plus the call site. Every uncertainty in the record (``unverifiable``) resolves
to silence, never a flag. That asymmetry is the product (tenet #1): a missed drift is
tolerable, a false alarm is not.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from apidrift.introspect import (
    IntrospectionRecord,
    did_you_mean,
    introspect_fqname,
    package_version,
)
from apidrift.resolver import ResolvedCall

if TYPE_CHECKING:
    from apidrift.cache import IntrospectionCache

#: How records are produced for a batch of ``(root_package, fqname)`` pairs. Injected so
#: ``checks.py`` stays free of the subprocess machinery — the CLI passes the isolated
#: worker (``apidrift.worker.introspect_batch``); a test may pass an in-process stand-in.
IntrospectBatch = Callable[
    [Sequence[tuple[str, str]]], dict[tuple[str, str], IntrospectionRecord]
]


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
# Record acquisition (cache-aware)
# --------------------------------------------------------------------------- #
def record_for(call: ResolvedCall, cache: IntrospectionCache | None = None) -> IntrospectionRecord:
    """The introspection record for a call, from the cache when possible.

    With no cache (the default, used in tests) every call is introspected live. With a
    cache, a record is served by ``(package, version, fqname)`` — and only re-introspected
    on a miss, which is the only path that imports the package.
    """
    if cache is None:
        return introspect_fqname(call.root_package, call.fqname)

    version = package_version(call.root_package)
    if version is None:
        return introspect_fqname(call.root_package, call.fqname)  # version-less -> never cache

    cached = cache.get(call.root_package, version, call.fqname)
    if cached is not None:
        return cached
    record = introspect_fqname(call.root_package, call.fqname)
    cache.put(call.root_package, version, call.fqname, record)
    return record


def resolve_records(
    calls: Sequence[ResolvedCall],
    cache: IntrospectionCache | None,
    introspect_batch: IntrospectBatch,
) -> dict[tuple[str, str], IntrospectionRecord]:
    """A ``(root_package, fqname) -> record`` map for every call, cache-first.

    The whole run's introspection funnels through here: each distinct ``(root, fqname)``
    is served from the cache when its version is known and present, and everything else
    is gathered into a single ``introspect_batch`` call (one isolated subprocess per root,
    importing each package once). New definitive records are written back to the cache.
    This is the only path the CLI uses, so no package is ever imported in apidrift's own
    process.
    """
    wanted = {(call.root_package, call.fqname) for call in calls}
    versions = {root: package_version(root) for root, _ in wanted}

    records: dict[tuple[str, str], IntrospectionRecord] = {}
    misses: list[tuple[str, str]] = []
    for key in wanted:
        root, fqname = key
        version = versions[root]
        if cache is not None and version is not None:
            cached = cache.get(root, version, fqname)
            if cached is not None:
                records[key] = cached
                continue
        misses.append(key)

    if misses:
        for key, record in introspect_batch(misses).items():
            records[key] = record
            root, fqname = key
            version = versions[root]
            if cache is not None and version is not None:
                cache.put(root, version, fqname, record)  # put() ignores non-definitive records
    return records


# --------------------------------------------------------------------------- #
# Check A — symbol existence
# --------------------------------------------------------------------------- #
def check_existence(call: ResolvedCall) -> Violation | None:
    """Flag a resolved call whose dotted target is absent in the installed package."""
    return _existence(call, record_for(call))


def _existence(call: ResolvedCall, record: IntrospectionRecord) -> Violation | None:
    if record.status != "absent":
        return None
    segments = call.fqname.split(".")
    index = record.missing_index
    # Defense in depth: only flag from an absent record that is *consistent* with this
    # call. A record whose index is out of range, or whose missing segment does not match
    # the fqname, is malformed or poisoned — stay silent rather than emit a wrong symbol.
    if not 0 <= index < len(segments) or segments[index] != record.missing_segment:
        return None
    return Violation(
        check="existence",
        severity=Severity.ERROR,
        lineno=call.lineno,
        col_offset=call.col_offset,
        symbol=".".join(segments[: index + 1]),
        token=record.missing_segment,
        package=call.root_package,
        version=package_version(call.root_package),
        suggestions=record.suggestions,
    )


# --------------------------------------------------------------------------- #
# Check B — keyword-arg validity
# --------------------------------------------------------------------------- #
def check_keywords(call: ResolvedCall) -> list[Violation]:
    """Flag keyword arguments not accepted by the resolved callable's signature."""
    return _keywords(call, record_for(call))


def _keywords(call: ResolvedCall, record: IntrospectionRecord) -> list[Violation]:
    if record.status != "resolved" or not record.has_signature or record.has_var_keyword:
        # absent / unverifiable / no signature / **kwargs declared -> silent.
        return []

    acceptable = set(record.acceptable_keywords)
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
# Check C — PEP 702 deprecation (and ONLY that)
# --------------------------------------------------------------------------- #
def check_deprecation(call: ResolvedCall) -> Violation | None:
    """Flag a resolved symbol carrying a PEP 702 ``__deprecated__`` marker (NOTICE).

    Exactly one signal: the ``__deprecated__`` attribute set by ``warnings.deprecated`` /
    ``typing_extensions.deprecated`` / the ``@deprecated`` decorator. Deprecations
    expressed any other way (custom proxies, docstrings, runtime warnings, a curated
    version database) are intentionally NOT detected — that keeps the check sound and
    deterministic. NOTICE, never ERROR: deprecated code still works, so it does not gate
    CI by default.
    """
    return _deprecation(call, record_for(call))


def _deprecation(call: ResolvedCall, record: IntrospectionRecord) -> Violation | None:
    if record.status != "resolved" or record.deprecated_message is None:
        return None
    return Violation(
        check="deprecation",
        severity=Severity.NOTICE,
        lineno=call.lineno,
        col_offset=call.col_offset,
        symbol=call.fqname,
        token="",
        package=call.root_package,
        version=package_version(call.root_package),
        note=record.deprecated_message or None,
    )


# --------------------------------------------------------------------------- #
# Aggregate — one record, all checks
# --------------------------------------------------------------------------- #
def run_checks(call: ResolvedCall, record: IntrospectionRecord) -> list[Violation]:
    """Run every check against one call from an already-acquired record."""
    existence = _existence(call, record)
    if existence is not None:
        return [existence]  # an absent symbol short-circuits: no point checking it further
    violations = _keywords(call, record)
    deprecation = _deprecation(call, record)
    if deprecation is not None:
        violations.append(deprecation)
    return violations


def check_call(call: ResolvedCall, cache: IntrospectionCache | None = None) -> list[Violation]:
    """Run every check against one call, acquiring its record in-process (test/library API).

    The CLI instead uses :func:`resolve_records` + :func:`run_checks` so all importing
    happens in an isolated worker; this single-call form imports in-process and is kept
    for unit tests and direct library use against known-safe packages.
    """
    return run_checks(call, record_for(call, cache))
