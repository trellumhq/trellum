"""Auditing authentication.

The audit log recorded that a role was changed, but not who logged in, from
where, when, or how many times they failed first. That is the gap that matters
when the question is "was this account compromised?" — the trail could describe
every consequence of an intrusion and nothing about the intrusion.

Django already emits the three signals; nothing was listening. Receivers are
thin on purpose: they translate a signal into the same `audit()` call every
other mutating action uses, so impersonation attribution, client-IP resolution
and the append-only guarantees come along for free.

Failed logins are recorded with the *submitted* email rather than a user
reference, because the interesting case is an address that does not exist —
someone guessing. The password is never in `credentials` by the time it reaches
here (Django strips it), but the dictionary is not passed through wholesale
regardless.
"""
from __future__ import annotations

from django.contrib.auth import signals as auth_signals
from django.dispatch import receiver

from apps.core.audit import audit


@receiver(auth_signals.user_logged_in, dispatch_uid="core.audit.login")
def _logged_in(sender, request, user, **kwargs):  # noqa: ARG001
    # Impersonation starting or ending is not a login. It calls login() to swap
    # the session, and writes its own operator.impersonate.* row; without this
    # every impersonation would also record a bare auth.login by the target and
    # read as if they had signed in themselves.
    #
    # The flag rather than is_impersonating(): login() cycles the session key,
    # so start() can only write its markers afterwards — at signal time the
    # request does not yet look like an impersonation.
    if getattr(request, "_bi_impersonation_login", False):
        return
    # OrgSSOAdapter.pre_social_login stamps this request attribute before
    # allauth's own login() call reaches here -- the same request-attribute
    # handoff _bi_impersonation_login above uses, because a signal receiver
    # cannot ask allauth "how did this session get authenticated" any other
    # way. Its absence means an ordinary password login.
    provider = getattr(request, "_trellum_sso_provider", "")
    if provider:
        extra = {"method": "sso", "provider": provider}
    else:
        # apps.accounts.views stamps "ldap" for a directory login -- the
        # portal checked the password, but not against its own table.
        extra = {"method": getattr(request, "_trellum_login_method", "") or "password"}
    # apps.accounts.views.mfa_verify_view stamps this the same way, once a
    # factored login clears its second prompt -- lets the trail distinguish
    # plain/totp/recovery logins with no new receiver.
    mfa_method = getattr(request, "_trellum_mfa_method", "")
    if mfa_method:
        extra["mfa"] = mfa_method
    audit(request, "auth.login", target=user, actor=user, **extra)


@receiver(auth_signals.user_logged_out, dispatch_uid="core.audit.logout")
def _logged_out(sender, request, user, **kwargs):  # noqa: ARG001
    if user is None:
        return
    audit(request, "auth.logout", target=user, actor=user)


@receiver(auth_signals.user_login_failed, dispatch_uid="core.audit.login_failed")
def _login_failed(sender, credentials, request=None, **kwargs):  # noqa: ARG001
    if request is None:
        # Signals can fire outside a request (a management command calling
        # authenticate()). Without a request there is no actor and no IP, so
        # the row would say nothing.
        return
    identifier = credentials.get("username") or credentials.get("email") or ""
    audit(
        request, "auth.login_failed", outcome="failure",
        attempted=str(identifier)[:150],
    )
