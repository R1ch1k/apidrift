"""Command-line entry point.

M1 surface: resolve each file, run Check A (symbol existence) against the installed
packages, and print the diagnostics + the pitch summary. Exit 1 if any drift is
found, 0 if clean — so a single line gates CI.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from apidrift import __version__
from apidrift.checks import Severity, Violation, check_call
from apidrift.report import render_report
from apidrift.resolver import FileResolution, resolve_file

_GLOB_CHARS = "*?["


def collect_python_files(paths: Sequence[str]) -> list[Path]:
    """Expand the given paths into a de-duplicated, ordered list of ``.py`` files.

    A path may be a file, a directory (walked recursively), or a glob pattern.
    """
    files: list[Path] = []
    seen: set[Path] = set()

    for raw in paths:
        if any(ch in raw for ch in _GLOB_CHARS):
            candidates = [Path(match) for match in glob.glob(raw, recursive=True)]
        else:
            path = Path(raw)
            candidates = sorted(path.rglob("*.py")) if path.is_dir() else [path]

        for candidate in candidates:
            if candidate.suffix == ".py" and candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                files.append(candidate)

    return files


def _check(resolution: FileResolution) -> list[Violation]:
    violations: list[Violation] = []
    for call in resolution.resolved:
        violations.extend(check_call(call))
    return violations


def _configure_utf8() -> None:
    """Best-effort UTF-8 stdout/stderr so box-drawing diagnostics survive on Windows."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apidrift",
        description=(
            "Flag API calls that drifted from the installed dependency version "
            "(Check A: symbol existence)."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="Files, directories, or glob patterns to check.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also report calls skipped as unresolvable, and why (trust-building).",
    )
    parser.add_argument("--version", action="version", version=f"apidrift {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8()
    args = build_parser().parse_args(argv)

    files = collect_python_files(args.paths)
    if not files:
        print("apidrift: no Python files found in the given paths", file=sys.stderr)
        return 2

    per_file: list[tuple[str, Sequence[Violation]]] = []
    total_checked = 0
    total_skipped = 0
    verbose_lines: list[str] = []

    for path in files:
        resolution = resolve_file(path)
        if resolution.syntax_error is not None:
            if args.verbose:
                verbose_lines.append(
                    f"{resolution.path}: skipped file (syntax error: {resolution.syntax_error.msg})"
                )
            continue

        per_file.append((resolution.path, _check(resolution)))
        total_checked += len(resolution.resolved)
        total_skipped += len(resolution.skipped)

        if args.verbose:
            for skip in sorted(resolution.skipped, key=lambda s: (s.lineno, s.col_offset)):
                verbose_lines.append(
                    f"{resolution.path}:{skip.lineno}   - skipped {skip.display}  "
                    f"({skip.reason.value})"
                )

    print(render_report(per_file))

    if args.verbose:
        print()
        print(f"checked {total_checked} resolved target(s) against installed packages")
        if verbose_lines:
            print(f"{total_skipped} call(s) skipped as unresolvable:")
            for line in verbose_lines:
                print(f"  {line}")

    # Errors gate CI; notices (future deprecation check) do not, by default.
    total_errors = sum(
        1 for _, violations in per_file for v in violations if v.severity is Severity.ERROR
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
