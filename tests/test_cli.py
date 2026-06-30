"""CLI integration tests — exercise the real pipeline on a temp file and the fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apidrift.cli import collect_python_files, main

_FIXTURE = Path("examples/ai_generated.py")


def test_collect_files_dedupes_and_filters(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "c.py").write_text("", encoding="utf-8")

    found = collect_python_files([str(tmp_path), str(tmp_path / "a.py")])
    names = sorted(p.name for p in found)
    assert names == ["a.py", "c.py"]  # b.txt excluded, a.py not duplicated


def test_clean_file_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "clean.py"
    target.write_text("import pandas as pd\npd.read_csv('x')\n", encoding="utf-8")
    code = main([str(target)])
    out = capsys.readouterr().out
    assert code == 0
    assert "0 problems" in out


def test_drift_file_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "drift.py"
    target.write_text("import pandas as pd\npd.read_exel('x')\n", encoding="utf-8")
    code = main([str(target)])
    out = capsys.readouterr().out
    assert code == 1
    assert "pandas.read_exel not found" in out
    assert "did you mean" in out
    assert "1 problem " in out


def test_no_files_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["does_not_exist_zzz.py"])
    assert code == 2
    assert "no Python files" in capsys.readouterr().err


def test_deprecation_notice_does_not_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A deprecation NOTICE is shown but must NOT change the exit code (still 0).
    # legacy_lib is importable via tests/conftest.py's sys.path insertion.
    target = tmp_path / "uses_legacy.py"
    target.write_text("import legacy_lib\nlegacy_lib.deprecated_fn(1)\n", encoding="utf-8")
    code = main([str(target)])
    out = capsys.readouterr().out
    assert code == 0  # notice-only run passes CI
    assert "NOTICE" in out
    assert "legacy_lib.deprecated_fn is deprecated" in out
    assert "1 deprecation notice" in out
    assert "0 problems" not in out


def test_json_output_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "drift.py"
    target.write_text("import pandas as pd\npd.read_exel('x')\n", encoding="utf-8")
    code = main([str(target), "--json", "--no-cache"])
    payload = json.loads(capsys.readouterr().out)  # must parse as a single JSON doc
    assert code == payload["summary"]["exit_code"] == 1
    (finding,) = payload["findings"]
    assert finding["check"] == "existence"
    assert finding["symbol"] == "pandas.read_exel"
    assert finding["suggestion"] == "pandas.read_excel"
    assert finding["package"] == "pandas"


def test_clear_cache_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APIDRIFT_CACHE_DIR", str(tmp_path / "cc"))
    code = main(["--clear-cache"])
    assert code == 0
    assert "cleared introspection cache" in capsys.readouterr().out


def test_unreadable_file_does_not_abort_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # One bad-encoding file must NOT abort the run. The good file is still scanned and its
    # finding reported; the exit code reflects the real drift, not the read failure.
    # (bad.py sorts before good.py, so the failure is hit FIRST.)
    (tmp_path / "good.py").write_text(
        "import pandas as pd\npd.read_exel('x')\n", encoding="utf-8"
    )
    (tmp_path / "bad.py").write_bytes(b'x = "caf\xe9"\n')  # invalid UTF-8
    code = main([str(tmp_path), "--no-cache"])
    out = capsys.readouterr().out
    assert code == 1
    assert "pandas.read_exel not found" in out


def test_deeply_nested_file_does_not_abort_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A pathologically deep file makes ast.parse raise MemoryError/RecursionError (not
    # SyntaxError). It must be skipped like any other bad file, not abort the run: the good
    # file is still scanned and its drift reported, and the exit reflects the real finding.
    # (deep.py sorts before good.py, so the bad parse is hit FIRST.)
    (tmp_path / "deep.py").write_text("-" * 50_000 + "1\n", encoding="utf-8")
    (tmp_path / "good.py").write_text(
        "import pandas as pd\npd.read_exel('x')\n", encoding="utf-8"
    )
    code = main([str(tmp_path), "--no-cache"])
    out = capsys.readouterr().out
    assert code == 1  # the real drift gates CI; the deep file did not crash the run
    assert "pandas.read_exel not found" in out


def test_verbose_surfaces_unreadable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bad.py").write_bytes("x = 1\n".encode("utf-16"))  # UTF-16 BOM
    code = main([str(tmp_path), "--no-cache", "--verbose"])
    out = capsys.readouterr().out
    assert code == 0  # no real findings; the unreadable file is a skip, not an error
    assert "bad.py" in out
    assert "unreadable" in out


def test_local_shadow_is_not_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A sibling pandas.py shadows the installed pandas at runtime. `local_only` is absent
    # in the INSTALLED pandas, but apidrift must not flag it against the wrong package.
    (tmp_path / "pandas.py").write_text("def local_only():\n    return 1\n", encoding="utf-8")
    app = tmp_path / "app.py"
    app.write_text("import pandas\npandas.local_only()\n", encoding="utf-8")
    code = main([str(app), "--no-cache"])
    out = capsys.readouterr().out
    assert code == 0
    assert "not found" not in out


@pytest.mark.skipif(not _FIXTURE.exists(), reason="run from repo root")
def test_fixture_demo_flags_four(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([str(_FIXTURE)])
    out = capsys.readouterr().out
    assert code == 1
    # 3 existence errors (Check A) + 1 unexpected-keyword error (Check B).
    assert "4 problems · checked against your installed versions" in out
    for symbol in ("read_exel", "concatenate", "TimeGrouper"):
        assert symbol in out
    assert "unexpected keyword 'mangle_dupe_cols'" in out
    # The deprecation proxy must stay silent under both checks (exists; signature raises).
    assert "ChatCompletion" not in out
