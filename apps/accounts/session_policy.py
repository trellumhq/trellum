"""Session lifetime enforcement: idle timeout, absolute cap, instance-wide
and per-user revocation, and (piggybacked on the same per-request pass)
the forced-enrollment redirect-lock for an org's MFA requirement.

See ``.lavish/session-security-design.md`` §3 for the full design; this
module is that design's §3.1-3.6. The short version:

Sessions belong to a *user*, and a user can be a member of several
organizations, so there is no honest "which org's session policy applies"
answer -- policy is instance-scoped in v1 (``InstanceConfig``). A per-org
override is deferred.

Enforcement is retroactive and per-request, not a sweep: three keys are
stamped into the session at login (``sp_login_at``, ``sp_last_seen``,
``sp_epoch``) by the ``user_logged_in`` receiver below, and
``SessionSecurityMiddleware`` checks them against the current policy on
every authenticated request. Tightening a policy therefore applies to every
existing session on its very next request -- there is nothing to migrate,
and no session sweep is ever required for enforcement (``django_session``'s
own ``expire_date`` stays as a belt-and-braces outer bound; see
``trellum_portal/settings/base.py``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import signals as auth_signals
from django.core.cache import cache
from django.dispatch import receiver

_POLICY_CACHE_KEY = "session-policy:v1"
_POLICY_CACHE_TTL = 60
#: Only write sp_last_seen (and touch the UserSession row) this often. Keeps
#: a dev/test run (no SESSION_SAVE_EVERY_REQUEST) from writing the session on
#: every single request; production already writes it every request anyway
#: (trellum_portal/settings/base.py).
_COALESCE_SECONDS = 60


def _now() -> int:
    """Epoch seconds. A seam: tests monkeypatch this instead of freezing the
    real clock (the suite carries no freezegun dependency, matching its
    existing no-new-test-deps posture)."""
    return int(time.time())


@dataclass(frozen=True)
class SessionPolicy:
    idle_seconds: int  # 0 = disabled
    absolute_seconds: int  # 0 = disabled
    expire_at_browser_close: bool
    remember_me_enabled: bool
    remember_me_seconds: int
    invalidated_at: datetime | None


def _load_policy() -> SessionPolicy:
    from apps.core.models import InstanceConfig

    cfg = InstanceConfig.load()
    return SessionPolicy(
        idle_seconds=cfg.session_idle_minutes * 60,
        absolute_seconds=cfg.session_absolute_hours * 3600,
        expire_at_browser_close=cfg.session_expire_at_browser_close,
        remember_me_enabled=cfg.session_remember_me_enabled,
        remember_me_seconds=cfg.session_remember_me_days * 86400,
        invalidated_at=cfg.sessions_invalidated_at,
    )


def current_policy() -> SessionPolicy:
    """The active session policy, memoized for ``_POLICY_CACHE_TTL`` seconds
    in the shared DB cache so the middleware does not add a query to every
    request. A save on ``/system`` calls :func:`invalidate_policy_cache` for
    immediate effect; absent that, a change reaches every worker within one
    TTL window."""
    policy = cache.get(_POLICY_CACHE_KEY)
    if policy is None:
        policy = _load_policy()
        cache.set(_POLICY_CACHE_KEY, policy, _POLICY_CACHE_TTL)
    return policy


def invalidate_policy_cache() -> None:
    cache.delete(_POLICY_CACHE_KEY)


# ── Login stamping ───────────────────────────────────────────────────────

@receiver(auth_signals.user_logged_in, dispatch_uid="accounts.session_policy.stamp_session")
def _stamp_session(sender, request, user, **kwargs):
    """Stamp a freshly-established session with the three keys the
    middleware enforces against, and set Django's own expiry as the
    belt-and-braces outer bound.

    Fires for every path that ends in Django's ``login()``: local password
    login, SSO's ``sociallogin.connect()``/signup, invite acceptance, the
    first-run wizard, and impersonation start/stop. Impersonation is
    deliberately NOT excluded here (unlike ``apps.core.auth_events``): an
    impersonated session is a genuinely new session and correctly gets its
    own fresh stamps -- its own tighter time-box
    (``apps.core.impersonation.SESSION_EXPIRES_AT``) still applies on top.
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    now = _now()
    policy = current_policy()
    # The remember-me checkbox is not yet built (deferred; see InstanceConfig
    # .session_remember_me_enabled's docstring), so this is always False
    # today -- the read is here so a future login form only has to POST the
    # field, not touch this receiver.
    remember = bool(policy.remember_me_enabled and request.POST.get("remember_me"))

    session["sp_login_at"] = now
    session["sp_last_seen"] = now
    session["sp_epoch"] = user.auth_epoch
    session["sp_remember"] = remember
    # OrgSSOAdapter.pre_social_login stamps this request attribute before
    # allauth's login() reaches here (apps/accounts/adapters.py) -- the same
    # handoff apps.core.auth_events uses to tell auth.login apart from a
    # password login. Recorded in the session (not just the request) because
    # the MFA redirect-lock needs to know "was THIS session established via
    # SSO" on every later request, long after the login request is gone.
    session["sp_via_sso"] = bool(getattr(request, "_trellum_sso_provider", ""))

    if policy.expire_at_browser_close and not remember:
        session.set_expiry(0)
    else:
        window = policy.remember_me_seconds if remember else policy.idle_seconds
        if not window:
            # Idle enforcement is off (or, for remember-me, its window is
            # zero) -- fall back to the absolute cap, or failing that the
            # env-level SESSION_COOKIE_AGE outer bound. set_expiry(0) would
            # mean "browser-close" to Django, which is not what "idle
            # disabled" should mean, so 0 is never passed through here.
            from django.conf import settings

            window = policy.absolute_seconds or settings.SESSION_COOKIE_AGE
        session.set_expiry(window)

    _upsert_user_session_row(request, user, remember)


