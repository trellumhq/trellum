"""The SSO settings page says when listed domains do not route, and refuses
to turn off password login while nothing would route."""
import pytest

from apps.accounts import sso
from apps.orgs import domains
from apps.orgs.models import OrgDomain, OrgSSOConfig

pytestmark = pytest.mark.django_db

ISSUER = "https://idp.example/realms/demo"
UNVERIFIED = "Not yet sending logins to single sign-on"
NO_ROUTE = "No email domain routes to single sign-on"


@pytest.fixture(autouse=True)
def _offline_discovery(monkeypatch):
    monkeypatch.setattr(sso, "discover", lambda issuer: {})


@pytest.fixture
def verification(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True


def _cfg(org, **overrides):
    values = {
        "org": org, "enabled": True, "issuer_url": ISSUER, "client_id": "cid",
        "email_domains": ["demo.example"],
    }
    values.update(overrides)
    return OrgSSOConfig.objects.create(**values)


def _verify(org, domain):
    record, _ = domains.claim(org, domain)
    record.verified_at = record.created_at
    record.save(update_fields=["verified_at"])


def _page(login, org_admin, org):
    return login(org_admin).get(f"/orgs/{org.slug}/settings/sso")


def _post(client, org, **overrides):
    data = {
        "enabled": "on", "issuer_url": ISSUER, "client_id": "cid",
        "email_domains_text": "demo.example", "default_org_role": "member",
        "groups_claim": "groups",
    }
    data.update(overrides)
    return client.post(f"/orgs/{org.slug}/settings/sso", data)


class TestRoutingWarnings:
    def test_unverified_domain_is_listed(self, login, org_admin, org, verification):
        _cfg(org, email_domains=["demo.example", "other.example"])
        _verify(org, "other.example")
        body = _page(login, org_admin, org).content.decode()
        assert UNVERIFIED in body
        assert "<code>demo.example</code>" in body.split(UNVERIFIED)[1].split("</div>")[0]
        assert "TRELLUM_SSO_DOMAIN_VERIFICATION=false" in body
        assert NO_ROUTE not in body

    def test_no_routable_domain_warns(self, login, org_admin, org, verification):
        _cfg(org)
        assert NO_ROUTE in _page(login, org_admin, org).content.decode()

    def test_hidden_when_every_domain_is_verified(self, login, org_admin, org, verification):
        _cfg(org)
        _verify(org, "demo.example")
        body = _page(login, org_admin, org).content.decode()
        assert UNVERIFIED not in body and NO_ROUTE not in body

    def test_hidden_when_config_is_disabled(self, login, org_admin, org, verification):
        _cfg(org, enabled=False)
        body = _page(login, org_admin, org).content.decode()
        assert UNVERIFIED not in body and NO_ROUTE not in body

    def test_verification_off_has_no_unverified_warning(self, login, org_admin, org, settings):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
        _cfg(org)
        body = _page(login, org_admin, org).content.decode()
        assert UNVERIFIED not in body and NO_ROUTE not in body


class TestPasswordLoginNeedsARoute:
    def test_unverified_domain_blocks_disabling_password_login(
        self, login, org_admin, org, verification
    ):
        resp = _post(login(org_admin), org)
        assert resp.status_code == 200
        assert b"Password login can only be turned off" in resp.content
        assert not OrgSSOConfig.objects.filter(org=org).exists()

    def test_verified_domain_allows_it(self, login, org_admin, org, verification):
        _verify(org, "demo.example")
        assert _post(login(org_admin), org).status_code == 302
        assert OrgSSOConfig.objects.get(org=org).allow_password_login is False

    def test_verification_off_allows_it(self, login, org_admin, org, settings):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
        assert _post(login(org_admin), org).status_code == 302

    def test_verification_off_still_needs_a_domain(self, login, org_admin, org, settings):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
        resp = _post(login(org_admin), org, email_domains_text="")
        assert resp.status_code == 200
        assert b"Password login can only be turned off" in resp.content

    def test_ldap_follows_the_same_rule(self, login, org_admin, org, verification):
        resp = _post(
            login(org_admin), org, auth_method="ldap", issuer_url="", client_id="",
            ldap_server_uri="ldaps://dc.example:636", ldap_user_search_base="dc=demo",
            ldap_user_filter="(mail={login})", ldap_email_attr="mail",
            ldap_name_attr="cn", ldap_group_attr="memberOf",
        )
        assert resp.status_code == 200
        assert b"Password login can only be turned off" in resp.content

    def test_password_login_on_is_never_blocked(self, login, org_admin, org, verification):
        resp = _post(login(org_admin), org, allow_password_login="on")
        assert resp.status_code == 302

    def test_disabled_config_is_never_blocked(self, login, org_admin, org, verification):
        resp = _post(login(org_admin), org, enabled="")
        assert resp.status_code == 302

    def test_enforce_sso_is_a_warning_not_an_error(self, login, org_admin, org, verification):
        resp = _post(login(org_admin), org, allow_password_login="on", enforce_sso="on")
        assert resp.status_code == 302
        assert OrgSSOConfig.objects.get(org=org).enforce_sso is True


class TestTestResultInvalidation:
    """A recorded Test sign-in pass is cleared when the identity source or
    the domains it was tested against change."""

    def test_changing_the_issuer_clears_a_pass(self, login, org_admin, org):
        cfg = _cfg(org, last_test_ok=True)
        _post(
            login(org_admin), org, allow_password_login="on",
            issuer_url="https://idp.example/realms/other",
        )
        cfg.refresh_from_db()
        assert cfg.last_test_ok is None

    def test_changing_the_domains_clears_a_pass(self, login, org_admin, org):
        cfg = _cfg(org, last_test_ok=True)
        _post(
            login(org_admin), org, allow_password_login="on",
            email_domains_text="demo.example\nother.example",
        )
        cfg.refresh_from_db()
        assert cfg.last_test_ok is None

    def test_unrelated_change_keeps_a_pass(self, login, org_admin, org):
        cfg = _cfg(org, last_test_ok=True)
        _post(login(org_admin), org, allow_password_login="on", groups_claim="roles")
        cfg.refresh_from_db()
        assert cfg.groups_claim == "roles"
        assert cfg.last_test_ok is True
