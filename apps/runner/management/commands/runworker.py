"""The worker process, in two roles that compose into one.

    --role=all           coordinator + runner in one process (the default)
    --role=coordinator   cron scheduler + stale-run reaper. Exactly one, held
                         by a Postgres advisory lock. Scheduled tasks also
                         use the shared studio data and audit archive paths.
    --role=runner        claims runs, spawns builds, and git-syncs the studios
                         on shared persistent storage. Scale to N replicas.

The split exists because the queue was never the bottleneck — ``Run`` with
``SELECT ... FOR UPDATE SKIP LOCKED`` already supports many consumers. What
forced a single worker was that the cron scheduler lived in the same process as
the executor, so the lock protecting the scheduler also serialised the builds.
Separating the roles removes that without adding any infrastructure.

A deployment can use one combined ``worker`` service or one active coordinator
and multiple runners from the same image. Hosts share PostgreSQL and the
persistent data directory.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connections, transaction
from django.utils import timezone

from apps.runner.executor import Executor, _tmp_root
from apps.runner.models import Run, WorkerHeartbeat

logger = logging.getLogger("trellum.worker")

# Spells "BI PORTAL", from before the rename. The mnemonic is stale; the VALUE
# must not move. Two releases with different keys would take different locks
# and both consider themselves the only worker, which is exactly what this
# guards against during a rolling upgrade.
ADVISORY_LOCK_KEY = 0xB1_707A1

ROLE_ALL = "all"
ROLE_COORDINATOR = "coordinator"
ROLE_RUNNER = "runner"
ROLES = (ROLE_ALL, ROLE_COORDINATOR, ROLE_RUNNER)


class Command(BaseCommand):
    help = "Run the report-build worker (coordinator, runner, or both)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true",
            help="Run a single tick and exit (for tests/debugging).",
        )
        parser.add_argument(
            "--role", choices=ROLES, default=None,
            help=(
                "Which role to run. Defaults to TRELLUM_RUNNER_ROLE, or 'all' — "
                "one process doing both, which is the single-node topology."
            ),
        )

    def handle(self, *args, **opts):  # noqa: ARG002
        role = opts.get("role") or settings.TRELLUM_RUNNER_ROLE
        if role not in ROLES:
            raise CommandError(f"--role must be one of {ROLES!r}, got {role!r}")
        is_coordinator = role in (ROLE_ALL, ROLE_COORDINATOR)
        is_runner = role in (ROLE_ALL, ROLE_RUNNER)

        worker_id = f"{socket.gethostname()}-{os.getpid()}"
        shutdown = threading.Event()

        def _sigterm(_signum, _frame):
            logger.info("SIGTERM received: draining...")
            shutdown.set()

        signal.signal(signal.SIGTERM, _sigterm)
        signal.signal(signal.SIGINT, _sigterm)

        # Only the coordinator is a singleton. Runners must never take this
        # lock — that is precisely what used to cap the fleet at one process.
        if is_coordinator and not self._acquire_lock():
            if role == ROLE_ALL:
                # Someone else is already coordinating. Rather than idle, keep
                # the useful half alive: run as a pure runner and let the
                # existing coordinator schedule.
                logger.warning(
                    "another coordinator holds the lock; continuing as runner only"
                )
                is_coordinator = False
            else:
                # An explicit coordinator stays alive but inert so orchestrators
                # don't crash-loop it; the heartbeat table shows what happened.
                logger.warning(
                    "another coordinator holds the lock; standing by inert"
                )
                if opts["once"]:
                    return
                while not shutdown.is_set():
                    time.sleep(10)
                    if self._acquire_lock():
                        logger.info("lock acquired; taking over")
                        break
                if shutdown.is_set():
                    return

        # What we actually ended up doing, which is not always what was asked:
        # a combined worker demotes itself when another coordinator exists.
        effective_role = (
            ROLE_ALL if (is_coordinator and is_runner)
            else ROLE_COORDINATOR if is_coordinator
            else ROLE_RUNNER
        )

        executor = None
        if is_runner:
            executor = Executor(
                worker_id=worker_id,
                max_concurrent=settings.WORKER_MAX_CONCURRENT,
                default_timeout=settings.TRELLUM_RUN_TIMEOUT,
                memory_budget_mb=settings.TRELLUM_RUNNER_MEMORY_BUDGET_MB,
            )
            budget = (
                f"{executor.memory_budget_mb} MB"
                if executor.memory_budget_mb
                else "unbounded"
            )
            logger.info(
                f"{worker_id} starting as {effective_role} "
                f"(max_concurrent={executor.max_concurrent}, memory budget={budget})",
                extra={"worker_id": worker_id, "role": effective_role},
            )
        else:
            logger.info(f"{worker_id} starting as {effective_role}")

        # Only a runner spawns tenant builds, so only a runner needs a sandbox.
        # In docker mode a broken sandbox is fatal: we fail closed rather than
        # fall back to running tenant code loose in this container.
        if is_runner:
            from apps.runner.sandbox import DockerSandbox, SandboxError, sandbox_mode

            if sandbox_mode() == "docker":
                try:
                    DockerSandbox().preflight()
                    logger.info("docker sandbox ready")
                except SandboxError as exc:
                    logger.critical(str(exc))
                    raise SystemExit(1)
            elif not settings.DEBUG:
                logger.warning(
                    "TRELLUM_SANDBOX=off — tenant report code runs UNSANDBOXED in this "
                    "container. Set TRELLUM_SANDBOX=docker."
                )

        # Database-wide bookkeeping belongs to the single coordinator.
        if is_coordinator:
            self._recover_stale_runs()
            if settings.WORKER_CATCHUP:
                self._enqueue_catchup()
        # Sandboxes are this node's local disk, so every runner sweeps its own.
        if is_runner:
            self._sweep_orphan_sandboxes()

        scheduler = None
        if is_coordinator:
            scheduler = _SchedulerManager()
            scheduler.start()

        git_thread = None
        if is_runner and not opts["once"]:
            # Git sync writes into the studio checkout on THIS node's disk, so
            # it belongs to the runner, not the coordinator. Concurrent runners
            # are kept off the same studio by a per-studio advisory lock.
            # --once is a single diagnostic tick; a background poller has no
            # place in it.
            from apps.runner.gitsync import GitSyncThread

            git_thread = GitSyncThread()
            git_thread.start()

        # The heartbeat gets its own thread rather than a slot in the tick
        # loop: the tick blocks on real work — most notably publishing a
        # finished build to object storage, which can legitimately take
        # minutes — and a beat that waits for the tick reads as a dead worker
        # to the coordinator, which then marks this worker's live runs as
        # errors mid-publish. The heartbeat answers "is the process alive",
        # and that must not depend on how long the current tick takes.
        # (A truly wedged tick keeps beating; the per-run timeout is what
        # covers runaway builds.)
        # Register before claiming work so another worker cannot reap our runs.
        self._beat(worker_id, executor, role=effective_role)
        beat_thread = None
        if not opts["once"]:
            beat_thread = threading.Thread(
                target=self._beat_loop,
                args=(worker_id, executor, effective_role, shutdown),
                name="heartbeat",
                daemon=True,
            )
            beat_thread.start()

        last_reap = time.time()  # _recover_stale_runs already ran above
        try:
            while not shutdown.is_set():
                try:
                    # Recycling connections mid-transaction would abort it.
                    # The worker never holds one here; a test harness might.
                    if not connections["default"].in_atomic_block:
                        close_old_connections()
                    if executor is not None:
                        executor.tick()
                        self._claim_loop(executor)
                    if scheduler is not None:
                        scheduler.refresh_if_due()
                    # Boot-time reaping alone is not enough: a worker killed
                    # uncleanly keeps a live-looking heartbeat until it goes
                    # stale, so its runs must be reclaimed on a timer too.
                    now = time.time()
                    if is_coordinator and now - last_reap >= settings.WORKER_REAP_SECONDS:
                        self._recover_stale_runs(self_worker_id=worker_id)
                        last_reap = now
                except Exception as exc:  # noqa: BLE001 - the watchdog must live
                    logger.exception(f"{type(exc).__name__}: {exc}")
                    import traceback

                    traceback.print_exc()
                if opts["once"]:
                    break
                time.sleep(1)
        finally:
            if beat_thread is not None:
                beat_thread.join(timeout=5)
            if git_thread is not None:
                git_thread.stop()
            if scheduler is not None:
                scheduler.shutdown()
            if executor is not None and not opts["once"]:
                logger.info("draining running builds (max 540s)...")
                executor.drain(timeout=540)
            WorkerHeartbeat.objects.filter(worker_id=worker_id).delete()
            logger.info("stopped")

    # ── pieces ───────────────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """Session-scoped advisory lock on the default connection."""
        with connections["default"].cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_KEY])
            return bool(cur.fetchone()[0])

    def _recover_stale_runs(self, self_worker_id: str | None = None) -> None:
        """Reclaim in-flight runs whose worker is gone.

        A run is orphaned only when *its own* worker has stopped beating. The
        naive "everything active at boot is dead" version is correct with a
        single worker and catastrophic with several: restarting one runner
        would mark every other runner's live builds as errors.

        ``self_worker_id`` is never reaped: we are the ones calling, so we are
        alive by definition, even if a DB stall let our heartbeat go stale.
        """
        # Evaluate liveness in the UPDATE so newly registered workers are visible.
        orphaned = (
            Run.objects.filter(status__in=(Run.STARTING, Run.RUNNING))
            .exclude(worker_id__in=WorkerHeartbeat.alive().values("worker_id"))
        )
        if self_worker_id:
            orphaned = orphaned.exclude(worker_id=self_worker_id)
        n = orphaned.update(
            status=Run.ERROR,
            finished_at=timezone.now(),
            stderr_tail="worker restarted while this run was in flight",
        )
        if n:
            logger.info(f"marked {n} orphaned run(s) as error")

    def _sweep_orphan_sandboxes(self) -> None:
        import shutil
        from uuid import UUID

        root = _tmp_root()
        scratch_candidates = list(root.glob("run-*"))
        runs_dir = root / "runs"
        log_candidates = list(runs_dir.iterdir()) if runs_dir.is_dir() else []
        active_ids = {
            str(pk)
            for pk in Run.objects.filter(status__in=Run.ACTIVE_STATUSES).values_list(
                "pk", flat=True
            )
        }
        # Scratch paths have no persisted owner, so a live run may own any of them.
        if not active_ids:
            for entry in scratch_candidates:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
        for entry in log_candidates:
            if entry.is_dir() and entry.name not in active_ids:
                try:
                    UUID(entry.name)
                except ValueError:
                    continue
                shutil.rmtree(entry, ignore_errors=True)

        # Containers left behind by a crashed runner (their Run is no longer
        # active). Skips containers whose run is still ACTIVE — another runner
        # sharing this daemon may own them.
        from apps.runner.sandbox import DockerSandbox, SandboxError, sandbox_mode

        if sandbox_mode() == "docker":
            try:
                n = DockerSandbox().sweep_orphans()
                if n:
                    logger.info(f"swept {n} orphan container(s)")
            except SandboxError as exc:
                logger.info(f"orphan sweep skipped: {exc}")

    def _enqueue_catchup(self) -> None:
        """Enqueue reports whose last build didn't succeed (boot catch-up).

        Status comes through the storage layer, not this node's disk: on the
        s3 backend a fresh coordinator has an empty local output directory,
        and reading it directly would re-enqueue every report on every boot.
        An unreachable store skips catch-up entirely — an outage is not
        "never succeeded", and a rebuild storm is the wrong response to one.
        """
        from apps.core import storage
        from apps.reports.models import Report
        from apps.runner.services import enqueue

        n = 0
        for report in Report.objects.filter(present_in_scan=True, disabled=False).select_related(
            "studio", "studio__org"
        ):
            try:
                meta = storage.read_meta(report.studio, report.slug)
            except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
                logger.warning(
                    f"catch-up skipped, report storage unreachable: "
                    f"{type(exc).__name__}: {exc}"
                )
                return
            if meta.get("last_status") != "success":
                if enqueue(report, trigger="catchup") == "queued":
                    n += 1
        if n:
            logger.info(f"catch-up enqueued {n} report(s)")

    def _claim_loop(self, executor: Executor, pools=None) -> None:
        from apps.orgs import quotas
        from apps.runner.services import next_run_ids

        if pools is None:
            pools = settings.TRELLUM_RUNNER_POOLS

        while executor.has_capacity:
            candidates = next_run_ids(pools=pools)
            if not candidates:
                return

            claimed = False
            for run_id in candidates:
                if not executor.has_capacity:
                    return
                if self._try_claim(executor, run_id, quotas):
                    claimed = True
                    break
            if not claimed:
                # Every candidate was taken by another runner, blocked by a
                # quota, or too big for us right now. Waiting is correct.
                return

    def _try_claim(self, executor: Executor, run_id, quotas) -> bool:
        """Lock one queued run and start it. False means 'try the next one'."""
        with transaction.atomic():
            run = (
                Run.objects.select_for_update(skip_locked=True)
                .select_related("report", "studio", "studio__org")
                .filter(pk=run_id, status=Run.QUEUED, stop_requested=False)
                .first()
            )
            if run is None:
                return False  # a racing runner got there first

            memory_mb = executor.memory_for(run)

            # next_run_ids() already skips orgs at their concurrency cap, but
            # that selection is not inside this lock — re-check authoritatively.
            decision = quotas.check_run_allowed(run.studio.org, memory_mb=memory_mb)
            if not decision.allowed:
                if not decision.fatal:
                    return False  # clears itself; leave it queued
                run.status = Run.ERROR
                run.worker_id = executor.worker_id
                run.finished_at = timezone.now()
                run.stderr_tail = decision.reason
                run.save(
                    update_fields=["status", "worker_id", "finished_at", "stderr_tail"]
                )
                logger.warning(
                    f"claim rejected {run.studio}/{run.slug}: {decision.reason}",
                    extra={"run_id": str(run.pk), "slug": run.slug},
                )
                return False
            if executor.exceeds_budget(memory_mb):
                # No amount of waiting frees enough memory for this one.
                # Fail it loudly rather than leaving it queued forever.
                run.status = Run.ERROR
                run.worker_id = executor.worker_id
                run.finished_at = timezone.now()
                run.stderr_tail = (
                    f"builds get {memory_mb} MB (TRELLUM_DEFAULT_JOB_MEMORY_MB), "
                    f"which exceeds this runner's whole memory budget of "
                    f"{executor.memory_budget_mb} MB. Lower the default or raise "
                    f"TRELLUM_RUNNER_MEMORY_BUDGET_MB."
                )
                run.save(
                    update_fields=[
                        "status", "worker_id", "finished_at", "stderr_tail",
                    ]
                )
                logger.warning(
                    f"claim rejected {run.studio}/{run.slug}: {run.stderr_tail}",
                    extra={"run_id": str(run.pk), "slug": run.slug},
                )
                return False

            if not executor.can_admit(memory_mb):
                # Does not fit right now. Leave it queued: it is the fairest
                # next run, so waiting for room beats skipping past it to
                # something smaller and starving it indefinitely.
                return False

            run.status = Run.STARTING
            run.worker_id = executor.worker_id
            run.save(update_fields=["status", "worker_id"])
        # Spawn OUTSIDE the transaction: copytree + Popen can be slow.
        executor.start_run(run)
        return True

    def _beat_loop(
        self,
        worker_id: str,
        executor: Executor | None,
        role: str,
        shutdown: threading.Event,
    ) -> None:
        """Beat until shutdown. Runs on its own thread with its own DB
        connection (Django connections are thread-local); a failed beat is
        logged and retried next interval — the store of record for liveness
        must not take the worker down."""
        try:
            while not shutdown.is_set():
                try:
                    close_old_connections()
                    self._beat(worker_id, executor, role=role)
                except Exception as exc:  # noqa: BLE001 - keep beating
                    logger.warning(f"heartbeat: {type(exc).__name__}: {exc}")
                shutdown.wait(settings.WORKER_HEARTBEAT_SECONDS)
        finally:
            connections.close_all()

    def _beat(self, worker_id: str, executor: Executor | None, role: str = ROLE_ALL) -> None:
        from apps.runner.sandbox import sandbox_mode

        WorkerHeartbeat.objects.update_or_create(
            worker_id=worker_id,
            defaults={
                "last_beat_at": timezone.now(),
                "role": role,
                # A pure coordinator hosts no builds; report zero capacity so
                # the fleet view doesn't imply it can take work.
                "max_concurrent": executor.max_concurrent if executor else 0,
                "running_count": executor.running_count if executor else 0,
                "memory_budget_mb": executor.memory_budget_mb if executor else 0,
                "reserved_memory_mb": executor.reserved_memory_mb if executor else 0,
                # Only a runner isolates builds; a pure coordinator reports "".
                "sandbox_mode": sandbox_mode() if executor else "",
            },
        )
        # Prune heartbeats of long-gone workers.
        WorkerHeartbeat.objects.filter(
            last_beat_at__lt=timezone.now() - timezone.timedelta(minutes=10)
        ).exclude(worker_id=worker_id).delete()


class _SchedulerManager:
    """APScheduler wrapper that keeps cron jobs in sync with Report rows,
    EmailSchedule rows and schedule-triggered AlertRule rows.

    Rebuilt by diffing every 60 s — no cross-process signalling needed when
    a git sync, an import, or a user editing their delivery cadence changes
    a schedule.
    """

    REFRESH_SECONDS = 60

    def __init__(self):
        self._scheduler = None
        self._last_refresh = 0.0

    def start(self) -> None:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(daemon=True)

        def _on_event(event):
            if event.code == EVENT_JOB_ERROR:
                logger.error(f"{event.job_id}: {event.exception!r}")
            elif event.code == EVENT_JOB_MISSED:
                logger.warning(f"{event.job_id} at {event.scheduled_run_time}")

        self._scheduler.add_listener(_on_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
        self._scheduler.start()
        self._schedule_cleanup()
        self._schedule_update_check()
        self.refresh()

    def _schedule_cleanup(self) -> None:
        """Nightly retention, on the one process that is guaranteed singular.

        The coordinator already holds a Postgres advisory lock, so it is the
        instance-wide cron host by construction — no new service, no broker, and
        no risk of two boxes purging at once. Registered once at start rather
        than in refresh(), which diffs against Report rows.
        """
        from apscheduler.triggers.cron import CronTrigger

        if not settings.CLEANUP_ENABLED:
            logger.info("retention cleanup disabled (CLEANUP_ENABLED=false)")
            return

        try:
            trigger = CronTrigger.from_crontab(settings.CLEANUP_CRON)
        except ValueError as exc:
            logger.error(f"CLEANUP_CRON {settings.CLEANUP_CRON!r}: {exc}")
            return

        self._scheduler.add_job(
            _run_cleanup,
            trigger=trigger,
            id="retention-cleanup",
            replace_existing=True,
            # A missed nightly cleanup should still run when the box comes back;
            # skipping it means another day of growth.
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        logger.info(f"retention cleanup at {settings.CLEANUP_CRON}")

    def _schedule_update_check(self) -> None:
        """Once at start and then daily: which release is newest (internal planning ticket #066).

        Opt-in, because an outbound call is an audit finding on an air-gapped
        install. Same singular host as cleanup, for the same reason.
        """
        if not settings.TRELLUM_UPDATE_CHECK:
            return
        from apscheduler.triggers.interval import IntervalTrigger

        self._scheduler.add_job(
            _run_update_check,
            trigger=IntervalTrigger(hours=24),
            next_run_time=timezone.now(),
            id="update-check",
            replace_existing=True,
            # As for cleanup: a coordinator that was down at the hour still
            # asks when it is back, instead of waiting another day.
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        logger.info("release check on, once a day")

    def refresh_if_due(self) -> None:
        if time.time() - self._last_refresh >= self.REFRESH_SECONDS:
            self.refresh()

    def refresh(self) -> None:
        from apscheduler.triggers.cron import CronTrigger
        from zoneinfo import ZoneInfo

        from apps.alerts.models import AlertRule
        from apps.reports.models import EmailSchedule, Report
        from apps.reports.notify import flush_broken_buffers

        self._last_refresh = time.time()
        if self._scheduler is None:
            return

        # Piggyback broken-build coalescing on this same ~60s cadence: any
        # studio's buffered broken-transitions whose coalescing window has
        # elapsed (see apps.reports.notify.COALESCE_WINDOW_SECONDS) get
        # flushed here rather than owning a separate timer.
        flush_broken_buffers()

        desired: dict[str, tuple[int, str, str]] = {}
        rows = Report.objects.filter(present_in_scan=True, disabled=False, kind=Report.KIND_REPORT).exclude(
            schedule_cron=""
        )
        for r in rows:
            if len(r.schedule_cron.split()) >= 5:
                desired[f"report-{r.pk}"] = (r.pk, r.schedule_cron, r.schedule_timezone)

        # Personal delivery cadences ride the same diffed-every-60s scheduler
        # as the build cron above — one less mechanism to keep symmetrical.
        email_rows = EmailSchedule.objects.filter(enabled=True).select_related("report")
        for s in email_rows:
            if s.report.present_in_scan and not s.report.disabled:
                desired[f"email-{s.pk}"] = (s.pk, s, s.timezone)

        # Schedule-triggered alert rules (apps.alerts): same cadence fields,
        # same trigger builder, a different job target.
        alert_rows = AlertRule.objects.filter(
            enabled=True, trigger=AlertRule.TRIGGER_SCHEDULE, report__kind=Report.KIND_REPORT
        ).select_related("report")
        for a in alert_rows:
            if a.report.present_in_scan and not a.report.disabled:
                desired[f"alert-{a.pk}"] = (a.pk, a, a.timezone)

        current = {job.id: job for job in self._scheduler.get_jobs()}
        for job_id in current:
            if job_id not in desired:
                self._scheduler.remove_job(job_id)
        for job_id, (obj_id, cron_or_schedule, tz_name) in desired.items():
            try:
                tz = ZoneInfo(tz_name or "UTC")
            except Exception:
                # A garbage timezone must never crash the coordinator's sync
                # loop — it would take every OTHER report/schedule's job down
                # with it. EmailSchedule.clean() and the create/update
                # endpoints reject this before it can be saved through the
                # UI, so reaching this branch means a row was written some
                # other way (direct ORM, a fixture, a future migration).
                logger.info(
                    f"invalid timezone {tz_name!r} for job {job_id}; falling back to UTC"
                )
                tz = ZoneInfo("UTC")
            if job_id.startswith("report-"):
                try:
                    trigger = CronTrigger.from_crontab(cron_or_schedule.strip(), timezone=tz)
                except ValueError:
                    logger.info(f"invalid cron for report {obj_id}: {cron_or_schedule!r}")
                    continue
                self._scheduler.add_job(
                    _enqueue_scheduled,
                    trigger=trigger,
                    args=[obj_id],
                    id=job_id,
                    replace_existing=True,
                    # Catch up missed triggers within 5 min so a brief worker
                    # stall doesn't silently drop a daily run.
                    misfire_grace_time=300,
                )
            else:
                trigger = _email_schedule_trigger(cron_or_schedule, tz)
                if trigger is None:
                    logger.info(f"invalid cadence for {job_id}")
                    continue
                self._scheduler.add_job(
                    _evaluate_alert if job_id.startswith("alert-") else _send_scheduled_email,
                    trigger=trigger,
                    args=[obj_id],
                    id=job_id,
                    replace_existing=True,
                    misfire_grace_time=300,
                )

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None


def _run_cleanup() -> None:
    """APScheduler job target: apply the retention policy.

    Errors are swallowed after logging — a failing cleanup must never take the
    coordinator down with it, because the coordinator is also what fires every
    report schedule on the instance. The `retention` health check is what
    notices that cleanup has stopped.
    """
    close_old_connections()
    from django.core.management import call_command

    try:
        call_command("cleanup")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"cleanup failed: {exc!r}")


def _run_update_check() -> None:
    """APScheduler job target: refresh the recorded releases.

    A feed that does not answer is silence by design -- the row stays as it
    was and nothing is logged. Only a programming error reaches the log, and
    like cleanup it must never take the coordinator down.
    """
    close_old_connections()
    from apps.core import releases

    try:
        releases.refresh()
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"release check failed: {exc!r}")


def _enqueue_scheduled(report_id: int) -> None:
    """APScheduler job target (runs on a scheduler thread)."""
    close_old_connections()
    from apps.reports.models import Report
    from apps.runner.services import enqueue

    report = (
        Report.objects.filter(pk=report_id, present_in_scan=True, disabled=False, kind=Report.KIND_REPORT)
        .select_related("studio", "studio__org")
        .first()
    )
    if report is not None:
        enqueue(report, trigger="schedule")


def _email_schedule_trigger(schedule, tz):
    """CronTrigger for one EmailSchedule's cadence, or None if it's out of
    range (defensive: EmailSchedule.clean() should have caught this already,
    but a row written straight through the ORM skips model validation)."""
    from apscheduler.triggers.cron import CronTrigger

    from apps.reports.models import EmailSchedule

    hour, minute = schedule.send_hour, schedule.send_minute
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if schedule.freq == "hourly":  # AlertRule.FREQ_HOURLY; no EmailSchedule has it
        return CronTrigger(minute=minute, timezone=tz)
    if schedule.freq == EmailSchedule.FREQ_DAILY:
        return CronTrigger(hour=hour, minute=minute, timezone=tz)
    if schedule.freq == EmailSchedule.FREQ_WEEKDAYS:
        return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz)
    if schedule.freq == EmailSchedule.FREQ_WEEKLY:
        if not 0 <= schedule.weekday <= 6:
            return None
        return CronTrigger(day_of_week=schedule.weekday, hour=hour, minute=minute, timezone=tz)
    if schedule.freq == EmailSchedule.FREQ_MONTHLY:
        if not 1 <= schedule.month_day <= 28:
            return None
        return CronTrigger(day=schedule.month_day, hour=hour, minute=minute, timezone=tz)
    return None


def _send_scheduled_email(schedule_id: int) -> None:
    """APScheduler job target (runs on a scheduler thread)."""
    close_old_connections()
    from apps.reports.notify import send_scheduled

    send_scheduled(schedule_id)


def _evaluate_alert(rule_id: int) -> None:
    """APScheduler job target (runs on a scheduler thread)."""
    close_old_connections()
    from apps.alerts.evaluator import evaluate
    from apps.alerts.models import AlertRule

    try:
        rule = (
            AlertRule.objects.filter(
                pk=rule_id, enabled=True, report__present_in_scan=True, report__disabled=False,
                report__kind="report",
            )
            .select_related("report", "studio", "org", "created_by")
            .first()
        )
        if rule is not None:  # deleted/disabled between the job firing and now
            evaluate(rule)
    except Exception as exc:  # noqa: BLE001 - must never wedge the scheduler thread
        logger.exception(f"alert {rule_id} evaluation failed: {exc!r}")
