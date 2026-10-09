"""Worker mechanics: claiming, priority order, stale-run recovery,
scheduler diffing. The loop itself is exercised with --once."""
import pytest
from django.utils import timezone

from apps.runner.management.commands.runworker import (
    ADVISORY_LOCK_KEY,
    Command,
    _SchedulerManager,
)
from apps.runner.models import Run, WorkerHeartbeat

pytestmark = pytest.mark.django_db


class StubExecutor:
    """Mirrors the Executor surface the claim loop depends on."""

    def __init__(self, max_concurrent=3, memory_budget_mb=0, per_run_mb=100):
        self.worker_id = "stub-1"
        self.max_concurrent = max_concurrent
        self.memory_budget_mb = memory_budget_mb
        self.per_run_mb = per_run_mb
        self.started: list[Run] = []

    @property
    def running_count(self):
        return len(self.started)

    @property
    def has_capacity(self):
        return self.running_count < self.max_concurrent

    @property
    def reserved_memory_mb(self):
        return sum(self.per_run_mb for _ in self.started)

    def memory_for(self, run):
        return self.per_run_mb

    def can_admit(self, memory_mb):
        if not self.has_capacity:
            return False
        if self.memory_budget_mb <= 0:
            return True
        return self.reserved_memory_mb + memory_mb <= self.memory_budget_mb

    def exceeds_budget(self, memory_mb):
        return self.memory_budget_mb > 0 and memory_mb > self.memory_budget_mb

    def start_run(self, run):
        self.started.append(run)
        Run.objects.filter(pk=run.pk).update(status=Run.RUNNING)
        return True


@pytest.fixture
def make_run(report_row):
    def _make(**kwargs):
        return Run.objects.create(
            report=report_row,
            studio=report_row.studio,
            slug=report_row.slug,
            **kwargs,
        )

    return _make


class TestClaimLoop:
    def test_claims_in_priority_then_fifo_order(self, make_run):
        last = make_run(priority=9)
        first = make_run(priority=1)
        mid = make_run(priority=5)
        stub = StubExecutor(max_concurrent=2)
        Command()._claim_loop(stub)
        assert [r.pk for r in stub.started] == [first.pk, mid.pk]
        assert Run.objects.get(pk=last.pk).status == Run.QUEUED  # capacity reached

    def test_skips_stop_requested(self, make_run):
        make_run(stop_requested=True)
        stub = StubExecutor()
        Command()._claim_loop(stub)
        assert stub.started == []

    def test_claimed_run_marked_starting_before_spawn(self, make_run):
        run = make_run()
        events = []

        class Recorder(StubExecutor):
            def start_run(self, r):
                events.append(Run.objects.get(pk=r.pk).status)
                return super().start_run(r)

        Command()._claim_loop(Recorder())
        assert events == [Run.STARTING]
        assert Run.objects.get(pk=run.pk).status == Run.RUNNING


class TestMemoryAdmission:
    def test_claims_until_the_memory_budget_is_spent(self, make_run):
        """Three 400 MB builds do not fit in a 1000 MB budget."""
        for _ in range(3):
            make_run()
        stub = StubExecutor(max_concurrent=10, memory_budget_mb=1000, per_run_mb=400)
        Command()._claim_loop(stub)
        assert len(stub.started) == 2
        assert Run.objects.filter(status=Run.QUEUED).count() == 1

    def test_head_of_queue_blocks_rather_than_being_skipped(self, make_run):
        """A large report must not starve behind a stream of small ones."""
        big = make_run(priority=1)
        small = make_run(priority=9)

        stub = StubExecutor(max_concurrent=10, memory_budget_mb=1000, per_run_mb=900)
        stub.started.append(object())  # 900 MB already in flight

        Command()._claim_loop(stub)

        # Neither started: we stop at the head instead of letting `small` pass.
        assert Run.objects.get(pk=big.pk).status == Run.QUEUED
        assert Run.objects.get(pk=small.pk).status == Run.QUEUED

    def test_unbudgeted_runner_keeps_count_only_behaviour(self, make_run):
        for _ in range(5):
            make_run()
        stub = StubExecutor(max_concurrent=3, memory_budget_mb=0)
        Command()._claim_loop(stub)
        assert len(stub.started) == 3

    def test_run_too_big_for_the_budget_fails_with_an_explanation(self, make_run):
        """Leaving it queued forever would be a silent hang."""
        run = make_run()

        stub = StubExecutor(max_concurrent=3, memory_budget_mb=1000, per_run_mb=9999)
        Command()._claim_loop(stub)

        run.refresh_from_db()
        assert run.status == Run.ERROR
        assert "exceeds this runner's whole memory budget" in run.stderr_tail
        assert stub.started == []


