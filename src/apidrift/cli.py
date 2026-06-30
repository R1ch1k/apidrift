"""Command-line entry point.

Resolve each file, run the checks (A: existence, B: keyword-arg, C: deprecation)
against the installed packages, and emit diagnostics — text by default, or a stable
JSON document with ``--json``. Exit 1 if any ERROR is found, 0 if clean or only NOTICEs
(deprecations) remain, so a single line gates CI. Repeat runs are sped up by an on-disk
per-(package, version) introspection cache.
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
from apidrift.cache import IntrospectionCache, default_cache_dir
from apidrift.checks import Severity, Violation, resolve_records, run_checks
from apidrift.report import render_json, render_report
from apidrift.resolver import FileResolution, resolve_file
from apidrift.worker import introspect_batch

_GLOB_CHARS = "*?["


def collect_python_files(paths: Sequence[str]) -> list[Path]:
    """Expand the given paths into a de-duplicated, ordered list of ``.py`` files.

    A path may be a file, a directory (walked recursively), or a glob pattern.
    """
    files: list[Path] = []
    seen: set[Path] = set()

    for raw in paths:
        if any(ch in raw for ch in _GLOB_CHARS):
            # sorted(): glob.glob returns filesystem order, which varies across machines —
            # sort it so a glob argument produces the same output order everywhere (directory
            # walks below are already sorted). Keeps the "deterministic" guarantee unqualified.
            candidates = [Path(match) for match in sorted(glob.glob(raw, recursive=True))]
        else:
            path = Path(raw)
            candidates = sorted(path.rglob("*.py")) if path.is_dir() else [path]

        for candidate in candidates:
            if candidate.suffix == ".py" and candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                files.append(candidate)

    return files


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
            "Flag API calls that drifted from the installed dependency version: "
            "missing symbols, invalid keyword args, and PEP 702 deprecations."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Files, directories, or glob patterns to check.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also report calls skipped as unresolvable, and why (text mode only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit findings as a JSON document instead of text (same exit codes).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write the on-disk introspection cache.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete the on-disk introspection cache and exit.",
    )
    parser.add_argument("--version", action="version", version=f"apidrift {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8()
    args = build_parser().parse_args(argv)

    if args.clear_cache:
        cache_dir = default_cache_dir()
        IntrospectionCache(cache_dir).clear()
        print(f"apidrift: cleared introspection cache at {cache_dir}")
        return 0

    if not args.paths:
        print("apidrift: no paths given (nothing to check)", file=sys.stderr)
        return 2

    files = collect_python_files(args.paths)
    if not files:
        print("apidrift: no Python files found in the given paths", file=sys.stderr)
        return 2

    cache = None if args.no_cache else IntrospectionCache()

    # Pass 1: resolve every file (capturing, never raising on a bad file).
    resolutions: list[FileResolution] = []
    total_skipped = 0
    verbose_lines: list[str] = []
    for path in files:
        resolution = resolve_file(path)
        if resolution.read_error is not None:
            # A file we cannot read/decode is skipped, never fatal — the run continues
            # and the good files are still scanned and reported.
            if args.verbose:
                verbose_lines.append(
                    f"{resolution.path}: skipped file (unreadable: {resolution.read_error})"
                )
            continue
        if resolution.syntax_error is not None:
            if args.verbose:
                verbose_lines.append(
                    f"{resolution.path}: skipped file (syntax error: {resolution.syntax_error.msg})"
                )
            continue
        resolutions.append(resolution)
        total_skipped += len(resolution.skipped)
        if args.verbose:
            for skip in sorted(resolution.skipped, key=lambda s: (s.lineno, s.col_offset)):
                verbose_lines.append(
                    f"{resolution.path}:{skip.lineno}   - skipped {skip.display}  "
                    f"({skip.reason.value})"
                )

    # Acquire every record up front: cache hits, then one isolated worker per remaining
    # root package (so no third-party code is ever imported in apidrift's own process).
    all_calls = [call for resolution in resolutions for call in resolution.resolved]
    records = resolve_records(all_calls, cache, introspect_batch)

    # Pass 2: run the checks from the acquired records (pure; no importing here).
    per_file: list[tuple[str, Sequence[Violation]]] = []
    total_checked = 0
    for resolution in resolutions:
        violations: list[Violation] = []
        for call in resolution.resolved:
            record = records.get((call.root_package, call.fqname))
            if record is not None:
                violations.extend(run_checks(call, record))
        per_file.append((resolution.path, violations))
        total_checked += len(resolution.resolved)

    if cache is not None:
        cache.flush()
        if args.verbose and cache.write_error is not None:
            verbose_lines.append(f"cache: not written ({cache.write_error})")

    if args.json:
        print(render_json(per_file))
    else:
        print(render_report(per_file))
        if args.verbose:
            print()
            print(f"checked {total_checked} resolved target(s) against installed packages")
            if verbose_lines:
                print(f"{total_skipped} call(s) skipped as unresolvable:")
                for line in verbose_lines:
                    print(f"  {line}")

    # Errors gate CI; deprecation notices do not.
    total_errors = sum(
        1 for _, violations in per_file for v in violations if v.severity is Severity.ERROR
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
