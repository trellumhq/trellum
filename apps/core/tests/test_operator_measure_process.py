"""The real child process: a hung check is killed and the next one runs."""
import os
import signal
import sys
import time

import pytest
from django.utils import timezone

from apps.core import operator_observations as observations
from apps.core.models import OpsState
from apps.datasources.models import DataSource

pytestmark = pytest.mark.django_db(transaction=True)


def alive(pid):
    """Running, not merely unreaped. In a container whose init does not reap
    orphans (CI runs tests that way), a killed grandchild lingers as a zombie,
    which ``kill(pid, 0)`` still reports as present."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except ProcessLookupError:
        # The process can exit after /proc/<pid>/stat is opened but before
        # read() completes. Treat only ESRCH as disappearance; other I/O
        # failures should still make the test fail visibly.
        return False
    except FileNotFoundError:
        return False


@pytest.mark.parametrize(("stat", "expected"), [("42 (child) S 1 2", True), ("42 (child) Z 1 2", False)])
def test_alive_reads_live_and_zombie_proc_stat(monkeypatch, stat, expected):
    from io import StringIO

    monkeypatch.setattr(os, "kill", lambda pid, signal: None)
    monkeypatch.setattr("builtins.open", lambda path: StringIO(stat))

    assert alive(42) is expected


def test_alive_treats_process_disappearance_during_stat_read_as_dead(monkeypatch):
    class VanishingStat:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            raise ProcessLookupError("process disappeared during read")

    monkeypatch.setattr(os, "kill", lambda pid, signal: None)
    monkeypatch.setattr("builtins.open", lambda path: VanishingStat())

    assert alive(42) is False


def test_alive_does_not_hide_permission_errors_from_stat_read(monkeypatch):
    class UnreadableStat:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            raise PermissionError("stat read denied")

    monkeypatch.setattr(os, "kill", lambda pid, signal: None)
    monkeypatch.setattr("builtins.open", lambda path: UnreadableStat())

    with pytest.raises(PermissionError, match="stat read denied"):
        alive(42)


def test_run_bounded_returns_output_of_a_good_child():
    assert observations.run_bounded([sys.executable, "-c", "print('ok')"], 10) == b"ok\n"
    assert observations.run_bounded([sys.executable, "-c", "raise SystemExit(3)"], 10) is None


def test_run_bounded_kills_the_whole_process_group_at_the_deadline(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    # sh, not a Python child: under load a Python interpreter can take longer
    # than the deadline just to start, and be killed before it records the pid.
    script = 'sleep 120 & echo $! > "$1"; wait'
    started = time.monotonic()
    assert observations.run_bounded(["sh", "-c", script, "sh", str(pidfile)], 3) is None
    assert time.monotonic() - started < 10
    grandchild = int(pidfile.read_text())
    for _ in range(50):  # init reaps the orphan; give it a moment
        if not alive(grandchild):
            break
        time.sleep(.1)
    else:
        os.kill(grandchild, signal.SIGKILL)
        pytest.fail("a grandchild of the timed-out check survived")


def test_a_hung_check_is_killed_and_the_next_one_runs(operator_child, monkeypatch):
    monkeypatch.setitem(observations.TIMEOUT, "health", 2)
    observations.note(observations.HEALTH)
    monkeypatch.setenv("OPERATOR_TEST_CHILD_HANG", "1")
    started = time.monotonic()
    assert observations.tick() == observations.HEALTH
    assert time.monotonic() - started < 15
    row = OpsState.objects.get(key=observations.HEALTH)
    assert row.ok is False and "result" not in row.payload and row.payload["attempted_at"]
    # Not retried at once, so a broken check cannot spin the coordinator ...
    assert observations.tick() is None
    # ... but after its interval the next attempt runs normally, with nothing to recover.
    monkeypatch.delenv("OPERATOR_TEST_CHILD_HANG")
    row.payload["attempted_at"] = (timezone.now() - timezone.timedelta(seconds=61)).isoformat()
    row.save(update_fields=["payload"])
    assert observations.tick() == observations.HEALTH
    row.refresh_from_db()
    # The real child's output, as the command prints it and measure() stores it.
    assert row.ok and row.payload["result"]["checks"]
    assert {"label", "ok", "detail"} == set(row.payload["result"]["checks"][0])
    assert "completed_at" in row.payload


def test_real_usage_measurement_counts_files_and_zero(operator_child, org):
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "a.csv").write_bytes(b"x" * 3000)
    DataSource.objects.create(org=org, name="a", type="file", config={"upload": True, "path": "a.csv"})
    key = observations.usage_key(org.pk)
    observations.note(key)
    observations.measure(key)
    assert OpsState.objects.get(key=key).payload["result"] == {"bytes": 3000}
    DataSource.objects.all().delete()
    observations.measure(key)
    assert OpsState.objects.get(key=key).payload["result"] == {"bytes": 0}
