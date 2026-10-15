"""The connection probe is bounded: past the deadline no probe process survives."""
import os
import sqlite3
import sys
import textwrap
import threading
import time

import pytest

from apps.datasources import probe as probe_mod
from apps.datasources import testing
from apps.datasources.models import DataSource
from apps.datasources.probe import ProbeBusy, run_probe

HANG = textwrap.dedent(
    """
    import os, subprocess, sys, time
    sys.stdin.read()
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    with open(os.environ["PROBE_PIDFILE"], "a") as fh:
        fh.write(f"{os.getpid()} {grandchild.pid}\\n")
    time.sleep(600)
    """
)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def _pids(path) -> list[int]:
    return [int(p) for line in path.read_text().splitlines() for p in line.split()]


@pytest.fixture
def script(tmp_path, monkeypatch):
    def _install(body: str):
        path = tmp_path / "child.py"
        path.write_text(body)
        monkeypatch.setattr(probe_mod, "_CHILD", path)
        return path

    return _install


@pytest.fixture
def pidfile(tmp_path, monkeypatch):
    path = tmp_path / "pids"
    path.write_text("")
    monkeypatch.setenv("PROBE_PIDFILE", str(path))
    return path


def test_real_child_runs_the_driver_path(tmp_path):
    db = tmp_path / "x.db"
    sqlite3.connect(db).close()
    result = run_probe("sqlite", {"path": str(db)}, timeout=10)
    assert result.ok and result.detail == "Connected successfully.", result.detail
    assert not result.timed_out


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_no_probe_survives_past_the_deadline(script, pidfile):
    script(HANG)
    started = time.monotonic()
    result = run_probe("postgres", {"host": "h"}, timeout=1.0)
    assert result.timed_out and not result.ok
    # Deadline plus the reviewed kill/reap grace, and the cleanup is reported.
    assert time.monotonic() - started < 1.0 + probe_mod.KILL_GRACE_SECONDS + 1.0
    assert result.cleanup_seconds <= probe_mod.KILL_GRACE_SECONDS + 0.5
    pids = _pids(pidfile)
    assert len(pids) == 2
    assert not any(_alive(pid) for pid in pids), "probe or its descendant survived the deadline"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_repeated_timeouts_do_not_pile_up(script, pidfile, monkeypatch):
    script(HANG)
    monkeypatch.setattr(testing, "TIMEOUT_SECONDS", 1)
    ds = DataSource(name="wh", type="postgres", config={"host": "h"}, credentials={"user": "u", "password": "s3cret"})
    threads_before = threading.active_count()
    for _ in range(3):
        ok, detail = testing.test_datasource(ds)
        assert not ok and "Timed out after 1s" in detail
    pids = _pids(pidfile)
    assert len(pids) == 6
    assert not any(_alive(pid) for pid in pids)
    assert threading.active_count() <= threads_before
    assert not probe_mod._active_binding and not probe_mod._active_scope


def test_credentials_travel_only_on_stdin_and_are_scrubbed(script, tmp_path):
    dump = tmp_path / "dump"
    script(textwrap.dedent(
        f"""
        import json, os, sys
        request = json.loads(sys.stdin.read())
        open({str(dump)!r}, "w").write(json.dumps([sys.argv, dict(os.environ)]))
        print("driver noise on stdout")
        info = request["info"]
        sys.__stdout__.write(json.dumps({{"ok": False, "detail": "login failed for " + info["user"] + "/" + info["password"]}}) + "\\n")
        """
    ))
    ds = DataSource(name="wh", type="postgres", config={"host": "h"}, credentials={"user": "svc-user-x", "password": "s3cret-pw"})
    ok, detail = testing.test_datasource(ds)
    assert not ok and "s3cret-pw" not in detail and "login failed" in detail
    seen = dump.read_text()
    assert "s3cret-pw" not in seen and "svc-user-x" not in seen


def test_oversized_and_garbage_output_are_bounded(script):
    script("import sys\nsys.stdin.read()\nsys.__stdout__.write('x' * 500000)\n")
    result = run_probe("postgres", {}, timeout=5)
    assert not result.ok and "oversized" in result.detail
    script("import sys\nsys.stdin.read()\n")
    result = run_probe("postgres", {}, timeout=5)
    assert not result.ok and "without an answer" in result.detail


def test_admission_limits_probes_per_binding(script, pidfile, monkeypatch):
    script(HANG)
    monkeypatch.setattr(testing, "TIMEOUT_SECONDS", 3)
    monkeypatch.setattr(probe_mod, "ADMISSION_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(testing, "admission", lambda k, s: probe_mod.admission(k, s, wait=0.05))
    ds = DataSource(name="wh", type="postgres", config={"host": "h"}, credentials={"user": "u", "password": "p"})
    threads = [threading.Thread(target=testing.test_datasource, args=(ds,)) for _ in range(probe_mod.MAX_PER_BINDING)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5
    while sum(probe_mod._active_binding.values()) < probe_mod.MAX_PER_BINDING and time.monotonic() < deadline:
        time.sleep(0.02)
    with pytest.raises(ProbeBusy):
        testing.test_datasource(ds)
    for t in threads:
        t.join(10)
    assert not probe_mod._active_binding
    # Another binding in the same studio is not blocked by this one (only the scope cap applies).
    assert len(_pids(pidfile)) == 2 * probe_mod.MAX_PER_BINDING


@pytest.mark.django_db
def test_busy_is_not_stored_as_check_evidence(studio_tree, monkeypatch):
    ds = DataSource.objects.create(
        studio=studio_tree, name="wh", type="postgres", config={"host": "h"},
        credentials={"user": "u", "password": "p"},
    )

    def busy(_source):
        raise ProbeBusy("Another connection test for this source is still running.")

    monkeypatch.setattr(testing, "test_datasource", busy)
    ok, detail = testing.check_binding(ds, studio_tree)
    assert not ok and "still running" in detail
    ds.refresh_from_db()
    assert ds.last_check_ok is None and ds.last_check_at is None
    from apps.datasources.models import UnblockRequest

    assert not UnblockRequest.objects.exists()
    ok, detail, stale = testing.check_draft(
        ds, type("S", (), {"binding": ds, "declaration": None})(), lambda: type("S", (), {"binding": ds, "declaration": None})(),
        expected_revision=testing.check_revision(ds),
    )
    assert not ok and not stale and "still running" in detail
