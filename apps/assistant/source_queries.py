"""Durable source reads: transactional admission, fenced execution and delivery.

External execution belongs exclusively to a dedicated worker slot. Expired
running work is never retried. Its capacity remains reserved for a further
bounded connection/statement deadline, even after recovery records uncertainty.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import Q
from django.utils import timezone

from apps.assistant.models import SourceQuery as Job
from apps.assistant.source_access import DENIED, eligible_context, policy_fingerprint, resolve_grant

ACCEPTED_TTL = timedelta(minutes=5)
KEY_RETENTION = timedelta(hours=24)
LEASE_SECONDS = 30
# Includes bounded DNS/connection setup, server statement timeout (10s), and
# cancellation/transport margin. Recovery holds capacity beyond the lost lease.
UNCERTAINTY_HOLD = timedelta(seconds=30)
ADMISSION_LOCK_NAMESPACE = 0x53524351
MAX_SQL_BYTES = 16384
MAX_RESULT_BYTES = 65536
RECOVERY_BATCH = 100
MSG_EXPIRED = "No worker picked up the source read in time."
MSG_UNCONFIRMED = "The worker stopped before confirming the source read outcome."
MSG_FAILED = "The source read could not be completed."
MSG_REVOKED = "Source read access changed; the result is unavailable."


class QueryAdmissionError(ValueError):
    """A safe, fixed-message invalid request or idempotency conflict."""


class PendingLimitReached(Exception):
    def __init__(self, scope):
        self.scope = scope
        self.retry_after = 5
        super().__init__("Source query capacity is currently occupied.")


def _executor():
    # The framework remains independent of Django and owns SQL parsing/IO.
    from trellum.data import assistant_query
    return assistant_query


def _audit(job, event):
    from apps.core.audit import audit_system

    audit_system(
        "assistant.source_" + event, org=job.org,
        outcome="failure" if job.state in (Job.FAILED, Job.UNCONFIRMED) else "success",
        query_id=str(job.pk), user_id=job.user_id, studio_id=job.studio_id,
        grant_id=job.grant_id, kind=job.kind, state=job.state,
    )


def _limit(name, default):
    return max(1, min(int(getattr(settings, name, default)), 1024))


def _occupied(now):
    return Job.objects.filter(
        Q(state__in=Job.ACTIVE_STATES)
        | Q(state=Job.UNCONFIRMED, lease_expires_at__gt=now)
    )


def recover_expired(now=None):
    """Bound each tick, skip locked claims and never rerun uncertain work."""
    now = now or timezone.now()
    completed = []
    with transaction.atomic():
        rows = (Job.objects.select_for_update(skip_locked=True, of=("self",))
                .filter(Q(state=Job.ACCEPTED, accepted_at__lt=now - ACCEPTED_TTL)
                        | Q(state=Job.RUNNING, lease_expires_at__lte=now))
                .select_related("org").order_by("accepted_at")[:RECOVERY_BATCH])
        for job in rows:
            if job.state == Job.ACCEPTED:
                job.state, job.message = Job.FAILED, MSG_EXPIRED
            else:
                job.state, job.message = Job.UNCONFIRMED, MSG_UNCONFIRMED
                job.lease_expires_at = max(now, job.lease_expires_at) + UNCERTAINTY_HOLD
            job.finished_at, job.lease_token, job.result = now, None, {}
            job.save(update_fields=["state", "message", "finished_at", "lease_token", "lease_expires_at", "result"])
            completed.append(job)
        # Bounded key retention cleanup, independent of transcript retention.
        old = list(Job.objects.filter(accepted_at__lt=now - KEY_RETENTION)
                   .exclude(state__in=Job.ACTIVE_STATES).exclude(idempotency_key="")
                   .order_by("accepted_at").values_list("pk", flat=True)[:RECOVERY_BATCH])
        Job.objects.filter(pk__in=old).update(idempotency_key="")
    for job in completed:
        _audit(job, "finished")
    return len(completed)


def admit(*, studio, actor, session, grant_id, sql="", kind="query", idempotency_key):
    """Return (job, created), never perform external IO on the request thread."""
    if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 80
            or any(not (c.isascii() and (c.isalnum() or c in "-_.:")) for c in idempotency_key)):
        raise QueryAdmissionError("Provide a valid source query request key.")
    if kind not in (Job.QUERY, Job.DESCRIBE) or not isinstance(sql, str) or len(sql.encode()) > MAX_SQL_BYTES:
        raise QueryAdmissionError("The source query request is invalid or too large.")
    if kind == Job.DESCRIBE and sql:
        raise QueryAdmissionError("Source description does not accept SQL.")
    with transaction.atomic():
        if connection.vendor != "postgresql":
            raise QueryAdmissionError("Source query admission requires PostgreSQL.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, 0)", [ADMISSION_LOCK_NAMESPACE])
        recover_expired()
        current, user, conversation = eligible_context(studio, actor, session=session)
        grant = resolve_grant(current, user, grant_id, session=conversation)
        normalized = _executor().validate_query(sql, grant.relations) if kind == Job.QUERY else ""
        if len(normalized.encode()) > MAX_SQL_BYTES:
            raise QueryAdmissionError("The source query request is invalid or too large.")
        fingerprint = policy_fingerprint(grant)
        now = timezone.now()
        scope = Job.objects.filter(user=user, session=conversation, idempotency_key=idempotency_key)
        existing = scope.first()
        if existing is not None:
            if existing.state not in Job.ACTIVE_STATES and existing.accepted_at < now - KEY_RETENTION:
                scope.update(idempotency_key="")
            else:
                if (existing.grant_id != grant.pk or existing.kind != kind
                        or existing.sql != normalized or existing.policy_fingerprint != fingerprint):
                    raise QueryAdmissionError("This request key was already used for another source read.")
                return existing, False
        occupied = _occupied(now)
        if occupied.filter(session=conversation).exists():
            raise PendingLimitReached("session")
        if occupied.filter(org_id=current.org_id).count() >= _limit("TRELLUM_SOURCE_QUERY_ORG_PENDING_LIMIT", 4):
            raise PendingLimitReached("org")
        if occupied.count() >= _limit("TRELLUM_SOURCE_QUERY_FLEET_PENDING_LIMIT", 32):
            raise PendingLimitReached("fleet")
        job = Job.objects.create(
            org_id=current.org_id, studio=current, user=user, session=conversation,
            grant=grant, kind=kind, sql=normalized, policy_fingerprint=fingerprint,
            idempotency_key=idempotency_key, message="Source read scheduled.",
        )
        _audit(job, "accepted")
        return job, True


def _authorized_job(job, *, actor, studio, session):
    """Never deliver fields from the caller's possibly stale model instance."""
    current, user, conversation = eligible_context(studio, actor, session=session)
    try:
        fresh = Job.objects.select_related("studio__org", "user", "session", "org").get(
            pk=getattr(job, "pk", job), user=user, studio=current, session=conversation, org_id=current.org_id,
        )
    except (Job.DoesNotExist, ValueError, TypeError, ValidationError):
        raise PermissionDenied(DENIED) from None
    grant = resolve_grant(current, user, fresh.grant_id, session=conversation)
    if policy_fingerprint(grant) != fresh.policy_fingerprint:
        raise PermissionDenied(MSG_REVOKED)
    return fresh


