"""Command-line entry point.

M0 surface: resolve and print third-party call targets. There are no checks yet —
this milestone exists to prove resolution is correct before any existence/kwargs
logic is layered on. Output formatting is intentionally minimal and will move to
``report.py`` at M3.
"""

from __future__ import annotations

import argparse
import glob
import sys
from collections.abc import Sequence
from pathlib import Path

from apidrift import __version__
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
            if candidate.suffix == ".py" and candidate not in seen:
                seen.add(candidate)
                files.append(candidate)

    return files


def _print_file(resolution: FileResolution, *, verbose: bool) -> int:
    """Print one file's resolved targets; return the count of resolved calls."""
    if resolution.syntax_error is not None:
        print(f"{resolution.path}: skipped (syntax error: {resolution.syntax_error.msg})")
        return 0

    for call in sorted(resolution.resolved, key=lambda c: (c.lineno, c.col_offset)):
        print(f"{resolution.path}:{call.lineno}   {call.fqname}")

    if verbose:
        for skip in sorted(resolution.skipped, key=lambda c: (c.lineno, c.col_offset)):
            print(
                f"{resolution.path}:{skip.lineno}   - skipped {skip.display}  "
                f"({skip.reason.value})"
            )
        for module in resolution.import_table.wildcard_modules:
            print(f"{resolution.path}: note - wildcard import 'from {module} import *'")

    return len(resolution.resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apidrift",
        description=(
            "Flag API calls that drifted from the installed dependency version. "
            "(M0: resolution only — prints the call targets apidrift can resolve.)"
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
        help="Show calls that were skipped, and why (trust-building).",
    )
    parser.add_argument("--version", action="version", version=f"apidrift {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    files = collect_python_files(args.paths)
    if not files:
        print("apidrift: no Python files found in the given paths", file=sys.stderr)
        return 2

    total_resolved = 0
    for path in files:
        total_resolved += _print_file(resolve_file(path), verbose=args.verbose)

    print()
    print(f"{total_resolved} resolved call target(s) across {len(files)} file(s)")
    print("M0 walking skeleton - resolution only, no checks yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
