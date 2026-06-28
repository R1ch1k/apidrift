"""Report rendering tests."""

from __future__ import annotations

from apidrift.checks import Violation
from apidrift.report import format_violation, render_report, summary_line


def _violation(**overrides: object) -> Violation:
    base: dict[str, object] = {
        "check": "existence",
        "lineno": 11,
        "col_offset": 0,
        "call_fqname": "pandas.read_exel",
        "missing_path": "pandas.read_exel",
        "missing_symbol": "read_exel",
        "parent_fqname": "pandas",
        "package": "pandas",
        "version": "2.3.3",
        "suggestions": ("read_excel",),
    }
    base.update(overrides)
    return Violation(**base)  # type: ignore[arg-type]


def test_format_violation_with_suggestion() -> None:
    lines = format_violation("f.py", _violation())
    assert lines[0] == "f.py:11   ERROR  pandas.read_exel not found in pandas 2.3.3"
    assert lines[1] == "   └─ did you mean: pandas.read_excel?"


def test_format_violation_without_suggestion() -> None:
    lines = format_violation("f.py", _violation(suggestions=()))
    assert len(lines) == 1
    assert "did you mean" not in lines[0]


def test_format_violation_without_version() -> None:
    lines = format_violation("f.py", _violation(version=None))
    assert lines[0] == "f.py:11   ERROR  pandas.read_exel not found in pandas"


def test_summary_line_pluralization() -> None:
    assert summary_line(1) == "1 problem · checked against your installed versions"
    assert summary_line(3) == "3 problems · checked against your installed versions"
    assert summary_line(0) == "0 problems · checked against your installed versions"


def test_render_report_orders_and_counts() -> None:
    v1 = _violation(lineno=20, missing_path="pandas.concatenate")
    v2 = _violation(lineno=11, missing_path="pandas.read_exel")
    report = render_report([("f.py", [v1, v2])])
    # Sorted by line: read_exel (11) before concatenate (20).
    assert report.index("read_exel") < report.index("concatenate")
    assert report.endswith("2 problems · checked against your installed versions")


def test_render_report_empty_is_clean() -> None:
    report = render_report([("f.py", [])])
    assert report == "0 problems · checked against your installed versions"
