"""The one way to write audit rows.

Two entry points: :func:`audit` for anything with a request, and
:func:`audit_system` for the handful of events that happen with no request in
sight (for example, the nightly retention purge). Both funnel
through the same redaction, size cap and category resolution, so "one table,
one write path" (see apps/core/models.py's AuditLog docstring) holds for
system rows too.

Recording must never break the thing it audits. Both functions catch and log
rather than raise once the row's shape is decided -- a database hiccup while
writing an audit row is not worth a 500 on the request it was describing.
"""
from __future__ import annotations

import logging

from apps.core.models import AuditLog
from apps.core.net import client_ip

logger = logging.getLogger(__name__)

#: Truncate HTTP_USER_AGENT to the column width -- a hostile or malformed
#: client sending a huge one must not become an oversized row.
_USER_AGENT_MAX = 256

#: A metadata dict serialized past this is a document, not a pointer. See
#: _cap_metadata.
_METADATA_MAX_BYTES = 4096

#: Any metadata key containing one of these (case-insensitively) gets its
#: value replaced with "[redacted]" -- belt-and-suspenders behind the
#: convention call sites already follow (token_suffix=link.token[-6:],
#: attempted=str(identifier)[:150]).
_SENSITIVE_KEY_MARKERS = (
    "password", "secret", "token", "authorization", "cookie", "key", "credential",
)

#: Keys that would otherwise match a marker above but are booleans/labels, not
#: the secret itself: token_suffix is the documented safe shape (a suffix,
#: not the token); require_password is share_policy.* 's own policy flag
#: (apps/reports/views.py's api_share_policy) -- whether a password is
#: mandatory, never a password value.
_REDACTION_ALLOWLIST = {"token_suffix", "require_password"}


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    if lowered in _REDACTION_ALLOWLIST:
        return False
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _scrub(d: dict) -> dict:
    return {k: ("[redacted]" if _is_sensitive_key(k) else v) for k, v in d.items()}


def _redact_metadata(metadata: dict) -> dict:
    """Shallow dict + one nesting level -- see the module docstring on why
    this is belt-and-suspenders rather than the only line of defense: call
    sites are expected to never pass a secret in the first place."""
    top = _scrub(metadata)
    for k, v in list(top.items()):
        if isinstance(v, dict):
            top[k] = _scrub(v)
    return top


def _cap_metadata(metadata: dict) -> dict:
    """An audit row is a pointer, not a document store. Metadata serialized
    past ``_METADATA_MAX_BYTES`` is replaced with a truncation marker plus as
    many of the original (already-redacted) keys as fit the budget, in their
    original order -- so the row still says *something* about what happened.
    """
    import json

    try:
        encoded = json.dumps(metadata, default=str)
    except (TypeError, ValueError):
        return {"_truncated": True}
    if len(encoded.encode("utf-8")) <= _METADATA_MAX_BYTES:
        return metadata

    capped: dict = {"_truncated": True}
    for k, v in metadata.items():
        candidate = {**capped, k: v}
        try:
            size = len(json.dumps(candidate, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            continue
        if size > _METADATA_MAX_BYTES:
            continue  # this one key doesn't fit; a later, smaller one might
        capped = candidate
    return capped


def _prepare_metadata(metadata: dict) -> dict:
    return _cap_metadata(_redact_metadata(metadata))


def _resolve_category(action: str) -> str:
    from django.conf import settings

    from apps.core.audit_actions import category_for

    # DEBUG only: an unregistered action raises here, before anything is
    # written, so a new call site is caught the first time a developer
    # exercises it. In production the registry's prefix fallback answers
    # instead and the row still gets written -- see audit_actions.category_for.
    return category_for(action, debug=settings.DEBUG)


def audit(
    request, action: str, target=None, org=None, actor=None,
    outcome: str = "success", **metadata,
) -> AuditLog | None:
    """Record an action tied to a request.

    ``target`` may be any model instance (its label/pk are stored) or None.
    ``org`` defaults to ``request.org`` when a permission decorator has
    resolved one. ``outcome`` is ``success`` (default), ``denied`` (a refusal
    -- SSO, a policy check), or ``failure`` (attempted, did not complete).
    Extra kwargs land in the JSON metadata column, after redaction.

    ``actor`` overrides ``request.user``, which is needed for exactly one case:
    the ``user_logged_in`` signal fires while the request is still mid-login, so
    ``request.user`` is not reliably the person who just authenticated. Passing
    it explicitly is better than a receiver mutating the request.

    Returns ``None`` (rather than raising) if the row could not be written --
    see the module docstring.
    """
    # Category resolution happens before the try below, deliberately: in
    # DEBUG it can raise on an unregistered action, and that is meant to
    # reach the developer, not be swallowed as "recording must never break
    # the request" -- see _resolve_category and audit_actions.category_for.
    category = _resolve_category(action)

    try:
        from apps.core import impersonation  # circular at module scope

        user = actor if actor is not None else getattr(request, "user", None)
        if user is not None and not user.is_authenticated:
            user = None
        # An operator acting as a customer is still the operator. Without
        # this the trail reads as if the customer did it -- for the whole
        # impersonation window, including the row that opened it.
        impersonator = impersonation.operator_of(request)
        if org is None:
            org = getattr(request, "org", None)
        target_type = ""
        target_id = ""
        if target is not None:
            target_type = target._meta.label_lower
            target_id = str(target.pk)

        meta = getattr(request, "META", None) or {}
        user_agent = str(meta.get("HTTP_USER_AGENT", "") or "")[:_USER_AGENT_MAX]

        metadata = _prepare_metadata(metadata)
        # A script acting under a personal key is still that person, but the
        # trail says which key did it (apps.accounts.api_keys). Added after
        # redaction on purpose: "key" is a sensitive marker, and this is the
        # row id, never the secret -- which is not stored anywhere anyway.
        api_key = getattr(request, "api_key", None)
        if api_key is not None:
            metadata["api_key"] = api_key.pk

        return AuditLog.objects.create(
            org=org,
            actor=user,
            impersonator=impersonator,
            action=action,
            category=category,
            outcome=outcome,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
            # Not REMOTE_ADDR: this portal runs behind a reverse proxy, so that is
            # the proxy's address for every user. See apps/core/net.py for why the
            # forwarded header is only trusted as far as TRELLUM_TRUSTED_PROXIES says.
            ip=client_ip(request),
            user_agent=user_agent,
        )
    except Exception:  # noqa: BLE001 - recording must never break the request
        logger.exception("audit() failed to record action=%s", action)
        return None


def audit_system(action: str, org=None, **metadata) -> AuditLog | None:
    """Record an event with no request behind it, such as the nightly
    retention purge's summary row.

    ``actor``/``impersonator``/``ip``/``user_agent`` are all empty -- an
    honest "the instance did this" rather than a manufactured user.
    ``apps.core.auth_events`` drops request-less auth signals because "the row
    would say nothing"; these rows are the deliberate exception, where the
    action name itself is the whole payload.
    """
    category = _resolve_category(action)
    try:
        return AuditLog.objects.create(
            org=org,
            actor=None,
            impersonator=None,
            action=action,
            category=category,
            outcome="success",
            target_type="",
            target_id="",
            metadata=_prepare_metadata(metadata),
            ip=None,
            user_agent="",
        )
    except Exception:  # noqa: BLE001 - recording must never break the caller
        logger.exception("audit_system() failed to record action=%s", action)
        return None
