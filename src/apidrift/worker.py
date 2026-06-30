"""Isolated, fail-safe introspection — the hard process boundary for the checks.

Importing a third-party package to introspect it is the one operation in apidrift that
runs *arbitrary user code*. In-process that is unbounded risk: a package can ``sys.exit()``
(a ``BaseException`` that ``except Exception`` cannot catch), hang forever on a slow
import, ``os._exit()``, or even segfault — none of which any in-process guard can
contain. It can also print to stdout/stderr on import, which would corrupt ``--json``.

So every import + introspection runs in a short-lived *subprocess worker*, one per root
package, with:

* a wall-clock timeout (:data:`IMPORT_TIMEOUT_SECONDS`) — a package that hangs is killed
  and treated as ``unverifiable`` (silent), never a crash;
* a ``BaseException`` catch inside the worker, so a ``sys.exit()`` on import degrades the
  one package to ``unverifiable`` instead of taking the run down;
* stdout and stderr routed to the null device, so an import-time ``print`` or warning can
  never leak into apidrift's output.

Whatever the worker does — crash, hang, spew — the parent observes only a clean result
file or a failure, and *any* failure becomes ``unverifiable``. The parent can never be
crashed, hung, or polluted by a package it introspects. The worker emits the same
serializable :class:`~apidrift.introspect.IntrospectionRecord` the in-process path would,
so ``checks.py`` stays pure and never knows where a record came from.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from apidrift.introspect import (
    UNVERIFIABLE,
    IntrospectionRecord,
    introspect_fqname,
    record_from_dict,
)

#: Wall-clock budget for importing + introspecting ONE root package. A package whose
#: import hangs (or is pathologically slow) past this is killed and treated as
#: unverifiable. Per package, not per run — a clean package never waits on a slow one.
#: 12s balances bounding the worst-case "looks hung" wait on a cold run against not timing
#: out a legitimately-slow import (8s was too tight — a ~8.4s import fell to silence). The
#: real fix (bounded-concurrency workers + a global time budget) is backlog, not a launch gate.
IMPORT_TIMEOUT_SECONDS = 12.0

_UNVERIFIABLE = IntrospectionRecord(status=UNVERIFIABLE)


# --------------------------------------------------------------------------- #
# Parent side — spawn one worker per root, fail safe to unverifiable
# --------------------------------------------------------------------------- #
def introspect_batch(
    requests: Sequence[tuple[str, str]],
    *,
    timeout: float | None = None,
) -> dict[tuple[str, str], IntrospectionRecord]:
    """Introspect ``(root_package, fqname)`` pairs in isolated subprocess workers.

    One worker per distinct root package, so each package is imported at most once per
    run and one package's crash or hang never costs another's results. Every requested
    pair is answered: anything a worker cannot deliver cleanly becomes an ``unverifiable``
    record, which the checks treat as silence. Never raises.
    """
    budget = IMPORT_TIMEOUT_SECONDS if timeout is None else timeout
    by_root: dict[str, list[str]] = {}
    for root, fqname in requests:
        by_root.setdefault(root, []).append(fqname)

    out: dict[tuple[str, str], IntrospectionRecord] = {}
    for root, fqnames in by_root.items():
        records = _run_worker(root, fqnames, budget)
        for fqname in fqnames:
            out[(root, fqname)] = records.get(fqname, _UNVERIFIABLE)
    return out


def _run_worker(root: str, fqnames: list[str], timeout: float) -> dict[str, IntrospectionRecord]:
    """Run one worker for ``root`` over ``fqnames``; ``{}`` (all unverifiable) on any failure.

    Creating the result temp file AND the empty spawn-cwd directory are *inside* the fail-safe
    too: a missing or unusable temp directory (a bad ``TMPDIR`` / ``tempfile.tempdir``) makes the
    ``mkstemp`` or ``mkdtemp`` call raise, which becomes unverifiable, never a crash before the
    guard. ``result_path`` / ``cwd_dir`` stay ``None`` until each creation succeeds so the
    ``finally`` cleanup only runs when there is something to remove.
    """
    result_path: Path | None = None
    cwd_dir: str | None = None
    try:
        result_fd, result_name = tempfile.mkstemp(prefix="apidrift-", suffix=".json")
        os.close(result_fd)  # the worker reopens it by path; we only needed a unique name
        result_path = Path(result_name)
        # A freshly-created EMPTY directory to be the worker's cwd. `-m` puts the cwd first on
        # the worker's sys.path and `-S` does NOT remove it, so a stdlib-shadowing file in the
        # directory apidrift happens to run from (e.g. a planted ``json.py`` in a scanned repo)
        # would be imported and execute during startup. Spawning in an empty dir means there is
        # nothing there to shadow. Safe: the request's sys_path and result_path are absolute, so
        # the worker's cwd is irrelevant to resolution.
        cwd_dir = tempfile.mkdtemp(prefix="apidrift-cwd-")
        request = json.dumps(
            {
                "sys_path": [entry for entry in sys.path if isinstance(entry, str)],
                "root": root,
                "fqnames": fqnames,
                "result_path": str(result_path),
            }
        )
        proc = subprocess.Popen(
            # Hardened startup: -S disables site / sitecustomize / .pth auto-run, and the empty
            # `cwd` (with PYTHONSAFEPATH in the env) keeps the current directory off sys.path[0],
            # so a stdlib-shadowing file in the scanned tree cannot be imported before the worker
            # pins sys.path from the request.
            [sys.executable, "-S", "-m", "apidrift.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,  # suppress any import-time stdout from the package
            stderr=subprocess.DEVNULL,  # ...and stderr (warnings) — never leak to the parent
            cwd=cwd_dir,
            env=_worker_env(),
        )
        try:
            proc.communicate(request.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()  # reap the killed child so no zombie/handle lingers
            return {}
        if proc.returncode != 0:
            return {}
        return _read_result(result_path, fqnames)
    except Exception:  # deliberate fail-safe: any temp-file/spawn/pipe/OS error => unverifiable
        return {}
    finally:
        if result_path is not None:
            with contextlib.suppress(OSError):
                result_path.unlink()
        if cwd_dir is not None:
            shutil.rmtree(cwd_dir, ignore_errors=True)


def _worker_env() -> dict[str, str]:
    """The child's environment, carrying ONLY the path needed to import apidrift itself.

    The worker is launched with ``-S`` (no ``site`` / ``sitecustomize`` / ``.pth`` auto-run),
    so it boots from a controlled path — just the directory that contains the ``apidrift``
    package, enough for ``python -S -m apidrift.worker`` to start — rather than inheriting the
    parent's whole ``PYTHONPATH``. The worker then pins ``sys.path`` exactly from the request
    for faithful user-package resolution, so it never relies on the ambient ``PYTHONPATH``.

    This closes the easiest pre-binding patch vector: a project ``sitecustomize`` that would
    otherwise run at interpreter startup, before the worker binds its serialization primitives.
    It is hardening, not a complete forgery defense — see ``SECURITY.md`` for the threat model.
    """
    import apidrift

    bootstrap = str(Path(apidrift.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = bootstrap
    # PYTHONSAFEPATH (3.11+) drops the automatic current-directory / script-dir entry from
    # sys.path[0] entirely, so even the empty spawn cwd cannot become an import vector; it is
    # ignored on 3.10, where the empty-cwd spawn is the cross-version guarantee. The bootstrap
    # PYTHONPATH above is a separate, explicit entry and is unaffected.
    env["PYTHONSAFEPATH"] = "1"
    return env


def _read_result(path: Path, fqnames: list[str]) -> dict[str, IntrospectionRecord]:
    """Parse the worker's result file; drop anything that does not validate."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, IntrospectionRecord] = {}
    for fqname in fqnames:
        record = record_from_dict(data.get(fqname))
        if record is not None:
            out[fqname] = record
    return out


