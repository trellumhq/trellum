"""Exercise canonical callbacks through allauth's real HTTP and state flow."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount
from django.urls import resolve, reverse

from apps.accounts import sso
from apps.accounts.oidc import views
from apps.accounts.oidc.provider import PublicURLProvider
from apps.accounts.oidc.views import PublicURLAdapter
from apps.core.models import AuditLog, InstanceConfig
from apps.orgs.models import OrgSSOConfig

pytestmark = pytest.mark.django_db

ISSUER = "https://identity.example/realms/demo"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZE = f"{ISSUER}/authorize"
TOKEN = f"{ISSUER}/token"
USERINFO = f"{ISSUER}/userinfo"
INTERNAL_HOST = "portal.internal:8000"
PROVIDER = "oidc-demo"


def _route(name, provider=PROVIDER):
    return reverse(f"openid_connect_{name}", kwargs={"provider_id": provider})


def _get(client, path, data=None):
    return client.get(path, data or {}, HTTP_HOST=INTERNAL_HOST, secure=False)


def _start(client):
    response = _get(client, _route("login"))
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location._replace(query="").geturl() == AUTHORIZE
    query = parse_qs(location.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["callback-client"]
    assert query["state"][0]
    assert not response.wsgi_request.is_secure()
    assert response.wsgi_request.get_host() == INTERNAL_HOST
    return query


@pytest.fixture
def cfg(org, settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url=ISSUER,
        client_id="callback-client", client_secret="mock-client-secret",
        email_domains=["demo.example"],
    )


@pytest.fixture
def provider_http(monkeypatch):
    """Intercept prepared HTTP requests; unexpected traffic fails closed."""
    calls = []

    def send(session, request, **kwargs):
        calls.append(request)
        if (request.method, request.url) == ("GET", DISCOVERY):
            payload = {
                "issuer": ISSUER, "authorization_endpoint": AUTHORIZE,
                "token_endpoint": TOKEN, "userinfo_endpoint": USERINFO,
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            }
        elif (request.method, request.url) == ("POST", TOKEN):
            payload = {"access_token": "mock-access-token", "token_type": "Bearer"}
        elif (request.method, request.url) == ("GET", USERINFO):
            assert request.headers["Authorization"] == "Bearer mock-access-token"
            payload = {"sub": "callback-subject", "email": "callback@demo.example", "email_verified": True}
        else:
            pytest.fail(f"Unexpected provider HTTP: {request.method} {request.url}")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return calls


def test_routes_and_provider_use_custom_adapter(cfg, rf):
    request = rf.get("/", HTTP_HOST=INTERNAL_HOST)
    provider = get_adapter(request).get_provider(request, PROVIDER)
    assert type(provider) is PublicURLProvider
    assert type(provider.get_oauth2_adapter(request)) is PublicURLAdapter
    for name in ("login", "callback"):
        match = resolve(_route(name))
        assert match.func is getattr(views, name)
        assert match.kwargs == {"provider_id": PROVIDER}
    assert provider.get_login_url(request) == _route("login")
    assert provider.get_callback_url() == _route("callback")


@pytest.mark.parametrize(
    "public_url,fallback,expected",
    [
        ("https://public.example/", "https://stale.example", "https://public.example"),
        ("", "https://fallback.example/", "https://fallback.example"),
        ("http://localhost:8765/", "https://stale.example", "http://localhost:8765"),
    ],
    ids=["database-public-url-wins", "portal-setting-fallback", "local-http-preserved"],
)
def test_authorization_and_token_exchange_share_canonical_callback(
    client, cfg, provider_http, settings, make_user, org, mailoutbox,
    public_url, fallback, expected,
):
    settings.PORTAL_BASE_URL = fallback
    instance = InstanceConfig.load()
    instance.public_base_url = public_url
    instance.save()
    member = make_user("callback@demo.example", org=org)
    expected_uri = expected + _route("callback")

    query = _start(client)
    assert query["redirect_uri"] == [expected_uri]
    assert sso.redirect_uri_for_org(org) == expected_uri
    assert [(r.method, r.url) for r in provider_http] == [("GET", DISCOVERY)]

    response = _get(client, _route("callback"), {"state": query["state"][0], "code": "mock-code"})
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(member.pk)
    assert SocialAccount.objects.filter(user=member, provider=PROVIDER, uid="callback-subject").exists()
    posts = [r for r in provider_http if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url == TOKEN
    assert parse_qs(posts[0].body) == {
        "redirect_uri": [expected_uri], "grant_type": ["authorization_code"],
        "code": ["mock-code"], "client_id": ["callback-client"],
        "client_secret": ["mock-client-secret"],
    }
    assert [(r.method, r.url) for r in provider_http] == [
        ("GET", DISCOVERY), ("GET", DISCOVERY), ("POST", TOKEN), ("GET", USERINFO),
    ]
    assert not mailoutbox

    # The successful callback consumed the real allauth state; replay cannot exchange a code.
    before = len(provider_http)
    replay = _get(client, _route("callback"), {"state": query["state"][0], "code": "mock-code"})
    assert replay.status_code == 401
    assert len(provider_http) == before


@pytest.mark.parametrize("name", ["login", "callback"])
@pytest.mark.parametrize("invalid", ["unknown-provider", "missing-org", "disabled", "inactive-org", "incomplete", "ldap"])
def test_unavailable_provider_is_rejected_without_http(client, cfg, provider_http, name, invalid):
    provider = PROVIDER
    if invalid == "unknown-provider":
        provider = "not-oidc"
    elif invalid == "missing-org":
        provider = "oidc-missing"
    elif invalid == "inactive-org":
        cfg.org.is_active = False
        cfg.org.save()
    else:
        if invalid == "disabled":
            cfg.enabled = False
        elif invalid == "incomplete":
            cfg.client_id = ""
        elif invalid == "ldap":
            cfg.auth_method = OrgSSOConfig.AUTH_LDAP
        cfg.save()
    assert _get(client, _route(name, provider), {"code": "mock-code", "state": "forged"}).status_code == 404
    assert not provider_http
    assert not SocialAccount.objects.exists()


@pytest.mark.parametrize("state", [None, "forged"])
def test_missing_or_invalid_state_never_exchanges_code(client, cfg, provider_http, state):
    query = _start(client)
    data = {"code": "mock-code"}
    if state is not None:
        data["state"] = state
    before = len(provider_http)
    response = _get(client, _route("callback"), data)
    assert response.status_code == 401
    assert len(provider_http) == before
    assert "_auth_user_id" not in client.session
    assert not SocialAccount.objects.exists()
    # Rejecting a forged callback does not consume the legitimate flow's state.
    response = _get(client, _route("callback"), {"state": query["state"][0], "error": "access_denied"})
    assert response.status_code == 401
    assert "Sign-in was cancelled." in response.content.decode()


@pytest.mark.parametrize("error,reason", [("access_denied", "idp_cancelled"), ("server_error", "idp_error")])
def test_provider_error_consumes_state_without_token_exchange(client, cfg, provider_http, error, reason):
    query = _start(client)
    before = len(provider_http)
    response = _get(client, _route("callback"), {"state": query["state"][0], "error": error})
    assert response.status_code == 401
    assert len(provider_http) == before
    assert "_auth_user_id" not in client.session
    assert not SocialAccount.objects.exists()
    assert AuditLog.objects.filter(action="auth.sso_denied", metadata__reason_code=reason).exists()
    replay = _get(client, _route("callback"), {"state": query["state"][0], "code": "mock-code"})
    assert replay.status_code == 401
    assert len(provider_http) == before


def test_provider_disabled_during_round_trip_cannot_exchange_code(client, cfg, provider_http):
    query = _start(client)
    cfg.enabled = False
    cfg.save()
    before = len(provider_http)
    response = _get(client, _route("callback"), {"state": query["state"][0], "code": "mock-code"})
    assert response.status_code == 404
    assert len(provider_http) == before
    assert "_auth_user_id" not in client.session
    assert not SocialAccount.objects.exists()
