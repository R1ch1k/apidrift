"""apidrift — flag API calls that drifted from the installed dependency version.

apidrift is a deterministic, $0 CI guard. It checks whether the API calls in your
code actually *exist* in the version of each third-party package installed in the
current environment — the way LLM-generated code breaks: hallucinated names,
renamed functions, and keyword arguments removed in a later major version.

Wedge vs pyright/mypy: type-checkers verify *types against stubs*; apidrift verifies
*existence and version-validity against the live installed package*.

Design tenet #1 (non-negotiable): silence beats a false alarm. If a call cannot be
resolved with confidence, apidrift emits nothing.
"""

from __future__ import annotations

__version__ = "0.0.2"
__all__ = ["__version__"]