def _upsert_user_session_row(request, user, remember: bool) -> None:
    from apps.accounts.models import UserSession
    from apps.core.net import client_ip

    session = request.session
    if not session.session_key:
        session.save()  # ensure a key exists to key the registry row on
    if not session.session_key:
        return  # sessionless test client / RequestFactory edge case
    meta = getattr(request, "META", None) or {}
    UserSession.objects.update_or_create(
        session_key=session.session_key,
        defaults={
            "user": user,
            "last_seen": _aware_now(),
            "ip": client_ip(request),
            "user_agent": str(meta.get("HTTP_USER_AGENT", "") or "")[:256],
            "remember_me": remember,
        },
    )


def _aware_now():
    from django.utils import timezone

    return timezone.now()


# ── Enforcement ───────────────────────────────────────────────────────────

class SessionSecurityMiddleware:
    """Enforces the session policy retroactively, per request.

    Registered immediately after ``AuthenticationMiddleware`` (needs
    ``request.user``) and before ``ImpersonationMiddleware`` (killing the
    session must also end any impersonation riding on it) -- see
    ``trellum_portal/settings/base.py``.

    Anything unauthenticated is naturally exempt: the whole body below is
    skipped unless ``request.user.is_authenticated``, so ``/healthz``,
    static files, ``/login`` itself, and the no-login unsubscribe endpoint
    need no explicit allowlist.
    """

    #: Paths the forced-MFA-enrollment redirect-lock must never intercept, or
    #: the user could never reach the page that lets them finish enrolling,
    #: nor log out. Compare exact paths so unrelated security actions cannot
    #: be reached before enrollment.
    _MFA_LOCK_EXEMPT_PATHS = ("/account/security/mfa/setup", "/logout")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            outcome = self._enforce(request, user)
            if outcome is not None:
                return outcome
        return self.get_response(request)

    # -- session expiry (idle / absolute / instance / epoch) ---------------

    def _enforce(self, request, user):
        session = request.session
        now = _now()
        policy = current_policy()
        login_at = session.get("sp_login_at")
        last_seen = session.get("sp_last_seen")

        if login_at is None or last_seen is None:
            # A session that predates this feature, or was created by a path
            # that bypassed the login signal (a sessionless helper cycling
            # the key directly). Adopt it rather than kill it: one fresh
            # absolute window starting now. See design §3.9 -- a routine
            # upgrade must never sign out every user of every org at once.
            session["sp_login_at"] = session["sp_last_seen"] = now
            session.setdefault("sp_epoch", user.auth_epoch)
            session.setdefault("sp_via_sso", False)
        else:
            reason = self._expiry_reason(policy, user, session, login_at, last_seen, now)
            if reason is not None:
                return self._expire(request, reason)
            if now - last_seen >= _COALESCE_SECONDS:
                session["sp_last_seen"] = now
                self._touch_user_session_row(session, now)
        return self._mfa_lock(request, user)

    @staticmethod
    def _expiry_reason(policy: SessionPolicy, user, session, login_at, last_seen, now) -> str | None:
        if policy.invalidated_at and login_at < policy.invalidated_at.timestamp():
            return "revoked"
        if session.get("sp_epoch") != user.auth_epoch:
            return "revoked"
        if policy.absolute_seconds and now - login_at > policy.absolute_seconds:
            return "expired"
        idle = policy.remember_me_seconds if session.get("sp_remember") else policy.idle_seconds
        if idle and now - last_seen > idle:
            return "idle"
        return None

    def _expire(self, request, reason: str):
        from django.contrib.auth import logout as auth_logout
        from django.http import JsonResponse
        from django.shortcuts import redirect

        from apps.core.permissions import wants_json

        path = request.get_full_path() if request.method == "GET" else None
        auth_logout(request)  # flushes the session; fires auth.logout via apps.core.auth_events
        if wants_json(request):
            return JsonResponse({"error": "session_expired", "reason": reason}, status=401)
        from urllib.parse import urlencode

        params = {"reason": reason}
        if path and path != "/login":
            params["next"] = path
        return redirect(f"/login?{urlencode(params)}")

    def _touch_user_session_row(self, session, now: int) -> None:
        from apps.accounts.models import UserSession

        key = session.session_key
        if not key:
            return
        UserSession.objects.filter(session_key=key).update(last_seen=_aware_now())

    # -- forced MFA enrollment redirect-lock --------------------------------

    def _mfa_lock(self, request, user):
        from apps.core.permissions import wants_json

        from apps.accounts import mfa as mfa_mod

        if not mfa_mod.enrollment_required(request, user):
            return None
        if request.path in self._MFA_LOCK_EXEMPT_PATHS or request.path.startswith("/static/"):
            return None
        if wants_json(request):
            from django.http import JsonResponse

            return JsonResponse(
                {"error": "mfa_enrollment_required", "enrollment_url": "/account/security/mfa/setup"},
                status=403,
            )
        from django.shortcuts import redirect

        return redirect("/account/security/mfa/setup")


