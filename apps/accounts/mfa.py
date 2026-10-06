"""First-party TOTP multi-factor authentication.

``pyotp`` for RFC 6238 TOTP, ``segno`` for the enrollment QR code rendered as
inline SVG (CSP-safe: it never touches ``img-src``, because it is markup, not
a fetched resource). Deliberately not ``allauth.mfa`` (``SOCIALACCOUNT_ONLY``
forbids it, ``trellum_portal/settings/base.py``) and not ``django-otp`` (its
own device models/middleware are built around a login view this portal does
not use) -- see ``.lavish/session-security-design.md`` §4.1.

Enrollment and organization policy management are available in every
installation. :func:`enrollment_required` reads ``OrgSecurityPolicy`` rows
directly so enforcement follows saved security policy.
"""
from __future__ import annotations

import io
import secrets
import time
from datetime import timedelta

import pyotp

#: 30s steps, ±1 step accepted (90s total drift window) -- design §4.4.
_STEP_SECONDS = 30
_DRIFT_STEPS = 1
#: Unambiguous alphabet for recovery codes: no 0/O, 1/I/l.
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_RECOVERY_CODE_COUNT = 10


def _now_step() -> int:
    return int(time.time() // _STEP_SECONDS)


# ── Enrollment ───────────────────────────────────────────────────────────

def begin_enrollment(user):
    """Create (or replace) an unconfirmed TotpDevice with a fresh secret.

    Unconfirmed devices are inert -- never consulted by login step-up or by
    ``User.has_mfa`` -- so re-beginning enrollment (a user who scanned the
    QR but never confirmed, then comes back) simply discards the old secret.
    """
    from apps.accounts.models import TotpDevice

    device, _ = TotpDevice.objects.update_or_create(
        user=user,
        defaults={"secret": pyotp.random_base32(), "confirmed_at": None, "last_used_step": 0},
    )
    return device


def provisioning_uri(user, device, *, issuer_name: str) -> str:
    return pyotp.totp.TOTP(device.secret).provisioning_uri(
        name=user.email, issuer_name=issuer_name or "trellum"
    )


def qr_svg(uri: str) -> str:
    """Inline ``<svg>...</svg>`` markup (no XML declaration/DOCTYPE) suitable
    for embedding directly in a template with ``|safe``."""
    import re
    import segno

    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", xmldecl=False)
    svg = buf.getvalue().decode("utf-8")
    # segno stamps width/height in module units but NO viewBox, so CSS that
    # sizes the code (mfa_setup fills a fixed box) stretches the element
    # without scaling its ~21-unit contents -- a tiny code in a big white box.
    # Add a viewBox matching the intrinsic size so `width:100%` scales the
    # whole QR to fill.
    m = re.search(r'width="(\d+)" height="(\d+)"', svg)
    if m and "viewBox" not in svg:
        svg = svg.replace(
            m.group(0), f'{m.group(0)} viewBox="0 0 {m.group(1)} {m.group(2)}"', 1
        )
    return svg


def confirm_enrollment(device, code: str) -> list[str] | None:
    """Verify the first code from a pending enrollment. On success, confirms
    the device and returns ten freshly-generated recovery codes (plaintext,
    shown exactly once -- the caller is responsible for displaying them and
    never storing the plaintext). Returns ``None`` on a wrong code."""
    if not _check_code(device, code):
        return None
    from django.utils import timezone

    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed_at", "last_used_step"])
    return generate_recovery_codes(device.user)


def verify_login_code(device, code: str) -> bool:
    """Verify a code from a CONFIRMED device (login step-up, or a self-serve
    disable/regenerate that re-checks a live factor). Persists the advanced
    replay-guard step on success."""
    if device.confirmed_at is None:
        return False
    if _check_code(device, code):
        device.save(update_fields=["last_used_step"])
        return True
    return False


def _check_code(device, code: str) -> bool:
    """Accept a code within ±1 step of now, provided that step has not
    already been consumed (replay guard: a step verifies at most once)."""
    code = (code or "").strip().replace(" ", "")
    if not code:
        return False
    totp = pyotp.totp.TOTP(device.secret)
    now_step = _now_step()
    for step in range(now_step - _DRIFT_STEPS, now_step + _DRIFT_STEPS + 1):
        if step <= device.last_used_step:
            continue  # already consumed -- the replay guard
        if secrets.compare_digest(totp.at(step * _STEP_SECONDS), code):
            device.last_used_step = step
            return True
    return False


# ── Recovery codes ──────────────────────────────────────────────────────────

def _format_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def generate_recovery_codes(user) -> list[str]:
    """Ten new single-use codes, replacing any previous set. Returns the
    plaintext (shown once); only the hash is persisted."""
    from django.contrib.auth.hashers import make_password

    from apps.accounts.models import RecoveryCode

    RecoveryCode.objects.filter(user=user).delete()
    plaintext = [_format_code() for _ in range(_RECOVERY_CODE_COUNT)]
    RecoveryCode.objects.bulk_create(
        [RecoveryCode(user=user, code_hash=make_password(code)) for code in plaintext]
    )
    return plaintext


def verify_recovery_code(user, code: str):
    """Check ``code`` against the user's unused recovery codes. On a match,
    marks it used and returns the row; returns ``None`` otherwise. Iterates
    the (at most 10) unused hashes -- acceptable per design §4.2, this path
    is not the common case."""
    from django.contrib.auth.hashers import check_password
    from django.utils import timezone

    code = (code or "").strip()
    if not code:
        return None
    for row in user.recovery_codes.filter(used_at__isnull=True):
        if check_password(code, row.code_hash):
            row.used_at = timezone.now()
            row.save(update_fields=["used_at"])
            return row
    return None


def remaining_recovery_codes(user) -> int:
    return user.recovery_codes.filter(used_at__isnull=True).count()


# ── Removal / reset ─────────────────────────────────────────────────────────

def remove_device(user) -> None:
    """Delete a user's device and recovery codes -- self-disable (the caller
    has already re-verified password + a factor) or admin reset (the caller
    is responsible for bumping ``user.auth_epoch`` afterward; see
    ``apps.accounts.views.mfa_admin_reset``)."""
    from apps.accounts.models import RecoveryCode, TotpDevice

    TotpDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()


# ── Enforcement: does this session need to be redirect-locked? ──────────────

def enrollment_required(request, user) -> bool:
    """True when ``user`` must enroll MFA before doing anything else in
    ``SessionSecurityMiddleware``'s redirect-lock.

    SSO users are never locked: the IdP owns MFA for a session established
    through it (``session["sp_via_sso"]``, stamped at login by
    ``apps.accounts.session_policy``) -- see design §4.5 and §3.7.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    session = getattr(request, "session", None)
    if session is not None and session.get("sp_via_sso"):
        return False
    if user.has_mfa:
        return False
    if getattr(user, "is_operator", False) and _instance_requires_mfa_operators():
        return True
    return _org_requires_mfa(user) and not _within_org_grace(user)


def policy_requires_mfa(request, user) -> bool:
    """Would policy require ``user`` to have MFA enrolled, ignoring whether
    they currently do? Used to refuse self-disable while an applicable
    policy is in force (grace is not consulted here: a member inside their
    grace window may not yet be forced to enroll, but is not entitled to
    disable an existing device either). SSO-established sessions stay exempt.
    """
    session = getattr(request, "session", None)
    if session is not None and session.get("sp_via_sso"):
        return False
    if getattr(user, "is_operator", False) and _instance_requires_mfa_operators():
        return True
    return _org_requires_mfa(user)


def _instance_requires_mfa_operators() -> bool:
    from apps.core.models import InstanceConfig

    return InstanceConfig.load().require_mfa_operators


def _applicable_policies(user):
    from apps.orgs.models import OrgSecurityPolicy

    return OrgSecurityPolicy.objects.filter(
        require_mfa=True, org__memberships__user=user, org__is_active=True
    )


def _org_requires_mfa(user) -> bool:
    """Any org requiring MFA requires it (OR semantics across memberships).
    Reads OrgSecurityPolicy directly so authentication enforcement follows the
    saved policy regardless of which management page last changed it."""
    return _applicable_policies(user).exists()


def _within_org_grace(user) -> bool:
    """Grace ends at the earliest of every applicable org's window, anchored
    at whichever is later: the policy's own ``updated_at`` (when it turned
    on) or the user's ``date_joined`` (a user who joins after the policy is
    already live gets grace from their own join date, not the policy's).
    Any applicable org with ``mfa_grace_days=0`` makes it hard immediately,
    because OR semantics mean that org's requirement is already binding
    regardless of what a more lenient sibling org grants.
    """
    from django.utils import timezone

    policies = list(_applicable_policies(user))
    if not policies:
        return False
    now = timezone.now()
    deadlines = []
    for policy in policies:
        if policy.mfa_grace_days <= 0:
            return False
        anchor = max(policy.updated_at, user.date_joined)
        deadlines.append(anchor + timedelta(days=policy.mfa_grace_days))
    return now < min(deadlines)
