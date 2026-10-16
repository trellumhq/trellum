"""Identifier-first login: /login takes the email, /login/password the
password. SSO routing runs on both steps, so the second can never be used to
get around it."""
import logging
import re

import pytest

from apps.accounts import throttle
from apps.orgs.models import OrgSSOConfig

pytestmark = pytest.mark.django_db

PW = "pw-Str0ng-pw"


@pytest.fixture(autouse=True)
def _portal_is_set_up(make_user, settings):
    # Without any user the login page redirects to first-run setup.
    make_user("owner@setup.example")
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False


@pytest.fixture
def sso_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url="https://idp.example/realms/demo",
        client_id="cid", email_domains=["demo.example"],
    )


@pytest.fixture
def ldap_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, auth_method=OrgSSOConfig.AUTH_LDAP,
        ldap_server_uri="ldaps://dc.corp.example:636", ldap_user_search_base="dc=corp",
        ldap_bind_dn="cn=svc", ldap_bind_password="x", email_domains=["corp.example"],
    )


def _step1(client, email, query=""):
    return client.post(f"/login{query}", {"email": email})


def _normalised(html: str, email: str) -> str:
    # The CSRF token differs per render; nothing else may.
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", html)
    return html.replace(email, "EMAIL")


class TestStepOne:
    def test_page_is_email_only_with_the_sso_help_line(self, client):
        html = client.get("/login").content.decode()
        assert 'id="id_email"' in html and 'autocomplete="username"' in html
        assert "id_password" not in html
        assert "Continue" in html
        assert "If your organization uses single sign-on, you&#x27;ll be sent" in html or (
            "If your organization uses single sign-on, you'll be sent to its sign-in page." in html
        )
        assert "leave blank" not in html

    @pytest.mark.parametrize("reason,text", [
        ("idle", "signed out due to inactivity"),
        ("expired", "maximum age"),
        ("revoked", "session was ended"),
    ])
    def test_reason_banners_stay_on_step_one(self, client, reason, text):
        assert text in client.get(f"/login?reason={reason}").content.decode()

    def test_routable_domain_redirects_to_the_idp(self, client, sso_cfg):
        resp = _step1(client, "someone@demo.example")
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url

    def test_enforced_member_redirects_even_off_domain(self, client, sso_cfg, make_user, org):
        sso_cfg.enforce_sso = True
        sso_cfg.save()
        make_user("cons@elsewhere.example", org=org)
        resp = _step1(client, "cons@elsewhere.example")
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url

    def test_unrouted_email_continues_to_step_two(self, client):
        resp = _step1(client, "someone@nowhere.example")
        assert resp.status_code == 302 and resp.url == "/login/password"

    def test_next_is_carried_to_step_two_and_back(self, client):
        resp = _step1(client, "someone@nowhere.example", "?next=/studios")
        assert resp.url == "/login/password?next=%2Fstudios"
        page = client.get(resp.url).content.decode()
        assert 'action="/login/password?next=%2Fstudios"' in page
        assert 'href="/login?next=%2Fstudios"' in page  # use a different email

    def test_invalid_email_stays_on_step_one(self, client):
        resp = _step1(client, "not-an-email")
        assert resp.status_code == 200 and "id_password" not in resp.content.decode()


