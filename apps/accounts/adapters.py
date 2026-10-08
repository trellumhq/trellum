"""allauth adapters.

Local accounts are managed by our own views (login, invites, bootstrap);
allauth drives only the per-org OpenID Connect flow. ``OrgSSOAdapter`` is the
single multi-tenant integration point — deliberately isolated here because
it rides allauth's semi-internal app-resolution surface (pinned version;
tests break loudly on upgrade).

Login rules enforced in ``pre_social_login`` (see the SSO milestone spec):

1. The IdP-asserted email's domain MUST be one of the org's configured
   ``email_domains`` — an org's IdP can never assert emails outside its own
   domains (blocks cross-tenant identity claims).
2. Existing portal users are auto-linked only if they are already a member
   of the org (invited beforehand). Otherwise:
3. New users are auto-provisioned only when the org enables it.

Rules 2 and 3 are ``admission()`` below, shared with the directory login in
``apps.accounts.views`` so an LDAP org and an OpenID Connect org admit the
same people; ``denied()`` is the one audit-and-refuse path for both.
"""
from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.shortcuts import render

from apps.accounts import sso
from apps.core.audit import audit

#: auth.sso_denied reason codes -- one per refusal path in pre_social_login,
#: in the order they can fire. internal planning#76's whole point was that
#: these were invisible; the code is the stable, filterable thing the
#: dashboard and any downstream alerting key on, the ``message`` argument to
#: _denied() stays free-text for the human on the 403 page.
REASON_NOT_ENABLED = "sso_not_enabled"
REASON_NO_EMAIL = "no_email_claim"
REASON_UNVERIFIED_EMAIL = "unverified_email_claim"
REASON_OUTSIDE_DOMAINS = "outside_verified_domains"
REASON_EXISTING_NON_MEMBER = "existing_non_member_account"
REASON_AUTO_PROVISION_OFF = "auto_provision_disabled"
#: Directory login only: the directory could not be reached or answered with
#: an error (outcome ``failure``, not ``denied`` -- nobody was refused).
REASON_DIRECTORY_ERROR = "directory_error"


def denied(
    request, message: str, *, reason_code: str,
    provider: str = "", asserted_email: str = "", org=None,
):
    """Audit ``auth.sso_denied`` and render the 403 page."""
    audit(
        request, "auth.sso_denied", outcome="denied", org=org,
        reason_code=reason_code, provider=provider, asserted_email=asserted_email,
    )
    return render(request, "accounts/sso_denied.html", {"problem": message}, status=403)


def admission(cfg, email: str):
    """Who may sign in to ``cfg.org`` as ``email``, once the identity source
    has vouched for the address.

    ``(user, None)``: an existing member -- log them in. ``(None, None)``: no
    account yet and the org may create one. ``(None, (reason_code, message))``:
    refused. Checked before anything is written, so a refusal leaves nothing
    half-created to unwind.
    """
    from apps.orgs.models import OrgMembership

    existing = get_user_model().objects.filter(email__iexact=email).first()
    if existing is not None:
        if not OrgMembership.objects.filter(user=existing, org=cfg.org).exists():
            return None, (
                REASON_EXISTING_NON_MEMBER,
                (
                    f"{email} already has a portal account that is not a member of "
                    f"{cfg.org.name}. Ask an org admin for an invitation first."
                ),
            )
        return existing, None
    if not cfg.auto_provision:
        return None, (
            REASON_AUTO_PROVISION_OFF,
            (
                f"No portal account exists for {email} and {cfg.org.name} does not "
                f"auto-create accounts. Ask an org admin for an invitation."
            ),
        )
    return None, None


def _trusted_entra_upn(cfg, sociallogin, email: str) -> bool:
    """Only a tenant-specific Entra ID token can vouch for a mailbox-less UPN."""
    from urllib.parse import urlsplit
    from uuid import UUID

    issuer = urlsplit(cfg.issuer_url)
    if (
        issuer.scheme != "https" or issuer.netloc != "login.microsoftonline.com"
        or issuer.query or issuer.fragment
    ):
        return False
    parts = issuer.path.strip("/").split("/")
    if len(parts) != 2 or parts[1] != "v2.0":
        return False
    try:
        tenant = str(UUID(parts[0]))
    except ValueError:
        return False
    token = (sociallogin.account.extra_data or {}).get("id_token") or {}
    return (
        not sso.claims(sociallogin).get("email")
        and token.get("tid", "").lower() == tenant
        and token.get("preferred_username", "").strip().lower() == email.lower()
    )


class NoSignupAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):  # noqa: ARG002
        return False  # portal accounts come from /setup, invites, or SSO