class TestRecovery:
    def test_stale_active_runs_marked_error(self, make_run):
        r1 = make_run(status=Run.RUNNING)
        r2 = make_run(status=Run.STARTING)
        done = make_run(status=Run.SUCCESS)
        Command()._recover_stale_runs()
        assert Run.objects.get(pk=r1.pk).status == Run.ERROR
        assert "Worker heartbeat missing" in Run.objects.get(pk=r1.pk).stderr_tail
        assert Run.objects.get(pk=r2.pk).status == Run.ERROR
        assert Run.objects.get(pk=done.pk).status == Run.SUCCESS

    def test_runs_of_a_live_worker_are_left_alone(self, make_run):
        """The whole point of the multi-runner fix: restarting one runner must
        not error another runner's in-flight builds."""
        WorkerHeartbeat.objects.create(worker_id="runner-b", running_count=1)
        mine = make_run(status=Run.RUNNING, worker_id="runner-a")  # dead worker
        theirs = make_run(status=Run.RUNNING, worker_id="runner-b")  # live worker

        Command()._recover_stale_runs()

        assert Run.objects.get(pk=mine.pk).status == Run.ERROR
        assert Run.objects.get(pk=theirs.pk).status == Run.RUNNING

    def test_runs_of_a_stale_worker_are_reclaimed(self, make_run):
        beat = WorkerHeartbeat.objects.create(worker_id="runner-b")
        WorkerHeartbeat.objects.filter(pk=beat.pk).update(
            last_beat_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        run = make_run(status=Run.RUNNING, worker_id="runner-b")

        Command()._recover_stale_runs()

        assert Run.objects.get(pk=run.pk).status == Run.ERROR

    def test_never_reaps_its_own_runs_even_with_a_stale_heartbeat(self, make_run):
        """A DB stall can let our own heartbeat lapse; we know we are alive, so
        our live subprocesses must not be declared orphans."""
        run = make_run(status=Run.RUNNING, worker_id="me-1")

        Command()._recover_stale_runs(self_worker_id="me-1")

        assert Run.objects.get(pk=run.pk).status == Run.RUNNING

    def test_newly_registered_worker_is_visible_to_recovery_update(
        self, make_run, monkeypatch
    ):
        from django.db.models.query import QuerySet

        new_run = []
        original_update = QuerySet.update

        def register_worker_before_update(queryset, **kwargs):
            if queryset.model is Run and not new_run:
                WorkerHeartbeat.objects.create(worker_id="runner-arriving")
                new_run.append(
                    make_run(status=Run.RUNNING, worker_id="runner-arriving")
                )
            return original_update(queryset, **kwargs)

        monkeypatch.setattr(QuerySet, "update", register_worker_before_update)

        Command()._recover_stale_runs()

        assert Run.objects.get(pk=new_run[0].pk).status == Run.RUNNING

    def test_heartbeat_upsert_and_alive_window(self):
        stub = StubExecutor()
        Command()._beat("w-test", stub)
        assert WorkerHeartbeat.alive().count() == 1
        WorkerHeartbeat.objects.update(
            last_beat_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        assert WorkerHeartbeat.alive().count() == 0


class TestSandboxSweep:
    def _sweep(self, monkeypatch, root):
        monkeypatch.setattr(
            "apps.runner.management.commands.runworker._tmp_root", lambda: root
        )
        monkeypatch.setattr("apps.runner.sandbox.sandbox_mode", lambda: "local")
        Command()._sweep_orphan_sandboxes()

    @pytest.mark.parametrize("status", [Run.STARTING, Run.RUNNING, Run.QUEUED])
    def test_live_run_preserves_shared_scratch_and_its_logs(
        self, make_run, monkeypatch, tmp_path, status
    ):
        run = make_run(status=status, worker_id="another-worker")
        root = tmp_path / "tmp"
        scratch = root / "run-shared-random"
        scratch.mkdir(parents=True)
        (scratch / "build.txt").write_text("in use")
        logs = root / "runs" / str(run.pk)
        logs.mkdir(parents=True)
        (logs / "worker.log").write_text("in use")

        self._sweep(monkeypatch, root)

        assert scratch.is_dir()
        assert logs.is_dir()

    def test_quiescent_sweep_removes_orphans_and_preserves_unrelated_files(
        self, monkeypatch, tmp_path
    ):
        root = tmp_path / "tmp"
        scratch = root / "run-orphan-random"
        scratch.mkdir(parents=True)
        orphan_logs = root / "runs" / "00000000-0000-0000-0000-000000000001"
        orphan_logs.mkdir(parents=True)
        unrelated_dir = root / "runs" / "keep-this"
        unrelated_dir.mkdir(parents=True)
        unrelated_file = root / "readme.txt"
        root.mkdir(exist_ok=True)
        unrelated_file.write_text("keep")

        self._sweep(monkeypatch, root)

        assert not scratch.exists()
        assert not orphan_logs.exists()
        assert unrelated_dir.is_dir()
        assert unrelated_file.read_text() == "keep"

    def test_paths_created_after_candidate_snapshot_are_not_swept(
        self, make_run, monkeypatch, tmp_path
    ):
        run = make_run(status=Run.RUNNING)
        root = tmp_path / "tmp"
        root.mkdir()
        late_scratch = root / "run-created-during-snapshot"
        late_logs = root / "runs" / "00000000-0000-0000-0000-000000000002"
        original_filter = Run.objects.filter

        def create_paths_then_filter(**kwargs):
            late_scratch.mkdir()
            late_logs.mkdir(parents=True)
            return original_filter(**kwargs)

        monkeypatch.setattr(Run.objects, "filter", create_paths_then_filter)

        self._sweep(monkeypatch, root)

        assert late_scratch.is_dir()
        assert late_logs.is_dir()


@pytest.fixture
def release_advisory_locks():
    """Advisory locks are session-scoped, so a test that takes one leaks it
    into every later test on the same connection."""
    yield
    from django.db import connections

    with connections["default"].cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock_all()")


class TestAdvisoryLock:
    def test_lock_is_exclusive_across_sessions(self, release_advisory_locks):
        # Two independent connections: only one may hold the lock.
        import psycopg
        from django.db import connections

        assert Command()._acquire_lock() is True

        params = connections["default"].get_connection_params()
        for k in ("cursor_factory", "context"):
            params.pop(k, None)
        with psycopg.connect(**params) as other:
            with other.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_KEY])
                assert cur.fetchone()[0] is False


