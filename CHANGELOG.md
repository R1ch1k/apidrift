# Changelog

Notable changes to apidrift. Versioning follows [Semantic Versioning](https://semver.org/).

## 0.0.2 — 2026-07-01

**Security & hardening release — upgrading from 0.0.1 is recommended.** The published 0.0.1
GitHub Action has a command-injection flaw that is fixed here, so anyone using the Action or the
pre-commit hook should move their pin to `v0.0.2`, and run `pip install -U apidrift`.

### Security

- **GitHub Action command injection (fixed).** In 0.0.1, `action.yml` interpolated
  `apidrift ${{ inputs.paths }}` directly into the shell step, so a workflow that fed untrusted
  data into `paths` could run arbitrary commands on the runner. The input is now passed through
  the environment (`APIDRIFT_PATHS`) and is never templated into the script. The README also
  recommends pinning the Action by commit SHA.
- **Isolated introspection worker with hardened startup.** apidrift imports your dependencies in
  order to check them; that now runs in a short-lived **subprocess worker** (0.0.1 introspected
  in-process) with a wall-clock timeout, a `BaseException` catch, and stdout/stderr suppression, so
  a dependency that crashes, hangs, exits, or prints on import can no longer take down the run or
  corrupt `--json`. The worker is launched from a freshly-created empty working directory with
  `PYTHONSAFEPATH=1` and `-S`, so a standard-library-shadowing file (e.g. a planted `json.py`) in
  the scanned directory cannot execute during the worker's startup. The threat model is documented
  in `SECURITY.md`.

### Fixed

- **A single bad file never aborts the run.** Pathologically deep input that makes the parser raise
  `RecursionError` / `MemoryError`, and non-UTF-8 or otherwise unreadable files, are now skipped
  rather than crashing the whole scan.
- **The introspection cache never raises a false alarm.** The on-disk cache validates every record,
  keys it by `(package, version)` plus interpreter/platform, and re-confirms — through live
  introspection — any cached record that would produce a finding before reporting it, so a stale or
  tampered cache entry cannot cry wolf. Escape hatches: `--no-cache`, `--clear-cache`.
- **Deterministic output for glob arguments** (results are sorted, matching directory walks).
- Additional resolver soundness fixes: ambiguous, reassigned, wildcard-origin, and
  locally-shadowed names are dropped; lying `__wrapped__` and synthetic signatures are ignored
  rather than trusted.

### Changed / tooling

- Continuous integration added (ruff + mypy on Python 3.10–3.13; full test suite + benchmark on
  3.13), with pinned dev and test-anchor dependencies so the verified environment reproduces on a
  clean runner.
- README rewritten to lead with the independently-verified zero-false-positive result; the
  editable-install limitation is documented in `SECURITY.md`.

## 0.0.1 — 2026-06-29

First public release: symbol-existence (Check A), keyword-argument validity (Check B), and
PEP 702 deprecation (Check C) checks against your actually-installed dependency versions;
`--json` output; an on-disk introspection cache; a pre-commit hook and a GitHub Action; and a
30-case version-drift benchmark curated from pandas and scikit-learn release notes.
