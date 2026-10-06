"""Operator "act as user" support.

Support work needs to see what a customer sees. Three rules make that safe:

1. **Always attributable.** Both ends are written to the audit log, and every
   action taken while impersonating is attributed to the impersonated user with
   the operator recorded alongside — so the trail never says "the customer did
   this" when an operator did.
2. **Time-boxed.** The session expires on its own; a forgotten impersonation is
   not a permanent backdoor.
3. **Visible.** The shell renders a persistent banner with an exit control, so
   nobody forgets whose account they are in.

Operators cannot impersonate other operators — that would let a compromised
support account escalate sideways into a colleague's privileges.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.utils import timezone

SESSION_OPERATOR_ID = "impersonator_id"
SESSION_EXPIRES_AT = "impersonation_expires_at"


class ImpersonationError(Exception):
    pass


def _session(request):
    """Sessionless requests exist (RequestFactory, some middleware order), and
    a missing session simply means nobody is impersonating."""
    return getattr(request, "session", None)


def is_impersonating(request) -> bool:
    session = _session(request)
    return bool(session and session.get(SESSION_OPERATOR_ID))


def operator_of(request):
    """The real operator behind an impersonated session, if any."""
    session = _session(request)
    operator_id = session.get(SESSION_OPERATOR_ID) if session else None
    if not operator_id:
        return None
    return get_user_model().objects.filter(pk=operator_id).first()


def start(request, target):
    """Begin acting as ``target``. Returns the audit metadata."""
    from django.conf import settings

    if not settings.TRELLUM_IMPERSONATION_ENABLED:
        raise ImpersonationError("impersonation is disabled on this instance")

    operator = request.user
    if not getattr(operator, "is_operator", False):
        raise ImpersonationError("operator access required")
    if is_impersonating(request):
        raise ImpersonationError("already impersonating; end that session first")
    if target.pk == operator.pk:
        raise ImpersonationError("cannot impersonate yourself")
    if getattr(target, "is_operator", False):
        # Sideways escalation: a compromised support account must not be able
        # to reach a colleague's privileges.
        raise ImpersonationError("cannot impersonate another operator")
    if not target.is_active:
        raise ImpersonationError("cannot impersonate a deactivated account")

    operator_id = operator.pk
    expires_at = timezone.now() + timezone.timedelta(
        minutes=settings.TRELLUM_IMPERSONATION_MINUTES
    )
    # login() cycles the session key, so the markers must be written after it —
    # which means the user_logged_in signal fires before this request looks
    # like an impersonation. The audit receiver keys on this flag instead, or
    # every impersonation would also record a bare auth.login by the target and
    # read as if they signed in themselves.
    request._bi_impersonation_login = True
    login(request, target, backend="django.contrib.auth.backends.ModelBackend")
    request.session[SESSION_OPERATOR_ID] = operator_id
    request.session[SESSION_EXPIRES_AT] = expires_at.isoformat()
    return {"expires_at": expires_at.isoformat()}


def stop(request):
    """Return to the operator's own account. Returns them, or None — in which
    case the session is ended: a borrowed account with nobody to hand it back
    to must not stay signed in as the customer."""
    operator = operator_of(request)
    session = _session(request)
    if session is not None:
        session.pop(SESSION_OPERATOR_ID, None)
        session.pop(SESSION_EXPIRES_AT, None)
    if operator is None:
        if session is not None:
            logout(request)
        return None
    # Returning to your own account is `operator.impersonate.stop`, not a login.
    request._bi_impersonation_login = True
    login(request, operator, backend="django.contrib.auth.backends.ModelBackend")
    return operator


def expires_at(request):
    session = _session(request)
    raw = session.get(SESSION_EXPIRES_AT) if session else None
    if not raw:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


class ImpersonationMiddleware:
    """Ends an impersonated session once it has run out of time.

    This belongs in middleware rather than a template hook: the deadline has to
    hold for API calls too, not only for pages that happen to render the shell.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_impersonating(request):
            deadline = expires_at(request)
            if deadline is None or timezone.now() >= deadline:
                # Read before stop(), which logs the operator back in.
                target_email = getattr(request.user, "email", "")
                operator = stop(request)
                if operator is not None:
                    from apps.core.audit import audit

                    audit(
                        request,
                        "operator.impersonate.expired",
                        target_email=target_email,
                    )
        return self.get_response(request)