class TestRoles:
    """The split that lets runners scale past one. --role=all must stay
    byte-for-byte the historical worker."""

    def _run(self, **opts):
        from django.core.management import call_command

        call_command("runworker", once=True, **opts)

    def test_default_role_is_all(self, settings):
        from apps.runner.management.commands.runworker import ROLE_ALL

        assert settings.TRELLUM_RUNNER_ROLE == ROLE_ALL

    def test_unknown_role_is_refused(self):
        from django.core.management.base import CommandError

        cmd = Command()
        with pytest.raises(CommandError):
            cmd.handle(once=True, role="wizard")

    def test_runner_does_not_take_the_global_lock(self, release_advisory_locks):
        """The whole point: many runners must coexist. If a runner took the
        coordinator lock, the fleet would be capped at one again."""
        import psycopg
        from django.db import connections

        self._run(role="runner")

        params = connections["default"].get_connection_params()
        for k in ("cursor_factory", "context"):
            params.pop(k, None)
        with psycopg.connect(**params) as other:
            with other.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_KEY])
                assert cur.fetchone()[0] is True, (
                    "a runner is holding the coordinator lock"
                )

    def test_runner_heartbeat_carries_role_and_capacity(self):
        """The worker deletes its heartbeat on exit, so assert on _beat itself
        rather than on what survives a --once run."""
        from apps.runner.executor import Executor

        ex = Executor("w-runner", max_concurrent=4, memory_budget_mb=8000)
        Command()._beat("w-runner", ex, role="runner")

        beat = WorkerHeartbeat.objects.get(worker_id="w-runner")
        assert beat.role == "runner"
        assert beat.max_concurrent == 4
        assert beat.memory_budget_mb == 8000

    def test_coordinator_heartbeat_reports_no_build_capacity(self):
        """A pure coordinator hosts no builds; the fleet view must not imply
        it can take work."""
        Command()._beat("w-coord", None, role="coordinator")

        beat = WorkerHeartbeat.objects.get(worker_id="w-coord")
        assert beat.role == "coordinator"
        assert beat.max_concurrent == 0
        assert beat.running_count == 0
        assert beat.memory_budget_mb == 0

    def test_coordinator_does_not_claim_runs(self, make_run):
        run = make_run()
        self._run(role="coordinator")
        assert Run.objects.get(pk=run.pk).status == Run.QUEUED

    def test_runner_claims_and_starts(self, make_run, monkeypatch):
        started = []
        monkeypatch.setattr(
            "apps.runner.executor.Executor.start_run",
            lambda self, run, extra_env=None: started.append(run.pk) or True,
        )
        run = make_run()
        self._run(role="runner")
        assert started == [run.pk]

    def test_runner_beats_before_claiming_work(self, make_run, monkeypatch):
        import threading

        import apps.runner.management.commands.runworker as runworker

        started = []
        events = []
        shutdown = threading.Event()
        original_beat = Command._beat

        class StubThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def join(self, timeout=None):
                pass

        class StubGitSyncThread:
            def start(self):
                pass

            def stop(self):
                pass

        def record_beat(self, *args, **kwargs):
            events.append("beat")
            return original_beat(self, *args, **kwargs)

        def record_start(self, run, extra_env=None):
            events.append("claim")
            started.append(run.pk)
            return True

        monkeypatch.setattr(Command, "_beat", record_beat)
        monkeypatch.setattr(runworker.threading, "Event", lambda: shutdown)
        monkeypatch.setattr(runworker.threading, "Thread", StubThread)
        monkeypatch.setattr(runworker.time, "sleep", lambda _: shutdown.set())
        monkeypatch.setattr("apps.runner.executor.Executor.tick", lambda self: None)
        monkeypatch.setattr(
            "apps.runner.executor.Executor.drain", lambda self, timeout: None
        )
        monkeypatch.setattr(
            "apps.runner.executor.Executor.start_run", record_start
        )
        monkeypatch.setattr(
            "apps.runner.management.commands.runworker.Command._sweep_orphan_sandboxes",
            lambda self: None,
        )
        monkeypatch.setattr("apps.runner.sandbox.sandbox_mode", lambda: "off")
        monkeypatch.setattr("apps.runner.gitsync.GitSyncThread", StubGitSyncThread)
        run = make_run()
        Command().handle(once=False, role="runner")
        assert events[:2] == ["beat", "claim"]
        assert started == [run.pk]

    def test_runner_does_not_reap(self, make_run):
        """Reaping is database-wide bookkeeping and belongs to the single
        coordinator; a booting runner must not touch other workers' runs."""
        orphan = make_run(status=Run.RUNNING, worker_id="long-dead")
        self._run(role="runner")
        assert Run.objects.get(pk=orphan.pk).status == Run.RUNNING

    def test_coordinator_reaps(self, make_run):
        orphan = make_run(status=Run.RUNNING, worker_id="long-dead")
        self._run(role="coordinator")
        assert Run.objects.get(pk=orphan.pk).status == Run.ERROR


