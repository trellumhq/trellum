"""Closed operator diagnostic observations; never a general job queue."""
from datetime import timedelta
import hashlib
import json
import secrets
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.actions import action_payload
from apps.core.models import OpsState
from apps.core.workspace import cached_workspace_identity

SLOT = "operator.probe"
KINDS = {"full-health": (30, 35), "upload-usage": (60, 65), "maintenance": (10, 15)}
EXPLICIT = frozenset({"full-health", "upload-usage"})
TERMINAL = frozenset({"succeeded", "failed", "unconfirmed"})
NONCE_SALT = "operator-probe-v1"


class ObservationError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def stamp(value):
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
        return parsed if parsed and timezone.is_aware(parsed) else None
    except ValueError:
        return None


def _endpoint(value):
    """A URL fingerprint never includes credentials, query strings or tokens."""
    try:
        parsed = urlsplit(str(value or ""))
        return (parsed.scheme, parsed.hostname)
    except ValueError:
        return ("invalid", None)


def fingerprint(kind, *, producer=False, workspace=None):
    import trellum_portal

    values = {
        "protocol": 1, "kind": kind, "version": trellum_portal.__version__,
        "sha": settings.TRELLUM_VERSION_SHA,
        "workspace": cached_workspace_identity() if workspace is None else workspace,
        "debug": settings.DEBUG, "sandbox": settings.TRELLUM_SANDBOX,
        "egress": settings.TRELLUM_SANDBOX_EGRESS,
        "cleanup": settings.CLEANUP_ENABLED,
    }
    if producer:
        values.update({
            "backend": settings.TRELLUM_STORAGE_BACKEND,
            "bucket": getattr(settings, "TRELLUM_REPORTS_BUCKET", ""),
            "endpoint": _endpoint(getattr(settings, "TRELLUM_STORAGE_ENDPOINT_URL", "")),
            "access_model": getattr(settings, "TRELLUM_REPORT_ACCESS_MODEL", "proxy"),
            "cdn_endpoint": _endpoint(getattr(settings, "TRELLUM_CDN_BASE_URL", "")),
            "sandbox_network": settings.TRELLUM_SANDBOX_NETWORK,
            "backup_path": str(settings.BACKUP_DIR),
            "backup_age": settings.BACKUP_MAX_AGE_HOURS,
            "cleanup_age": settings.CLEANUP_MAX_AGE_HOURS,
        })
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def nonce(actor_id, kind, org_id=None):
    if kind not in EXPLICIT:
        raise ValueError("Invalid operator operation")
    return signing.dumps({"id": str(uuid4()), "actor": actor_id, "kind": kind, "org": org_id}, salt=NONCE_SALT)


def _slot():
    OpsState.objects.get_or_create(key=SLOT, defaults={"ran_at": timezone.now(), "payload": {"version": 1, "active": None, "recent": [], "blocked": None}})
    return OpsState.objects.select_for_update().get(key=SLOT)


def _write(row, payload):
    if len(json.dumps(payload).encode()) > 32768:
        raise ObservationError("Diagnostic state exceeds its limit", 503)
    row.payload, row.ran_at = payload, timezone.now()
    row.save(update_fields=["payload", "ran_at"])


def _coordinator():
    from apps.runner.models import WorkerHeartbeat
    return (WorkerHeartbeat.alive()
            .filter(role__in=(WorkerHeartbeat.ROLE_ALL, WorkerHeartbeat.ROLE_COORDINATOR))
            .order_by("-last_beat_at", "pk").values_list("worker_id", flat=True).first())


def admit(actor_id, kind, key, *, org_id=None):
    if kind not in EXPLICIT:
        raise ObservationError("Invalid diagnostic operation")
    try:
        signed = signing.loads(key, salt=NONCE_SALT, max_age=300)
        operation_id = str(UUID(signed["id"]))
    except (signing.BadSignature, TypeError, ValueError, KeyError):
        raise ObservationError("Refresh request expired or is invalid. Reload before refreshing.") from None
    if signed != {"id": operation_id, "actor": actor_id, "kind": kind, "org": org_id}:
        raise ObservationError("Invalid diagnostic scope")
    now = timezone.now()
    with transaction.atomic():
        row = _slot()
        payload = row.payload
        for entry in payload.get("recent", []):
            if entry["id"] == operation_id:
                return entry, False
        active = payload.get("active")
        if active and active["state"] not in TERMINAL:
            raise ObservationError("Another diagnostic measurement is in progress.", 409)
        if payload.get("blocked"):
            raise ObservationError("Previous measurement termination is unconfirmed. Check the coordinator.", 503)
        last = stamp(payload.get("last_admitted"))
        if last and now - last < timedelta(seconds=30):
            raise ObservationError("Wait thirty seconds between diagnostic measurements.", 429)
        node = _coordinator()
        if not node or sys.platform != "linux":
            raise ObservationError("A supported live coordinator is required.", 503)
        workspace = cached_workspace_identity()
        if kind == "upload-usage" and not workspace:
            raise ObservationError("Shared workspace identity is unavailable.", 503)
        entry = {
            "id": operation_id, "kind": kind, "org_id": org_id, "actor_id": actor_id,
            "state": "accepted", "message": "Measurement accepted.",
            "requested_at": now.isoformat(), "workspace_id": workspace,
            "request_fingerprint": fingerprint(kind), "producer_node": node,
            "lease_until": (now + timedelta(seconds=KINDS[kind][1] + 10)).isoformat(),
        }
        payload.update(active=entry, recent=([entry.copy()] + payload.get("recent", []))[:16], last_admitted=now.isoformat())
        _write(row, payload)
        return entry.copy(), True


