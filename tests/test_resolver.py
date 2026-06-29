"""Resolver tests — the M0 walking skeleton.

These cover two things: (1) that resolution produces the correct fully-qualified
targets across every import form, and (2) — the part that matters most — that
resolution stays SILENT on the cases it cannot resolve with confidence. Soundness
is the product, so the silence cases are tested like a feature, not an afterthought.

``pytest`` itself is used as the reliable installed-third-party anchor (it must be
present for these tests to run at all); ``os`` anchors stdlib; a deliberately absent
name anchors the not-installed path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from apidrift.resolver import (
    RootKind,
    SkipReason,
    build_import_table,
    classify_root,
    resolve_file,
    resolve_source,
)


def _fqnames(source: str) -> set[str]:
    return {call.fqname for call in resolve_source(source).resolved}


def _skips_by_reason(source: str) -> dict[SkipReason, list[str]]:
    out: dict[SkipReason, list[str]] = {}
    for skip in resolve_source(source).skipped:
        out.setdefault(skip.reason, []).append(skip.display)
    return out


# --------------------------------------------------------------------------- #
# Import table
# --------------------------------------------------------------------------- #
def test_import_forms_map_to_correct_base() -> None:
    source = (
        "import pandas\n"
        "import pandas as pd\n"
        "import os.path\n"
        "import os.path as osp\n"
        "from openai import OpenAI\n"
        "from openai import OpenAI as Client\n"
    )
    table = build_import_table(ast.parse(source))
    names = table.names

    assert names["pandas"].base_fqname == "pandas"
    assert names["pd"].base_fqname == "pandas"
    # `import os.path` binds only the top name `os`.
    assert names["os"].base_fqname == "os"
    assert names["os"].root_package == "os"
    # `import os.path as osp` binds the full dotted path.
    assert names["osp"].base_fqname == "os.path"
    assert names["osp"].root_package == "os"
    assert names["OpenAI"].base_fqname == "openai.OpenAI"
    assert names["Client"].base_fqname == "openai.OpenAI"


def test_relative_imports_recorded_not_resolved() -> None:
    table = build_import_table(ast.parse("from . import helpers\nfrom ..pkg import util\n"))
    assert table.names == {}
    assert "helpers" in table.relative_names
    assert "util" in table.relative_names


def test_wildcard_import_recorded() -> None:
    table = build_import_table(ast.parse("from pandas import *\n"))
    assert table.wildcard_modules == ("pandas",)


# --------------------------------------------------------------------------- #
# Call resolution — the targets that SHOULD resolve
# --------------------------------------------------------------------------- #
def test_resolves_attribute_chain() -> None:
    source = "import openai\nopenai.ChatCompletion.create(model='x')\n"
    assert _fqnames(source) == {"openai.ChatCompletion.create"}


def test_resolves_deep_attribute_chain() -> None:
    source = "import openai\nopenai.chat.completions.create()\n"
    assert _fqnames(source) == {"openai.chat.completions.create"}


def test_resolves_aliased_module() -> None:
    source = "import pandas as pd\npd.read_csv('x')\n"
    assert _fqnames(source) == {"pandas.read_csv"}


def test_resolves_from_import_bare_name() -> None:
    source = "from pandas import read_csv\nread_csv('x')\n"
    assert _fqnames(source) == {"pandas.read_csv"}


def test_resolves_via_submodule_alias() -> None:
    # `import pkg.sub as s; s.func()` -> pkg.sub.func  (use a guaranteed-installed pkg)
    source = "import pytest as p\np.raises(ValueError)\n"
    assert _fqnames(source) == {"pytest.raises"}


# --------------------------------------------------------------------------- #
# Soundness — the cases that MUST stay silent
# --------------------------------------------------------------------------- #
def test_method_call_on_local_is_skipped() -> None:
    source = "import pandas as pd\ndf = pd.read_csv('x')\ndf.merge(other, on='id')\n"
    resolved = _fqnames(source)
    assert "pandas.read_csv" in resolved
    # `df.merge(...)` must NOT resolve — df is a local, not an imported name.
    assert not any(name.endswith("merge") for name in resolved)
    assert "df.merge" in _skips_by_reason(source)[SkipReason.NOT_RESOLVABLE]


def test_bare_builtin_call_not_recorded() -> None:
    # print()/len() are not imported and not dotted -> ignored, not even recorded.
    source = "import pandas as pd\nprint(pd.read_csv('x'))\nlen([])\n"
    result = resolve_source(source)
    assert {c.fqname for c in result.resolved} == {"pandas.read_csv"}
    assert result.skipped == ()


def test_call_on_call_result_is_skipped() -> None:
    # The outer `.create()` has a Call as its receiver -> not resolvable.
    # (The inner `openai.OpenAI()` is itself a normal, resolvable call — that is fine.)
    source = "import openai\nopenai.OpenAI().chat.completions.create()\n"
    resolved = _fqnames(source)
    assert "openai.OpenAI" in resolved
    assert not any(name.endswith("create") for name in resolved)


def test_reassigned_import_is_dropped() -> None:
    source = "import pandas as pd\npd = make_fake()\npd.read_csv('x')\n"
    # `pd` is rebound -> its target is no longer trustworthy -> drop it entirely.
    assert _fqnames(source) == set()


def test_ambiguous_import_is_dropped() -> None:
    source = "import pandas as x\nimport requests as x\nx.get('u')\n"
    assert _fqnames(source) == set()


def test_function_def_shadow_is_dropped() -> None:
    # A local `def read_csv` shadows the import. There is NO Store Name node for a
    # def, so without explicit handling this slips through and the resolver would
    # wrongly target pandas.read_csv on the user's own function (a false positive).
    source = (
        "from pandas import read_csv\n"
        "def read_csv(path):\n"
        "    return path\n"
        "read_csv('x')\n"
    )
    assert _fqnames(source) == set()


def test_async_function_def_shadow_is_dropped() -> None:
    source = (
        "from pandas import read_csv\n"
        "async def read_csv(path):\n"
        "    return path\n"
        "read_csv('x')\n"
    )
    assert _fqnames(source) == set()


def test_class_def_shadow_is_dropped() -> None:
    source = (
        "from pandas import read_csv\n"
        "class read_csv:\n"
        "    pass\n"
        "read_csv()\n"
    )
    assert _fqnames(source) == set()


def test_wildcard_bare_name_is_refused() -> None:
    source = "from pandas import *\nread_exel('x')\n"
    assert _fqnames(source) == set()
    assert "read_exel" in _skips_by_reason(source)[SkipReason.WILDCARD]


def test_stdlib_is_skipped() -> None:
    source = "import os\nos.getcwd()\n"
    assert _fqnames(source) == set()
    assert "os.getcwd" in _skips_by_reason(source)[SkipReason.STDLIB]


def test_not_installed_is_skipped() -> None:
    source = "import definitely_absent_pkg_zzz as z\nz.frob()\n"
    assert _fqnames(source) == set()
    assert "definitely_absent_pkg_zzz.frob" in _skips_by_reason(source)[SkipReason.NOT_INSTALLED]


def test_relative_import_call_is_skipped() -> None:
    source = "from . import helpers\nhelpers.do_thing()\n"
    assert _fqnames(source) == set()
    assert "helpers.do_thing" in _skips_by_reason(source)[SkipReason.RELATIVE_IMPORT]


# --------------------------------------------------------------------------- #
# Classification + error handling
# --------------------------------------------------------------------------- #
def test_classify_root() -> None:
    assert classify_root("os") is RootKind.STDLIB
    assert classify_root("pytest") is RootKind.THIRD_PARTY
    assert classify_root("definitely_absent_pkg_zzz") is RootKind.NOT_INSTALLED


def test_syntax_error_is_captured_not_raised() -> None:
    result = resolve_source("def oops(:\n")
    assert result.syntax_error is not None
    assert result.resolved == ()


def test_locations_are_reported() -> None:
    source = "import pandas as pd\n\npd.read_csv('x')\n"
    (call,) = resolve_source(source).resolved
    assert call.lineno == 3
    assert call.root_package == "pandas"
    assert call.attr_path == ("read_csv",)


# --------------------------------------------------------------------------- #
# resolve_file — an unreadable file is captured, NEVER raised (crash-safety).
# A single bad file in a real tree must not abort the whole run.
# --------------------------------------------------------------------------- #
def test_invalid_utf8_file_is_captured_not_raised(tmp_path: Path) -> None:
    bad = tmp_path / "bad_utf8.py"
    bad.write_bytes(b'x = "caf\xe9"\n')  # lone 0xE9 -> invalid UTF-8
    result = resolve_file(bad)
    assert result.read_error is not None
    assert "UnicodeDecodeError" in result.read_error
    assert result.syntax_error is None
    assert result.resolved == ()


def test_utf16_bom_file_is_captured_not_raised(tmp_path: Path) -> None:
    bad = tmp_path / "utf16.py"
    bad.write_bytes("import os\nos.getcwd()\n".encode("utf-16"))  # BOM + NUL bytes
    result = resolve_file(bad)
    assert result.read_error is not None
    assert "UnicodeDecodeError" in result.read_error
    assert result.resolved == ()


def test_missing_file_is_captured_not_raised(tmp_path: Path) -> None:
    # A path that does not exist (e.g. it vanished mid-run) -> OSError family, captured.
    result = resolve_file(tmp_path / "gone.py")
    assert result.read_error is not None
    assert result.resolved == ()


def test_arbitrary_read_error_is_captured_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The catch is exception-agnostic, not decode-only: a permission error is captured too.
    target = tmp_path / "locked.py"
    target.write_text("import os\n", encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> str:
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_text", boom)
    result = resolve_file(target)
    assert result.read_error is not None
    assert "PermissionError" in result.read_error
    assert result.resolved == ()


def test_null_byte_file_degrades_cleanly(tmp_path: Path) -> None:
    # A NUL byte is valid UTF-8 but not valid source -> handled as a syntax error
    # (captured), never a read_error and never a raise.
    f = tmp_path / "nul.py"
    f.write_bytes(b"x = 1\x00\ny = 2\n")
    result = resolve_file(f)
    assert result.read_error is None
    assert result.syntax_error is not None
    assert result.resolved == ()
