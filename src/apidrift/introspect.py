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
import inspect
from dataclasses import asdict, dataclass
from enum import Enum
from functools import cache
from types import ModuleType

#: The three verdicts a record can carry. ``resolved`` and ``absent`` are definitive;
#: ``unverifiable`` means "could not be proven either way" and always resolves to silence.
RESOLVED = "resolved"
ABSENT = "absent"
UNVERIFIABLE = "unverifiable"
_VALID_STATUS = frozenset({RESOLVED, ABSENT, UNVERIFIABLE})


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


def safe_signature(obj: object, *, follow_wrapped: bool = True) -> inspect.Signature | None:
    """``inspect.signature(obj)`` or ``None`` if it cannot be introspected — fail-safe.

    The catch is deliberately broad. Beyond the documented ``ValueError`` /
    ``TypeError`` (C-extension callables, some builtins), deprecation proxies can make
    ``signature()`` raise a *custom* exception of their own — e.g. openai 1.x's
    ``ChatCompletion`` raises ``APIRemovedInV1`` when introspected. Any such failure is
    unverifiable and must be silent, never a crash and never a flag.

    ``follow_wrapped`` is threaded through so a caller can read a wrapper's *own* signature
    (``follow_wrapped=False``) instead of the ``functools.wraps`` ``__wrapped__`` target.
    """
    if not callable(obj):
        return None
    try:
        return inspect.signature(obj, follow_wrapped=follow_wrapped)
    except Exception:  # any failure, incl. custom proxy exceptions -> unverifiable
        return None


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


def _deprecated_marker(obj: object) -> str | None:
    """The PEP 702 ``__deprecated__`` message if present in the object's OWN namespace.

    Read from ``vars(obj)``, never ``getattr``: a non-deprecated subclass inherits a
    deprecated base's ``__deprecated__`` via attribute lookup, which would be a false
    positive. The own ``__dict__`` is exactly where the ``@deprecated`` decorator stores
    it. Returns ``None`` when not deprecated, the (possibly empty) message otherwise.
    """
    try:
        own = vars(obj)
    except TypeError:  # objects without a __dict__ (most C-level callables) -> silent
        return None
    marker = own.get("__deprecated__")
    if marker is None:
        return None
    return marker if isinstance(marker, str) else ""


# --------------------------------------------------------------------------- #
# Whole-symbol introspection -> a serializable record
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IntrospectionRecord:
    """Everything the checks need about one fully-qualified symbol, as plain data.

    This is the cache unit: it is deterministic for a given ``(package, version,
    fqname)`` and contains no live objects, so it round-trips to disk. ``status`` is
    one of ``"resolved"`` / ``"absent"`` / ``"unverifiable"``. The remaining fields are
    populated per status; a check reads only the fields relevant to its verdict.
    """

    status: str
    # status == "absent":
    missing_index: int = -1
    missing_segment: str = ""
    suggestions: tuple[str, ...] = ()  # precomputed "did you mean" for the missing segment
    # status == "resolved":
    has_signature: bool = False
    has_var_keyword: bool = False
    acceptable_keywords: tuple[str, ...] = ()  # params passable by keyword
    deprecated_message: str | None = None  # PEP 702 marker; None => not deprecated


def record_to_dict(record: IntrospectionRecord) -> dict[str, object]:
    """Serialize a record to a JSON-safe dict (tuples become lists on the wire)."""
    return asdict(record)


def _str_tuple(value: object) -> tuple[str, ...] | None:
    """``value`` as a tuple of strings, or ``None`` if it is not a list/tuple of strings."""
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def record_from_dict(raw: object) -> IntrospectionRecord | None:
    """Rebuild a record from untrusted JSON, returning ``None`` on ANY irregularity.

    The single trust boundary for every serialized record — both on-disk cache entries
    and subprocess-worker results pass through here. It validates the status, every
    field's type, and the per-status invariants (an ``absent`` record must carry a real
    ``missing_index``/``missing_segment``; a poisoned or partial entry must not). A
    ``None`` means "do not trust this" — the caller re-introspects rather than risk a
    wrong verdict from corrupt or forward-incompatible data.
    """
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if status not in _VALID_STATUS:
        return None

    if status == ABSENT:
        missing_index = raw.get("missing_index")
        missing_segment = raw.get("missing_segment")
        suggestions = _str_tuple(raw.get("suggestions", ()))
        # bool is an int subclass; a JSON true/false is never a valid index.
        if not isinstance(missing_index, int) or isinstance(missing_index, bool):
            return None
        if missing_index < 0 or not isinstance(missing_segment, str) or not missing_segment:
            return None
        if suggestions is None:
            return None
        return IntrospectionRecord(
            status=ABSENT,
            missing_index=missing_index,
            missing_segment=missing_segment,
            suggestions=suggestions,
        )

    if status == RESOLVED:
        has_signature = raw.get("has_signature", False)
        has_var_keyword = raw.get("has_var_keyword", False)
        acceptable = _str_tuple(raw.get("acceptable_keywords", ()))
        deprecated = raw.get("deprecated_message")
        if not isinstance(has_signature, bool) or not isinstance(has_var_keyword, bool):
            return None
        if acceptable is None or (deprecated is not None and not isinstance(deprecated, str)):
            return None
        return IntrospectionRecord(
            status=RESOLVED,
            has_signature=has_signature,
            has_var_keyword=has_var_keyword,
            acceptable_keywords=acceptable,
            deprecated_message=deprecated,
        )

    return IntrospectionRecord(status=UNVERIFIABLE)


