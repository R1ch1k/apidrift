"""Subprocess-isolation boundary tests — the cases an in-process import cannot survive.

A package can do anything when imported. The worker must turn every one of these into a
silent ``unverifiable`` and let the run complete:

* ``sys.exit()`` on import — a ``BaseException`` that ``except Exception`` would miss;
* a ``sys.exit()`` raised from a *submodule* import mid-chain;
* an import that hangs — only a wall-clock timeout + kill can contain it;
* an import that prints — its output must never reach apidrift's (``--json``) stdout.

These drive the real subprocess (no mocking of the boundary): each builds a throwaway
package on ``sys.path`` so the worker actually imports it.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from apidrift.cli import main
from apidrift.worker import introspect_batch


def _install_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, body: str) -> None:
    """Write ``name.py`` with ``body`` and put it on ``sys.path`` (parent → inherited by worker)."""
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))


# --------------------------------------------------------------------------- #
# Sanity — the worker really imports and introspects (not silently no-op'ing)
# --------------------------------------------------------------------------- #
def test_worker_introspects_a_real_package() -> None:
    records = introspect_batch([("pandas", "pandas.read_exel"), ("pandas", "pandas.read_csv")])
    assert records[("pandas", "pandas.read_exel")].status == "absent"
    assert records[("pandas", "pandas.read_csv")].status == "resolved"


# --------------------------------------------------------------------------- #
# The escapes — every one must become a silent `unverifiable`
# --------------------------------------------------------------------------- #
def test_sys_exit_on_import_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_package(tmp_path, monkeypatch, "sysexit_pkg_zzz", "import sys\nsys.exit(7)\n")
    records = introspect_batch([("sysexit_pkg_zzz", "sysexit_pkg_zzz.go")])
    # sys.exit is a BaseException; an in-process `except Exception` would let it through.
    assert records[("sysexit_pkg_zzz", "sysexit_pkg_zzz.go")].status == "unverifiable"


def test_submodule_sys_exit_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "exitsub_pkg_zzz"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub.py").write_text("import sys\nsys.exit(9)\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    records = introspect_batch([("exitsub_pkg_zzz", "exitsub_pkg_zzz.sub.func")])
    assert records[("exitsub_pkg_zzz", "exitsub_pkg_zzz.sub.func")].status == "unverifiable"


def test_hang_on_import_times_out_to_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_package(tmp_path, monkeypatch, "hang_pkg_zzz", "import time\ntime.sleep(60)\n")
    # A short timeout proves the parent kills the hung worker rather than waiting it out.
    records = introspect_batch([("hang_pkg_zzz", "hang_pkg_zzz.go")], timeout=1.0)
    assert records[("hang_pkg_zzz", "hang_pkg_zzz.go")].status == "unverifiable"


def test_one_bad_root_does_not_sink_a_clean_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crashing package costs only itself: a healthy package in the same batch is fine.
    _install_package(tmp_path, monkeypatch, "boom_root_zzz", "import sys\nsys.exit(1)\n")
    records = introspect_batch(
        [("boom_root_zzz", "boom_root_zzz.x"), ("pandas", "pandas.read_csv")],
        timeout=1.0,
    )
    assert records[("boom_root_zzz", "boom_root_zzz.x")].status == "unverifiable"
    assert records[("pandas", "pandas.read_csv")].status == "resolved"


# --------------------------------------------------------------------------- #
# Output isolation — an import-time print must not pollute --json
# --------------------------------------------------------------------------- #
def test_print_on_import_keeps_json_pure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_package(
        tmp_path,
        monkeypatch,
        "noisy_pkg_zzz",
        "import sys\n"
        "print('NOISE-stdout')\n"
        "print('NOISE-stderr', file=sys.stderr)\n"
        "def go():\n    return 1\n",  # a real symbol: the package imports cleanly, just noisily
    )
    app = tmp_path / "app.py"
    app.write_text("import noisy_pkg_zzz as n\nn.go()\n", encoding="utf-8")

    code = main([str(app), "--json", "--no-cache"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # must parse as ONE clean JSON document
    assert code == 0
    assert "NOISE" not in captured.out  # the package's stdout never reached ours
    assert payload["findings"] == []  # unverifiable -> nothing flagged, run completed


def test_sys_exit_package_does_not_abort_cli_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_package(tmp_path, monkeypatch, "exitcli_pkg_zzz", "import sys\nsys.exit(3)\n")
    app = tmp_path / "uses_exit.py"
    app.write_text("import exitcli_pkg_zzz as e\ne.frob()\n", encoding="utf-8")

    code = main([str(app), "--no-cache"])
    out = capsys.readouterr().out
    assert code == 0  # the package's sys.exit did not become apidrift's exit code
    assert "0 problems" in out


# --------------------------------------------------------------------------- #
# Forgery resistance — imported code cannot forge the worker's result by monkeypatching
# the serialization / file-write path on import.
# --------------------------------------------------------------------------- #
def test_import_time_write_text_patch_cannot_forge_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The package patches Path.write_text AND json.dumps on import to forge an "absent"
    # record for its own real symbol. The worker binds its serialization + write primitives
    # before importing, so the forgery cannot land: `go` resolves correctly (not flagged).
    forging = (
        "import json\n"
        "from pathlib import Path\n"
        "_forged = '{\"forger_pkg_zzz.go\": {\"status\": \"absent\", "
        "\"missing_index\": 1, \"missing_segment\": \"go\"}}'\n"
        "def _evil_write_text(self, *a, **k):\n"
        "    with open(self, 'w', encoding='utf-8') as f:\n"
        "        f.write(_forged)\n"
        "Path.write_text = _evil_write_text\n"
        "json.dumps = lambda *a, **k: _forged\n"
        "def go():\n    return 1\n"
    )
    _install_package(tmp_path, monkeypatch, "forger_pkg_zzz", forging)
    records = introspect_batch([("forger_pkg_zzz", "forger_pkg_zzz.go")])
    # If the forgery had landed, this would be "absent"; the real symbol resolves.
    assert records[("forger_pkg_zzz", "forger_pkg_zzz.go")].status == "resolved"


# --------------------------------------------------------------------------- #
# Temp-dir failure — an unusable temp directory must not crash before the fail-safe.
# --------------------------------------------------------------------------- #
def test_missing_tempdir_is_unverifiable_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tempfile.mkstemp runs INSIDE the worker fail-safe now: a missing temp dir degrades the
    # batch to unverifiable (silent) rather than raising before the guard.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "does-not-exist"))
    records = introspect_batch([("pandas", "pandas.read_csv")])
    assert records[("pandas", "pandas.read_csv")].status == "unverifiable"


def test_failing_mkdtemp_is_unverifiable_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    # Creating the empty spawn-cwd directory is inside the fail-safe too: if mkdtemp fails (a bad
    # TMPDIR / OS error), the batch degrades to unverifiable (silent), the same rule as mkstemp —
    # never a crash before the guard, and the already-created result file is still cleaned up.
    def boom(*args: object, **kwargs: object) -> str:
        raise OSError("no temp dir")

    monkeypatch.setattr(tempfile, "mkdtemp", boom)
    records = introspect_batch([("pandas", "pandas.read_csv")])
    assert records[("pandas", "pandas.read_csv")].status == "unverifiable"


# --------------------------------------------------------------------------- #
# Hardened startup — launch with -S + an empty cwd + a controlled bootstrap path, so nothing
# of the project's runs before the worker binds its primitives, without breaking real resolution.
# --------------------------------------------------------------------------- #
def test_worker_launched_with_dash_S_and_controlled_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import apidrift

    captured: dict[str, Any] = {}
    real_popen = subprocess.Popen

    def spy_popen(args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        captured["args"] = list(args)
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return real_popen(args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy_popen)
    records = introspect_batch([("pandas", "pandas.read_csv")])

    assert records[("pandas", "pandas.read_csv")].status == "resolved"  # real resolution intact
    assert "-S" in captured["args"]
    bootstrap = str(Path(apidrift.__file__).resolve().parent.parent)
    assert captured["env"]["PYTHONPATH"] == bootstrap  # controlled, not the full PYTHONPATH
    assert captured["env"]["PYTHONSAFEPATH"] == "1"  # cwd/script-dir dropped on 3.11+
    assert captured["cwd"] is not None  # spawned in an empty dir, never the scanned cwd


def test_dash_S_does_not_run_project_sitecustomize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project sitecustomize.py would, under default startup, run BEFORE the worker binds its
    # primitives (the easiest pre-binding patch vector). With -S it never runs — proven by the
    # absent marker — while the package on the pinned sys.path still introspects correctly.
    marker = tmp_path / "sitecustomize_ran.txt"
    (tmp_path / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8"
    )
    (tmp_path / "sitec_victim_zzz.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    records = introspect_batch([("sitec_victim_zzz", "sitec_victim_zzz.go")])
    assert records[("sitec_victim_zzz", "sitec_victim_zzz.go")].status == "resolved"
    assert not marker.exists()  # sitecustomize did NOT run before the worker


# --------------------------------------------------------------------------- #
# Scanned-dir module shadow — a file in the working directory that shadows a stdlib module
# the worker imports at startup must NOT execute. `python -m` puts the cwd first on sys.path
# and `-S` does not remove it; spawning the worker in a fresh empty cwd closes that vector.
# --------------------------------------------------------------------------- #
def test_scanned_dir_module_shadow_does_not_execute_in_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A planted json.py (a stdlib name the worker imports) writes a sentinel on import. With the
    # parent's cwd set to this directory, an un-hardened spawn would import it during the worker's
    # startup and run it. The worker is launched in a fresh empty cwd (+ PYTHONSAFEPATH), so the
    # shadow is never on its import path: the sentinel stays unwritten and the real package still
    # resolves.
    sentinel = tmp_path / "pwned.txt"
    (tmp_path / "json.py").write_text(f"open({str(sentinel)!r}, 'w').close()\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    records = introspect_batch([("pandas", "pandas.read_csv")])
    assert not sentinel.exists()  # the shadowing json.py was never imported by the worker
    assert records[("pandas", "pandas.read_csv")].status == "resolved"  # resolution intact