def observation(operation_id, *, kind, org_id=None):
    row = OpsState.objects.filter(key=SLOT).values("payload").first()
    if row:
        candidates = [row["payload"].get("active"), *row["payload"].get("recent", [])]
        for entry in candidates:
            if entry and entry["id"] == str(operation_id) and entry["kind"] == kind and entry.get("org_id") == org_id:
                result = entry.copy()
                expiry = stamp(result.get("lease_until"))
                if result["state"] not in TERMINAL and expiry and expiry <= timezone.now():
                    result.update(state="unconfirmed", message="Measurement deadline passed; its outcome is unconfirmed.")
                if result.get("request_fingerprint") != fingerprint(kind):
                    result.update(state="unconfirmed", message="Request context changed. This observation is historical.")
                return result
    raise ObservationError("Diagnostic observation has retired or is unavailable.", 404)


def current_operation(kind, org_id=None):
    row = OpsState.objects.filter(key=SLOT).values("payload").first()
    entry = row and row["payload"].get("active")
    if entry and entry["kind"] == kind and entry.get("org_id") == org_id and entry["state"] not in TERMINAL:
        return observation(entry["id"], kind=kind, org_id=org_id)
    return None


def envelope(entry, status_url):
    return action_payload(action_id=entry["id"], state=entry["state"], message=entry["message"], status_url=status_url)


def snapshot_key(kind, org_id=None):
    if kind == "upload-usage":
        return f"operator.usage.{int(org_id)}"
    return "operator.health" if kind == "full-health" else "operator.maintenance"


def snapshot(kind, org_id=None):
    row = OpsState.objects.filter(key=snapshot_key(kind, org_id)).values("payload").first()
    if not row:
        return {"state": "unknown", "message": "No completed observation."}
    result = row["payload"].copy()
    completed = stamp(result.get("completed_at"))
    fresh = completed and timezone.now() - completed <= timedelta(seconds=90 if kind == "maintenance" else 300)
    if kind == "upload-usage" and (not cached_workspace_identity() or result.get("workspace_id") != cached_workspace_identity()):
        return {"state": "unknown", "message": "Observation belongs to another or unavailable workspace."}
    result["state"] = "observed" if fresh else "stale"
    result["message"] = "Last observed" if fresh else "Last observation is stale"
    return result


def _remember(payload, entry):
    if entry["kind"] in EXPLICIT:
        compact = {key: entry.get(key) for key in (
            "id", "kind", "org_id", "actor_id", "state", "message", "requested_at", "request_fingerprint",
        )}
        payload["recent"] = [compact if item["id"] == entry["id"] else item for item in payload.get("recent", [])][:16]


def claim(node, epoch, *, allow_maintenance=True):
    now = timezone.now()
    with transaction.atomic():
        row = _slot()
        payload = row.payload
        entry = payload.get("active")
        if payload.get("blocked"):
            return None
        if entry and entry["state"] not in TERMINAL:
            if entry["producer_node"] != node or entry["state"] == "running":
                return None
            if stamp(entry["lease_until"]) <= now:
                entry.update(state="unconfirmed", message="Measurement was not started before its deadline.")
                _remember(payload, entry)
                _write(row, payload)
                return None
        else:
            if not allow_maintenance:
                return None
            last = stamp(payload.get("maintenance_at"))
            if last and now - last < timedelta(seconds=30):
                return None
            entry = {"id": str(uuid4()), "kind": "maintenance", "org_id": None,
                     "requested_at": now.isoformat(), "workspace_id": cached_workspace_identity(),
                     "request_fingerprint": fingerprint("maintenance"), "producer_node": node}
            payload["maintenance_at"] = now.isoformat()
        kind = entry["kind"]
        if kind == "upload-usage" and entry["workspace_id"] != cached_workspace_identity():
            entry.update(state="failed", message="Coordinator has a different or unavailable workspace.")
            payload["active"] = entry
            _remember(payload, entry)
            _write(row, payload)
            return None
        entry.update(state="running", message="Measurement running.", lease_token=secrets.token_hex(16),
                     producer_epoch=epoch, producer_fingerprint=fingerprint(kind, producer=True),
                     producer_workspace_id=cached_workspace_identity(),
                     started_at=now.isoformat(), deadline_at=(now + timedelta(seconds=KINDS[kind][0])).isoformat(),
                     lease_until=(now + timedelta(seconds=KINDS[kind][1])).isoformat())
        payload["active"] = entry
        _remember(payload, entry)
        _write(row, payload)
        return entry.copy()


