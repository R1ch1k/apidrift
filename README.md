# apidrift

> A CI guard that flags API calls which don't exist in the dependency version **actually installed right now** — the way LLM-generated code breaks.

LLM-generated Python confidently calls functions, methods, and keyword arguments
that were valid in some *older* version of a library and are gone in the one you
have pinned: hallucinated names, renamed functions, parameters removed in a later
major version. A type-checker reads stubs and can miss this; apidrift checks
**existence and version-validity against the live installed package**.

```text
$ apidrift examples/ai_generated.py
examples/ai_generated.py:16  ERROR  openai.ChatCompletion missing in openai 1.x
   └─ removed in openai>=1.0 · did you mean: openai.chat.completions?
examples/ai_generated.py:19  ERROR  pandas.read_csv() unexpected keyword 'mangle_dupe_cols'
   └─ removed in pandas>=2.0
examples/ai_generated.py:22  ERROR  pandas.read_exel not found in pandas 2.2.x
   └─ did you mean: read_excel?

3 problems · checked against your installed versions
```

That last line is the point: apidrift is checked against *your* environment, not a
stub set.

## The wedge vs pyright / mypy

pyright and mypy check **types against stubs**; apidrift checks **existence and
version-validity against the live installed package**. An LLM trained on pandas 1.x
that writes a removed keyword passes a stale-stub type check but breaks at runtime —
apidrift catches that; type-checkers don't. Package-*name* hallucination is already
owned by other tools; apidrift owns the **signature / parameter-level** slice for
your pinned versions.

## Design tenets

1. **Silence beats a false alarm.** If a call can't be resolved with confidence,
   apidrift emits nothing. Sound-by-default — precision over recall.
2. **Deterministic, $0.** No model or network calls anywhere in detection. Pure
   AST + introspection.
3. **DX is the moat.** Zero-config, CI-native, near-zero dependencies. "Did-you-mean"
   suggestions are first-class.

## Status

🚧 **Early build.** M0 (the resolution walking skeleton) is in place: apidrift parses
a file, builds the import table, and resolves third-party call targets. The existence
and keyword checks (M1 / M2) are next.

```bash
# Try the M0 resolver (resolution only — no checks yet):
python -m apidrift examples/ai_generated.py
python -m apidrift examples/ai_generated.py --verbose   # show what was skipped, and why
```

## Scope (v0)

**In:** Python; calls whose receiver traces to an imported module or imported name
(`mod.func(...)`, `mod.sub.Class(...)`, `from mod import func; func(...)`); checked
against third-party packages installed in the current environment.

**Out (honest limitations, not bugs):** method calls on inferred-type receivers
(`df.merge(...)` — that's a type-inference problem, mypy's job); cross-file flow
analysis; autofix; C-extension callables with no introspectable signature
(unverifiable → never flagged); stdlib and relative/own imports.

## License

MIT
