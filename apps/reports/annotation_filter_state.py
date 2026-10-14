"""Bounded, durable UI filter state. Never annotation source authority.

HTTP must mint a fresh bound context from its guarded request, enforce CSRF,
validate transport limits, and establish its read snapshot. A token is only a
reference. State reads do not renew, repair, authorize other actors, or load an
entire hidden-type set. Idempotency lasts until expired state is cleaned up.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db import OperationalError, connection, transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.core.request_access import request_org_access
from apps.core.request_context import actor_key
from .models import AnnotationFilterMembership, AnnotationFilterReceipt, AnnotationFilterState

CHUNK_SIZE = 25
BODY_BYTES = 32 * 1024
LIFETIME = timedelta(minutes=30)
TOUCH_INTERVAL = timedelta(minutes=5)
MAX_COUNTER = (1 << 63) - 1
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
ADMISSION_NAMESPACE = b"trellum:annotation-filter-admission:v1:"
_SEAL_KEY = secrets.token_bytes(32)


class FilterStateError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class FilterContext:
    user_id: int
    studio_id: int
    actor_fingerprint: str
    _seal: bytes | None = field(default=None, repr=False, compare=False)


def context_from_request(request):
    """Only guarded session requests can mint a context; do not reuse it later.

    Resolve through the existing request-scoped permission contract rather
    than trusting a posted studio ID, caller-provided role, or org_roles field.
    The future HTTP adapter still owns route guards and per-request revocation.
    """
    if not isinstance(request, HttpRequest):
        raise PermissionDenied("guarded request required")
    user = getattr(request, "user", None)
    studio = getattr(request, "studio", None)
    org = getattr(request, "org", None)
    if (user is None or not user.is_authenticated or not user.is_active or getattr(request, "api_key", None) is not None
            or studio is None or org is None or studio.org_id != org.pk or not org.is_active):
        raise PermissionDenied("filter state unavailable")
    # C21 request facts: the guard already resolved these for this request.
    access = getattr(request, "org_roles", None) or request_org_access(request, org)
    if not access.has_full_studio_visibility(studio):
        raise PermissionDenied("full studio access required")
    identity = json.dumps(actor_key(request), separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(identity).hexdigest()
    message = f"{user.pk}:{studio.pk}:{fingerprint}".encode("ascii")
    seal = hmac.new(_SEAL_KEY, message, hashlib.sha256).digest()
    return FilterContext(user.pk, studio.pk, fingerprint, seal)


def _context(context):
    if not isinstance(context, FilterContext) or not isinstance(context._seal, bytes):
        raise PermissionDenied("bound filter context required")
    if (type(context.user_id) is not int or context.user_id <= 0
            or type(context.studio_id) is not int or context.studio_id <= 0
            or not isinstance(context.actor_fingerprint, str)
            or len(context.actor_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in context.actor_fingerprint)):
        raise PermissionDenied("bound filter context required")
    message = f"{context.user_id}:{context.studio_id}:{context.actor_fingerprint}".encode("ascii")
    seal = hmac.new(_SEAL_KEY, message, hashlib.sha256).digest()
    if not hmac.compare_digest(context._seal, seal):
        raise PermissionDenied("bound filter context required")
    if connection.vendor != "postgresql":
        raise RuntimeError("annotation filter state requires PostgreSQL")


def _now(value):
    value = value or timezone.now()
    if not timezone.is_aware(value):
        raise ValueError("aware observation time required")
    return value


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise FilterStateError("invalid_text", 400)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise FilterStateError("invalid_text", 400) from None
    return value


def _operation(value):
    value = _text(value, 128)
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise FilterStateError("invalid_operation", 400)
    return value


def _counter(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise FilterStateError("invalid_counter", 400)
    return value


def _token(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise FilterStateError("invalid_token", 400)
    return value


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise FilterStateError("invalid_digest", 400)
    return value


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > BODY_BYTES:
        raise FilterStateError("oversized_body", 400)
    return raw


def _body(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _keys(keys):
    if not isinstance(keys, (list, tuple)) or not 1 <= len(keys) <= CHUNK_SIZE:
        raise FilterStateError("invalid_chunk", 400)
    result = [_text(key, 256) for key in keys]
    _encoded(result)
    return result


def chain_digest(previous, keys):
    """Public deterministic manifest helper, also used by each bounded append."""
    return hashlib.sha256(bytes.fromhex(_digest(previous)) + _encoded(_keys(keys))).hexdigest()


def _owner(context):
    return dict(user_id=context.user_id, studio_id=context.studio_id, actor_fingerprint=context.actor_fingerprint)


def _snapshot(state):
    return dict(token=state.token, mode=state.mode, state=state.status, revision=state.revision,
                expires_at=state.expires_at, accepted_items=state.accepted_items,
                accepted_distinct=state.accepted_distinct, next_chunk=state.next_chunk)


def _live(state, now):
    if state.status == "retiring" or state.expires_at <= now:
        raise FilterStateError("expired", 410)


def _state(context, token, now, *, lock=False):
    query = AnnotationFilterState.objects.filter(**_owner(context), token=_token(token))
    if lock:
        query = query.select_for_update(nowait=True)
    state = query.first()
    if state is None:
        raise FilterStateError("not_found", 404)
    _live(state, now)
    return state


def _mutating(function):
    @wraps(function)
    def wrapper(context, *args, **kwargs):
        _context(context)
        try:
            with transaction.atomic():
                return function(context, *args, **kwargs)
        except OperationalError as exc:
            if getattr(exc.__cause__, "sqlstate", None) == "55P03" or getattr(exc.__cause__, "pgcode", None) == "55P03":
                raise FilterStateError("busy", 423) from None
            raise
    return wrapper


def admission_lock_key(context):
    """Dedicated signed64-bit TRY xact lock; collisions only deny admission."""
    _context(context)
    scope = f"{context.actor_fingerprint}:{context.user_id}:{context.studio_id}".encode("ascii")
    return int.from_bytes(hashlib.sha256(ADMISSION_NAMESPACE + scope).digest()[:8], "big", signed=True)


def _receipt(state, operation_id, kind, body_digest):
    receipt = AnnotationFilterReceipt.objects.filter(state=state, operation_id=operation_id).first()
    if receipt is not None and (receipt.kind != kind or receipt.body_digest != body_digest):
        raise FilterStateError("operation_conflict")
    return receipt


def _result(state, receipt):
    return dict(token=state.token, operation_id=receipt.operation_id, kind=receipt.kind,
                status=receipt.status, revision=receipt.result_revision,
                accepted_items=receipt.accepted_items, accepted_distinct=receipt.accepted_distinct,
                next_chunk=receipt.next_chunk)


def _record(state, operation_id, kind, body_digest, *, base_revision=0, chunk_index=None, now):
    receipt = AnnotationFilterReceipt.objects.create(
        state=state, operation_id=operation_id, kind=kind, body_digest=body_digest,
        base_revision=base_revision, chunk_index=chunk_index, result_revision=state.revision,
        accepted_items=state.accepted_items, accepted_distinct=state.accepted_distinct,
        next_chunk=state.next_chunk, created_at=now,
    )
    return _result(state, receipt)


def _activity(state, now):
    state.last_activity_at = now
    state.expires_at = now + LIFETIME


@_mutating
def create(context, *, operation_id, mode, expected_items, expected_chain_digest, now=None):
    now = _now(now)
    operation_id = _operation(operation_id)
    expected_items = _counter(expected_items)
    expected_chain_digest = _digest(expected_chain_digest)
    if mode not in ("explicit", "defaults"):
        raise FilterStateError("invalid_mode", 400)
    if expected_items == 0 and expected_chain_digest != EMPTY_DIGEST:
        raise FilterStateError("invalid_manifest", 400)
    digest = _body([mode, expected_items, expected_chain_digest])
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [admission_lock_key(context)])
        if not cursor.fetchone()[0]:
            raise FilterStateError("busy", 423)
    previous = AnnotationFilterState.objects.filter(**_owner(context), client_operation_id=operation_id).select_for_update(nowait=True).first()
    if previous is not None:
        _live(previous, now)
        receipt = _receipt(previous, operation_id, "create", digest)
        if receipt is None:
            raise FilterStateError("operation_conflict")
        return _result(previous, receipt)
    if AnnotationFilterState.objects.filter(**_owner(context), status="draft", expires_at__gt=now).exists():
        raise FilterStateError("busy", 423)
    state = AnnotationFilterState.objects.create(
        **_owner(context), client_operation_id=operation_id, mode=mode, token=secrets.token_hex(32),
        expected_items=expected_items, expected_chunks=(expected_items + CHUNK_SIZE - 1) // CHUNK_SIZE,
        expected_chain_digest=expected_chain_digest, rolling_digest=EMPTY_DIGEST,
        created_at=now, last_activity_at=now, expires_at=now + LIFETIME,
    )
    return _record(state, operation_id, "create", digest, now=now)


@_mutating
def append(context, token, *, operation_id, chunk_index, keys, now=None):
    now = _now(now)
    operation_id, chunk_index, keys = _operation(operation_id), _counter(chunk_index), _keys(keys)
    digest = _body([chunk_index, keys])
    state = _state(context, token, now, lock=True)
    previous = _receipt(state, operation_id, "append", digest)
    if previous is not None:
        return _result(state, previous)
    if state.status != "draft" or chunk_index != state.next_chunk:
        raise FilterStateError("chunk_conflict")
    expected_size = min(CHUNK_SIZE, state.expected_items - state.accepted_items)
    if len(keys) != expected_size or chunk_index >= state.expected_chunks:
        raise FilterStateError("chunk_conflict")
    unique = list(dict.fromkeys(keys))
    existing = set(AnnotationFilterMembership.objects.filter(state=state, event_type__in=unique).values_list("event_type", flat=True))
    additions = [AnnotationFilterMembership(state=state, event_type=key, hidden=True) for key in unique if key not in existing]
    AnnotationFilterMembership.objects.bulk_create(additions, batch_size=CHUNK_SIZE)
    state.accepted_items += len(keys)
    state.accepted_distinct += len(additions)
    state.next_chunk += 1
    state.rolling_digest = chain_digest(state.rolling_digest, keys)
    _activity(state, now)
    state.save(update_fields=["accepted_items", "accepted_distinct", "next_chunk", "rolling_digest", "last_activity_at", "expires_at"])
    return _record(state, operation_id, "append", digest, chunk_index=chunk_index, now=now)


@_mutating
def finalize(context, token, *, operation_id, now=None):
    now = _now(now)
    operation_id = _operation(operation_id)
    digest = _body(["finalize"])
    state = _state(context, token, now, lock=True)
    previous = _receipt(state, operation_id, "finalize", digest)
    if previous is not None:
        return _result(state, previous)
    if state.status != "draft" or state.next_chunk != state.expected_chunks or state.accepted_items != state.expected_items or state.rolling_digest != state.expected_chain_digest:
        raise FilterStateError("incomplete_import")
    state.status, state.revision = "ready", 1
    _activity(state, now)
    state.save(update_fields=["status", "revision", "last_activity_at", "expires_at"])
    return _record(state, operation_id, "finalize", digest, now=now)


def _changes(changes):
    if not isinstance(changes, (list, tuple)) or not 1 <= len(changes) <= CHUNK_SIZE:
        raise FilterStateError("invalid_delta", 400)
    result = []
    seen = set()
    for item in changes:
        if not isinstance(item, (list, tuple)) or len(item) != 2 or type(item[1]) is not bool:
            raise FilterStateError("invalid_delta", 400)
        key = _text(item[0], 256)
        if key in seen:
            raise FilterStateError("duplicate_delta", 400)
        seen.add(key)
        result.append((key, item[1]))
    _encoded(result)
    return result


@_mutating
def delta(context, token, *, operation_id, base_revision, changes, now=None):
    now = _now(now)
    operation_id, base_revision, changes = _operation(operation_id), _counter(base_revision), _changes(changes)
    digest = _body([base_revision, changes])
    state = _state(context, token, now, lock=True)
    previous = _receipt(state, operation_id, "delta", digest)
    if previous is not None:
        return _result(state, previous)
    if state.status != "ready" or state.revision != base_revision:
        raise FilterStateError("revision_conflict")
    if state.revision == MAX_COUNTER:
        raise FilterStateError("revision_exhausted")
    keys = [key for key, _hidden in changes]
    # Transfer only requested keys, never the entire saved selection.
    existing = dict(AnnotationFilterMembership.objects.filter(state=state, event_type__in=keys).values_list("event_type", "hidden"))
    changed = False
    additions, removals = [], []
    for key, hidden in changes:
        if state.mode == "explicit" and not hidden:
            if key in existing:
                removals.append(key)
                changed = True
        elif existing.get(key) is not hidden:
            additions.append(AnnotationFilterMembership(state=state, event_type=key, hidden=hidden))
            changed = True
    if removals:
        AnnotationFilterMembership.objects.filter(state=state, event_type__in=removals)._raw_delete("default")
    if additions:
        AnnotationFilterMembership.objects.bulk_create(additions, batch_size=CHUNK_SIZE, update_conflicts=True,
            unique_fields=["state", "event_type"], update_fields=["hidden"])
    if changed:
        state.revision += 1
    _activity(state, now)
    state.save(update_fields=["revision", "last_activity_at", "expires_at"])
    return _record(state, operation_id, "delta", digest, base_revision=base_revision, now=now)


@_mutating
def touch(context, token, *, operation_id, now=None):
    now = _now(now)
    operation_id = _operation(operation_id)
    digest = _body(["touch"])
    state = _state(context, token, now, lock=True)
    previous = _receipt(state, operation_id, "touch", digest)
    if previous is not None:
        return _result(state, previous)
    if now - state.last_activity_at < TOUCH_INTERVAL:
        # No receipt or mutation for a throttled touch. Explicit retry with
        # this operation ID may renew later; caller sees it was not accepted.
        return dict(token=state.token, operation_id=operation_id, kind="touch", status="throttled", revision=state.revision)
    _activity(state, now)
    state.save(update_fields=["last_activity_at", "expires_at"])
    return _record(state, operation_id, "touch", digest, now=now)


def read(context, token, *, revision, now=None):
    """Narrow snapshot data, no keys or implicit renew. Caller owns RR transaction."""
    _context(context)
    revision = _counter(revision)
    state = _state(context, token, _now(now))
    if state.status != "ready" or state.revision != revision:
        raise FilterStateError("revision_conflict")
    return _snapshot(state)


def observe(context, token, *, operation_id, now=None):
    """Read-back of one operation; missing means unconfirmed, never success."""
    _context(context)
    operation_id = _operation(operation_id)
    state = _state(context, token, _now(now))
    receipt = AnnotationFilterReceipt.objects.filter(state=state, operation_id=operation_id).first()
    if receipt is None:
        return dict(token=state.token, operation_id=operation_id, status="unconfirmed")
    return _result(state, receipt)


def membership(context, token, *, revision, hidden, now=None):
    """Validate current state, then return a narrow unevaluated SQL relation.

    Must be evaluated in the same RR snapshot as read(), never across commits.
    Future combined HTTP context lookup can eliminate the separate read query.
    """
    if type(hidden) is not bool:
        raise FilterStateError("invalid_hidden", 400)
    read(context, token, revision=revision, now=now)
    return AnnotationFilterMembership.objects.filter(state__token=token, **{
        "state__user_id": context.user_id, "state__studio_id": context.studio_id,
        "state__actor_fingerprint": context.actor_fingerprint,
        "state__status": "ready", "state__revision": revision, "hidden": hidden,
    }).values("event_type")


def active_import(context, *, now=None):
    """One bounded owned draft lookup for busy/recovery feedback, no writes."""
    _context(context)
    now = _now(now)
    state = AnnotationFilterState.objects.filter(**_owner(context), status="draft", expires_at__gt=now).order_by("id").first()
    if state is None:
        return None
    result = _snapshot(state)
    result["operation_id"] = state.client_operation_id
    return result


def relations(context, token, *, revision=None, now=None):
    """One narrow state read plus unevaluated hidden/shown relations.

    ``revision=None`` means "the current ready revision" (explicit load-latest);
    otherwise it is an exact compare. Evaluate in the same RR snapshot.
    """
    _context(context)
    state = _state(context, token, _now(now))
    if state.status != "ready":
        raise FilterStateError("revision_conflict")
    if revision is not None and state.revision != _counter(revision):
        raise FilterStateError("revision_conflict")
    base = AnnotationFilterMembership.objects.filter(state_id=state.pk)
    return (_snapshot(state), base.filter(hidden=True).values("event_type"),
            base.filter(hidden=False).values("event_type"))