class TestStudioSyncLock:
    """Concurrent runners must not clone into the same studio directory."""

    def test_second_holder_is_refused_across_connections(self, studio):
        import psycopg
        from django.db import connections

        from apps.runner.gitsync import GITSYNC_LOCK_NAMESPACE, studio_sync_lock

        held = studio_sync_lock(studio.pk)
        assert held.__enter__() is True
        try:
            params = connections["default"].get_connection_params()
            for k in ("cursor_factory", "context"):
                params.pop(k, None)
            with psycopg.connect(**params) as other:
                with other.cursor() as cur:
                    cur.execute(
                        "SELECT pg_try_advisory_lock(%s, %s)",
                        [GITSYNC_LOCK_NAMESPACE, studio.pk],
                    )
                    assert cur.fetchone()[0] is False
        finally:
            held.__exit__(None, None, None)

    def test_release_lets_the_next_holder_in(self, studio):
        from apps.runner.gitsync import studio_sync_lock

        first = studio_sync_lock(studio.pk)
        assert first.__enter__() is True
        first.__exit__(None, None, None)

        second = studio_sync_lock(studio.pk)
        assert second.__enter__() is True
        second.__exit__(None, None, None)

    def test_different_studios_do_not_contend(self, studio, studio2):
        from apps.runner.gitsync import studio_sync_lock

        a = studio_sync_lock(studio.pk)
        b = studio_sync_lock(studio2.pk)
        assert a.__enter__() is True
        assert b.__enter__() is True
        a.__exit__(None, None, None)
        b.__exit__(None, None, None)