def public_result(job, *, actor, studio, session):
    recover_expired()
    fresh = _authorized_job(job, actor=actor, studio=studio, session=session)
    payload = {"id": str(fresh.pk), "state": fresh.state, "kind": fresh.kind,
               "message": fresh.message, "cancel_requested": fresh.cancel_requested}
    if fresh.state == Job.SUCCEEDED:
        payload["result"] = fresh.result
    return payload


def request_cancel(job, *, actor, studio, session):
    fresh = _authorized_job(job, actor=actor, studio=studio, session=session)
    # Row locking serializes cancellation with result publication.
    with transaction.atomic():
        fresh = Job.objects.select_for_update().select_related("org").get(pk=fresh.pk)
        if fresh.state == Job.ACCEPTED:
            fresh.state, fresh.finished_at = Job.CANCELLED, timezone.now()
            fresh.message = "Source read cancelled before execution."
            fresh.cancel_requested = True
            fresh.save(update_fields=["state", "finished_at", "message", "cancel_requested"])
            _audit(fresh, "finished")
        elif fresh.state == Job.RUNNING:
            fresh.cancel_requested = True
            fresh.save(update_fields=["cancel_requested"])
    return public_result(fresh, actor=actor, studio=studio, session=session)


def _current_grant(job):
    grant = resolve_grant(job.studio, job.user, job.grant_id, session=job.session)
    if policy_fingerprint(grant) != job.policy_fingerprint:
        raise PermissionDenied(MSG_REVOKED)
    return grant


