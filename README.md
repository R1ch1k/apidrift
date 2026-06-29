# apidrift

> A CI guard that flags API calls which don't exist in the dependency version **actually installed right now** — the way LLM-generated code breaks.

LLM-generated Python confidently calls functions, methods, and keyword arguments that
were valid in some *older* version of a library and are gone in the one you have pinned:
hallucinated names, renamed functions, parameters removed in a later major version. A
type-checker reads stubs and can miss this; apidrift checks **existence and
version-validity against the live installed package**. Deterministic, zero network, $0.

<!-- DEMO GIF: replace with examples/demo.gif once recorded -->

```text
$ apidrift examples/ai_generated.py
examples/ai_generated.py:17   ERROR  pandas.read_exel not found in pandas 2.3.3
   └─ did you mean: pandas.read_excel?
examples/ai_generated.py:20   ERROR  pandas.concatenate not found in pandas 2.3.3
   └─ did you mean: pandas.concat?
examples/ai_generated.py:23   ERROR  pandas.TimeGrouper not found in pandas 2.3.3
   └─ did you mean: pandas.Grouper?
examples/ai_generated.py:37   ERROR  pandas.read_csv() unexpected keyword 'mangle_dupe_cols'

4 problems · checked against your installed versions
```

Four findings, four distinct drift mechanisms — a typo, a cross-library confusion
(`concatenate` is numpy; pandas spells it `concat`), a symbol removed in a major
version, and a keyword argument (`mangle_dupe_cols`) removed in pandas 2.0. That last
line is the point: apidrift is checked against *your* environment, not a stub set.

## The wedge vs pyright / mypy

**pyright and mypy check types against stubs; apidrift checks existence and
version-validity against your actually-installed package.** An LLM trained on pandas 1.x
that writes a removed keyword passes a stale-stub type check but breaks at runtime —
apidrift catches that; type-checkers don't. Package-*name* hallucination is already owned
by other tools; apidrift owns the **signature / parameter-level** slice for your pinned
versions.

## Install

```bash
pip install apidrift          # PyPI release pending — see "from source" below
```

Until the first PyPI release, install from source (works once the repo is public):

```bash
pip install git+https://github.com/R1ch1k/apidrift
```

**Important:** apidrift checks calls against the versions of your dependencies that are
*actually installed*, so install it into — and run it from — the same environment as your
project's dependencies. Run in a clean env with nothing installed and apidrift will (by
design) find nothing to check.

## Quickstart

```bash
apidrift path/ file.py              # check files, directories, or globs
apidrift src                        # walk a package recursively
apidrift examples/ai_generated.py --verbose   # also show what was skipped, and why
```

