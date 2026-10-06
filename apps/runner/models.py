"""Run queue + history. The Run table IS the job queue: the web process
inserts ``queued`` rows, the single worker claims them with
``select_for_update(skip_locked=True)`` and drives them to a terminal state.
History is durable — it survives restarts, unlike the legacy in-memory dicts.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Run(models.Model):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    STOPPED = "stopped"
    TIMEOUT = "timeout"
    OOM_KILLED = "oom_killed"
    STATUS_CHOICES = [
        (QUEUED, "Queued"), (STARTING, "Starting"), (RUNNING, "Running"),
        (SUCCESS, "Success"), (ERROR, "Error"), (STOPPED, "Stopped"),
        (TIMEOUT, "Timed out"), (OOM_KILLED, "OOM killed"),
    ]
    ACTIVE_STATUSES = (QUEUED, STARTING, RUNNING)
    CACHE_MODES = [
        ("normal", "Normal"), ("fresh", "Fresh (no cache)"),
        ("force", "Force cache"), ("debug", "Debug"), ("debug-fresh", "Debug + fresh"),
    ]
    TRIGGERS = [
        ("manual", "Manual"), ("schedule", "Schedule"),
        ("git", "Git push"), ("catchup", "Boot catch-up"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey("reports.Report", on_delete=models.PROTECT, related_name="runs")
    studio = models.ForeignKey("studios.Studio", on_delete=models.CASCADE, related_name="runs")
    slug = models.CharField(max_length=200)  # denormalized; survives report renames
    #: Which runner pool may claim this. Copied from the studio at enqueue so
    #: moving a studio between pools never strands an already-queued run.
    pool = models.CharField(max_length=32, default="standard")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    cache_mode = models.CharField(max_length=16, choices=CACHE_MODES, default="normal")
    priority = models.IntegerField(default=0)
    trigger = models.CharField(max_length=16, choices=TRIGGERS, default="manual")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    stop_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    stdout_tail = models.TextField(blank=True, default="")
    stderr_tail = models.TextField(blank=True, default="")
    pid = models.IntegerField(null=True, blank=True)
    #: Sandbox container id for docker-mode runs (empty for in-process runs).
    #: Lets the worker reattach/kill/sweep the right container after a restart.
    container_id = models.CharField(max_length=80, blank=True, default="")
    worker_id = models.CharField(max_length=100, blank=True, default="")
    log_dir = models.CharField(max_length=400, blank=True, default="")
    #: Memory budget this run was admitted against and capped at (MB): the
    #: runner's TRELLUM_DEFAULT_JOB_MEMORY_MB when it started, recorded so a
    #: later change to the setting never rewrites what a past run was allowed.
    memory_limit_mb = models.PositiveIntegerField(default=0)
    #: Observed peak RSS of the build subprocess (MB), when the platform can
    #: report it. Feeds capacity planning and right-sizing advice.
    peak_memory_mb = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "-priority", "created_at"]),
            models.Index(fields=["studio", "slug", "-created_at"]),
            # Fair claiming groups queued work by pool before choosing an org.
            models.Index(fields=["status", "pool", "created_at"]),
            # Per-report history + aggregates (drawer stats, api_report_status).
            models.Index(fields=["report", "-created_at"], name="run_report_created_idx"),
        ]
        ordering = ["-created_at"]

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 1)
        return None

    @property
    def queue_wait_seconds(self) -> float | None:
        if self.created_at and self.started_at:
            return round((self.started_at - self.created_at).total_seconds(), 1)
        return None

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    def to_history_dict(self, include_output: bool = False) -> dict:
        """Legacy RunRecord shape (portal.js renders these fields)."""
        d = {
            "slug": self.slug,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "queue_wait_seconds": self.queue_wait_seconds,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "exit_code": self.exit_code,
            "trigger": self.trigger,
            "requested_by": self.requested_by.email if self.requested_by else None,
            "memory_limit_mb": self.memory_limit_mb,
            "peak_memory_mb": self.peak_memory_mb,
        }
        if include_output:
            d["stdout"] = self.stdout_tail
            d["stderr"] = self.stderr_tail
        return d

    def __str__(self) -> str:
        return f"{self.studio}/{self.slug} [{self.status}]"


class WorkerHeartbeat(models.Model):
    ROLE_ALL = "all"
    ROLE_COORDINATOR = "coordinator"
    ROLE_RUNNER = "runner"
    ROLE_CHOICES = [
        (ROLE_ALL, "Coordinator + runner"),
        (ROLE_COORDINATOR, "Coordinator"),
        (ROLE_RUNNER, "Runner"),
    ]

    worker_id = models.CharField(max_length=100, unique=True)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_ALL)
    started_at = models.DateTimeField(default=timezone.now)
    last_beat_at = models.DateTimeField(default=timezone.now)
    max_concurrent = models.IntegerField(default=3)
    running_count = models.IntegerField(default=0)
    memory_budget_mb = models.PositiveIntegerField(default=0)
    reserved_memory_mb = models.PositiveIntegerField(default=0)
    #: How this runner isolates builds ("docker" | "off" | ""). Surfaced in the
    #: fleet view so an operator can see at a glance that prod is sandboxing.
    sandbox_mode = models.CharField(max_length=16, blank=True, default="")

    @classmethod
    def alive(cls, stale_seconds: int = 60):
        cutoff = timezone.now() - timezone.timedelta(seconds=stale_seconds)
        return cls.objects.filter(last_beat_at__gte=cutoff)

    def __str__(self) -> str:
        return f"worker {self.worker_id} @ {self.last_beat_at:%H:%M:%S}"