class OrgSSOAdapter(DefaultSocialAccountAdapter):
    # ── app resolution: transient apps from OrgSSOConfig ────────────────
    def list_apps(self, request, provider=None, client_id=None):
        apps = super().list_apps(request, provider=provider, client_id=client_id)
        for cfg in sso.enabled_configs():
            if cfg.is_ldap:
                continue  # a directory has no OpenID Connect app
            app = sso.build_transient_app(cfg)
            if provider and provider not in ("openid_connect", app.provider_id):
                continue
            if client_id and app.client_id != client_id:
                continue
            apps.append(app)
        return apps

    # ── signup policy ────────────────────────────────────────────────────
    def is_open_for_signup(self, request, sociallogin):  # noqa: ARG002
        cfg = sso.config_for_provider(sociallogin.account.provider)
        return bool(cfg and cfg.auto_provision)

    # ── the gate ─────────────────────────────────────────────────────────
    def pre_social_login(self, request, sociallogin):
        provider = sociallogin.account.provider
        cfg = sso.config_for_provider(provider)
        if cfg is None:
            # A disabled/never-configured provider id can still name a real
            # org (oidc-<slug>) -- resolve it so a denial for "SSO turned
            # off after this link was bookmarked" still lands on the right
            # org's trail rather than nowhere.
            from apps.orgs.models import Organization

            slug = sso.org_slug_from_provider(provider)
            org = Organization.objects.filter(slug=slug).first() if slug else None
            raise ImmediateHttpResponse(
                denied(
                    request, "Single sign-on is not enabled for this organization.",
                    reason_code=REASON_NOT_ENABLED, provider=provider, org=org,
                )
            )
        # The rest of this method only runs for a live, enabled config, so
        # every login that gets this far is unambiguously SSO -- stamp the
        # request now so auth_events._logged_in can tell auth.login apart from
        # a password login. Read only if the login actually succeeds below;
        # a denial past this point never fires user_logged_in, so a stale
        # attribute on a refused request is simply never looked at.
        request._trellum_sso_provider = provider

        email = sso.extract_email(sociallogin)
        if not email or "@" not in email:
            raise ImmediateHttpResponse(
                denied(
                    request, "Your identity provider did not supply an email address.",
                    reason_code=REASON_NO_EMAIL, provider=provider, org=cfg.org,
                )
            )
        from apps.orgs import domains as domain_service

        domain = domain_service.normalise(email)
        if domain not in domain_service.routable_domains(cfg):
            raise ImmediateHttpResponse(
                denied(
                    request,
                    f"The account {email} is outside this organization's "
                    f"verified domains.",
                    reason_code=REASON_OUTSIDE_DOMAINS, provider=provider,
                    asserted_email=email, org=cfg.org,
                )
            )

        if sociallogin.is_existing and sociallogin.account.pk is not None:
            # Returning SSO user: refresh the group mapping and move on.
            # (Membership is NOT re-created: if an admin removed them, SSO
            # login still works but shows an empty portal.)
            sso.sync_groups(
                sociallogin.user, cfg, sso.claims(sociallogin)
            )
            return

        identity_claims = sso.claims(sociallogin)
        verified = (
            (
                identity_claims.get("email_verified") is True
                and identity_claims.get("email", "").lower() == email.lower()
            )
            or any(a.email.lower() == email.lower() and a.verified is True for a in sociallogin.email_addresses)
        )
        if ("email_verified" in identity_claims and identity_claims["email_verified"] is not True) or not (
            verified or _trusted_entra_upn(cfg, sociallogin, email)
        ):
            raise ImmediateHttpResponse(
                denied(
                    request, "Your identity provider must verify your email before linking an account.",
                    reason_code=REASON_UNVERIFIED_EMAIL, provider=provider,
                    asserted_email=email, org=cfg.org,
                )
            )
        if not sociallogin.email_addresses:
            from allauth.account.models import EmailAddress

            sociallogin.email_addresses = [EmailAddress(email=email, verified=True, primary=True)]

        existing, refusal = admission(cfg, email)
        if refusal is not None:
            reason_code, message = refusal
            raise ImmediateHttpResponse(
                denied(
                    request, message, reason_code=reason_code, provider=provider,
                    asserted_email=email, org=cfg.org,
                )
            )
        if existing is not None:
            sociallogin.connect(request, existing)  # links + logs in
            sso.sync_groups(existing, cfg, sso.claims(sociallogin))
            return
        # Fall through: allauth proceeds to signup; save_user() provisions.

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.email = sso.extract_email(sociallogin) or user.email
        user.name = (
            sso.claims(sociallogin).get("name") or user.name or ""
        )
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        cfg = sso.config_for_provider(sociallogin.account.provider)
        if cfg is not None:
            sso.provision_membership(user, cfg)
            sso.sync_groups(user, cfg, sso.claims(sociallogin))
            # The user is not logged in yet at this point in allauth's flow
            # (complete_social_login()'s login() call happens after save_user
            # returns), so request.user is still anonymous -- actor is passed
            # explicitly, the same trick auth_events uses for auth.login.
            # Auto-provisioning otherwise leaves no authz trace at all: the
            # membership row appears with nothing in the trail explaining why.
            audit(
                request, "member.provisioned", target=user, actor=user, org=cfg.org,
                provider=sociallogin.account.provider, via="sso_auto_provision",
            )
        return user
