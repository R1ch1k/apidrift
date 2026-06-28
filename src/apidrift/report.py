"""Diagnostic rendering — turns violations into the human-facing text output.

Kept separate from the checks so the same ``Violation`` data can later feed a
``--json`` formatter (M3) without touching detection logic. The text format is the
README demo, so it is built to match the brief's mock.
"""

from __future__ import annotations

from collections.abc import Sequence

from apidrift.checks import Violation

_BRANCH = "└─"  # "└─"
_DOT = "·"  # "·"


def format_violation(path: str, violation: Violation) -> list[str]:
    """Render one violation as its headline line plus an optional detail line."""
    where = violation.package
    if violation.version is not None:
        where = f"{violation.package} {violation.version}"
    lines = [f"{path}:{violation.lineno}   ERROR  {violation.missing_path} not found in {where}"]
    if violation.suggestions:
        suggestion = violation.suggestions[0]
        lines.append(f"   {_BRANCH} did you mean: {violation.parent_fqname}.{suggestion}?")
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
