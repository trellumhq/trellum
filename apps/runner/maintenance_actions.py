"""Durable studio maintenance actions: admission, claiming, leases, outcome.

Scope is deliberately narrow (see ``StudioMaintenanceAction``): only
``registry_refresh`` has a worker today. The web request admits a row and
returns; a runner thread executes it on the shared studio filesystem.

Lease rules
-----------
* A claimed action holds ``lease_token`` + ``lease_expires_at``; the executing
  thread renews while it works. Publishing the outcome is fenced by action id,
  token and ``state=running``, so a stale executor can never overwrite a
  recovered row.
* An expired lease is recorded as ``unconfirmed`` and never re-run: the scan
  may have finished, half-finished, or still be running elsewhere. A user may
  start a fresh refresh, which is a new, visible action. Registry refresh is an
  idempotent upsert, so unconfirmed does not block new admission.
* A never-claimed (``accepted``) action stays claimable across restarts.
* Lock conflict with Git sync is a terminal ``failed`` with a stable message;
  the user can retry. We never scan while Git is copying.

Idempotency keys are honoured for ``KEY_RETENTION``; older terminal rows lose
their key so it can be reused.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone

from apps.runner.models import StudioMaintenanceAction as Action

logger = logging.getLogger("trellum.worker")

ADMISSION_LOCK_NAMESPACE = 0x4D41494E  # one short global lock; admission is tiny
KEY_RETENTION = timedelta(hours=24)
MAX_KEY_LENGTH = 80

# Public, fixed messages: never paths or exception text.
MSG_ACCEPTED = "Registry refresh scheduled"
MSG_SUCCESS = "Registry refreshed"
MSG_WARNING = "Registry refreshed with a warning"
MSG_FAILED = "Registry refresh failed"
MSG_LOCK = "A repository sync is in progress; try the refresh again when it finishes."
MSG_UNCONFIRMED = (
    "The worker stopped before confirming the result. Check the registry and "
    "refresh again if needed."
)


def _limit(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def lease_seconds() -> int:
    return _limit("TRELLUM_MAINT_LEASE_SECONDS", 120)


class PendingLimitReached(Exception):
    def __init__(self, scope: str, retry_after: int = 30):
        super().__init__(scope)
        self.scope = scope
        self.retry_after = retry_after


def valid_key(key: str) -> bool:
    return (
        len(key) <= MAX_KEY_LENGTH
        and all(c.isalnum() or c in "-_.:" for c in key)
    )


def admit(*, studio, user, kind=Action.REGISTRY_REFRESH, idempotency_key: str = ""):
    """Durably admit an action. Returns ``(action, created)``.

    One short transaction under a global advisory lock makes the idempotency
    lookup, the one-active-per-studio-kind rule and both pending limits atomic
    across web processes; the partial unique indexes are the backstop.
    """
    actor = user if getattr(user, "is_authenticated", False) else None
    now = timezone.now()
    try:
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(%s, 0)", [ADMISSION_LOCK_NAMESPACE]
                    )
            if idempotency_key and actor is not None:
                scope = Action.objects.filter(
                    requested_by=actor, studio=studio, kind=kind,
                    idempotency_key=idempotency_key,
                )
                existing = scope.first()
                if existing is not None:
                    if existing.is_terminal and existing.accepted_at < now - KEY_RETENTION:
                        scope.update(idempotency_key="")  # expired: key is reusable
                    else:
                        return existing, False
            active = Action.objects.filter(
                studio=studio, kind=kind, state__in=Action.ACTIVE_STATES
            ).first()
            if active is not None:
                return active, False
            pending = Action.objects.filter(state__in=Action.ACTIVE_STATES)
            if pending.filter(org_id=studio.org_id).count() >= _limit(
                "TRELLUM_MAINT_ORG_PENDING_LIMIT", 10
            ):
                raise PendingLimitReached("org")
            if pending.count() >= _limit("TRELLUM_MAINT_FLEET_PENDING_LIMIT", 100):
                raise PendingLimitReached("fleet")
            action = Action.objects.create(
                studio=studio, org_id=studio.org_id, kind=kind, requested_by=actor,
                idempotency_key=idempotency_key if actor is not None else "",
                message=MSG_ACCEPTED, accepted_at=now,
            )
            return action, True
    except IntegrityError:
        # A constraint beat the lookups (non-PostgreSQL, or a bug): resolve to
        # the winner rather than failing the user.
        winner = Action.objects.filter(
            studio=studio, kind=kind, state__in=Action.ACTIVE_STATES
        ).first()
        if winner is None and idempotency_key and actor is not None:
            winner = Action.objects.filter(
                requested_by=actor, studio=studio, kind=kind, idempotency_key=idempotency_key
            ).first()
        if winner is None:
            raise
        return winner, False


# ── worker side ───────────────────────────────────────────────────────────

def _audit_outcome(action: Action, state: str, **extra) -> None:
    from apps.core.audit import audit_system

    audit_system(
        "registry.refresh_finished",
        org=action.org,
        outcome="failure" if state in (Action.FAILED, Action.UNCONFIRMED) else "success",
        action_id=str(action.pk), studio=action.studio.slug, state=state, **extra,
    )


def recover_expired(now=None) -> int:
    """Mark running actions whose lease lapsed as ``unconfirmed`` (never re-run)."""
    now = now or timezone.now()
    done = []
    with transaction.atomic():
        rows = list(
            Action.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(state=Action.RUNNING, lease_expires_at__lt=now)
            .select_related("studio", "org")
        )
        for action in rows:
            action.state = Action.UNCONFIRMED
            action.message = MSG_UNCONFIRMED
            action.finished_at = now
            action.lease_token = None
            action.lease_expires_at = None
            action.save(update_fields=[
                "state", "message", "finished_at", "lease_token", "lease_expires_at",
            ])
            done.append(action)
    for action in done:
        _audit_outcome(action, Action.UNCONFIRMED)
    return len(done)


def claim_one(worker_id: str, pools=None):
    """Claim at most one accepted action; ``SKIP LOCKED`` keeps runners apart."""
    with transaction.atomic():
        qs = Action.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            state=Action.ACCEPTED, kind=Action.REGISTRY_REFRESH
        )
        if pools:
            qs = qs.filter(studio__pool__in=pools)
        action = qs.select_related("studio", "org").order_by("accepted_at", "pk").first()
        if action is None:
            return None
        now = timezone.now()
        action.state = Action.RUNNING
        action.started_at = now
        action.worker_id = worker_id[:100]
        action.lease_token = uuid.uuid4()
        action.lease_expires_at = now + timedelta(seconds=lease_seconds())
        action.message = "Registry refresh running"
        action.save(update_fields=[
            "state", "started_at", "worker_id", "lease_token", "lease_expires_at", "message",
        ])
        return action


def _renew(action: Action) -> bool:
    return bool(
        Action.objects.filter(
            pk=action.pk, lease_token=action.lease_token, state=Action.RUNNING
        ).update(lease_expires_at=timezone.now() + timedelta(seconds=lease_seconds()))
    )


def _finish(action: Action, state: str, message: str, **fields) -> bool:
    """Publish the outcome, fenced by action id + lease token + running state."""
    changed = Action.objects.filter(
        pk=action.pk, lease_token=action.lease_token, state=Action.RUNNING
    ).update(
        state=state, message=message, finished_at=timezone.now(),
        lease_token=None, lease_expires_at=None, **fields,
    )
    if not changed:
        logger.warning("maintenance action %s lost its lease; outcome discarded", action.pk)
        return False
    _audit_outcome(action, state, reports=fields.get("reports"), warned=bool(fields.get("warning")))
    return True


def execute(action: Action, *, renew_every: float | None = None) -> str | None:
    """Run a claimed registry refresh under ``studio_sync_lock``.

    Returns the final state, or ``None`` if the lease was lost and the outcome
    was therefore not published.
    """
    from apps.reports.models import Report
    from apps.reports.scan import ReportQuotaExceeded, sync_studio_registry
    from apps.runner.gitsync import studio_sync_lock

    stop = threading.Event()
    interval = renew_every if renew_every is not None else max(lease_seconds() / 3, 1)

    def renewer():
        # Only a lost fence (the row is no longer ours) ends the loop. A
        # transient database error must not silently stop renewing, or a scan
        # that finishes fine would be recovered as ``unconfirmed``.
        try:
            while not stop.wait(interval):
                try:
                    if not _renew(action):
                        return
                except Exception:  # noqa: BLE001 - retry on the next interval
                    logger.warning(
                        "maintenance lease renewal failed; retrying",
                        exc_info=True, extra={"action_id": str(action.pk)},
                    )
                    connection.close()  # a broken connection reconnects next time
        finally:
            connection.close()

    thread = threading.Thread(target=renewer, name="maintenance-lease", daemon=True)
    thread.start()
    try:
        if not _renew(action):
            return None
        with studio_sync_lock(action.studio_id) as acquired:
            if not acquired:
                return Action.FAILED if _finish(action, Action.FAILED, MSG_LOCK) else None
            try:
                count = sync_studio_registry(action.studio)
                warning = ""
            except ReportQuotaExceeded as exc:
                # Reports that fit are registered: terminal success + warning.
                warning = str(exc)[:300]
                count = Report.objects.filter(
                    studio=action.studio, present_in_scan=True
                ).count()
            except Exception:  # noqa: BLE001 - public outcome is fixed text
                logger.exception("registry refresh failed", extra={"action_id": str(action.pk)})
                return Action.FAILED if _finish(action, Action.FAILED, MSG_FAILED) else None
        state = Action.SUCCEEDED
        ok = _finish(
            action, state, MSG_WARNING if warning else MSG_SUCCESS,
            reports=count, warning=warning,
        )
        return state if ok else None
    finally:
        stop.set()
        thread.join(timeout=5)


def run_once(worker_id: str, pools=None) -> bool:
    """One maintenance tick: recover expired leases, then claim and run one."""
    recover_expired()
    action = claim_one(worker_id, pools)
    if action is None:
        return False
    execute(action)
    return True


class MaintenanceThread(threading.Thread):
    """Dedicated slot so scans never occupy the report-build claim loop."""

    def __init__(self, worker_id: str, pools):
        super().__init__(name="maintenance-actions", daemon=True)
        self.worker_id = worker_id
        self.pools = list(pools or [])
        self.shutdown = threading.Event()

    def stop(self):
        self.shutdown.set()
        self.join(timeout=10)

    def run(self):
        while not self.shutdown.is_set():
            close_old_connections()
            worked = False
            try:
                worked = run_once(self.worker_id, self.pools)
            except Exception:  # noqa: BLE001 - the loop must live
                logger.exception("maintenance action tick failed")
            finally:
                close_old_connections()
            if not worked:
                self.shutdown.wait(2)


def start_for_worker(*, is_runner, pools, worker_id):
    if not is_runner:
        return None  # a coordinator-only process must not touch the checkout
    thread = MaintenanceThread(worker_id, pools)
    thread.start()
    return thread