class TestSchedulerDiff:
    def test_desired_jobs_follow_report_rows(self, report_row):
        report_row.schedule_cron = "0 7 * * *"
        report_row.save(update_fields=["schedule_cron"])
        mgr = _SchedulerManager()
        mgr.start()
        try:
            jobs = {j.id for j in mgr._scheduler.get_jobs()}
            assert f"report-{report_row.pk}" in jobs
            # Disabling drops the job on the next refresh.
            report_row.disabled = True
            report_row.save(update_fields=["disabled"])
            mgr.refresh()
            assert not mgr._scheduler.get_jobs()
        finally:
            mgr.shutdown()

    def test_invalid_cron_is_skipped(self, report_row):
        report_row.schedule_cron = "not a cron"
        report_row.save(update_fields=["schedule_cron"])
        mgr = _SchedulerManager()
        mgr.start()
        try:
            assert not mgr._scheduler.get_jobs()
        finally:
            mgr.shutdown()


class TestReleaseCheckJob:
    """Opt-in: the coordinator registers the daily release check only when
    TRELLUM_UPDATE_CHECK is on (internal planning ticket #066). A recording scheduler stands in
    for APScheduler so nothing here fires the job, let alone the network."""

    class _Recorder:
        def __init__(self):
            self.jobs: dict[str, dict] = {}

        def add_job(self, fn, **kw):
            self.jobs[kw["id"]] = kw

    def _manager(self):
        mgr = _SchedulerManager()
        mgr._scheduler = self._Recorder()
        return mgr

    def test_nothing_registered_by_default(self, settings):
        settings.TRELLUM_UPDATE_CHECK = False
        mgr = self._manager()
        mgr._schedule_update_check()
        assert mgr._scheduler.jobs == {}

    def test_once_at_start_then_daily_when_on(self, settings):
        settings.TRELLUM_UPDATE_CHECK = True
        mgr = self._manager()
        mgr._schedule_update_check()
        job = mgr._scheduler.jobs["update-check"]
        assert job["next_run_time"] is not None, "runs at start, not a day later"
        assert job["trigger"].interval.total_seconds() == 24 * 3600
        assert job["misfire_grace_time"] == 3600, "a missed run still happens when the box is back"


class TestBootCatchup:
    """Catch-up decides from the storage layer, never this node's disk: on
    the s3 backend a fresh coordinator's disk is empty, and reading it
    directly would re-enqueue every report on every boot."""

    @pytest.fixture
    def remote(self, settings, monkeypatch):
        from apps.core import storage
        from apps.core.tests.test_storage import FakeClient, FakeS3

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        fake = FakeS3()
        monkeypatch.setattr(storage, "_s3", lambda: fake)
        monkeypatch.setattr(storage, "_client", lambda: FakeClient(fake))
        monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 0.0)
        storage._pointer_memo.clear()
        return fake

    def _catchup_runs(self):
        return Run.objects.filter(trigger="catchup").count()

    def test_bucket_success_is_believed_despite_an_empty_local_disk(
        self, report_row, remote
    ):
        from apps.core.tests.test_storage import install_build

        install_build(remote, slug=report_row.slug)
        Command()._enqueue_catchup()
        assert self._catchup_runs() == 0

    def test_a_never_built_report_is_enqueued(self, report_row, remote):
        Command()._enqueue_catchup()
        assert self._catchup_runs() == 1

    def test_an_unreachable_store_skips_catchup_instead_of_storming(
        self, report_row, remote, monkeypatch
    ):
        from apps.core import storage

        def down(*_a, **_kw):
            raise OSError("connection refused")

        monkeypatch.setattr(storage, "_client", down)
        Command()._enqueue_catchup()  # must not raise
        assert self._catchup_runs() == 0

    def test_local_backend_still_reads_the_data_volume(
        self, report_row, studio_tree, settings
    ):
        settings.TRELLUM_STORAGE_BACKEND = "local"
        from trellum.meta import write_meta

        out = studio_tree.output_dir / report_row.slug
        write_meta(str(out), {"last_run": "r1", "last_status": "success"})
        Command()._enqueue_catchup()
        assert self._catchup_runs() == 0


