"""Real PostgreSQL and killable subprocess gates; no provider endpoints."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote

import pytest
from django.db import connection

from apps.core import operator_observations as observations
from apps.core.models import OpsState
from apps.core.operator_probe_worker import ProbeSupervisor, kill_and_reap, process_birth
from apps.core.workspace import initialize_workspace_identity
from apps.runner.models import WorkerHeartbeat

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def probe_process_env(settings, tmp_path, monkeypatch):
    assert connection.vendor == "postgresql", "probe process gate requires disposable PostgreSQL"
    settings.DATA_DIR = tmp_path / "data"
    settings.BACKUP_DIR = str(tmp_path / "backups")
    settings.DEBUG = True
    settings.TRELLUM_SANDBOX = "off"
    settings.CLEANUP_ENABLED = False
    settings.TRELLUM_STORAGE_BACKEND = "local"
    initialize_workspace_identity()
    database = connection.settings_dict
    url = (f"postgres://{quote(database['USER'])}:{quote(database['PASSWORD'])}@"
           f"{database['HOST']}:{database['PORT']}/{database['NAME']}")
    module = tmp_path / "operator_child_settings.py"
    module.write_text(
        "from trellum_portal.settings.test import *\nfrom pathlib import Path\nimport sys,time\n"
        f"DATA_DIR=Path({str(settings.DATA_DIR)!r})\nBACKUP_DIR={str(settings.BACKUP_DIR)!r}\n"
        "DEBUG=True\nTRELLUM_SANDBOX='off'\nCLEANUP_ENABLED=False\nTRELLUM_STORAGE_BACKEND='local'\nTRELLUM_REPORT_ACCESS_MODEL='proxy'\n"
        "if __import__('os').environ.get('OPERATOR_TEST_CHILD_DELAY') and len(sys.argv)>1 and sys.argv[1]=='maintenance': time.sleep(60)\n"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "operator_child_settings")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + str(settings.BASE_DIR))
    node = f"{socket.gethostname()}-{os.getpid()}"
    WorkerHeartbeat.objects.create(worker_id=node, sandbox_mode="off")
    return node


def test_real_child_measurement_publishes_only_after_reap(probe_process_env):
    supervisor = ProbeSupervisor(probe_process_env)
    supervisor.tick()
    result = observations.snapshot("maintenance")
    assert result["scope"] == "coordinator-local"
    assert result["checks"] and result["id"]
    assert supervisor.process is None
    assert OpsState.objects.get(key=observations.SLOT).payload["active"]["state"] == "succeeded"


def test_watchdog_covers_blocked_settings_before_django(probe_process_env, monkeypatch):
    monkeypatch.setenv("OPERATOR_TEST_CHILD_DELAY", "1")
    supervisor = ProbeSupervisor(probe_process_env)
    started = time.monotonic()
    supervisor.tick()
    assert 8 <= time.monotonic() - started <= 15
    assert supervisor.process is None
    assert observations.snapshot("maintenance")["state"] == "unknown"
    assert OpsState.objects.get(key=observations.SLOT).payload["active"]["state"] in ("failed", "unconfirmed")


def test_supervisor_sigkill_never_publishes_or_replays(probe_process_env, monkeypatch):
    monkeypatch.setenv("OPERATOR_TEST_CHILD_DELAY", "1")
    parent = subprocess.Popen([
        sys.executable, "-c",
        "import django;django.setup();from apps.core.workspace import initialize_workspace_identity;initialize_workspace_identity();"
        "from apps.core.operator_probe_worker import ProbeSupervisor;ProbeSupervisor(" + repr(probe_process_env) + ").tick()",
    ], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proof = None
        for unused in range(120):
            row = OpsState.objects.filter(key=observations.SLOT).first()
            proof = row and row.payload.get("active", {}).get("child")
            if proof:
                break
            if parent.poll() is not None:
                raise AssertionError("supervisor exited before child started")
            time.sleep(.05)
        assert proof
        parent.kill(); parent.wait(timeout=3)
        # Parent death/watchdog must prevent any result publication. A zombie
        # cannot execute; if its namespace cannot prove reaping, admission must
        # remain blocked rather than claiming physical cancellation.
        time.sleep(1)
        assert observations.snapshot("maintenance")["state"] == "unknown"
        row.refresh_from_db()
        operation_id = row.payload["active"]["id"]
        assert row.payload["active"]["state"] == "running"
        assert process_birth(proof["pid"]) is None or " Z " in Path(f"/proc/{proof['pid']}/stat").read_text()
        assert OpsState.objects.get(key=observations.SLOT).payload["active"]["id"] == operation_id
    finally:
        if parent.poll() is None:
            kill_and_reap(parent)


def test_real_pipe_output_limit_kills_child(probe_process_env, monkeypatch):
    # Test-only child program replaces the fixed spawn at the supervisor seam;
    # no portal/health response is mocked, and the pipe/kill/reap path is real.
    original = subprocess.Popen
    def flood(argv, **kwargs):
        return original([sys.executable, "-c", "import os,time;os.write(1,b'x'*200000);time.sleep(60)"], **kwargs)
    monkeypatch.setattr("apps.core.operator_probe_worker.subprocess.Popen", flood)
    supervisor = ProbeSupervisor(probe_process_env)
    supervisor.tick()
    assert supervisor.process is None
    assert observations.snapshot("maintenance")["state"] == "unknown"
    assert OpsState.objects.get(key=observations.SLOT).payload["active"]["state"] == "failed"
