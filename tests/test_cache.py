"""Persistent introspection cache tests.

The cache's contract is narrow and load-bearing: a hit must produce results identical
to a cold run, and a version change must never serve a stale-version record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apidrift import resolver
from apidrift.cache import IntrospectionCache
from apidrift.checks import check_call
from apidrift.cli import main
from apidrift.introspect import IntrospectionRecord, package_version


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


# --------------------------------------------------------------------------- #
# Hardening: a corrupt or poisoned cache must never crash or manufacture a flag
# --------------------------------------------------------------------------- #
def _write_table(cache: IntrospectionCache, package: str, version: str, table: object) -> None:
    path = cache._path(package, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table), encoding="utf-8")


def test_poisoned_absent_record_for_real_symbol_does_not_flag(tmp_path: Path) -> None:
    # A cache entry claiming the REAL pandas.read_csv is "absent" must not flag it. The
    # entry is structurally invalid (missing_index = -1 is impossible for an absent
    # verdict), so it is rejected on read -> miss -> re-introspect -> present -> silent.
    version = package_version("pandas")
    assert version is not None
    cache = IntrospectionCache(tmp_path / "c")
    # missing_index = -1 is impossible for a genuine "absent" verdict.
    poison = {"status": "absent", "missing_index": -1, "missing_segment": "read_csv"}
    _write_table(cache, "pandas", version, {"pandas.read_csv": poison})
    violations = check_call(_one_call("import pandas as pd\npd.read_csv('x')\n"), cache)
    assert all(v.check != "existence" for v in violations)


def test_wellformed_but_mismatched_absent_record_does_not_flag(tmp_path: Path) -> None:
    # Even a structurally-valid absent record is not trusted blindly: if its missing
    # segment does not match the call's fqname, the check stays silent (defense in depth).
    version = package_version("pandas")
    assert version is not None
    cache = IntrospectionCache(tmp_path / "c")
    # claims segment[1] == "DIFFERENT", but the call's segment[1] is "read_csv"
    poison = {"status": "absent", "missing_index": 1, "missing_segment": "DIFFERENT"}
    _write_table(cache, "pandas", version, {"pandas.read_csv": poison})
    violations = check_call(_one_call("import pandas as pd\npd.read_csv('x')\n"), cache)
    assert all(v.check != "existence" for v in violations)


def test_malformed_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache = IntrospectionCache(tmp_path / "c")
    _write_table(cache, "pkg", "1.0.0", {"pkg.x": "not-a-record"})  # value is a bare string
    assert IntrospectionCache(tmp_path / "c").get("pkg", "1.0.0", "pkg.x") is None


def test_non_dict_table_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache = IntrospectionCache(tmp_path / "c")
    _write_table(cache, "pkg", "1.0.0", ["not", "a", "table"])  # whole file is a JSON array
    assert IntrospectionCache(tmp_path / "c").get("pkg", "1.0.0", "pkg.x") is None


def test_cross_environment_salt_isolates_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A record written under one interpreter/platform must not be served under another.
    root = tmp_path / "c"
    monkeypatch.setattr("apidrift.cache._environment_salt", lambda: "envA")
    writer = IntrospectionCache(root)
    writer.put("pkg", "1.0.0", "pkg.x", IntrospectionRecord(status="resolved"))
    writer.flush()

    monkeypatch.setattr("apidrift.cache._environment_salt", lambda: "envB")
    assert IntrospectionCache(root).get("pkg", "1.0.0", "pkg.x") is None  # foreign env -> miss

    monkeypatch.setattr("apidrift.cache._environment_salt", lambda: "envA")
    assert IntrospectionCache(root).get("pkg", "1.0.0", "pkg.x") is not None  # same env -> hit


def test_cache_dir_pointing_at_a_file_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # APIDRIFT_CACHE_DIR points at an existing FILE: reads and the final flush must
    # degrade to no-cache, and the scan must still complete and report the real drift.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setenv("APIDRIFT_CACHE_DIR", str(blocker))
    target = tmp_path / "drift.py"
    target.write_text("import pandas as pd\npd.read_exel('x')\n", encoding="utf-8")

    code = main([str(target)])
    out = capsys.readouterr().out
    assert code == 1  # the real drift is still found despite the unusable cache
    assert "pandas.read_exel not found" in out
