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
examples/ai_generated.py:37   ERROR  pandas.read_csv() unexpected keyword 'mangle_dupe_cols'

4 problems · checked against your installed versions
```

Four findings, four distinct drift mechanisms — a typo, a cross-library confusion
(`concatenate` is numpy; pandas spells it `concat`), a symbol removed in a major
version, and a keyword argument (`mangle_dupe_cols`) removed in pandas 2.0. That last
line is the point: apidrift is checked against *your* environment, not a stub set.

### What apidrift deliberately does *not* flag

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
- **Anything unverifiable** — if a package fails to import, exposes a C-extension
  callable with no introspectable signature, is a dynamic `__getattr__` module, or the
  called function declares `**kwargs`, apidrift emits nothing. It never guesses.

apidrift would always rather miss a real drift than raise a false alarm on code that is
genuinely fine. A linter that cries wolf gets uninstalled.

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

🚧 **Early build.** All three v0 checks are live, run against the packages installed in
your environment:

- **Check A — symbol existence.** Walks the resolved dotted path; flags a segment that is
  genuinely absent from a cleanly introspectable parent, with a `difflib` "did-you-mean".
- **Check B — keyword-arg validity.** Flags a keyword the resolved callable's signature
  does not accept. Stays silent if the signature declares `**kwargs`.
- **Check C — PEP 702 deprecation.** Flags a symbol carrying a `__deprecated__` marker
  (set by `warnings.deprecated` / `typing_extensions.deprecated`). This is a **NOTICE**,
  not an error: deprecated code still works, so it **does not gate CI** (exit 0).

Errors (Checks A and B) exit `1`; a clean run or notices-only run exits `0` — one line
gates CI.

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
