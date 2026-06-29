"""Shared pytest setup.

Put ``tests/fixtures`` on ``sys.path`` so the small modules there are importable as
top-level names. apidrift's resolver then classifies them as installed third-party
packages (``find_spec`` succeeds, they are not stdlib), which lets the check tests run
against real, importable callables with controlled signatures and markers — without
shipping or installing a separate package.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))


@pytest.fixture(autouse=True, scope="session")
def _isolate_cache(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point the on-disk cache at a throwaway dir so tests never touch the real one."""
    os.environ["APIDRIFT_CACHE_DIR"] = str(tmp_path_factory.mktemp("apidrift-cache"))
    yield
