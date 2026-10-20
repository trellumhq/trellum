"""Entra domain evidence stays bound to a tenant, subject and token email."""
import copy
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
import requests
from allauth.account.models import EmailAddress
from allauth.socialaccount.internal import jwtkit
from allauth.socialaccount.models import SocialAccount, SocialLogin, SocialToken
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts import sso, sso_test
from apps.accounts.adapters import _trusted_entra_email_domain, email_is_verified
from apps.core.models import AuditLog
from apps.orgs.models import OrgMembership, OrgSSOConfig, PermissionGroup, PermissionGroupMembership

pytestmark = pytest.mark.django_db
User = get_user_model()
TENANT = "a1111111-2222-3333-4444-555555555555"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
EMAIL = "entra@demo.example"
SUBJECT = "pairwise-stable-subject"
PROVIDER = "oidc-demo"


def _token(**updates):
    return {"iss": ISSUER, "tid": TENANT, "sub": SUBJECT, "email": EMAIL, "xms_edov": True, **updates}


def _login(token=None, userinfo=None, *, provider=PROVIDER, uid=SUBJECT):
    return SocialLogin(
        user=User(),
        account=SocialAccount(
            provider=provider, uid=uid,
            extra_data={"id_token": _token() if token is None else token, "userinfo": userinfo or {}},
        ),
    )


@pytest.fixture
def cfg(org, settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url=ISSUER, client_id="entra-client",
        client_secret="mock-secret", email_domains=["demo.example"],
    )


def test_token_email_domain_evidence_is_accepted_without_mutating_subject(cfg):
    login = _login()
    assert email_is_verified(cfg, login, sso.extract_email(login))
    assert login.account.uid == SUBJECT
    assert login.account.extra_data["id_token"]["sub"] == SUBJECT


def test_email_and_tenant_case_and_surrounding_email_whitespace(cfg):
    login = _login(_token(email="  ENTRA@DEMO.EXAMPLE  ", tid=TENANT.upper()), {"email": " Entra@Demo.Example "})
    assert sso.extract_email(login) == "Entra@Demo.Example"
    assert email_is_verified(cfg, login, sso.extract_email(login))


@pytest.mark.parametrize("value", [None, False, "true", "True", "false", 1, 0, {}, []])
def test_edov_requires_strict_boolean_true(cfg, value):
    login = _login(_token(xms_edov=value))
    assert not email_is_verified(cfg, login, EMAIL)


def test_missing_edov_is_not_trusted(cfg):
    token = _token()
    del token["xms_edov"]
    assert not email_is_verified(cfg, _login(token), EMAIL)


@pytest.mark.parametrize("claim,value", [
    ("tid", None), ("tid", 1), ("tid", []), ("tid", "not-a-tenant"),
    ("tid", "b1111111-2222-3333-4444-555555555555"),
    ("iss", None), ("iss", []), ("iss", ISSUER + "/"), ("iss", "https://other.example"),
    ("sub", None), ("sub", 1), ("sub", []), ("sub", ""), ("sub", "  "), ("sub", "other-subject"),
    ("email", None), ("email", 1), ("email", []), ("email", ""), ("email", "  "),
    ("email", "someone-else@demo.example"),
])
def test_missing_malformed_or_unbound_token_claims_fail_closed(cfg, claim, value):
    login = _login(_token(**{claim: value}))
    assert not _trusted_entra_email_domain(cfg, login, EMAIL)
    assert not email_is_verified(cfg, login, EMAIL)


@pytest.mark.parametrize("claim", ["tid", "iss", "sub", "email"])
def test_required_token_claim_cannot_come_from_userinfo(cfg, claim):
    token = _token()
    value = token.pop(claim)
    assert not email_is_verified(cfg, _login(token, {claim: value}), EMAIL)


@pytest.mark.parametrize("issuer", [
    None, 123, "https://[bad", "http://login.microsoftonline.com/" + TENANT + "/v2.0",
    "https://login.microsoftonline.com/common/v2.0",
    "https://login.microsoftonline.com/organizations/v2.0",
    "https://login.microsoftonline.com/consumers/v2.0",
    "https://login.microsoftonline.com/not-a-uuid/v2.0",
    "https://login.microsoftonline.com/" + TENANT.replace("-", "") + "/v2.0",
    ISSUER.replace("login.microsoftonline.com", "login.microsoftonline.com.evil.example"),
    ISSUER.replace("login.microsoftonline.com", "login.microsoftonline.us"),
    ISSUER.replace("login.microsoftonline.com", "user@login.microsoftonline.com"),
    ISSUER.replace("login.microsoftonline.com", "login.microsoftonline.com:443"),
    ISSUER + "?foo=bar", ISSUER + "#fragment", ISSUER + "?", ISSUER + "#",
    ISSUER + "/", ISSUER + "/extra", ISSUER.replace("/v2.0", "/v1.0"),
    "\n" + ISSUER, ISSUER + "\n",
])
def test_only_exact_tenant_specific_issuer_is_allowed(cfg, issuer):
    cfg.issuer_url = issuer
    assert not email_is_verified(cfg, _login(_token(iss=issuer)), EMAIL)