# ── Invalidation helpers ────────────────────────────────────────────────────

def sign_out_everywhere(request) -> None:
    """"Sign out my other sessions": bump the user's auth_epoch (kills every
    OTHER session on its next request) and immediately re-stamp the
    initiating request's own session so it does not also self-lock the next
    time this same request's owner clicks something.
    """
    user = request.user
    user.bump_auth_epoch()
    if getattr(request, "session", None) is not None:
        request.session["sp_epoch"] = user.auth_epoch


def sign_everyone_out() -> None:
    """Operator "Sign everyone out": every session on the instance, on its
    next request. No sweep -- see the module docstring."""
    from apps.core.models import InstanceConfig

    cfg = InstanceConfig.load()
    cfg.sessions_invalidated_at = _aware_now()
    cfg.save(update_fields=["sessions_invalidated_at", "updated_at"])
    invalidate_policy_cache()


def revoke_session(session_key: str) -> None:
    """End one specific session immediately -- the account page's per-row
    "Sign out" and the admin/operator "revoke session" action."""
    from django.contrib.sessions.backends.db import SessionStore

    from apps.accounts.models import UserSession

    SessionStore(session_key=session_key).delete()
    UserSession.objects.filter(session_key=session_key).delete()


def dead_user_sessions():
    """Queryset of ``UserSession`` rows whose ``django_session`` row no
    longer exists -- expired, revoked, or replaced. Used by
    ``apps.core.retention.purge_user_sessions``; a queryset (not a count) so
    the caller can both count it (dry-run) and batch-delete it."""
    from django.contrib.sessions.models import Session

    from apps.accounts.models import UserSession

    live_keys = Session.objects.values_list("session_key", flat=True)
    return UserSession.objects.exclude(session_key__in=live_keys)