class TestEmailScheduleDiff:
    """Mirrors TestSchedulerDiff above: personal delivery cadences ride the
    same diffed-every-60s scheduler as the build cron."""

    def test_desired_jobs_follow_schedule_rows(self, report_row, org_admin):
        from apps.reports.models import EmailSchedule

        schedule = EmailSchedule.objects.create(
            report=report_row,
            created_by=org_admin,
            freq=EmailSchedule.FREQ_DAILY,
            send_hour=8,
            send_minute=0,
        )
        mgr = _SchedulerManager()
        mgr.start()
        try:
            jobs = {j.id for j in mgr._scheduler.get_jobs()}
            assert f"email-{schedule.pk}" in jobs
            # Disabling drops the job on the next refresh, same as a report row.
            schedule.enabled = False
            schedule.save(update_fields=["enabled"])
            mgr.refresh()
            assert f"email-{schedule.pk}" not in {j.id for j in mgr._scheduler.get_jobs()}
        finally:
            mgr.shutdown()

    def test_report_leaving_scan_drops_its_email_jobs_too(self, report_row, org_admin):
        from apps.reports.models import EmailSchedule

        schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
        mgr = _SchedulerManager()
        mgr.start()
        try:
            assert f"email-{schedule.pk}" in {j.id for j in mgr._scheduler.get_jobs()}
            report_row.present_in_scan = False
            report_row.save(update_fields=["present_in_scan"])
            mgr.refresh()
            assert f"email-{schedule.pk}" not in {j.id for j in mgr._scheduler.get_jobs()}
        finally:
            mgr.shutdown()

    def test_invalid_weekday_is_skipped(self, report_row, org_admin):
        from apps.reports.models import EmailSchedule

        # Bypasses EmailSchedule.clean() on purpose: a row could reach this
        # state via a bug elsewhere, and the scheduler must not crash on it.
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=org_admin, freq=EmailSchedule.FREQ_WEEKLY, weekday=9
        )
        mgr = _SchedulerManager()
        mgr.start()
        try:
            assert f"email-{schedule.pk}" not in {j.id for j in mgr._scheduler.get_jobs()}
        finally:
            mgr.shutdown()

    def test_invalid_timezone_falls_back_to_utc(self, report_row, org_admin, caplog):
        from apps.reports.models import EmailSchedule

        # Same bypass as test_invalid_weekday_is_skipped: EmailSchedule.clean()
        # (and the create/update endpoints) now reject a bad timezone before
        # it can be saved through the UI, so reaching this state means a row
        # was written straight through the ORM. Unlike an out-of-range
        # weekday, this must not drop the job -- it falls back to UTC and
        # logs, so the delivery still goes out (at the wrong hour, which is
        # recoverable) rather than silently never firing at all.
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=org_admin, timezone="Not/AZone"
        )
        mgr = _SchedulerManager()
        with caplog.at_level("INFO", logger="trellum.worker"):
            mgr.start()
            try:
                jobs = {j.id: j for j in mgr._scheduler.get_jobs()}
                job = jobs.get(f"email-{schedule.pk}")
                assert job is not None
                assert str(job.trigger.timezone) == "UTC"
            finally:
                mgr.shutdown()
        logged = caplog.text
        assert "invalid timezone" in logged
        assert "Not/AZone" in logged

    def test_report_and_email_jobs_coexist(self, report_row, org_admin):
        from apps.reports.models import EmailSchedule

        report_row.schedule_cron = "0 7 * * *"
        report_row.save(update_fields=["schedule_cron"])
        schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
        mgr = _SchedulerManager()
        mgr.start()
        try:
            jobs = {j.id for j in mgr._scheduler.get_jobs()}
            assert f"report-{report_row.pk}" in jobs
            assert f"email-{schedule.pk}" in jobs
        finally:
            mgr.shutdown()