def fence(entry):
    return tuple(entry.get(key) for key in ("id", "lease_token", "producer_epoch", "workspace_id", "producer_workspace_id", "producer_fingerprint"))


def child_started(entry, proof):
    with transaction.atomic():
        row = _slot()
        active = row.payload.get("active")
        if active and fence(active) == fence(entry) and active["state"] == "running":
            active["child"] = proof
            _write(row, row.payload)


def finish(entry, *, result=None, state="unconfirmed", message="Measurement outcome is unconfirmed.", terminated=True):
    now = timezone.now()
    with transaction.atomic():
        row = _slot()
        active = row.payload.get("active")
        if not active or fence(active) != fence(entry) or active["state"] != "running":
            return False
        if result is not None:
            limit = 8192 if entry["kind"] == "upload-usage" else 65536
            if (stamp(active["lease_until"]) <= now or result.get("producer_fingerprint") != entry["producer_fingerprint"]
                    or fingerprint(entry["kind"], producer=True) != entry["producer_fingerprint"]
                    or len(json.dumps(result).encode()) > limit or not terminated):
                result = None
            elif (result.get("version") != 1 or result.get("kind") != entry["kind"]
                  or result.get("workspace_id") != entry["producer_workspace_id"]):
                result = None
                state, message = "failed", "Measurement returned an invalid scope."
            elif entry["kind"] != "upload-usage" and (
                    not isinstance(result.get("checks"), list) or len(result["checks"]) > 32
                    or any(not isinstance(check, dict) or not isinstance(check.get("ok"), bool)
                           or not isinstance(check.get("label"), str) or len(check["label"]) > 80
                           or not isinstance(check.get("detail"), str) or len(check["detail"]) > 1024
                           for check in result["checks"])):
                result = None
                state, message = "failed", "Measurement returned invalid check data."
            elif entry["kind"] == "upload-usage":
                progress = {name: result.get(name, 0) for name in ("inspected", "total_candidates", "metadata_calls", "missing")}
                if any(type(value) is not int or value < 0 for value in progress.values()):
                    result = None
                    state, message = "failed", "Storage measurement returned invalid progress."
                else:
                    active["progress"] = progress
                    if (result.get("workspace_id") != entry["workspace_id"] or result.get("quality") != "complete"
                            or type(result.get("bytes")) is not int or result["bytes"] < 0):
                        result = None
                        state, message = "failed", "Storage measurement is incomplete; the previous total is retained."
        if result is not None:
            result.update(id=entry["id"], completed_at=now.isoformat(), producer_node=entry["producer_node"], request_fingerprint=entry["request_fingerprint"])
            OpsState.objects.update_or_create(key=snapshot_key(entry["kind"], entry.get("org_id")),
                defaults={"ran_at": now, "ok": True, "payload": result})
            state, message = "succeeded", "Measurement completed."
        active.update(state=state, message=message)
        if not terminated:
            row.payload["blocked"] = active.get("child") or {"unknown": True}
        _remember(row.payload, active)
        _write(row, row.payload)
        return True


def recover(node, termination_known):
    """Background-only recovery. Expired leases cannot prove OS termination."""
    with transaction.atomic():
        row = _slot()
        payload = row.payload
        blocked = payload.get("blocked")
        if blocked:
            if termination_known(blocked):
                payload["blocked"] = None
                _write(row, payload)
            return
        active = payload.get("active")
        if not active or active["state"] in TERMINAL:
            return
        if active["producer_node"] == node and active["state"] == "accepted":
            return
        if stamp(active["lease_until"]) > timezone.now():
            return
        was_running = active["state"] == "running"
        active.update(state="unconfirmed", message="Coordinator changed or measurement expired. It will not be replayed.")
        if active.get("child") and not termination_known(active["child"]):
            payload["blocked"] = active["child"]
        elif was_running and not active.get("child"):
            payload["blocked"] = {"unknown": True}
        _remember(payload, active)
        _write(row, payload)
