"""CLI integration tests — exercise the real pipeline on a temp file and the fixture."""

from __future__ import annotations

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
