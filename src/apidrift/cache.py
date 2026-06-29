"""Persistent per-(package, version) introspection cache.

Stores :class:`IntrospectionRecord` data keyed by ``(package, version, fqname)`` under
a platform-appropriate cache directory (stdlib only — no extra dependency). The version
is part of the key, so a version bump can never serve a record introspected against a
different installed version: it simply misses and re-introspects. That keying is the
whole soundness story of the cache.

Only definitive verdicts (``resolved`` / ``absent``) are cached. ``unverifiable`` is
never persisted — it is often transient (a temporarily broken environment), and not
caching it means a fixed environment is re-checked on the next run rather than staying
silent forever.

Records are loaded per file lazily, held in memory for the run, and flushed once at the
end, so a run touches each ``(package, version)`` file at most once for read and once
for write.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, fields
from pathlib import Path

from apidrift.introspect import IntrospectionRecord

_FORMAT = "v1"  # cache layout version; bump to invalidate everything on a format change

_Table = dict[str, dict[str, object]]
_FIELDS = {f.name for f in fields(IntrospectionRecord)}
_TUPLE_FIELDS = ("suggestions", "acceptable_keywords")


def default_cache_dir() -> Path:
    """Platform-appropriate cache root, overridable via ``APIDRIFT_CACHE_DIR``."""
    override = os.environ.get("APIDRIFT_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "apidrift"


class IntrospectionCache:
    """A lazily-loaded, write-back cache of introspection records."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else default_cache_dir()
        self._tables: dict[tuple[str, str], _Table] = {}
        self._dirty: set[tuple[str, str]] = set()

    def get(self, package: str, version: str, fqname: str) -> IntrospectionRecord | None:
        raw = self._table(package, version).get(fqname)
        return _deserialize(raw) if raw is not None else None

    def put(self, package: str, version: str, fqname: str, record: IntrospectionRecord) -> None:
        # Only definitive verdicts are worth persisting (see module docstring).
        if record.status not in ("resolved", "absent"):
            return
        self._table(package, version)[fqname] = asdict(record)
        self._dirty.add((package, version))

    def flush(self) -> None:
        """Write back every table modified this run (atomically per file)."""
        for key in self._dirty:
            path = self._path(*key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self._tables[key]), encoding="utf-8")
            tmp.replace(path)
        self._dirty.clear()

    def clear(self) -> None:
        """Delete the entire on-disk cache."""
        shutil.rmtree(self._root, ignore_errors=True)
        self._tables.clear()
        self._dirty.clear()

    # -- internals -- #
    def _table(self, package: str, version: str) -> _Table:
        key = (package, version)
        if key not in self._tables:
            self._tables[key] = _read(self._path(package, version))
        return self._tables[key]

    def _path(self, package: str, version: str) -> Path:
        return self._root / _FORMAT / _slug(package) / f"{_slug(version)}.json"


def _slug(text: str) -> str:
    """Filesystem-safe form of a package/version string."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in text) or "_"


def _read(path: Path) -> _Table:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing or corrupt cache file -> treat as empty (fail-safe)
    return data if isinstance(data, dict) else {}


def _deserialize(raw: dict[str, object]) -> IntrospectionRecord | None:
    """Rebuild a record from JSON, tolerating corrupt/forward-incompatible entries.

    Returns ``None`` on any mismatch so the caller falls back to a fresh introspection —
    a bad cache entry must never produce a wrong verdict.
    """
    payload = {key: raw[key] for key in _FIELDS if key in raw}
    for key in _TUPLE_FIELDS:
        value = payload.get(key)
        if isinstance(value, list):
            payload[key] = tuple(value)
    try:
        return IntrospectionRecord(**payload)  # type: ignore[arg-type]
    except TypeError:
        return None
