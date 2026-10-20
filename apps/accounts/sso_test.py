"""The org admin's "Test sign-in": verify single sign-on end to end before
enforcing it, without logging anyone in.

OpenID Connect. The settings page POSTs ``test-signin``; :func:`begin` puts a
short-lived, signed marker in the *admin's own session* and sends the browser
into the normal allauth flow. When the callback arrives,
``PublicURLAdapter.complete_login`` asks :func:`consume` whether this run is a
test, before allauth looks up and updates a linked identity. If so it never
gets to log in, link, provision or sync groups:
:func:`finish_oidc` evaluates what *would* happen, records the result and
redirects back to the settings page, and allauth's session handling is never
reached -- the admin is the same user afterwards as before.

The marker is what keeps this from being a back door, so it is:

* signed (``django.core.signing``) and kept in the server-side session, so it
  cannot be forged or carried to another browser;
* bound to one org and one admin -- the callback must be for that org's
  provider and arrive on a session still logged in as that admin;
* single-use -- popped the first time any callback looks at it;
* expiring -- ``TTL_SECONDS`` after it was issued.

Anything else (no marker, expired, wrong admin, wrong org) is an ordinary
login, handled exactly as before.

Directory. :func:`run_directory` checks a typed email and password with
``ldap.authenticate`` and evaluates the same rules; it creates and changes
nothing, and never stores or audits the password.
"""
from __future__ import annotations

import secrets
import time

from django.core import signing
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.accounts import ldap, sso
from apps.core.audit import audit

SESSION_MARKER = "sso_test_signin"
SESSION_RESULT = "sso_test_result"
SALT = "trellum.sso.test-signin"
TTL_SECONDS = 600

PASS, FAIL, WARN, INFO = "pass", "fail", "warn", "info"


# ── the session marker ───────────────────────────────────────────────────
def begin(request, org) -> None:
    """Mark the admin's session as running a test for ``org``."""
    request.session[SESSION_MARKER] = signing.dumps(
        {"org": org.pk, "uid": request.user.pk, "ts": time.time(), "n": secrets.token_hex(8)},
        salt=SALT,
    )


def consume(request, cfg) -> bool:
    """True when this callback is the admin's own, unexpired test for ``cfg``'s
    org. Burns the marker whenever it is looked at for this org (or is
    unreadable), so a marker works once."""
    session = getattr(request, "session", None)
    token = session.get(SESSION_MARKER) if session is not None else None
    if not token:
        return False
    try:
        data = signing.loads(token, salt=SALT, max_age=TTL_SECONDS)
    except signing.BadSignature:  # includes SignatureExpired
        session.pop(SESSION_MARKER, None)
        return False
    if data.get("org") != cfg.org_id:
        return False  # another org's callback: not ours to burn, it will expire
    session.pop(SESSION_MARKER, None)
    user = getattr(request, "user", None)
    return bool(
        user is not None and user.is_authenticated and user.pk == data.get("uid")
    )


# ── evaluation ───────────────────────────────────────────────────────────
def _check(level: str, label: str, detail: str = "") -> dict:
    return {"level": level, "label": label, "detail": detail}


def _routing(cfg, email: str) -> dict:
    from apps.orgs import domains

    domain = domains.normalise(email)
    if domain in domains.routable_domains(cfg):
        return _check(PASS, "Email domain routes to single sign-on", domain)
    if domain in domains.unroutable_domains(cfg):
        return _check(
            FAIL, "Email domain routes to single sign-on",
            f"{domain} is listed but not verified. Verify it under Domain ownership.",
        )
    return _check(
        FAIL, "Email domain routes to single sign-on",
        f"{domain} is not in this organization's email domains.",
    )


def _groups(cfg, claims: dict, existing) -> list[dict]:
    plan = sso.plan_group_sync(cfg, claims)
    if not cfg.group_map:
        return [_check(
            INFO, "Groups claim",
            f"{plan.count} group(s) in claim '{cfg.groups_claim}'. No group mapping is configured."
            if plan.present else "No group mapping is configured.",
        )]
    if plan.overflow:
        return [_check(
            WARN, "Groups claim",
            f"'{cfg.groups_claim}' overflowed the token; group sync would be skipped "
            "and existing memberships left as they are.",
        )]
    if not plan.present:
        return [_check(
            WARN, "Groups claim",
            f"No '{cfg.groups_claim}' claim in the token. Every mapped permission group "
            "would be removed. Check the groups claim name and extra scopes.",
        )]
    held = set()
    if existing is not None:
        held = _held(existing)
    grant = [g.name for g in plan.grant if g.pk not in held]
    revoke = [g.name for g in plan.revoke if g.pk in held]
    return [
        _check(PASS, "Groups claim found", f"{plan.count} group(s) in claim '{cfg.groups_claim}'"),
        _check(
            INFO, "Permission groups that would be granted",
            ", ".join(grant) if grant else "none",
        ),
        _check(
            INFO, "Permission groups that would be removed",
            ", ".join(revoke) if revoke else "none",
        ),
    ]


def _held(user) -> set:
    from apps.orgs.models import PermissionGroupMembership

    return set(PermissionGroupMembership.objects.filter(user=user).values_list("group_id", flat=True))


