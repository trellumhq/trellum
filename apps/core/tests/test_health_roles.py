"""The worker health check must distinguish the two roles: each half failing
is invisible in a different way — no runner means builds queue forever, no
coordinator means schedules silently never fire."""
import pytest

from apps.core.health import run_checks
from apps.runner.models import WorkerHeartbeat

pytestmark = pytest.mark.django_db


def _worker_check():
    return next(c for c in run_checks() if c["label"] == "worker")


class TestWorkerCheck:
    def test_no_workers_at_all_fails(self):
        result = _worker_check()
        assert result["ok"] is False
        assert "no live worker heartbeat" in result["detail"]

    def test_combined_worker_satisfies_both_roles(self):
        WorkerHeartbeat.objects.create(
            worker_id="w1", role=WorkerHeartbeat.ROLE_ALL, max_concurrent=3
        )
        result = _worker_check()
        assert result["ok"] is True
        assert "1 runner(s), 1 coordinator(s)" in result["detail"]

    def test_coordinator_without_runner_fails(self):
        WorkerHeartbeat.objects.create(
            worker_id="c1", role=WorkerHeartbeat.ROLE_COORDINATOR, max_concurrent=0
        )
        result = _worker_check()
        assert result["ok"] is False
        assert "no live runner" in result["detail"]

    def test_runner_without_coordinator_fails(self):
        WorkerHeartbeat.objects.create(
            worker_id="r1", role=WorkerHeartbeat.ROLE_RUNNER, max_concurrent=3
        )
        result = _worker_check()
        assert result["ok"] is False
        assert "no live coordinator" in result["detail"]

    def test_split_fleet_is_healthy_and_totals_capacity(self):
        WorkerHeartbeat.objects.create(
            worker_id="c1", role=WorkerHeartbeat.ROLE_COORDINATOR, max_concurrent=0
        )
        for i in range(3):
            WorkerHeartbeat.objects.create(
                worker_id=f"r{i}",
                role=WorkerHeartbeat.ROLE_RUNNER,
                max_concurrent=4,
                running_count=1,
            )
        result = _worker_check()
        assert result["ok"] is True
        assert "3 runner(s), 1 coordinator(s), 3/12 builds running" in result["detail"]
