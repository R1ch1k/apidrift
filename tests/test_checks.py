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
from apidrift.checks import Violation, check_existence


def _violations(source: str) -> list[Violation]:
    resolution = resolver.resolve_source(source)
    return [v for v in (check_existence(c) for c in resolution.resolved) if v is not None]


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
    assert violation.missing_path == "pandas.read_exel"
    assert violation.missing_symbol == "read_exel"
    assert violation.parent_fqname == "pandas"
    assert violation.package == "pandas"
    assert violation.version is not None
    assert "read_excel" in violation.suggestions


def test_cross_library_confusion_flags() -> None:
    # `concatenate` is numpy's spelling; pandas uses `concat`.
    (violation,) = _violations("import pandas as pd\npd.concatenate([])\n")
    assert violation.missing_path == "pandas.concatenate"
    assert "concat" in violation.suggestions


def test_version_removal_flags() -> None:
    # `TimeGrouper` was removed in pandas 1.0 (replaced by `Grouper`).
    (violation,) = _violations("import pandas as pd\npd.TimeGrouper('M')\n")
    assert violation.missing_path == "pandas.TimeGrouper"
    assert "Grouper" in violation.suggestions


def test_from_import_missing_name_flags() -> None:
    (violation,) = _violations("from pandas import read_exel\nread_exel('x')\n")
    assert violation.missing_path == "pandas.read_exel"


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
