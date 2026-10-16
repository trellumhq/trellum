"""Operator figures that keep themselves current, measured only while someone looks.

System health and an organization's uploaded-file usage are too slow to compute
while a page renders. One ``OpsState`` row per figure (``operator.health``,
``operator.usage.<org id>``) holds the last result and when somebody last asked
for it. A page view, or the page's quiet poll, only notes the request. The
coordinator's ``tick`` re-measures a figure that was asked for in the last two
minutes and has gone stale, in a child process that is killed at its deadline.

Nothing else is stored: no leases, fences or "in flight" state. A hung check is
killed, a coordinator restart or ``kill -9`` leaves nothing to recover, and the
next tick simply measures again. Without a live coordinator the pages compute the
figures in the request, as they always did.
"""
from datetime import timedelta
import json
import logging
import os
import signal
import subprocess
import sys

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.models import OpsState

logger = logging.getLogger(__name__)

HEALTH = "operator.health"
PREFIX = "operator."
#: Seconds before a figure counts as stale and is measured again.
REFRESH_AFTER = {"health": 60, "usage": 300}
#: Seconds a measurement may run before its whole process group is killed.
TIMEOUT = {"health": 30, "usage": 60}
#: A request keeps its figure measured for this many seconds.
LOOKING_FOR = 120
#: Pages note a request at most this often, so an open page writes rarely.
NOTE_EVERY = 30


def usage_key(org_id):
    return f"{PREFIX}usage.{int(org_id)}"


def _kind(key):
    return key.split(".")[1]


def stamp(value):
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return parsed if parsed and timezone.is_aware(parsed) else None


def available():
    """Is a live coordinator there to measure? Otherwise pages compute in the request."""
    from apps.runner.models import WorkerHeartbeat

    return WorkerHeartbeat.alive().filter(
        role__in=(WorkerHeartbeat.ROLE_ALL, WorkerHeartbeat.ROLE_COORDINATOR)).exists()


def snapshot(payload, now=None):
    """The last result with its age, as pages show it; ``{}`` before any attempt.

    ``failed`` says the latest attempt did not produce a result, so what is
    shown (possibly nothing) is older than the coordinator wanted.
    """
    now = now or timezone.now()
    done, tried = stamp(payload.get("completed_at")), stamp(payload.get("attempted_at"))
    failed = bool(tried and (not done or tried > done))
    result = payload.get("result")
    if not done or not isinstance(result, dict):
        return {"failed": failed}
    return {**result, "completed_at": done.isoformat(), "failed": failed,
            "age_seconds": max(0, int((now - done).total_seconds()))}


def age_text(seconds):
    """Relative age for the first paint; static/operator-observations.js keeps it current."""
    if seconds is None:
        return ""
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} seconds ago"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'} ago"


def note(key):
    """Record that someone is looking at ``key``; returns the row's payload.

    Writes at most once per ``NOTE_EVERY`` seconds. The row is locked while it
    is updated so a result stored at the same moment is never overwritten.
    """
    now = timezone.now()
    row = OpsState.objects.filter(key=key).only("payload").first()
    payload = row.payload if row else {}
    seen = stamp(payload.get("requested_at"))
    if seen and now - seen < timedelta(seconds=NOTE_EVERY):
        return payload
    with transaction.atomic():
        row, _ = OpsState.objects.select_for_update().get_or_create(key=key, defaults={"ran_at": now})
        row.payload = payload = {**row.payload, "requested_at": now.isoformat()}
        row.ran_at = now
        row.save(update_fields=["payload", "ran_at"])
    return payload


def poll(key):
    """What a page shows for ``key``, noting that someone is looking."""
    return snapshot(note(key))


def due(key, payload, now):
    refresh = REFRESH_AFTER.get(_kind(key))
    asked, tried = stamp(payload.get("requested_at")), stamp(payload.get("attempted_at"))
    if not refresh or not asked or now - asked > timedelta(seconds=LOOKING_FOR):
        return False
    return tried is None or now - tried >= timedelta(seconds=refresh)


def run_bounded(argv, timeout):
    """Standard output of ``argv`` if it exits 0 within ``timeout`` seconds, else ``None``.

    The child leads its own session, so on timeout the whole process group
    (docker, git and the like included) is killed. Nothing waits on a process
    that will not die, e.g. one stuck in uninterruptible I/O.
    """
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            logger.error("Operator measurement ignored SIGKILL; leaving it behind")
        return None
    return output if process.returncode == 0 else None


def _clean(kind, output):
    """The child's one JSON line reduced to the figures a page shows, or ``None``."""
    try:
        data = json.loads(output.decode().strip().splitlines()[-1])
    except (AttributeError, IndexError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if kind == "usage":
        used = data.get("bytes")
        return {"bytes": used} if type(used) is int and used >= 0 else None
    checks = data.get("checks")
    if not isinstance(checks, list) or len(checks) > 32:
        return None
    for check in checks:
        if (not isinstance(check, dict) or type(check.get("ok")) is not bool
                or not isinstance(check.get("label"), str) or len(check["label"]) > 80
                or not isinstance(check.get("detail"), str) or len(check["detail"]) > 1024):
            return None
    return {"checks": [{name: check[name] for name in ("label", "ok", "detail")} for check in checks]}


def measure(key):
    """Measure ``key`` once in a child process and store the outcome."""
    kind = _kind(key)
    argv = [sys.executable, str(settings.BASE_DIR / "manage.py"), "operator_measure", *key.split(".")[1:]]
    output = run_bounded(argv, TIMEOUT[kind])
    result = _clean(kind, output) if output is not None else None
    if result is None:
        logger.warning("Operator measurement %s did not complete", key)
    now = timezone.now()
    with transaction.atomic():
        row = OpsState.objects.select_for_update().filter(key=key).first()
        if row is None:
            return
        row.payload = {**row.payload, "attempted_at": now.isoformat()}
        if result is not None:
            row.payload.update(completed_at=now.isoformat(), result=result)
        row.ok, row.ran_at = result is not None, now
        row.save(update_fields=["payload", "ok", "ran_at"])


def tick():
    """Coordinator job: measure the one figure that is due. Idle, it is a single SELECT.

    Only rows touched in the last two minutes are looked at, and one
    measurement runs per tick; the next tick takes the next due figure. Returns
    the key it measured. Never raises: a broken measurement must not take the
    coordinator's other jobs down with it.
    """
    try:
        now = timezone.now()
        rows = list(OpsState.objects.filter(
            key__startswith=PREFIX, ran_at__gte=now - timedelta(seconds=LOOKING_FOR),
        ).order_by("key").values_list("key", "payload"))
        for key, payload in rows:
            if due(key, payload, now):
                measure(key)
                return key
    except Exception:
        logger.exception("Operator measurements unavailable")
