"""Shared real OIDC callback harness; all provider HTTP stays synthetic."""
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
from allauth.socialaccount.models import SocialAccount, SocialToken
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.orgs.models import OrgMembership, PermissionGroupMembership

User = get_user_model()
PROVIDER = "oidc-demo"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def mock_provider_http(monkeypatch, signing_key, cfg, token, *, verify_signature=True):
    """Real callback/state/JWT validation, with every provider request mocked.

    allauth permits skipping signature validation for a TLS token exchange.
    Force it on by default, exercising signed JWTs and the mocked JWKS as
    well. Passing verify_signature=False leaves the decoder unmodified.
    """
    issuer = cfg.issuer_url
    state = SimpleNamespace(token=token, userinfo={"sub": token["sub"]}, calls=[], decoded=[])
    verify = jwtkit.verify_and_decode

    def verify_signed(**kwargs):
        assert kwargs["issuer"] == issuer
        assert kwargs["audience"] == cfg.client_id
        assert kwargs["keys_url"] == issuer + "/keys"
        kwargs["verify_signature"] = True
        claims = verify(**kwargs)
        state.decoded.append(copy.deepcopy(claims))
        return claims

    if verify_signature:
        monkeypatch.setattr(jwtkit, "verify_and_decode", verify_signed)

    def send(session, request, **kwargs):
        state.calls.append((request.method, request.url))
        if (request.method, request.url) == ("GET", issuer + "/.well-known/openid-configuration"):
            payload = {
                "issuer": issuer, "authorization_endpoint": issuer + "/authorize",
                "token_endpoint": issuer + "/token", "userinfo_endpoint": issuer + "/userinfo",
                "jwks_uri": issuer + "/keys",
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            }
        elif (request.method, request.url) == ("POST", issuer + "/token"):
            payload = {
                "access_token": "mock-access-token", "token_type": "Bearer",
            }
            if state.token is not None:
                claims = {"aud": cfg.client_id, "exp": int(time.time()) + 300, **state.token}
                payload["id_token"] = jwt.encode(
                    claims, signing_key, algorithm="RS256", headers={"kid": "test-key"},
                )
        elif (request.method, request.url) == ("GET", issuer + "/userinfo"):
            assert request.headers["Authorization"] == "Bearer mock-access-token"
            payload = state.userinfo
        elif (request.method, request.url) == ("GET", issuer + "/keys"):
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