@pytest.mark.parametrize("provider", ["openid_connect", "microsoft", "oidc-rival", ""])
def test_provider_must_belong_to_configured_org(cfg, provider):
    assert not email_is_verified(cfg, _login(provider=provider), EMAIL)


@pytest.mark.parametrize("uid", ["different-subject", "", 123])
def test_subject_must_match_social_identity(cfg, uid):
    assert not email_is_verified(cfg, _login(uid=uid), EMAIL)


def test_flat_and_userinfo_domain_evidence_is_never_enough(cfg):
    token = _token()
    del token["xms_edov"]
    assert not email_is_verified(cfg, _login(token, {"email": EMAIL, "xms_edov": True}), EMAIL)
    login = _login()
    login.account.extra_data = _token()
    assert not email_is_verified(cfg, login, EMAIL)
    login.account.extra_data = {"userinfo": _token()}
    assert not email_is_verified(cfg, login, EMAIL)


def test_userinfo_cannot_transfer_token_trust_to_another_email(cfg):
    other = "other@demo.example"
    login = _login(userinfo={"email": other, "xms_edov": True})
    assert sso.extract_email(login) == other
    assert not email_is_verified(cfg, login, other)
    assert not email_is_verified(cfg, login, EMAIL)


@pytest.mark.parametrize("value", [None, "", "  ", False, 0, 123, [], {}])
def test_empty_or_malformed_userinfo_email_cannot_restore_the_token_email(cfg, value):
    login = _login(userinfo={"email": value})
    assert not _trusted_entra_email_domain(cfg, login, EMAIL)
    assert not email_is_verified(cfg, login, EMAIL)


@pytest.mark.parametrize("source", ["id_token", "userinfo"])
@pytest.mark.parametrize("value", [False, None, "true", "false", 1, 0])
def test_explicit_unverified_claim_is_not_overridden(cfg, source, value):
    login = _login()
    login.account.extra_data[source]["email_verified"] = value
    assert not email_is_verified(cfg, login, EMAIL)


def test_userinfo_true_cannot_mask_token_false_for_domain_trust(cfg):
    login = _login(_token(email_verified=False), {"email_verified": True})
    assert not _trusted_entra_email_domain(cfg, login, EMAIL)
    # The pre-existing generic verification rule remains independent.
    assert email_is_verified(cfg, login, EMAIL)


@pytest.mark.parametrize("data", [[], "claims", 123, {"id_token": []}, {"userinfo": "claims"}, {"id_token": 1}])
def test_malformed_claim_containers_fail_closed(cfg, data):
    login = _login()
    login.account.extra_data = data
    assert not _trusted_entra_email_domain(cfg, login, EMAIL)
    assert not email_is_verified(cfg, login, EMAIL)


@pytest.mark.parametrize("value", [None, 0, 1, [], {}])
def test_verification_flag_cannot_verify_a_non_string_email(cfg, value):
    assert not email_is_verified(cfg, _login(_token(email=value, email_verified=True)), EMAIL)


