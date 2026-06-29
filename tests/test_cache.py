"""Persistent introspection cache tests.

The cache's contract is narrow and load-bearing: a hit must produce results identical
to a cold run, and a version change must never serve a stale-version record.
"""

from __future__ import annotations

from pathlib import Path

from apidrift import resolver
from apidrift.cache import IntrospectionCache
from apidrift.checks import check_call
from apidrift.introspect import IntrospectionRecord


def _one_call(source: str) -> resolver.ResolvedCall:
    (call,) = resolver.resolve_source(source).resolved
    return call


def test_version_change_busts_the_entry(tmp_path: Path) -> None:
    cache = IntrospectionCache(tmp_path / "c")
    record = IntrospectionRecord(
        status="absent", missing_index=1, missing_segment="foo", suggestions=("bar",)
    )
    cache.put("pkg", "1.0.0", "pkg.foo", record)
    cache.flush()

    fresh = IntrospectionCache(tmp_path / "c")
    assert fresh.get("pkg", "1.0.0", "pkg.foo") == record  # same version -> hit
    assert fresh.get("pkg", "2.0.0", "pkg.foo") is None  # version changed -> miss


def test_cache_hit_matches_cold_run(tmp_path: Path) -> None:
    source = "import pandas as pd\npd.read_exel('x')\n"
    cold = check_call(_one_call(source), None)  # no cache, live introspection

    cache = IntrospectionCache(tmp_path / "c")
    miss = check_call(_one_call(source), cache)  # populates the cache
    hit = check_call(_one_call(source), cache)  # served from the in-memory table
    cache.flush()
    disk = check_call(_one_call(source), IntrospectionCache(tmp_path / "c"))  # from disk

    assert cold == miss == hit == disk
    assert cold and cold[0].check == "existence"


def test_unverifiable_is_not_cached(tmp_path: Path) -> None:
    cache = IntrospectionCache(tmp_path / "c")
    cache.put("pkg", "1.0.0", "pkg.x", IntrospectionRecord(status="unverifiable"))
    cache.flush()
    assert IntrospectionCache(tmp_path / "c").get("pkg", "1.0.0", "pkg.x") is None


def test_corrupt_cache_entry_falls_back_to_miss(tmp_path: Path) -> None:
    cache = IntrospectionCache(tmp_path / "c")
    path = cache._path("pkg", "1.0.0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pkg.x": {"unexpected": 1}}', encoding="utf-8")  # missing 'status'
    assert IntrospectionCache(tmp_path / "c").get("pkg", "1.0.0", "pkg.x") is None


def test_clear_removes_the_cache(tmp_path: Path) -> None:
    root = tmp_path / "c"
    cache = IntrospectionCache(root)
    cache.put("pkg", "1.0.0", "pkg.x", IntrospectionRecord(status="resolved"))
    cache.flush()
    assert root.exists()
    cache.clear()
    assert not root.exists()
