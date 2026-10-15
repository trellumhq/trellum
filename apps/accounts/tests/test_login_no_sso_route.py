"""A blank password with no SSO route asks for a password, rather than
reporting an invalid login, and is not counted as a failed attempt."""
import logging

import pytest

from apps.accounts import throttle
from apps.accounts.views import NO_SSO_ROUTE_MESSAGE
from apps.orgs.models import OrgSSOConfig

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _portal_is_set_up(make_user):
    # Without any user the login page redirects to first-run setup.
    make_user("owner@setup.example")


@pytest.fixture
def failures(monkeypatch):
    recorded = []
    monkeypatch.setattr(throttle, "record_failure", lambda email, request: recorded.append(email))
    return recorded


@pytest.fixture
def sso_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url="https://idp.example/realms/demo",
        client_id="cid", email_domains=["demo.example"],
    )


def _login(client, email, password=""):
    return client.post("/login", {"email": email, "password": password})


def test_blank_password_without_route_gets_the_neutral_message(client, failures, settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    resp = _login(client, "someone@nowhere.example")
    assert resp.status_code == 200
    assert resp.context["form"].errors["password"] == [NO_SSO_ROUTE_MESSAGE]
    assert b"Invalid email or password" not in resp.content
    assert failures == []


def test_message_is_identical_for_existing_and_unknown_accounts(
    client, make_user, failures, settings
):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    make_user("real@nowhere.example")
    known = _login(client, "real@nowhere.example")
    unknown = _login(client, "ghost@nowhere.example")
    assert known.context["form"].errors == unknown.context["form"].errors
    assert failures == []


def test_wrong_password_still_fails_and_is_recorded(client, make_user, failures, settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    make_user("real@nowhere.example")
    resp = _login(client, "real@nowhere.example", "wrong-password")
    assert b"Invalid email or password" in resp.content
    assert failures == ["real@nowhere.example"]


def test_unverified_listed_domain_is_logged_but_not_revealed(
    client, sso_cfg, failures, settings, caplog
):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
    with caplog.at_level(logging.WARNING, logger="apps.accounts.sso"):
        resp = _login(client, "someone@demo.example")
    assert resp.context["form"].errors["password"] == [NO_SSO_ROUTE_MESSAGE]
    assert "not verified" in caplog.text and "demo.example" in caplog.text
    assert "verified" not in resp.content.decode().replace("verification", "")


def test_unlisted_domain_logs_nothing(client, sso_cfg, settings, caplog):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
    with caplog.at_level(logging.WARNING, logger="apps.accounts.sso"):
        _login(client, "someone@elsewhere.example")
    assert caplog.text == ""


def test_routed_domain_still_redirects_on_blank_password(client, sso_cfg, settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    resp = _login(client, "someone@demo.example")
    assert resp.status_code == 302 and "oidc-demo/login" in resp.url