def test_generic_verification_and_mailboxless_upn_keep_their_existing_rules(cfg):
    cfg.issuer_url = "https://identity.example"
    login = _login(token={"email": EMAIL, "email_verified": True})
    assert email_is_verified(cfg, login, EMAIL)
    login = _login(token={"email": EMAIL})
    login.email_addresses = [EmailAddress(email=EMAIL, verified=True)]
    assert email_is_verified(cfg, login, EMAIL)
    login.account.extra_data["id_token"]["email_verified"] = False
    assert not email_is_verified(cfg, login, EMAIL)
    cfg.issuer_url = ISSUER
    login = _login(token={"tid": TENANT, "preferred_username": EMAIL})
    assert email_is_verified(cfg, login, EMAIL)
    login.account.extra_data["id_token"]["email"] = EMAIL
    assert not email_is_verified(cfg, login, EMAIL)


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def provider_http(monkeypatch, signing_key):
    """Real callback/state/JWT validation, with every provider request mocked.

    allauth permits skipping signature validation for a TLS token exchange.
    Force it on here, exercising signed JWTs and the mocked JWKS as well.
    """
    state = SimpleNamespace(token=_token(), userinfo={"sub": SUBJECT}, calls=[], decoded=[])
    verify = jwtkit.verify_and_decode

    def verify_signed(**kwargs):
        assert kwargs["issuer"] == ISSUER
        assert kwargs["audience"] == "entra-client"
        assert kwargs["keys_url"] == ISSUER + "/keys"
        kwargs["verify_signature"] = True
        claims = verify(**kwargs)
        state.decoded.append(copy.deepcopy(claims))
        return claims

    monkeypatch.setattr(jwtkit, "verify_and_decode", verify_signed)

    def send(session, request, **kwargs):
        state.calls.append((request.method, request.url))
        if (request.method, request.url) == ("GET", ISSUER + "/.well-known/openid-configuration"):
            payload = {
                "issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token", "userinfo_endpoint": ISSUER + "/userinfo",
                "jwks_uri": ISSUER + "/keys",
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            }
        elif (request.method, request.url) == ("POST", ISSUER + "/token"):
            claims = {"aud": "entra-client", "exp": int(time.time()) + 300, **state.token}
            payload = {
                "access_token": "mock-access-token", "token_type": "Bearer",
                "id_token": jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"}),
            }
        elif (request.method, request.url) == ("GET", ISSUER + "/userinfo"):
            assert request.headers["Authorization"] == "Bearer mock-access-token"
            payload = state.userinfo
        elif (request.method, request.url) == ("GET", ISSUER + "/keys"):
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
            payload = {"keys": [{**jwk, "kid": "test-key", "alg": "RS256"}]}
        else:
            pytest.fail(f"Unexpected HTTP: {request.method} {request.url}")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return state


def _route(name):
    return reverse(f"openid_connect_{name}", kwargs={"provider_id": PROVIDER})


def _callback(client):
    start = client.get(_route("login"))
    assert start.status_code == 302
    state = parse_qs(urlsplit(start.url).query)["state"][0]
    return client.get(_route("callback"), {"state": state, "code": "mock-code"})


def _identity_rows():
    # Compare actual rows, so updates as well as inserts/deletes are detected.
    return [list(model.objects.order_by("pk").values()) for model in (
        User, EmailAddress, SocialAccount, SocialToken, OrgMembership, PermissionGroupMembership,
    )]


def _identity_writes(queries):
    tables = [model._meta.db_table for model in (
        User, EmailAddress, SocialAccount, SocialToken, OrgMembership, PermissionGroupMembership,
    )]
    return [q["sql"] for q in queries if q["sql"].lstrip().split(" ", 1)[0] in {"INSERT", "UPDATE", "DELETE"}
            and any(f'"{table}"' in q["sql"] for table in tables)]


@pytest.mark.parametrize("auto_provision", [False, True])
def test_real_callback_links_only_existing_org_member(
    client, cfg, provider_http, make_user, org, auto_provision, mailoutbox,
):
    cfg.auto_provision = auto_provision
    cfg.save()
    member = make_user(EMAIL, org=org)
    response = _callback(client)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(member.pk)
    account = SocialAccount.objects.get(user=member, provider=PROVIDER)
    assert account.uid == SUBJECT
    assert account.extra_data["id_token"]["sub"] == SUBJECT
    assert provider_http.decoded[0]["xms_edov"] is True
    assert not mailoutbox


def test_real_callback_provisions_when_enabled(client, cfg, provider_http, mailoutbox):
    cfg.auto_provision = True
    cfg.save()
    group = PermissionGroup.objects.create(org=cfg.org, name="Default")
    cfg.default_groups.add(group)
    response = _callback(client)
    assert response.status_code == 302
    user = User.objects.get(email=EMAIL)
    assert client.session["_auth_user_id"] == str(user.pk)
    assert not user.has_usable_password()
    assert OrgMembership.objects.filter(user=user, org=cfg.org, role="member").exists()
    assert PermissionGroupMembership.objects.filter(user=user, group=group).exists()
    assert SocialAccount.objects.get(user=user).uid == SUBJECT
    assert not mailoutbox


