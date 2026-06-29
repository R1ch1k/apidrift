"""Check A (symbol existence) tests.

Weighted hard toward the silence side: an import that raises, a broken submodule, a
C-extension callable, a dynamic-``__getattr__`` module, and — the case that disproved
the brief's flagship demo — a deprecation proxy whose symbol still *exists*. All must
emit nothing. Only genuinely-absent symbols on cleanly introspectable parents flag.

The positives lean on real installed packages (pandas), so they double as a check that
resolution + introspection + suggestion work end to end against the live environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from apidrift import introspect, resolver
from apidrift.checks import (
    Severity,
    Violation,
    check_deprecation,
    check_existence,
    check_keywords,
)


def _violations(source: str) -> list[Violation]:
    resolution = resolver.resolve_source(source)
    return [v for v in (check_existence(c) for c in resolution.resolved) if v is not None]


def _kw_violations(source: str) -> list[Violation]:
    resolution = resolver.resolve_source(source)
    return [v for call in resolution.resolved for v in check_keywords(call)]


def _reset_env_caches() -> None:
    introspect.import_package.cache_clear()
    introspect.package_version.cache_clear()
    resolver.classify_root.cache_clear()


# --------------------------------------------------------------------------- #
# Positives — genuinely absent symbols flag, with a did-you-mean
# --------------------------------------------------------------------------- #
def test_typo_flags_with_suggestion() -> None:
    (violation,) = _violations("import pandas as pd\npd.read_exel('x')\n")
    assert violation.check == "existence"
    assert violation.severity is Severity.ERROR
    assert violation.symbol == "pandas.read_exel"
    assert violation.token == "read_exel"
    assert violation.package == "pandas"
    assert violation.version is not None
    assert "read_excel" in violation.suggestions


def test_cross_library_confusion_flags() -> None:
    # `concatenate` is numpy's spelling; pandas uses `concat`.
    (violation,) = _violations("import pandas as pd\npd.concatenate([])\n")
    assert violation.symbol == "pandas.concatenate"
    assert "concat" in violation.suggestions


def test_version_removal_flags() -> None:
    # `TimeGrouper` was removed in pandas 1.0 (replaced by `Grouper`).
    (violation,) = _violations("import pandas as pd\npd.TimeGrouper('M')\n")
    assert violation.symbol == "pandas.TimeGrouper"
    assert "Grouper" in violation.suggestions


def test_from_import_missing_name_flags() -> None:
    (violation,) = _violations("from pandas import read_exel\nread_exel('x')\n")
    assert violation.symbol == "pandas.read_exel"


# --------------------------------------------------------------------------- #
# Silence — the cases that MUST emit nothing
# --------------------------------------------------------------------------- #
def test_present_symbol_is_silent() -> None:
    assert _violations("import pandas as pd\npd.read_csv('x')\n") == []


def test_bad_kwarg_but_present_symbol_is_silent() -> None:
    # The removed `mangle_dupe_cols` keyword is a Check B problem; `read_csv` itself
    # exists, so Check A must say nothing.
    assert _violations("import pandas as pd\npd.read_csv('x', mangle_dupe_cols=True)\n") == []


def test_deprecation_proxy_is_silent() -> None:
    # THE flagship correction: openai 1.x keeps `ChatCompletion` as a proxy that
    # exists on attribute access and only raises when called. The symbol is present,
    # so existence-checking must stay silent — that drift is a different check.
    assert _violations("import openai\nopenai.ChatCompletion.create(model='x')\n") == []


def test_dynamic_getattr_module_is_silent() -> None:
    # numpy defines a module-level __getattr__ (lazy aliases); absence is unprovable.
    assert _violations("import numpy\nnumpy.totally_absent_zzz()\n") == []


def test_cextension_callable_is_silent() -> None:
    # numpy.add is a C ufunc (not a module/class); absence on it is untrustworthy.
    assert _violations("import numpy\nnumpy.add.nonexistent_zzz()\n") == []


def test_import_failure_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "boom_pkg_zzz.py"
    module.write_text("raise RuntimeError('top-level boom')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("boom_pkg_zzz", None)
    _reset_env_caches()
    # Resolves (find_spec sees the file) but importing it raises -> unverifiable.
    assert _violations("import boom_pkg_zzz as b\nb.go()\n") == []


def test_broken_submodule_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "goodpkg_zzz"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sub.py").write_text("raise RuntimeError('broken submodule')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("goodpkg_zzz", "goodpkg_zzz.sub"):
        sys.modules.pop(name, None)
    _reset_env_caches()
    # Mid-chain submodule exists but errors on import -> unverifiable, not a flag.
    assert _violations("import goodpkg_zzz\ngoodpkg_zzz.sub.func()\n") == []


def test_valid_deep_attribute_is_silent() -> None:
    # openai.OpenAI exists; the whole path resolves -> no violation.
    assert _violations("import openai\nopenai.OpenAI()\n") == []


# --------------------------------------------------------------------------- #
# Check B (keyword-arg validity)
# --------------------------------------------------------------------------- #
def test_unexpected_keyword_flags() -> None:
    # `mangle_dupe_cols` was removed in pandas 2.0; read_csv has no **kwargs.
    (violation,) = _kw_violations("import pandas as pd\npd.read_csv('x', mangle_dupe_cols=True)\n")
    assert violation.check == "keyword"
    assert violation.severity is Severity.ERROR
    assert violation.symbol == "pandas.read_csv"
    assert violation.token == "mangle_dupe_cols"
    assert violation.package == "pandas"


def test_valid_keyword_is_silent() -> None:
    assert _kw_violations("import pandas as pd\npd.read_csv('x', sep=',')\n") == []


def test_var_keyword_target_is_silent() -> None:
    # requests.get(url, **kwargs) declares **kwargs -> any keyword could be valid.
    assert _kw_violations("import requests\nrequests.get('u', verify=False, retries=3)\n") == []


def test_no_signature_callable_is_silent() -> None:
    # numpy.array is a C-extension callable; inspect.signature() raises -> silent.
    assert _kw_violations("import numpy\nnumpy.array([1], bogus_kw=2)\n") == []


def test_proxy_signature_raises_is_silent() -> None:
    # openai 1.x's ChatCompletion proxy raises a CUSTOM exception when introspected.
    # signature() must fail safe (not just ValueError/TypeError) -> no crash, no flag.
    source = "import openai\nopenai.ChatCompletion.create(model='x', messages=[])\n"
    assert _kw_violations(source) == []


def test_kwargs_unpacking_does_not_suppress_explicit_bad_keyword() -> None:
    # `**opts` keys are unknown (skipped), but an explicit bad keyword still flags.
    source = "import pandas as pd\nopts = {}\npd.read_csv('x', mangle_dupe_cols=True, **opts)\n"
    (violation,) = _kw_violations(source)
    assert violation.token == "mangle_dupe_cols"


def test_absent_symbol_does_not_also_run_kwargs() -> None:
    # check_call short-circuits on an absent symbol: exactly one (existence) violation.
    from apidrift.checks import check_call

    resolution = resolver.resolve_source("import pandas as pd\npd.read_exel('x', bogus=1)\n")
    violations = [v for call in resolution.resolved for v in check_call(call)]
    assert len(violations) == 1
    assert violations[0].check == "existence"


# --- positional-only soundness (uses the importable legacy_lib fixture) --- #
def test_valid_keyword_not_flagged_with_positional_only() -> None:
    # `c` is positional-or-keyword on positional_only(a, b, /, c) -> a VALID keyword.
    # The accepted-keyword set must not false-flag it, and must not crash on the `/`.
    source = "import legacy_lib\nlegacy_lib.positional_only(1, 2, c=3)\n"
    assert _kw_violations(source) == []


def test_normal_keyword_not_flagged() -> None:
    assert _kw_violations("import legacy_lib\nlegacy_lib.normal(x=1, y=2)\n") == []


def test_positional_only_passed_by_keyword_flags() -> None:
    # `a`/`b` are positional-only and there is no **kwargs, so passing them by keyword
    # is a genuine runtime error -> sound to flag. `c` (a valid keyword) stays unflagged.
    source = "import legacy_lib\nlegacy_lib.positional_only(a=1, b=2, c=3)\n"
    tokens = {v.token for v in _kw_violations(source)}
    assert tokens == {"a", "b"}


def test_functools_wraps_added_keyword_is_silent() -> None:
    # functools.wraps makes signature() follow __wrapped__ to a function without `new_kw`;
    # consulting the wrapper's own signature too keeps this valid call silent.
    source = "import legacy_lib\nlegacy_lib.wrapped_adds_kwarg(1, 2, new_kw=3)\n"
    assert _kw_violations(source) == []


def test_functools_wraps_genuinely_bad_keyword_still_flags() -> None:
    # A keyword that NEITHER the wrapper nor the wrapped function accepts is still a real
    # error — the __wrapped__ leniency must not suppress genuine drift.
    source = "import legacy_lib\nlegacy_lib.wrapped_adds_kwarg(1, 2, totally_bogus=9)\n"
    (violation,) = _kw_violations(source)
    assert violation.token == "totally_bogus"


def test_synthetic_signature_over_varkw_is_silent() -> None:
    # FIX 5: a hand-set, narrower __signature__ over a **kwargs callable must not turn a
    # real keyword into a false positive. A synthetic signature is an unreliable basis for
    # keyword rejection -> stay silent. (`beta` is not in the advertised sig, but the real
    # callable accepts it via **kwargs.)
    source = "import legacy_lib\nlegacy_lib.varkw_with_synthetic_signature(beta=1)\n"
    assert _kw_violations(source) == []


def test_genuinely_bad_keyword_on_real_signature_still_flags() -> None:
    # The flip side of FIX 5: a genuine (introspection-derived) signature still rejects a
    # bad keyword. `normal(x, y)` has no **kwargs and no synthetic signature, so `z` flags.
    source = "import legacy_lib\nlegacy_lib.normal(x=1, z=99)\n"
    (violation,) = _kw_violations(source)
    assert violation.token == "z"


# --------------------------------------------------------------------------- #
# Check C (PEP 702 __deprecated__ deprecation)
# --------------------------------------------------------------------------- #
def _deprecations(source: str) -> list[Violation]:
    resolution = resolver.resolve_source(source)
    out: list[Violation] = []
    for call in resolution.resolved:
        violation = check_deprecation(call)
        if violation is not None:
            out.append(violation)
    return out


def test_deprecated_function_flags_as_notice() -> None:
    (violation,) = _deprecations("import legacy_lib\nlegacy_lib.deprecated_fn(1)\n")
    assert violation.check == "deprecation"
    assert violation.severity is Severity.NOTICE
    assert violation.symbol == "legacy_lib.deprecated_fn"
    assert violation.note == "use renamed_fn() instead"


def test_deprecated_class_flags_as_notice() -> None:
    (violation,) = _deprecations("import legacy_lib\nlegacy_lib.Old()\n")
    assert violation.severity is Severity.NOTICE
    assert violation.note == "Old is replaced by New"


def test_non_deprecated_subclass_stays_silent() -> None:
    # NotDeprecatedChild inherits __deprecated__ via getattr but NOT in its own __dict__.
    # Reading the marker from getattr (instead of vars) would false-flag it.
    assert _deprecations("import legacy_lib\nlegacy_lib.NotDeprecatedChild()\n") == []


def test_runtime_warning_deprecation_stays_silent() -> None:
    # Deprecated via a call-time DeprecationWarning, no PEP 702 marker -> not detectable.
    assert _deprecations("import legacy_lib\nlegacy_lib.warns_at_call(1)\n") == []


def test_docstring_deprecation_stays_silent() -> None:
    # "deprecated" only in the docstring -> no marker -> silent (non-deterministic signal).
    assert _deprecations("import legacy_lib\nlegacy_lib.documented_legacy(1)\n") == []


def test_openai_proxy_is_not_deprecation_flagged() -> None:
    # The APIRemovedInV1 proxy is not a __deprecated__ marker -> silent. No special-casing.
    source = "import openai\nopenai.ChatCompletion.create(model='x', messages=[])\n"
    assert _deprecations(source) == []


def test_valid_symbol_is_not_deprecation_flagged() -> None:
    assert _deprecations("import legacy_lib\nlegacy_lib.normal(1, 2)\n") == []
