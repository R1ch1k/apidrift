# apidrift

> A CI guard that flags API calls which don't exist in the dependency version **actually installed right now** — the way LLM-generated code breaks.

LLM-generated Python confidently calls functions, methods, and keyword arguments
that were valid in some *older* version of a library and are gone in the one you
have pinned: hallucinated names, renamed functions, parameters removed in a later
major version. A type-checker reads stubs and can miss this; apidrift checks
**existence and version-validity against the live installed package**.

```text
$ apidrift examples/ai_generated.py
examples/ai_generated.py:17   ERROR  pandas.read_exel not found in pandas 2.3.3
   └─ did you mean: pandas.read_excel?
examples/ai_generated.py:20   ERROR  pandas.concatenate not found in pandas 2.3.3
   └─ did you mean: pandas.concat?
examples/ai_generated.py:23   ERROR  pandas.TimeGrouper not found in pandas 2.3.3
   └─ did you mean: pandas.Grouper?

3 problems · checked against your installed versions
```

Three distinct drift mechanisms — a typo, a cross-library confusion (`concatenate` is
numpy; pandas spells it `concat`), and a symbol removed in a major version. That last
line is the point: apidrift is checked against *your* environment, not a stub set.

### What apidrift deliberately does *not* flag

Precision is the whole adoption story, so silence is a feature. In the same fixture,
`openai.ChatCompletion.create(...)` is **not** flagged: openai 1.x keeps
`ChatCompletion` as a proxy object that still exists on attribute access and only
raises when *called*. The symbol exists, so an existence check correctly says nothing —
catching a call-time deprecation is a different, harder check. apidrift would rather
miss that than risk a false alarm on a name that is genuinely present.

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

🚧 **Early build.** M1 is in place: apidrift resolves third-party call targets and runs
**Check A (symbol existence)** against the installed packages, with `difflib`
"did-you-mean" suggestions. Exit 1 on drift, 0 when clean — one line gates CI.
**Check B (keyword-arg validity)** is next.

```bash
python -m apidrift examples/ai_generated.py
python -m apidrift examples/ai_generated.py --verbose   # + checked / skipped counts
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