Exit codes gate CI directly: `1` if any **error** (missing symbol or invalid keyword),
`0` if clean — or if only deprecation **notices** remain (they don't fail the build).

## The three checks

- **Check A — symbol existence.** Walks the resolved dotted path against the installed
  package and flags a segment genuinely absent from a cleanly introspectable parent, with
  a `difflib` "did you mean".
- **Check B — keyword-arg validity.** Flags a keyword the resolved callable's signature
  does not accept. Stays silent if the signature declares `**kwargs` (any keyword could be
  valid).
- **Check C — PEP 702 deprecation.** Flags a symbol carrying a `__deprecated__` marker
  (set by `warnings.deprecated` / `typing_extensions.deprecated`). This is a **NOTICE**,
  not an error — deprecated code still works, so it **does not gate CI** (exit 0).

## JSON output

`--json` emits a stable document instead of text (identical exit codes):

```bash
apidrift src --json
```

```json
{
  "schema_version": 1,
  "findings": [
    {
      "path": "examples/ai_generated.py",
      "line": 17,
      "column": 8,
      "severity": "ERROR",
      "check": "existence",
      "symbol": "pandas.read_exel",
      "message": "pandas.read_exel not found in pandas 2.3.3",
      "suggestion": "pandas.read_excel",
      "package": "pandas",
      "version": "2.3.3"
    }
  ],
  "summary": { "errors": 1, "notices": 0, "total": 1, "exit_code": 1 }
}
```

| field | meaning |
| --- | --- |
| `path` | source file, always forward-slashed (OS-independent; the text report keeps native separators) |
| `severity` | `ERROR` (gates CI) or `NOTICE` (deprecation; does not gate) |
| `check` | `existence`, `keyword`, or `deprecation` |
| `symbol` | the fully-qualified target the finding is about |
| `message` | the rendered human headline |
| `suggestion` | the "did you mean" replacement, or `null` |
| `package` / `version` | the resolved package and the installed version checked against |
| `summary.exit_code` | the process exit code — always matches the text run |

## pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/R1ch1k/apidrift
    rev: v0.0.1
    hooks:
      - id: apidrift
```

The hook uses `language: system` deliberately: apidrift must run in your project's
environment (where your dependencies live), so install it there — `pip install apidrift`.

## GitHub Action

Install your dependencies first, then run apidrift against the repo:

```yaml
# .github/workflows/apidrift.yml
name: apidrift
on: [push, pull_request]
jobs:
  apidrift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.x"
      - run: pip install -e .            # install YOUR deps so apidrift can introspect them
      - uses: R1ch1k/apidrift@v0.0.1
        with:
          paths: "src tests"
```

The step fails the build on errors and passes on a clean (or notices-only) run.

## What apidrift deliberately does *not* flag

Precision is the whole adoption story, so silence is a feature — and a linter that
documents its silence is one you can trust to gate CI. apidrift stays quiet, by design,
on:

- **`openai.ChatCompletion.create(...)`** — openai 1.x keeps `ChatCompletion` as an
  `APIRemovedInV1Proxy` whose `__getattr__` absorbs *any* attribute (so `.create`
  "exists") and raises only when the call is *executed*. The symbol genuinely exists, so
  an existence check correctly says nothing. That is a call-time/deprecation failure, a
  different and harder problem than existence — not a missing name.
- **A non-deprecated subclass of a deprecated class** — the deprecation check reads the
  symbol's *own* `__dict__`, never inherited attributes. A `__deprecated__` marker on a
  base class does not make a live subclass that merely inherits from it look deprecated.
- **Anything unverifiable** — if a package fails to import, exposes a C-extension callable
  with no introspectable signature, is a dynamic `__getattr__` module, or the called
  function declares `**kwargs`, apidrift emits nothing. It never guesses.

apidrift would always rather miss a real drift than raise a false alarm on code that is
genuinely fine. A linter that cries wolf gets uninstalled.

## Scope (v0)

**In:** Python; calls whose receiver traces to an imported module or imported name
(`mod.func(...)`, `mod.sub.Class(...)`, `from mod import func; func(...)`); checked
against third-party packages installed in the current environment.

**Out (honest limitations, not bugs):**

- Method calls on inferred-type receivers (`df.merge(...)` where `df` is a local) — that
  needs type inference, which is mypy's job.
- Cross-file flow analysis; autofix (suggestions only).
- C-extension callables with no introspectable signature → unverifiable, never flagged.
- stdlib (rarely LLM-hallucinated) and relative / first-party imports (no "installed
  version" to check against).

## How it works

1. **Resolve** (`resolver.py`) — AST → import table → fully-qualified call targets rooted
   at an installed third-party package. Sound-by-default: reassigned/ambiguous/shadowed
   names are dropped, wildcard-origin bare names are refused, method-on-local receivers
   are skipped.
2. **Introspect** (`introspect.py`) — import the package and walk the path into a
   serializable record (existence / signature / `__deprecated__`). Every failure mode
   degrades to "unverifiable".
3. **Check** (`checks.py`) — pure logic over the record: existence, keyword validity,
   deprecation.
4. **Cache** (`cache.py`) — records are cached to disk keyed by `(package, version)`, so
   repeat runs skip the import entirely. A version bump misses and re-introspects. Escape
   hatches: `--no-cache`, `--clear-cache`.

## Design tenets

1. **Silence beats a false alarm.** If a call can't be resolved with confidence, apidrift
   emits nothing. Sound-by-default — precision over recall.
2. **Deterministic, $0.** No model or network calls anywhere in detection. Pure AST +
   introspection.
3. **DX is the moat.** Zero-config, CI-native, near-zero dependencies. "Did-you-mean"
   suggestions are first-class.

## Validation

apidrift is tested with hand-written good/bad fixture pairs per seed library (openai,
pandas, requests, …) and a soundness-weighted suite that asserts it stays *silent* on the
ambiguous, `**kwargs`, C-extension, wildcard-import, dynamic-`__getattr__`, and
deprecation-proxy cases.

On top of that it is **validated against 30 real version-drift cases curated from the
pandas and scikit-learn release notes** (removed/renamed symbols and removed keyword
arguments), covering the same drift classes catalogued by benchmarks such as GitChameleon,
VersiCode, and CodeUpdateArena. Each case asserts *both* directions: the drifted call is
flagged, **and** its modern replacement stays silent. The set is pinned to the versions it
was verified against (`pandas==2.3.3`, `scikit-learn==1.8.0`); install them with
`pip install -e .[bench]` and run `pytest tests/test_benchmark.py` to reproduce the count.
Candidates that could not be flagged soundly (e.g. pydantic's `**kwargs`-accepting `Field`)
were dropped rather than counted — the number is the empirically passing total, not an
aspiration.

## License

MIT