class TestStepTwo:
    def test_without_a_step_one_it_goes_back_to_step_one(self, client):
        assert client.get("/login/password").url == "/login"
        assert client.post("/login/password", {"password": PW}).url == "/login"

    def test_shows_email_read_only_with_password_field(self, client):
        _step1(client, "someone@nowhere.example")
        html = client.get("/login/password").content.decode()
        assert 'value="someone@nowhere.example" readonly' in html
        assert 'autocomplete="username"' in html and 'autocomplete="current-password"' in html
        assert ">Password</label>" in html
        assert "Use a different email" in html

    def test_password_login(self, client, make_user):
        make_user("real@nowhere.example")
        _step1(client, "real@nowhere.example", "?next=/studios")
        resp = client.post("/login/password?next=/studios", {"password": PW})
        assert resp.status_code == 302 and resp.url == "/studios"
        assert "_auth_user_id" in client.session
        assert "login_email" not in client.session

    def test_next_is_not_an_open_redirect(self, client, make_user):
        make_user("real@nowhere.example")
        _step1(client, "real@nowhere.example")
        resp = client.post("/login/password?next=https://evil.example/", {"password": PW})
        assert resp.url == "/"

    def test_wrong_password_fails_and_is_recorded(self, client, make_user, monkeypatch):
        recorded = []
        monkeypatch.setattr(throttle, "record_failure", lambda email, request: recorded.append(email))
        make_user("real@nowhere.example")
        _step1(client, "real@nowhere.example")
        resp = client.post("/login/password", {"password": "wrong"})
        assert resp.status_code == 200 and b"Invalid email or password" in resp.content
        assert recorded == ["real@nowhere.example"]
        assert "_auth_user_id" not in client.session

    def test_blank_password_asks_and_is_not_a_failure(self, client, monkeypatch):
        recorded = []
        monkeypatch.setattr(throttle, "record_failure", lambda email, request: recorded.append(email))
        _step1(client, "x@nowhere.example")
        resp = client.post("/login/password", {"password": ""})
        assert b"Enter your password." in resp.content and recorded == []

    def test_page_is_identical_for_existing_and_unknown_accounts(self, client, make_user):
        make_user("real@nowhere.example")
        shown, failed = [], []
        for email in ("real@nowhere.example", "ghost@nowhere.example"):
            _step1(client, email)
            shown.append(_normalised(client.get("/login/password").content.decode(), email))
            failed.append(_normalised(
                client.post("/login/password", {"password": "wrong"}).content.decode(), email
            ))
        assert shown[0] == shown[1]
        assert failed[0] == failed[1]

    def test_use_a_different_email_resets_and_prefills(self, client):
        _step1(client, "someone@nowhere.example")
        page = client.get("/login").content.decode()
        assert 'value="someone@nowhere.example"' in page
        assert client.get("/login/password").url == "/login"

    def test_directory_domain_labels_the_field(self, client, ldap_cfg):
        resp = _step1(client, "alice@corp.example")
        assert resp.url == "/login/password"
        assert ">Directory password</label>" in client.get(resp.url).content.decode()

    def test_unverified_listed_domain_is_logged_when_landing_on_step_two(
        self, client, sso_cfg, settings, caplog
    ):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
        with caplog.at_level(logging.WARNING, logger="apps.accounts.sso"):
            resp = _step1(client, "someone@demo.example")
            assert caplog.text == ""
            page = client.get(resp.url)
        assert page.status_code == 200
        assert "not verified" in caplog.text and "demo.example" in caplog.text
        assert "not verified" not in page.content.decode()

    def test_unlisted_domain_logs_nothing(self, client, sso_cfg, settings, caplog):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
        with caplog.at_level(logging.WARNING, logger="apps.accounts.sso"):
            client.get(_step1(client, "someone@elsewhere.example").url)
        assert caplog.text == ""


class TestStepTwoReRoutes:
    def test_enforced_user_cannot_password_login_with_a_tampered_session(
        self, client, sso_cfg, make_user, org
    ):
        sso_cfg.enforce_sso = True
        sso_cfg.save()
        make_user("cons@elsewhere.example", org=org)
        # Step 1 for an unrouted address, then swap the session's email the
        # way a tampered flow would: step 2 still re-routes.
        _step1(client, "other@nowhere.example")
        session = client.session
        session["login_email"] = "cons@elsewhere.example"
        session.save()
        resp = client.post("/login/password", {"password": PW})
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url
        assert "_auth_user_id" not in client.session

    def test_combined_post_cannot_bypass_enforcement(self, client, sso_cfg, make_user, org):
        sso_cfg.enforce_sso = True
        sso_cfg.save()
        make_user("cons@elsewhere.example", org=org)
        resp = client.post("/login", {"email": "cons@elsewhere.example", "password": PW})
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url

    def test_operator_keeps_password_login(self, client, sso_cfg, superuser):
        sso_cfg.enforce_sso = True
        sso_cfg.save()
        _step1(client, "root@portal.local")
        resp = client.post("/login/password", {"password": PW})
        assert resp.status_code == 302 and resp.url == "/"

    def test_stale_step_two_for_a_domain_that_now_refuses_passwords_redirects(self, client, sso_cfg):
        sso_cfg.allow_password_login = False
        sso_cfg.save()
        session = client.session
        session["login_email"] = "a@demo.example"
        session.save()
        resp = client.get("/login/password")
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url


class TestCombinedPost:
    def test_email_and_password_in_one_post_still_logs_in(self, client, make_user):
        make_user("real@nowhere.example")
        resp = client.post("/login", {"email": "real@nowhere.example", "password": PW})
        assert resp.status_code == 302 and resp.url == "/"

    def test_wrong_combined_post_rerenders_with_the_error(self, client, make_user):
        make_user("real@nowhere.example")
        resp = client.post("/login", {"email": "real@nowhere.example", "password": "no"})
        assert resp.status_code == 200 and b"Invalid email or password" in resp.content


def _password_link(client, email, query=""):
    return client.post(f"/login{query}", {"email": email, "method": "password"})