def _finish(job, state, message, result=None):
    with transaction.atomic():
        fresh = Job.objects.select_for_update().filter(pk=job.pk, state=Job.RUNNING, lease_token=job.lease_token).first()
        if fresh is None or fresh.lease_expires_at <= timezone.now():
            return False
        if state == Job.SUCCEEDED:
            try:
                _current_grant(job)
            except PermissionDenied:
                state, message, result = Job.FAILED, MSG_REVOKED, {}
            if fresh.cancel_requested:
                # Successful execution confirms that remote SQL finished. A late
                # cancel can safely discard its output without claiming a kill.
                state, message, result = Job.CANCELLED, "Source read finished; its result was discarded.", {}
        fresh.state, fresh.message, fresh.result = state, message, result or {}
        fresh.finished_at, fresh.lease_token = timezone.now(), None
        if state != Job.UNCONFIRMED:
            fresh.lease_expires_at = None
        else:
            fresh.lease_expires_at = timezone.now() + UNCERTAINTY_HOLD
        fresh.save(update_fields=["state", "message", "result", "finished_at", "lease_token", "lease_expires_at"])
    _audit(fresh, "finished")
    return True


def _bounded_result(result):
    """Defense in depth against an executor violating the scalar/byte contract."""
    allowed = {"columns", "rows", "truncated", "elapsed_ms", "query", "executed_at"}
    if not isinstance(result, dict) or set(result) != allowed:
        raise ValueError("Invalid result")
    if (not isinstance(result["columns"], list) or len(result["columns"]) > 200
            or any(not isinstance(c, str) for c in result["columns"])
            or not isinstance(result["rows"], list) or len(result["rows"]) > 200
            or type(result["truncated"]) is not bool
            or type(result["elapsed_ms"]) is not int
            or not isinstance(result["query"], str) or not isinstance(result["executed_at"], str)):
        raise ValueError("Invalid result")
    for row in result["rows"]:
        if (not isinstance(row, list) or len(row) != len(result["columns"])
                or any(type(v) not in (str, int, float, bool, type(None)) for v in row)):
            raise ValueError("Invalid result")
    if len(json.dumps(result, allow_nan=False).encode()) > MAX_RESULT_BYTES:
        raise ValueError("Invalid result")
    return result


