"""Synthetic generic OIDC first-link regressions, not live provider validation.

The named examples exercise the common callback with deliberately supplied
verification evidence. They establish neither provider defaults nor a working
provider configuration (in particular OneLogin mappings and AD FS support).
No directory verification, claim issuance or live interoperability is tested.
Shared negative cases cover the common gate without a redundant provider matrix.
"""
import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.models import AuditLog
from apps.orgs.models import OrgMembership, OrgSSOConfig

from ._oidc_callback import (
    PROVIDER, _callback, _identity_rows, _identity_writes, mock_provider_http,
    signing_key as signing_key,
)

pytestmark = pytest.mark.django_db
User = get_user_model()
EMAIL = "callback@demo.example"
SUBJECT = "synthetic-pairwise-subject"
MISSING = object()


@pytest.fixture
def cfg(org, settings, request):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    return OrgSSOConfig.objects.create(
        org=org, enabled=True,
        issuer_url=getattr(request, "param", "https://identity.example/realms/demo"),
        client_id="generic-client", client_secret="mock-secret",
        email_domains=["demo.example"],
    )


@pytest.fixture
def provider_http(monkeypatch, signing_key, cfg):
    # Leave the production decoder unmodified: validation must reflect the
    # actual TLS code-exchange flow, not stronger test-only JWT requirements.
    return mock_provider_http(
        monkeypatch, signing_key, cfg,
        {"iss": cfg.issuer_url, "sub": SUBJECT}, verify_signature=False,
    )


def _prepare_admission(cfg, make_user, *, provision, email=EMAIL):
    cfg.auto_provision = provision
    cfg.save()
    assert not SocialAccount.objects.exists()  # Always a fresh first link.
    return None if provision else make_user(email, org=cfg.org)


def _assert_signed_in(client, response, cfg, *, email=EMAIL, subject=SUBJECT, member=None):
    assert response.status_code == 302
    user = User.objects.get(email=email)
    assert client.session["_auth_user_id"] == str(user.pk)
    assert SocialAccount.objects.get(provider=PROVIDER, uid=subject).user_id == user.pk
    assert OrgMembership.objects.filter(user=user, org=cfg.org, role="member").exists()
    if member is not None:
        assert user.pk == member.pk
        assert User.objects.count() == 1
    else:
        assert not user.has_usable_password()
        assert EmailAddress.objects.filter(user=user, email=email, verified=True).exists()
    return user


def _assert_denied_without_writes(client, *, reason="unverified_email_claim"):
    before = _identity_rows()
    with CaptureQueriesContext(connection) as queries:
        response = _callback(client)
    assert response.status_code in (401, 403)
    assert "_auth_user_id" not in client.session
    assert _identity_rows() == before
    assert not _identity_writes(queries)  # Also catch writes subsequently rolled back.
    assert AuditLog.objects.filter(action="auth.sso_denied", metadata__reason_code=reason).exists()


@pytest.mark.parametrize("cfg,subject,evidence_source", [
    pytest.param("https://accounts.google.com", "109876543210987654321", "id_token", id="google"),
    pytest.param("https://acme.okta.com", "00u1abcdefghijklmn5d7", "userinfo", id="okta"),
    pytest.param("https://auth.pingone.eu/synthetic-environment/as", "synthetic-pingone-user", "userinfo", id="pingone"),
    pytest.param("https://acme.onelogin.com/oidc/2", "12345678", "userinfo", id="onelogin"),
    pytest.param("https://acme.eu.auth0.com", "auth0|synthetic-user", "userinfo", id="auth0"),
    pytest.param("https://keycloak.internal/realms/acme", "synthetic-keycloak-user", "id_token", id="keycloak"),
    pytest.param("https://fs.demo.example/adfs", "synthetic-adfs-subject", "id_token", id="adfs"),
], indirect=["cfg"])
@pytest.mark.parametrize("provision", [False, True], ids=["existing-member", "allowed-provisioning"])
def test_generic_examples_require_supplied_boolean_verification(
    client, cfg, provider_http, make_user, subject, evidence_source, provision, mailoutbox,
):
    member = _prepare_admission(cfg, make_user, provision=provision)
    provider_http.token["sub"] = provider_http.userinfo["sub"] = subject
    evidence = provider_http.token if evidence_source == "id_token" else provider_http.userinfo
    evidence.update(email=EMAIL, email_verified=True)

    response = _callback(client)

    user = _assert_signed_in(client, response, cfg, member=member, subject=subject)
    account = SocialAccount.objects.get(user=user)
    assert account.extra_data[evidence_source]["email_verified"] is True
    assert account.extra_data["id_token"]["sub"] == account.extra_data["userinfo"]["sub"] == subject
    assert ("POST", cfg.issuer_url + "/token") in provider_http.calls
    assert ("GET", cfg.issuer_url + "/userinfo") in provider_http.calls
    assert not mailoutbox


@pytest.mark.parametrize("source", ["id_token", "userinfo"])
@pytest.mark.parametrize("value", [MISSING, False, "true"], ids=["omitted", "false", "string-true"])
@pytest.mark.parametrize("provision", [False, True], ids=["existing-member", "allowed-provisioning"])
def test_unverified_first_link_never_writes_identity(
    client, cfg, provider_http, make_user, source, value, provision, mailoutbox,
):
    _prepare_admission(cfg, make_user, provision=provision)
    evidence = provider_http.token if source == "id_token" else provider_http.userinfo
    evidence["email"] = EMAIL
    if value is not MISSING:
        evidence["email_verified"] = value
    _assert_denied_without_writes(client)
    assert not mailoutbox