@pytest.mark.parametrize("scenario,reason", [
    ("provision-disabled", "auto_provision_disabled"),
    ("existing-nonmember", "existing_non_member_account"),
    ("cross-org-member", "existing_non_member_account"),
    ("outside-domain", "outside_verified_domains"),
    ("unverified-domain", "outside_verified_domains"),
    ("different-userinfo-email", "unverified_email_claim"),
    ("userinfo-only-edov", "unverified_email_claim"),
    ("token-false", "unverified_email_claim"),
    ("userinfo-false", "unverified_email_claim"),
    ("wrong-tenant", "unverified_email_claim"),
    ("wrong-subject", "unverified_email_claim"),
    ("wrong-issuer", "idp_error"),
])
def test_real_callback_denies_without_identity_writes(
    client, cfg, provider_http, make_user, org, other_org, settings, mailoutbox, scenario, reason,
):
    cfg.auto_provision = scenario != "provision-disabled"
    cfg.save()
    if scenario == "existing-nonmember":
        make_user(EMAIL)
    elif scenario == "cross-org-member":
        make_user(EMAIL, org=other_org)
    elif scenario == "outside-domain":
        provider_http.token["email"] = "entra@outside.example"
    elif scenario == "unverified-domain":
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
    elif scenario == "different-userinfo-email":
        provider_http.userinfo["email"] = "other@demo.example"
        make_user("other@demo.example", org=org)
    elif scenario == "userinfo-only-edov":
        del provider_http.token["xms_edov"]
        provider_http.userinfo["xms_edov"] = True
    elif scenario == "token-false":
        provider_http.token["email_verified"] = False
    elif scenario == "userinfo-false":
        provider_http.userinfo["email_verified"] = False
    elif scenario == "wrong-tenant":
        provider_http.token["tid"] = "b1111111-2222-3333-4444-555555555555"
    elif scenario == "wrong-subject":
        provider_http.userinfo["sub"] = "different-subject"
    elif scenario == "wrong-issuer":
        provider_http.token["iss"] = "https://other.example"
    before = _identity_rows()
    with CaptureQueriesContext(connection) as queries:
        response = _callback(client)
    assert response.status_code in (401, 403)
    assert "_auth_user_id" not in client.session
    assert _identity_rows() == before
    assert not _identity_writes(queries)
    assert AuditLog.objects.filter(action="auth.sso_denied", metadata__reason_code=reason).exists()
    assert not mailoutbox


@pytest.mark.parametrize("scenario,ok,verified", [
    ("existing-member", True, True), ("new-enabled", True, True),
    ("new-disabled", False, True), ("nonmember", False, True),
    ("missing-edov", False, False), ("conflicting-email", False, False),
    ("explicit-false", False, False), ("outside-domain", False, True),
])
def test_real_test_signin_has_matching_decisions_without_identity_writes(
    client, cfg, provider_http, org_admin, make_user, org, scenario, ok, verified, mailoutbox,
):
    cfg.auto_provision = scenario != "new-disabled"
    cfg.save()
    group = PermissionGroup.objects.create(org=org, name="Mapped")
    cfg.group_map = {"provider-group": group.pk}
    cfg.save()
    provider_http.token["groups"] = ["provider-group"]
    if scenario == "existing-member":
        make_user(EMAIL, org=org)
    elif scenario == "nonmember":
        make_user(EMAIL)
    elif scenario == "missing-edov":
        del provider_http.token["xms_edov"]
    elif scenario == "conflicting-email":
        provider_http.userinfo["email"] = "other@demo.example"
    elif scenario == "explicit-false":
        provider_http.token["email_verified"] = False
    elif scenario == "outside-domain":
        provider_http.token["email"] = "entra@outside.example"
    client.force_login(org_admin)
    before = _identity_rows()
    response = client.post("/orgs/demo/settings/sso", {"action": "test-signin"})
    assert response.status_code == 302
    assert sso_test.SESSION_MARKER in client.session
    with CaptureQueriesContext(connection) as queries:
        response = _callback(client)
    assert response.status_code == 302 and response.url == "/orgs/demo/settings/sso"
    cfg.refresh_from_db()
    assert cfg.last_test_ok is ok
    checks = {check["label"]: check for check in cfg.last_test_summary["checks"]}
    assert checks["Email verified by the identity provider"]["level"] == ("pass" if verified else "fail")
    assert checks["Admission outcome"]["level"] == ("pass" if ok else "fail")
    assert client.session["_auth_user_id"] == str(org_admin.pk)
    assert sso_test.SESSION_MARKER not in client.session
    assert _identity_rows() == before
    assert not _identity_writes(queries)
    assert not mailoutbox


def test_returning_subject_link_keeps_existing_rules(client, cfg, provider_http, make_user, org):
    member = make_user(EMAIL, org=org)
    SocialAccount.objects.create(user=member, provider=PROVIDER, uid=SUBJECT)
    provider_http.token["xms_edov"] = False
    provider_http.token["email_verified"] = False
    response = _callback(client)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(member.pk)
    assert SocialAccount.objects.get(user=member).uid == SUBJECT
