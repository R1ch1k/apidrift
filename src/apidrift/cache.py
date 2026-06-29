"""Persistent per-(package, version) introspection cache.

Stores :class:`IntrospectionRecord` data keyed by ``(environment, package, version,
fqname)`` under a platform-appropriate cache directory (stdlib only — no extra
dependency). Two things make the cache *sound*, not just fast:

* **Keying.** The installed version is in the key (a version bump misses and
  re-introspects), and so is an *environment salt* — the interpreter, OS, and
  architecture — because a record from one environment is not valid in another.
* **Validation on read.** Every entry is re-validated through
  :func:`~apidrift.introspect.record_from_dict`; a corrupt, partial, poisoned, or
  forward-incompatible entry is treated as a miss, never trusted. A bad cache can slow a
  run down (re-introspection) but can never change a verdict.

Writes are a pure optimization and fail safe: an unwritable or blocked cache directory
degrades to no-write (recorded in :attr:`IntrospectionCache.write_error`) and never
crashes a scan.

Only definitive verdicts (``resolved`` / ``absent``) are cached. ``unverifiable`` is
never persisted — it is often transient (a temporarily broken environment), and not
caching it means a fixed environment is re-checked on the next run rather than staying
silent forever.

Records are loaded per file lazily, held in memory for the run, and flushed once at the
end, so a run touches each cache file at most once for read and once for write.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path

from apidrift.introspect import (
    ABSENT,
    RESOLVED,
    IntrospectionRecord,
    record_from_dict,
    record_to_dict,
)

_FORMAT = "v1"  # cache layout version; bump to invalidate everything on a format change

_Table = dict[str, object]


def _environment_salt() -> str:
    """A key namespace for the *running* interpreter + platform.

    A record introspected under one interpreter/OS/architecture is not valid for another
    (different builtins, C-extension shapes, even namespace-package layouts), so it must
    never be served across them. Folding this into the cache path makes a foreign-env
    record simply miss rather than mislead — bare ``(package, version)`` keys are unsafe.
    """
    parts = (
        platform.python_implementation(),
        platform.python_version(),
        sys.platform,
        platform.machine() or "unknown",
    )
    return _slug("-".join(parts))


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
        self._salt = _environment_salt()  # captured per instance (and so monkeypatchable)
        self._tables: dict[tuple[str, str], _Table] = {}
        self._dirty: set[tuple[str, str]] = set()
        #: Set by :meth:`flush` when the cache could not be written (e.g. the cache dir is
        #: blocked or points at a file). The scan still completes; the cache just degrades
        #: to no-write. Surfaced under ``--verbose`` and otherwise silent.
        self.write_error: str | None = None

    def get(self, package: str, version: str, fqname: str) -> IntrospectionRecord | None:
        """The validated record for a key, or ``None`` (a miss) on absence OR irregularity.

        Every entry is re-validated through :func:`record_from_dict`, so a corrupt,
        partial, poisoned, or forward-incompatible entry is indistinguishable from a miss:
        it is re-introspected rather than trusted. A bad cache can never produce a verdict.
        """
        return record_from_dict(self._table(package, version).get(fqname))

    def put(self, package: str, version: str, fqname: str, record: IntrospectionRecord) -> None:
        # Only definitive verdicts are worth persisting (see module docstring).
        if record.status not in (RESOLVED, ABSENT):
            return
        self._table(package, version)[fqname] = record_to_dict(record)
        self._dirty.add((package, version))

    def flush(self) -> None:
        """Write back every modified table (atomically per file), failing safe.

        Persisting the cache is a pure optimization: if the cache directory is unwritable
        or blocked (e.g. ``APIDRIFT_CACHE_DIR`` points at an existing file), the write is
        abandoned and recorded in :attr:`write_error` — the scan's result is unaffected.
        A cache write must never crash a run.
        """
        try:
            for key in self._dirty:
                path = self._path(*key)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(json.dumps(self._tables[key]), encoding="utf-8")
                tmp.replace(path)
        except Exception as exc:  # deliberate fail-safe: degrade to no-write, never raise
            self.write_error = f"{type(exc).__name__}: {exc}"
        finally:
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
        return self._root / _FORMAT / self._salt / _slug(package) / f"{_slug(version)}.json"


def _slug(text: str) -> str:
    """Filesystem-safe form of a package/version string."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in text) or "_"


def _read(path: Path) -> _Table:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing, unreadable, or corrupt cache file -> treat as empty (fail-safe)
    return data if isinstance(data, dict) else {}