class TestPasswordInsteadLink:
    def test_link_is_shown_when_an_openid_connect_provider_is_enabled(self, client, sso_cfg):
        html = client.get("/login").content.decode()
        assert "Sign in with a password instead" in html and 'name="method"' in html

    def test_link_is_hidden_without_any_sso_configuration(self, client):
        html = client.get("/login").content.decode()
        assert "Sign in with a password instead" not in html and 'name="method"' not in html
        assert "Continue" in html

    def test_link_is_hidden_for_a_directory_only_instance(self, client, ldap_cfg):
        # Continue already lands on the password step for a directory domain.
        assert "Sign in with a password instead" not in client.get("/login").content.decode()

    def test_link_is_hidden_when_the_provider_is_disabled_or_incomplete(self, client, sso_cfg):
        sso_cfg.enabled = False
        sso_cfg.save()
        assert "Sign in with a password instead" not in client.get("/login").content.decode()
        sso_cfg.enabled = True
        sso_cfg.client_id = ""
        sso_cfg.save()
        assert "Sign in with a password instead" not in client.get("/login").content.decode()

    def test_link_is_shown_next_to_a_directory_when_a_provider_also_exists(self, client, sso_cfg, other_org):
        OrgSSOConfig.objects.create(
            org=other_org, enabled=True, auth_method=OrgSSOConfig.AUTH_LDAP,
            ldap_server_uri="ldaps://dc.corp.example:636", ldap_user_search_base="dc=corp",
            ldap_bind_dn="cn=svc", ldap_bind_password="x", email_domains=["corp.example"],
        )
        assert "Sign in with a password instead" in client.get("/login").content.decode()

    def test_member_of_enforcing_org_is_still_redirected(self, client, sso_cfg, make_user, org):
        sso_cfg.enforce_sso = True
        sso_cfg.save()
        make_user("cons@demo.example", org=org)
        resp = _password_link(client, "cons@demo.example")
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url

    def test_non_member_on_routable_domain_can_use_a_password_when_allowed(
        self, client, sso_cfg, make_user
    ):
        make_user("loner@demo.example")
        assert sso_cfg.allow_password_login
        assert "oidc-demo/login" in _step1(client, "loner@demo.example").url
        resp = _password_link(client, "loner@demo.example")
        assert resp.url == "/login/password"
        assert client.post("/login/password", {"password": PW}).url == "/"
        assert "_auth_user_id" in client.session

    def test_domain_without_password_login_redirects_even_via_the_link(self, client, sso_cfg):
        sso_cfg.allow_password_login = False
        sso_cfg.save()
        resp = _password_link(client, "anyone@demo.example")
        assert "oidc-demo/login" in resp.url

    def test_operator_on_an_enforcing_sso_domain_can_still_log_in(self, client, sso_cfg):
        """Lockout safety: operators are exempt from enforce_sso, so with
        password login allowed they reach the password step on their own SSO
        domain even if the IdP is down."""
        from apps.accounts.models import User

        sso_cfg.enforce_sso = True
        sso_cfg.save()
        User.objects.create_superuser(email="root@demo.example", password=PW)
        assert _password_link(client, "root@demo.example").url == "/login/password"
        assert client.get("/login/password").status_code == 200
        assert client.post("/login/password", {"password": PW}).url == "/"
        assert "_auth_user_id" in client.session

    def test_operator_on_a_domain_with_passwords_off_is_still_redirected(self, client, sso_cfg):
        # Pre-existing behavior: the operator exemption covers enforce_sso
        # only; a domain whose org disabled password login always goes to the IdP.
        from apps.accounts.models import User

        sso_cfg.allow_password_login = False
        sso_cfg.save()
        User.objects.create_superuser(email="root@demo.example", password=PW)
        assert "oidc-demo/login" in _password_link(client, "root@demo.example").url

    def test_crafted_step_two_still_reroutes_enforced_users(self, client, sso_cfg, make_user, org):
        sso_cfg.enforce_sso = True
        sso_cfg.save()
        make_user("cons@demo.example", org=org)
        _password_link(client, "loner@demo.example")
        session = client.session
        session["login_email"] = "cons@demo.example"
        session.save()
        for resp in (client.get("/login/password"), client.post("/login/password", {"password": PW})):
            assert resp.status_code == 302 and "oidc-demo/login" in resp.url
        assert "_auth_user_id" not in client.session


class TestLoginEmailSessionKey:
    def test_the_stashed_email_does_not_survive_the_mfa_step_up(self, client, make_user):
        import pyotp

        from apps.accounts import mfa

        user = make_user("mfa@demo.example")
        device = mfa.begin_enrollment(user)
        mfa.confirm_enrollment(device, pyotp.TOTP(device.secret).now())
        assert client.post("/login", {"email": user.email}).url == "/login/password"
        assert client.session["login_email"] == user.email
        resp = client.post("/login/password", {"password": PW})
        assert resp.url == "/login/verify"
        assert "login_email" not in client.session
