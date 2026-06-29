"""Report rendering tests."""

from __future__ import annotations

from apidrift.checks import Severity, Violation
from apidrift.report import format_violation, render_report, summary_line


def _existence(**overrides: object) -> Violation:
    base: dict[str, object] = {
        "check": "existence",
        "severity": Severity.ERROR,
        "lineno": 11,
        "col_offset": 0,
        "symbol": "pandas.read_exel",
        "token": "read_exel",
        "package": "pandas",
        "version": "2.3.3",
        "suggestions": ("read_excel",),
    }
    base.update(overrides)
    return Violation(**base)  # type: ignore[arg-type]


def _keyword(**overrides: object) -> Violation:
    base: dict[str, object] = {
        "check": "keyword",
        "severity": Severity.ERROR,
        "lineno": 7,
        "col_offset": 0,
        "symbol": "pandas.read_csv",
        "token": "mangle_dupe_cols",
        "package": "pandas",
        "version": "2.3.3",
        "suggestions": (),
    }
    base.update(overrides)
    return Violation(**base)  # type: ignore[arg-type]


def test_existence_with_suggestion() -> None:
    lines = format_violation("f.py", _existence())
    assert lines[0] == "f.py:11   ERROR  pandas.read_exel not found in pandas 2.3.3"
    assert lines[1] == "   └─ did you mean: pandas.read_excel?"


def test_existence_without_suggestion() -> None:
    lines = format_violation("f.py", _existence(suggestions=()))
    assert len(lines) == 1
    assert "did you mean" not in lines[0]


def test_existence_without_version() -> None:
    lines = format_violation("f.py", _existence(version=None))
    assert lines[0] == "f.py:11   ERROR  pandas.read_exel not found in pandas"


def test_keyword_violation_format() -> None:
    lines = format_violation("f.py", _keyword())
    assert lines == ["f.py:7   ERROR  pandas.read_csv() unexpected keyword 'mangle_dupe_cols'"]


def test_keyword_violation_with_suggestion() -> None:
    lines = format_violation("f.py", _keyword(token="verbos", suggestions=("verbose",)))
    assert lines[1] == "   └─ did you mean: verbose?"


def test_deprecation_violation_format() -> None:
    violation = Violation(
        check="deprecation",
        severity=Severity.NOTICE,
        lineno=5,
        col_offset=0,
        symbol="legacy_lib.deprecated_fn",
        token="",
        package="legacy_lib",
        version="1.0",
        suggestions=(),
        note="use renamed_fn() instead",
    )
    lines = format_violation("f.py", violation)
    assert lines[0] == "f.py:5   NOTICE  legacy_lib.deprecated_fn is deprecated"
    assert lines[1] == "   └─ use renamed_fn() instead"


def test_summary_line_pluralization() -> None:
    assert summary_line(1) == "1 problem · checked against your installed versions"
    assert summary_line(3) == "3 problems · checked against your installed versions"
    assert summary_line(0) == "0 problems · checked against your installed versions"


def test_summary_line_with_notices() -> None:
    # A notice is not a "problem"; a notice-only run must not say "0 problems".
    assert summary_line(0, 1) == "1 deprecation notice · checked against your installed versions"
    assert summary_line(0, 2) == "2 deprecation notices · checked against your installed versions"
    assert (
        summary_line(2, 1)
        == "2 problems · 1 deprecation notice · checked against your installed versions"
    )


def test_render_report_orders_and_counts() -> None:
    v1 = _existence(lineno=20, symbol="pandas.concatenate")
    v2 = _existence(lineno=11, symbol="pandas.read_exel")
    report = render_report([("f.py", [v1, v2])])
    assert report.index("read_exel") < report.index("concatenate")
    assert report.endswith("2 problems · checked against your installed versions")


def test_render_report_empty_is_clean() -> None:
    report = render_report([("f.py", [])])
    assert report == "0 problems · checked against your installed versions"
