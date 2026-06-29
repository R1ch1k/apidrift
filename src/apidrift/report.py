"""Diagnostic rendering — turns violations into the human-facing text output.

Kept separate from the checks so the same ``Violation`` data can later feed a
``--json`` formatter (M3) without touching detection logic. report.py owns the
*phrasing* of each check; checks.py owns the structured data.
"""

from __future__ import annotations

from collections.abc import Sequence

from apidrift.checks import Severity, Violation

_BRANCH = "└─"  # "└─"
_DOT = "·"  # "·"


def _phrase(violation: Violation) -> tuple[str, str | None]:
    """Return ``(headline, detail)`` for a violation. ``detail`` may be ``None``."""
    where = violation.package
    if violation.version is not None:
        where = f"{violation.package} {violation.version}"

    if violation.check == "existence":
        parent = violation.package
        if "." in violation.symbol:
            parent = violation.symbol.rsplit(".", 1)[0]
        detail = None
        if violation.suggestions:
            detail = f"did you mean: {parent}.{violation.suggestions[0]}?"
        return f"{violation.symbol} not found in {where}", detail

    if violation.check == "keyword":
        detail = None
        if violation.suggestions:
            detail = f"did you mean: {violation.suggestions[0]}?"
        return f"{violation.symbol}() unexpected keyword '{violation.token}'", detail

    # Fallback (deprecation, etc.): headline is the symbol, detail is the free-form note.
    return violation.symbol, violation.note


def format_violation(path: str, violation: Violation) -> list[str]:
    """Render one violation as its headline line plus an optional detail line."""
    label = "ERROR" if violation.severity is Severity.ERROR else "NOTICE"
    headline, detail = _phrase(violation)
    lines = [f"{path}:{violation.lineno}   {label}  {headline}"]
    if detail is not None:
        lines.append(f"   {_BRANCH} {detail}")
    return lines


def summary_line(total: int) -> str:
    """The pitch line: count + the fact that it was checked against the live env."""
    noun = "problem" if total == 1 else "problems"
    return f"{total} {noun} {_DOT} checked against your installed versions"


def render_report(per_file: Sequence[tuple[str, Sequence[Violation]]]) -> str:
    """Render all violations (in source order per file) followed by the summary."""
    out: list[str] = []
    total = 0
    for path, violations in per_file:
        for violation in sorted(violations, key=lambda v: (v.lineno, v.col_offset)):
            out.extend(format_violation(path, violation))
            total += 1
    if out:
        out.append("")
    out.append(summary_line(total))
    return "\n".join(out)