def _admission(cfg, email: str, *, linked: bool, verified_ok: bool, routed: bool):
    """(check, existing user) for the outcome a real login would reach."""
    from apps.accounts.adapters import admission

    user, refusal = admission(cfg, email)
    label = "Admission outcome"
    if not routed:
        return _check(FAIL, label, "Would be refused: the email domain does not route."), user
    if linked:
        return _check(PASS, label, "Would sign in the existing linked account."), user
    if not verified_ok:
        return _check(FAIL, label, "Would be refused: the identity provider did not verify the email."), user
    if refusal is not None:
        return _check(FAIL, label, f"Would be refused: {refusal[1]}"), user
    if user is not None and not user.is_active:
        return _check(FAIL, label, "Would be refused: the portal account is disabled."), user
    if user is not None:
        return _check(PASS, label, f"Would sign in the existing member {user.email}."), user
    return _check(PASS, label, f"Would create a new account for {email}."), user


def evaluate_oidc(cfg, sociallogin) -> dict:
    from apps.accounts.adapters import email_is_verified

    claims = sso.claims(sociallogin)
    checks = [_check(PASS, "Identity provider reached", "The sign-in completed and the token was accepted.")]
    email = sso.extract_email(sociallogin)
    if not email or "@" not in email:
        checks.append(_check(FAIL, "Email claim found", "Your identity provider did not supply an email address."))
        return _summary("oidc", checks, email="")
    checks.append(_check(PASS, "Email claim found", email))
    routing = _routing(cfg, email)
    checks.append(routing)
    verified = email_is_verified(cfg, sociallogin, email)
    checks.append(_check(
        PASS if verified else FAIL, "Email verified by the identity provider",
        "" if verified else "The provider did not mark this email as verified.",
    ))
    linked = bool(sociallogin.is_existing and sociallogin.account.pk is not None)
    outcome, existing = _admission(
        cfg, email, linked=linked, verified_ok=verified, routed=routing["level"] == PASS,
    )
    checks.append(outcome)
    if linked:
        existing = sociallogin.user
    checks.extend(_groups(cfg, claims, existing))
    return _summary("oidc", checks, email=email)


def evaluate_directory(cfg, info: dict, typed_login: str) -> dict:
    checks = [_check(PASS, "Directory reached", "The directory accepted the password.")]
    email = info["email"] or typed_login
    if not email or "@" not in email:
        checks.append(_check(FAIL, "Email attribute found", f"No email in the '{cfg.ldap_email_attr}' attribute."))
        return _summary("directory", checks, email="")
    checks.append(_check(PASS, "Email attribute found", email))
    routing = _routing(cfg, email)
    checks.append(routing)
    outcome, existing = _admission(
        cfg, email, linked=False, verified_ok=True, routed=routing["level"] == PASS,
    )
    checks.append(outcome)
    checks.extend(_groups(cfg, {cfg.groups_claim: info["groups"]}, existing))
    return _summary("directory", checks, email=email)


def _summary(source: str, checks: list[dict], *, email: str) -> dict:
    return {
        "source": source,
        "email": email,
        "ok": not any(c["level"] == FAIL for c in checks),
        "checks": checks,
    }


# ── recording ────────────────────────────────────────────────────────────
def record(request, cfg, summary: dict) -> None:
    """Persist the result on the config, put it in the session for the
    settings page, and audit it. ``summary`` holds no tokens or passwords."""
    cfg.last_test_at = timezone.now()
    cfg.last_test_by = request.user if request.user.is_authenticated else None
    cfg.last_test_ok = summary["ok"]
    cfg.last_test_summary = summary
    cfg.save(update_fields=["last_test_at", "last_test_by", "last_test_ok", "last_test_summary"])
    request.session[SESSION_RESULT] = summary
    audit(
        request, "sso.test_signin", target=cfg.org, org=cfg.org,
        outcome="success" if summary["ok"] else "failure",
        source=summary["source"], asserted_email=summary["email"],
        # Labels and levels only: the details quote addresses, and erasure
        # (apps.orgs.personal_data) scrubs only the asserted_email key.
        checks=[{"label": c["label"], "level": c["level"]} for c in summary["checks"]],
    )


def settings_url(org) -> str:
    return reverse("org-sso", kwargs={"org_slug": org.slug})


def finish_oidc(request, cfg, sociallogin):
    """The response for a callback that was an admin's test: evaluate, record,
    and send the admin back to the settings page. Writes nothing but the
    result."""
    record(request, cfg, evaluate_oidc(cfg, sociallogin))
    return redirect(settings_url(cfg.org))


def run_directory(request, cfg, login: str, password: str) -> dict:
    """Check a directory login for the settings page's test form."""
    from apps.accounts import throttle

    if throttle.is_throttled(login, request):
        summary = _summary("directory", [_check(
            FAIL, "Directory reached", "Too many attempts for this account. Try again later.",
        )], email=login)
    else:
        try:
            info = ldap.authenticate(cfg, login, password)
        except ldap.LDAPError as exc:
            summary = _summary("directory", [_check(FAIL, "Directory reached", str(exc))], email=login)
        else:
            if info is None:
                throttle.record_failure(login, request)
                summary = _summary("directory", [
                    _check(PASS, "Directory reached"),
                    _check(
                        FAIL, "Credentials accepted",
                        "No single matching entry, or the password was wrong.",
                    ),
                ], email=login)
            else:
                summary = evaluate_directory(cfg, info, login)
    record(request, cfg, summary)
    return summary