def introspect_fqname(root_package: str, fqname: str) -> IntrospectionRecord:
    """Resolve ``fqname`` against the installed package and capture it as a record.

    Extends the module boundary along importable submodules first, then resolves the
    remaining segments via ``getattr``. The only place that actually imports the
    package — so on a cache hit upstream, the package is never imported. Fail-safe:
    any uncertainty becomes an ``"unverifiable"`` record (the checks stay silent on it).
    """
    module = import_package(root_package)
    if module is None:
        return IntrospectionRecord(status="unverifiable")

    segments = fqname.split(".")
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
            return IntrospectionRecord(status="unverifiable")

    while index < len(segments):
        segment = segments[index]
        parent = obj
        try:
            obj = getattr(parent, segment)
        except AttributeError:
            if not is_introspectable_parent(parent):
                return IntrospectionRecord(status="unverifiable")
            return IntrospectionRecord(
                status="absent",
                missing_index=index,
                missing_segment=segment,
                suggestions=did_you_mean(segment, public_members(parent)),
            )
        except Exception:  # a descriptor/property that raises -> unverifiable, stay silent
            return IntrospectionRecord(status="unverifiable")
        index += 1

    return _resolved_record(obj)


def _signature_kwargs(signature: inspect.Signature) -> tuple[bool, frozenset[str]]:
    """``(declares **kwargs, names passable by keyword)`` for one signature."""
    params = signature.parameters
    has_var_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    acceptable = frozenset(
        name
        for name, param in params.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )
    return has_var_keyword, acceptable


def _has_synthetic_signature(obj: object) -> bool:
    """True if ``obj`` carries an explicit ``__signature__`` that ``inspect`` would honor.

    ``inspect.signature`` honors an author-set ``__signature__`` over the callable's real
    parameters. A *narrower* synthetic signature laid over a ``**kwargs`` callable (a common
    decorator/proxy pattern) would turn perfectly valid keywords into false positives, so a
    synthetic signature is an unreliable basis for keyword REJECTION. Ordinary functions and
    classes have no ``__signature__`` (it is derived from ``__code__`` on demand), so they are
    unaffected — only a hand-set one trips this, and there we decline to judge keywords.
    """
    try:
        return hasattr(obj, "__signature__")
    except Exception:  # even probing is unreliable -> be conservative and treat as synthetic
        return True


def _keyword_signatures(obj: object) -> list[inspect.Signature]:
    """The signature(s) to trust for keyword validity, or ``[]`` if it is ambiguous.

    Normally just ``inspect.signature(obj)``. Two cases force silence instead:

    * A hand-set ``__signature__`` (see :func:`_has_synthetic_signature`) can understate a
      ``**kwargs`` callable, so it is not a sound basis for rejecting a keyword → ``[]``.
    * ``functools.wraps`` sets ``__wrapped__`` and ``inspect.signature`` follows it by
      default — reporting the *wrapped* function's parameters, which misrepresents a wrapper
      that adds or drops keywords and would false-flag a valid call. So when ``__wrapped__``
      is present we require BOTH the followed and the unwrapped signatures, and the caller
      treats a keyword as valid if *either* accepts it (only what both reject is flagged). If
      either signature cannot be read the two cannot be reconciled → ``[]`` → silence.
    """
    if not callable(obj):
        return []
    if _has_synthetic_signature(obj):
        # A synthetic signature may lie about a **kwargs callable; rejecting keywords against
        # it risks a false positive. Decline to judge keywords here (tenet #1: silence wins).
        return []
    followed = safe_signature(obj)  # follow_wrapped=True (the default)
    if not hasattr(obj, "__wrapped__"):
        return [followed] if followed is not None else []
    unwrapped = safe_signature(obj, follow_wrapped=False)
    if followed is None or unwrapped is None:
        return []
    return [followed, unwrapped]


def _resolved_record(obj: object) -> IntrospectionRecord:
    signatures = _keyword_signatures(obj)
    has_var_keyword = False
    acceptable: frozenset[str] = frozenset()
    for signature in signatures:
        var_keyword, names = _signature_kwargs(signature)
        has_var_keyword = has_var_keyword or var_keyword
        acceptable |= names  # union: a keyword valid in EITHER signature is not flagged
    return IntrospectionRecord(
        status=RESOLVED,
        has_signature=bool(signatures),
        has_var_keyword=has_var_keyword,
        acceptable_keywords=tuple(sorted(acceptable)),
        deprecated_message=_deprecated_marker(obj),
    )
