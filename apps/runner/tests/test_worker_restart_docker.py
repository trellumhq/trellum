"""Worker replacement against a real sandbox, without touching deployed runs."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.runner.management.commands.runworker import Command
from apps.runner.models import Run, WorkerHeartbeat
from apps.runner.services import request_stop


pytestmark = [pytest.mark.django_db, pytest.mark.heavy, pytest.mark.docker_sandbox]


@pytest.fixture
def docker_client():
    import docker

    client = None
    try:
        client = docker.from_env(timeout=5)
        client.ping()
        client.images.get("busybox:latest")
    except docker.errors.DockerException as exc:
        if client is not None:
            client.close()
        pytest.skip(f"Requires a Docker daemon and cached busybox:latest: {exc}")
    yield client
    client.close()


@pytest.mark.parametrize("cancel,persist_container_id", [(False, True), (True, True), (False, False)])
def test_replacement_worker_stops_abandoned_container(
    report_row, settings, docker_client, cancel, persist_container_id,
):
    settings.TRELLUM_SANDBOX = "docker"
    run = Run.objects.create(
        report=report_row, studio=report_row.studio, slug=report_row.slug,
        status=Run.RUNNING, termination_confirmed=False,
        worker_id="restart-fixture-original", started_at=timezone.now(),
    )
    heartbeat = WorkerHeartbeat.objects.create(worker_id=run.worker_id)
    container = docker_client.containers.run(
        "busybox:latest", ["sleep", "300"], detach=True,
        name=f"trellum-run-{run.pk}",
        labels={"trellum.sandbox": "1", "trellum.run-id": str(run.pk)},
        network_mode="none", read_only=True, cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"], mem_limit="32m", pids_limit=16,
    )
    try:
        if persist_container_id:
            Run.objects.filter(pk=run.pk).update(container_id=container.id)
        replacement = Command()
        replacement._recover_stale_runs(self_worker_id="restart-fixture-replacement")
        run.refresh_from_db()
        container.reload()
        assert run.status == Run.RUNNING
        assert container.attrs["State"]["Running"] is True

        # The old process is gone; its last heartbeat eventually expires.
        WorkerHeartbeat.objects.filter(pk=heartbeat.pk).update(
            last_beat_at=timezone.now() - timedelta(minutes=5),
        )
        if cancel:
            assert request_stop(report_row)
        replacement._recover_stale_runs(self_worker_id="restart-fixture-replacement")
        run.refresh_from_db()
        assert run.status == (Run.STOPPED if cancel else Run.ERROR)
        assert run.termination_confirmed is True
        assert run.finished_at is not None
        import docker.errors

        try:
            container.reload()
        except docker.errors.NotFound:
            pass
        else:
            assert container.attrs["State"]["Running"] is False
        replacement._recover_stale_runs(self_worker_id="restart-fixture-replacement")
        assert Run.objects.filter(report=report_row).count() == 1
    finally:
        import docker.errors

        try:
            container.remove(force=True)
        except docker.errors.NotFound:
            pass
