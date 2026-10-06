"""Per-org OpenID Connect single sign-on glue.

Each org with an enabled :class:`~apps.orgs.models.OrgSSOConfig` becomes one
transient allauth ``openid_connect`` app (``provider_id = oidc-<org-slug>``)
built on demand from the encrypted DB row — client secrets never land in
allauth's tables. Any OpenID Connect issuer works (Keycloak, Okta, Google,
Microsoft Entra ID, ...). See ``adapters.OrgSSOAdapter`` for the login rules.
"""
from __future__ import annotations

import logging

import requests
from django.urls import reverse

log = logging.getLogger(__name__)

PROVIDER_PREFIX = "oidc-"
#: allauth's own default for openid_connect; restated because a per-app
#: ``scope`` replaces the default rather than extending it.
DEFAULT_SCOPES = ("openid", "profile", "email")


def provider_id_for_org(org) -> str:
    return f"{PROVIDER_PREFIX}{org.slug}"


def org_slug_from_provider(provider_id: str) -> str | None:
    if provider_id.startswith(PROVIDER_PREFIX):
        return provider_id[len(PROVIDER_PREFIX):]
    return None


def config_for_provider(provider_id: str):
    """OrgSSOConfig for a provider id, or None."""
    from apps.orgs.models import OrgSSOConfig

    slug = org_slug_from_provider(provider_id)
    if not slug:
        return None
    return (
        OrgSSOConfig.objects.filter(
            org__slug=slug, org__is_active=True, enabled=True,
            auth_method=OrgSSOConfig.AUTH_OIDC,
        )
        .select_related("org")
        .first()
    )


def build_transient_app(cfg):
    """An in-memory SocialApp for one org's identity provider (never saved)."""
    from allauth.socialaccount.models import SocialApp

    app = SocialApp(
        provider="openid_connect",
        provider_id=provider_id_for_org(cfg.org),
        name=f"{cfg.org.name} single sign-on",
        client_id=cfg.client_id,
        secret=cfg.client_secret or "",
        key="",
    )
    app.settings = {"server_url": cfg.issuer_url}
    if cfg.extra_scopes:
        # Some providers only emit the groups claim for a scope of their own
        # (``groups`` at Okta's org server and OneLogin, ``allatclaims`` at
        # AD FS); every other provider leaves this empty.
        app.settings["scope"] = [*DEFAULT_SCOPES, *cfg.extra_scopes.split()]
    return app


def enabled_configs():
    """Enabled rows that are complete enough to authenticate someone: an
    OpenID Connect row needs a client id and an issuer URL, a directory row a
    server and a search base. Callers that build allauth apps skip the
    directory rows (``cfg.is_ldap``)."""
    from django.db.models import Q

    from apps.orgs.models import OrgSSOConfig

    oidc = (
        Q(auth_method=OrgSSOConfig.AUTH_OIDC)
        & ~Q(client_id="")
        & ~Q(issuer_url="")
    )
    ldap = (
        Q(auth_method=OrgSSOConfig.AUTH_LDAP)
        & ~Q(ldap_server_uri="")
        & ~Q(ldap_user_search_base="")
    )
    return (
        OrgSSOConfig.objects.filter(enabled=True, org__is_active=True)
        .filter(oidc | ldap)
        .select_related("org")
    )


def config_for_email_domain(email: str):
    """The org SSO config that may route this email's domain, or None.

    A claimed domain is not enough on a shared instance: whoever routes a
    domain decides who may authenticate as its users, so on a multi-tenant
    instance the org must have proved ownership by DNS. See
    :mod:`apps.orgs.domains`.
    """
    from apps.orgs import domains

    domain = domains.normalise(email)
    for cfg in enabled_configs():
        if domain in domains.routable_domains(cfg):
            return cfg
    return None


def enforced_config_for_user(email: str):
    """The SSO config of an org that MANDATES SSO for this user, or None.

    Applies to existing members of an enforcing org regardless of email
    domain. Instance operators are exempt: if SSO breaks, someone must
    still be able to log in and fix it.
    """
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(email__iexact=email).first()
    if user is None or user.is_superuser:
        return None
    return (
        enabled_configs()
        .filter(enforce_sso=True, org__memberships__user=user)
        .first()
    )


def provider_login_url(request, cfg) -> str:  # noqa: ARG001
    return reverse(
        "openid_connect_login", kwargs={"provider_id": provider_id_for_org(cfg.org)}
    )