def process_next(*, worker_id, pools=None):
    """Claim one query under a lease, execute once, and fence publication."""
    recover_expired()
    with transaction.atomic():
        candidates = Job.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            state=Job.ACCEPTED, accepted_at__gte=timezone.now() - ACCEPTED_TTL,
        )
        if pools:
            candidates = candidates.filter(studio__pool__in=pools)
        job = candidates.select_related("studio__org", "user", "session", "org").order_by("accepted_at").first()
        if job is None:
            return False
        job.state, job.started_at = Job.RUNNING, timezone.now()
        job.lease_token = uuid.uuid4()
        job.lease_expires_at = job.started_at + timedelta(seconds=LEASE_SECONDS)
        job.worker_id = str(worker_id)[:100]
        job.save(update_fields=["state", "started_at", "lease_token", "lease_expires_at", "worker_id"])

    owner_thread = threading.get_ident()
    last_check = 0.0
    last_heartbeat = 0.0
    stopped = False

    def cancelled():
        nonlocal last_check, last_heartbeat, stopped
        if stopped:
            return True
        if time.monotonic() - last_check < 0.25:
            return False
        last_check = time.monotonic()
        try:
            current = Job.objects.filter(pk=job.pk, state=Job.RUNNING, lease_token=job.lease_token).first()
            if current is None or current.cancel_requested or current.lease_expires_at <= timezone.now():
                stopped = True
            else:
                _current_grant(job)
                if time.monotonic() - last_heartbeat >= 1:
                    # A bounded heartbeat cannot extend a stuck query forever.
                    ceiling = job.started_at + timedelta(seconds=2 * LEASE_SECONDS)
                    if not Job.objects.filter(
                        pk=job.pk, state=Job.RUNNING, lease_token=job.lease_token,
                        cancel_requested=False, lease_expires_at__gt=timezone.now(),
                    ).update(lease_expires_at=min(ceiling, timezone.now() + timedelta(seconds=LEASE_SECONDS))):
                        stopped = True
                    last_heartbeat = time.monotonic()
            return stopped
        except Exception:
            # A failed policy read stops external work; no raw errors are stored.
            stopped = True
            return True
        finally:
            if threading.get_ident() != owner_thread:
                connections.close_all()

    def record_started(backend_pid):
        nonlocal last_check
        last_check = 0.0  # Always recheck authority immediately before user SQL.
        if cancelled():
            raise PermissionDenied(DENIED)
        if not Job.objects.filter(
            pk=job.pk, state=Job.RUNNING, lease_token=job.lease_token,
            cancel_requested=False, lease_expires_at__gt=timezone.now(),
        ).update(backend_pid=int(backend_pid)):
            raise PermissionDenied(DENIED)

    def on_started(backend_pid):
        try:
            record_started(backend_pid)
        finally:
            if threading.get_ident() != owner_thread:
                connections.close_all()

    attempted = completed = False
    try:
        grant = _current_grant(job)
        if cancelled():
            _finish(job, Job.CANCELLED, "Source read cancelled before execution.")
            return True
        executor = _executor()
        if job.kind == Job.QUERY:
            normalized = executor.validate_query(job.sql, grant.relations)
            if normalized != job.sql:
                raise PermissionDenied(MSG_REVOKED)
        fields = grant.datasource.all_fields()
        if grant.datasource.type != "postgres" or any(v for k, v in fields.items() if k.startswith("ssh_")):
            raise PermissionDenied(DENIED)
        info = {k: v for k, v in fields.items() if k in {"host", "port", "database", "user", "password", "sslmode"}}
        kwargs = {"timeout_seconds": 10, "max_rows": 200, "max_bytes": MAX_RESULT_BYTES,
                  "cancelled": cancelled, "on_started": on_started}
        attempted = True
        if job.kind == Job.DESCRIBE:
            result = executor.describe_source(info, grant.relations, **kwargs)
        else:
            result = executor.execute_query(info, job.sql, grant.relations, **kwargs)
        completed = True
        _finish(job, Job.SUCCEEDED, "Source read completed.", _bounded_result(result))
    except PermissionDenied:
        _finish(job, Job.FAILED, MSG_REVOKED)
    except Exception as exc:
        from trellum.data.assistant_query import QueryExecutionError, QueryRejected

        if isinstance(exc, QueryRejected):
            _finish(job, Job.FAILED, str(exc))
        elif isinstance(exc, QueryExecutionError) and exc.confirmed:
            _finish(job, Job.CANCELLED if exc.cancelled else Job.FAILED, str(exc))
        else:
            _finish(job, Job.UNCONFIRMED if attempted and not completed else Job.FAILED, MSG_FAILED)
        # An arbitrary executor/transport failure cannot prove remote completion.
        # No exception text (which can contain SQL, data or credentials) is logged.
    return True


class SourceQueryThread:
    """A dedicated slot; source reads never occupy report-build claim slots."""

    def __init__(self, worker_id, pools):
        self.worker_id, self.pools = worker_id, list(pools or [])
        self.shutdown = threading.Event()
        self.thread = threading.Thread(target=self.run, name="assistant-source-queries", daemon=True)

    def start(self):
        self.thread.start()

    def is_alive(self):
        return self.thread.is_alive()

    def stop(self):
        self.shutdown.set()
        # The executor enforces connection and statement deadlines independently.
        self.thread.join(timeout=20)

    def run(self):
        try:
            while not self.shutdown.is_set():
                close_old_connections()
                try:
                    worked = process_next(worker_id=self.worker_id, pools=self.pools)
                except Exception:
                    worked = False  # durable lease recovery handles interrupted work
                finally:
                    connections.close_all()
                if not worked:
                    self.shutdown.wait(1)
        finally:
            connections.close_all()


def start_for_worker(*, is_runner, pools, worker_id):
    if not is_runner:
        return None
    thread = SourceQueryThread(worker_id, pools)
    thread.start()
    return thread
