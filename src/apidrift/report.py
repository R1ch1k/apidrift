"""Diagnostic rendering — turns violations into human text or machine JSON.

Kept separate from the checks so the same ``Violation`` data drives both the default
text output and ``--json``. report.py owns *presentation* (phrasing, the JSON schema);
checks.py owns the structured data. Both formats are derived from the same violations,
so they never disagree — including the exit code.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from apidrift.checks import Severity, Violation

_BRANCH = "└─"  # "└─"
_DOT = "·"  # "·"

#: Bump when the --json structure changes incompatibly.
JSON_SCHEMA_VERSION = 1


def suggestion(violation: Violation) -> str | None:
    """The single actionable "did you mean" replacement, or ``None``.

    Existence suggestions are qualified with the parent (``pandas.read_excel``); keyword
    suggestions are the bare parameter name; deprecations have none.
    """
    if not violation.suggestions:
        return None
    top = violation.suggestions[0]
    if violation.check == "existence":
        parent = violation.package
        if "." in violation.symbol:
            parent = violation.symbol.rsplit(".", 1)[0]
        return f"{parent}.{top}"
    if violation.check == "keyword":
        return top
    return None


def _phrase(violation: Violation) -> tuple[str, str | None]:
    """Return ``(headline, detail)`` for a violation. ``detail`` may be ``None``."""
    where = violation.package
    if violation.version is not None:
        where = f"{violation.package} {violation.version}"

    if violation.check == "existence":
        hint = suggestion(violation)
        return f"{violation.symbol} not found in {where}", (
            f"did you mean: {hint}?" if hint else None
        )

    if violation.check == "keyword":
        hint = suggestion(violation)
        return f"{violation.symbol}() unexpected keyword '{violation.token}'", (
            f"did you mean: {hint}?" if hint else None
        )

    if violation.check == "deprecation":
        return f"{violation.symbol} is deprecated", violation.note

    # Defensive fallback for any future check kind.
    return violation.symbol, violation.note


def format_violation(path: str, violation: Violation) -> list[str]:
    """Render one violation as its headline line plus an optional detail line."""
    label = "ERROR" if violation.severity is Severity.ERROR else "NOTICE"
    headline, detail = _phrase(violation)
    lines = [f"{path}:{violation.lineno}   {label}  {headline}"]
    if detail is not None:
        lines.append(f"   {_BRANCH} {detail}")
    return lines


def summary_line(errors: int, notices: int = 0) -> str:
    """The pitch line: counts + the fact that it was checked against the live env.

    Errors and deprecation notices are counted separately — a notice is not a
    "problem" (it does not gate CI), so lumping them would misrepresent a passing run.
    """
    parts: list[str] = []
    if errors or not notices:
        parts.append(f"{errors} {'problem' if errors == 1 else 'problems'}")
    if notices:
        parts.append(f"{notices} deprecation {'notice' if notices == 1 else 'notices'}")
    parts.append("checked against your installed versions")
    return f" {_DOT} ".join(parts)


def _counts(per_file: Sequence[tuple[str, Sequence[Violation]]]) -> tuple[int, int]:
    errors = sum(1 for _, vs in per_file for v in vs if v.severity is Severity.ERROR)
    notices = sum(1 for _, vs in per_file for v in vs if v.severity is Severity.NOTICE)
    return errors, notices


def render_report(per_file: Sequence[tuple[str, Sequence[Violation]]]) -> str:
    """Render all violations (in source order per file) followed by the summary."""
    out: list[str] = []
    for path, violations in per_file:
        for violation in sorted(violations, key=lambda v: (v.lineno, v.col_offset)):
            out.extend(format_violation(path, violation))
    if out:
        out.append("")
    errors, notices = _counts(per_file)
    out.append(summary_line(errors, notices))
    return "\n".join(out)


def to_json_finding(path: str, violation: Violation) -> dict[str, object]:
    """One finding as a stable, machine-readable object."""
    headline, _ = _phrase(violation)
    return {
        "path": path,
        "line": violation.lineno,
        "column": violation.col_offset,
        "severity": "ERROR" if violation.severity is Severity.ERROR else "NOTICE",
        "check": violation.check,
        "symbol": violation.symbol,
        "message": headline,
        "suggestion": suggestion(violation),
        "package": violation.package,
        "version": violation.version,
    }


def render_json(per_file: Sequence[tuple[str, Sequence[Violation]]]) -> str:
    """Render findings + a summary as JSON (stable schema; see README)."""
    findings: list[dict[str, object]] = []
    for path, violations in per_file:
        for violation in sorted(violations, key=lambda v: (v.lineno, v.col_offset)):
            findings.append(to_json_finding(path, violation))
    errors, notices = _counts(per_file)
    payload: dict[str, object] = {
        "schema_version": JSON_SCHEMA_VERSION,
        "findings": findings,
        "summary": {
            "errors": errors,
            "notices": notices,
            "total": errors + notices,
            "exit_code": 1 if errors else 0,
        },
    }
    return json.dumps(payload, indent=2)
