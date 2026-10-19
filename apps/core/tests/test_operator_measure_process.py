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
    except FileNotFoundError:
        return False


def test_run_bounded_returns_output_of_a_good_child():
    assert observations.run_bounded([sys.executable, "-c", "print('ok')"], 10) == b"ok\n"
    assert observations.run_bounded([sys.executable, "-c", "raise SystemExit(3)"], 10) is None


def test_run_bounded_kills_the_whole_process_group_at_the_deadline(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen(['sleep', '120'])\nopen({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    started = time.monotonic()
    assert observations.run_bounded([sys.executable, "-c", script], 2) is None
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