# --------------------------------------------------------------------------- #
# Worker side — runs as `python -m apidrift.worker`, talks JSON over stdin + a file
# --------------------------------------------------------------------------- #
def _introspect_one(root: str, fqname: str) -> IntrospectionRecord:
    """Introspect one symbol, degrading *any* escape (incl. ``SystemExit``) to silence."""
    try:
        return introspect_fqname(root, fqname)
    except BaseException:  # SystemExit/KeyboardInterrupt included by design -> silence
        return _UNVERIFIABLE


def _record_payload(record: IntrospectionRecord) -> dict[str, object]:
    """Serialize a record by DIRECT field access — never ``dataclasses.asdict``.

    Mirrors :func:`~apidrift.introspect.record_to_dict` field-for-field, but reads the frozen
    record's own attributes so serialization does not route through a module-level helper a
    freshly-imported user package could have monkeypatched. Tuples are handed straight to
    ``json.dumps`` (which emits them as arrays, exactly as ``asdict`` would), so the wire
    shape stays in lockstep with :func:`~apidrift.introspect.record_from_dict`.
    """
    return {
        "status": record.status,
        "missing_index": record.missing_index,
        "missing_segment": record.missing_segment,
        "suggestions": record.suggestions,
        "has_signature": record.has_signature,
        "has_var_keyword": record.has_var_keyword,
        "acceptable_keywords": record.acceptable_keywords,
        "deprecated_message": record.deprecated_message,
    }


def _worker_main() -> int:
    """Read the request from stdin, introspect, write records to the result file.

    Every serialization + file-write primitive is bound to a LOCAL name up front, BEFORE any
    user package is imported. A package that monkeypatches ``json.dumps``,
    ``dataclasses.asdict``, ``os.write``, ``Path.write_text`` (etc.) on import therefore
    cannot reach the code that builds and flushes our result — it cannot forge a record. The
    result is written through the bound ``os`` primitives rather than ``Path.write_text``.
    """
    dumps = json.dumps
    os_open, os_write, os_close = os.open, os.write, os.close
    # O_BINARY (Windows only) writes the bytes verbatim — no newline translation, whatever
    # the payload; it does not exist on POSIX, where the write is already byte-faithful.
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)

    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        sys_path = request["sys_path"]
        root = request["root"]
        fqnames = request["fqnames"]
        result_path = request["result_path"]
    except Exception:
        return 1

    if isinstance(sys_path, list):
        # Pin the parent's import environment for faithful package resolution. apidrift
        # itself is already imported (in sys.modules), so replacing the path is safe.
        sys.path[:] = [entry for entry in sys_path if isinstance(entry, str)]

    # The one step that imports user code. Records are serialized by direct field access and
    # flushed through the primitives bound above, so import-time monkeypatching of the
    # serialization/write path cannot forge the result the parent reads back.
    payload = {fqname: _record_payload(_introspect_one(root, fqname)) for fqname in fqnames}
    try:
        data = dumps(payload).encode("utf-8")
        fd = os_open(result_path, write_flags, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os_write(fd, view) :]
        finally:
            os_close(fd)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    # os._exit, not sys.exit: skip atexit handlers a freshly-imported user package may
    # have registered (which could hang or error). The result file is already flushed.
    os._exit(_worker_main())