@pytest.mark.parametrize("token_flag,userinfo_flag,allowed", [
    pytest.param(True, MISSING, True, id="token-true-survives-omission"),
    pytest.param(False, True, True, id="userinfo-true-overlays-token-false"),
    pytest.param("true", True, True, id="userinfo-true-overlays-malformed-token-flag"),
    pytest.param(True, False, False, id="userinfo-false-overlays-token-true"),
    pytest.param(True, "true", False, id="userinfo-string-overlays-token-true"),
])
def test_userinfo_verification_overlays_token_policy(
    client, cfg, provider_http, make_user, token_flag, userinfo_flag, allowed, mailoutbox,
):
    member = _prepare_admission(cfg, make_user, provision=False)
    provider_http.token.update(email=EMAIL, email_verified=token_flag)
    provider_http.userinfo["email"] = EMAIL
    if userinfo_flag is not MISSING:
        provider_http.userinfo["email_verified"] = userinfo_flag
    if allowed:
        _assert_signed_in(client, _callback(client), cfg, member=member)
    else:
        _assert_denied_without_writes(client)
    assert not mailoutbox


@pytest.mark.parametrize("userinfo_flag", [MISSING, True], ids=["inherited-token-flag", "userinfo-flag"])
def test_userinfo_email_selects_member_even_when_token_email_differs(
    client, cfg, provider_http, make_user, userinfo_flag, mailoutbox,
):
    token_member = make_user("token@demo.example", org=cfg.org)
    selected_member = _prepare_admission(cfg, make_user, provision=False)
    provider_http.token.update(email=token_member.email, email_verified=True)
    provider_http.userinfo["email"] = EMAIL
    if userinfo_flag is not MISSING:
        provider_http.userinfo["email_verified"] = userinfo_flag
    # Current generic policy merges fields: omitted UserInfo flags inherit the
    # token flag, even when UserInfo supplies a different email. This is not
    # Entra's stricter token-email-bound xms_edov rule.
    response = _callback(client)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(selected_member.pk)
    assert SocialAccount.objects.get(provider=PROVIDER, uid=SUBJECT).user_id == selected_member.pk
    assert not SocialAccount.objects.filter(user=token_member).exists()
    assert User.objects.count() == 2
    assert not mailoutbox


@pytest.mark.parametrize("fallback", ["email", "preferred_username", "upn"])
@pytest.mark.parametrize("provision", [False, True], ids=["existing-member", "allowed-provisioning"])
def test_generic_issuer_cannot_use_entra_domain_or_upn_trust(
    client, cfg, provider_http, make_user, fallback, provision, mailoutbox,
):
    _prepare_admission(cfg, make_user, provision=provision)
    provider_http.token.update({
        fallback: EMAIL, "tid": "a1111111-2222-3333-4444-555555555555", "xms_edov": True,
    })
    _assert_denied_without_writes(client)
    assert not mailoutbox


@pytest.mark.parametrize("source", ["id_token", "userinfo"])
def test_true_flag_without_matching_email_claim_does_not_verify_upn_fallback(
    client, cfg, provider_http, make_user, source, mailoutbox,
):
    _prepare_admission(cfg, make_user, provision=True)
    evidence = provider_http.token if source == "id_token" else provider_http.userinfo
    evidence.update(upn=EMAIL, email_verified=True)
    _assert_denied_without_writes(client)
    assert not mailoutbox


@pytest.mark.parametrize("source", ["id_token", "userinfo"])
def test_callback_with_a_single_claim_source_uses_its_subject_and_evidence(
    client, cfg, provider_http, make_user, source, mailoutbox,
):
    _prepare_admission(cfg, make_user, provision=True)
    evidence = {"sub": SUBJECT, "email": EMAIL, "email_verified": True}
    if source == "id_token":
        provider_http.token.update(evidence)
        provider_http.userinfo = {}
    else:
        provider_http.token = None
        provider_http.userinfo = evidence
    user = _assert_signed_in(client, _callback(client), cfg)
    assert SocialAccount.objects.get(user=user).extra_data[source]["sub"] == SUBJECT
    assert not mailoutbox


@pytest.mark.parametrize("source", ["id_token", "userinfo"])
@pytest.mark.parametrize("subject,error", [(MISSING, KeyError), ("", ValueError)], ids=["missing", "empty"])
def test_provider_construction_requires_a_subject_from_selected_source(
    cfg, rf, source, subject, error,
):
    # This is the current generic provider's construction boundary. It raises
    # before admission for missing/empty subjects; it does not implement the
    # Entra domain helper's cross-source subject binding.
    request = rf.get("/accounts/oidc/oidc-demo/login/callback/")
    provider = get_adapter(request).get_provider(request, PROVIDER)
    evidence = {"email": EMAIL, "email_verified": True}
    if subject is not MISSING:
        evidence["sub"] = subject
    before = _identity_rows()
    with CaptureQueriesContext(connection) as queries, pytest.raises(error):
        provider.sociallogin_from_response(request, {source: evidence})
    assert _identity_rows() == before
    assert not _identity_writes(queries)


@pytest.mark.parametrize("scenario,reason", [
    ("provisioning-disabled", "auto_provision_disabled"),
    ("existing-nonmember", "existing_non_member_account"),
])
def test_verified_claim_still_requires_admission(
    client, cfg, provider_http, make_user, scenario, reason, mailoutbox,
):
    cfg.auto_provision = scenario != "provisioning-disabled"
    cfg.save()
    if scenario == "existing-nonmember":
        make_user(EMAIL)
    provider_http.userinfo.update(email=EMAIL, email_verified=True)
    _assert_denied_without_writes(client, reason=reason)
    assert not mailoutbox