def redirect_uri_for_org(org) -> str:
    """Shown read-only in settings: paste into the client registered at the IdP."""
    from apps.core.instance import base_url

    path = reverse(
        "openid_connect_callback", kwargs={"provider_id": provider_id_for_org(org)}
    )
    return f"{base_url()}{path}"


def discover(issuer: str) -> dict:
    """Fetch and sanity-check the issuer's OpenID Connect discovery document.

    Runs when an admin saves the config, so a wrong issuer fails there with
    a readable message instead of at someone's first login. Same ``requests``
    client allauth uses for the flow itself, so ``REQUESTS_CA_BUNDLE`` covers
    a private CA for both. Isolated so tests can replace it.
    """
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        resp = requests.get(url, timeout=5)
    except requests.RequestException as exc:
        raise ValueError(f"Could not fetch {url}: {exc}") from exc
    if resp.status_code != 200:
        raise ValueError(f"{url} answered HTTP {resp.status_code}.")
    try:
        doc = resp.json()
    except ValueError as exc:
        raise ValueError(f"{url} is not a JSON document.") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{url} is not a JSON object.")
    for key in ("authorization_endpoint", "token_endpoint"):
        if not doc.get(key):
            raise ValueError(f"{url} has no {key}.")
    found = str(doc.get("issuer") or "")
    if found.rstrip("/") != issuer.rstrip("/"):
        raise ValueError(f"{url} names the issuer {found or '(none)'}, not {issuer}.")
    return doc


def claims(sociallogin) -> dict:
    """One flat claims dict from allauth's extra_data.

    Since allauth 65.11 extra_data is nested: {"userinfo": {...},
    "id_token": {...}} — and Entra puts different claims in each place
    (userinfo may lack email entirely for mailbox-less users, while the
    ID token carries preferred_username and the groups claim). Merge
    id_token first, overlay userinfo, so both worlds are visible.
    Pre-65.11 flat dicts pass through unchanged."""
    data = sociallogin.account.extra_data or {}
    if "userinfo" in data or "id_token" in data:
        merged: dict = {}
        merged.update(data.get("id_token") or {})
        merged.update(data.get("userinfo") or {})
        return merged
    return data


def extract_email(sociallogin) -> str:
    c = claims(sociallogin)
    # Entra: 'email' when the account has a mailbox; otherwise the UPN in
    # 'preferred_username' (id_token) is the login identity.
    return (c.get("email") or c.get("preferred_username") or c.get("upn") or "").strip()


def sync_groups(user, cfg, claims: dict) -> None:
    """Map identity-provider groups -> portal PermissionGroups, on every login.

    Reads the claim named by ``cfg.groups_claim`` (``groups`` by default;
    ``roles`` or a namespaced claim elsewhere). Only groups referenced in
    the map are touched; memberships granted in the portal UI for unmapped
    groups are left alone.
    """
    from apps.orgs.models import PermissionGroup, PermissionGroupMembership

    mapping = cfg.group_map or {}
    if not mapping:
        return
    if cfg.groups_claim in (claims.get("_claim_names") or {}):
        # Group overage (Entra, above 200 groups): the list was replaced by a
        # pointer to a directory endpoint the portal does not follow. Leave
        # the user's mapped memberships as they are rather than revoking them
        # all for a claim that is merely elsewhere.
        log.warning("SSO groups claim for %s overflowed the token; sync skipped", user.email)
        return
    asserted = claims.get(cfg.groups_claim) or []
    if isinstance(asserted, str):  # a lone group comes as a bare string from some IdPs
        asserted = [asserted]
    # DNs are case-insensitive and directories return them in their own
    # casing; OpenID Connect group ids stay exact.
    fold = str.lower if cfg.is_ldap else str
    asserted = {fold(g) for g in asserted}
    for idp_group, group_pk in mapping.items():
        group = PermissionGroup.objects.filter(pk=group_pk, org=cfg.org).first()
        if group is None:
            continue
        if fold(idp_group) in asserted:
            PermissionGroupMembership.objects.get_or_create(user=user, group=group)
        else:
            PermissionGroupMembership.objects.filter(user=user, group=group).delete()


def provision_membership(user, cfg) -> None:
    """Attach org membership + default groups (idempotent)."""
    from apps.orgs.models import OrgMembership, PermissionGroupMembership

    OrgMembership.objects.get_or_create(
        user=user, org=cfg.org, defaults={"role": cfg.default_org_role or "member"}
    )
    for group in cfg.default_groups.all():
        PermissionGroupMembership.objects.get_or_create(user=user, group=group)
