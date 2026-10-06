"""LDAP / Active Directory sign-in (internal planning ticket #075): authenticate() against
ldap3's in-memory directory (real bind and search semantics, no socket), the
login view's directory branch, and the settings page."""
import json
import ssl

import ldap3
import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from ldap3.core.exceptions import LDAPSocketOpenError

from apps.accounts import ldap, mfa, sso
from apps.accounts.adapters import (
    REASON_AUTO_PROVISION_OFF,
    REASON_DIRECTORY_ERROR,
    REASON_EXISTING_NON_MEMBER,
    REASON_OUTSIDE_DOMAINS,
    OrgSSOAdapter,
)
from apps.core.models import AuditLog
from apps.orgs.models import OrgMembership, OrgSSOConfig, PermissionGroupMembership

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _domain_verification_is_covered_by_its_own_suite(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False

BASE = "dc=corp,dc=example"
ALICE_DN = f"cn=alice,ou=people,{BASE}"
SVC_DN = f"cn=svc,ou=people,{BASE}"
ANALYSTS = f"cn=analysts,ou=groups,{BASE}"
MANAGERS = f"cn=managers,ou=groups,{BASE}"
PASSWORD = "s3cret-dir-pw"
BIND_PASSWORD = "svc-bind-pw"
LOCAL_PASSWORD = "pw-Str0ng-pw"  # what make_user sets


@pytest.fixture
def directory(monkeypatch):
    """ldap3's mock directory behind ldap._open. Returns the strategy so a
    test can add_entry() more users."""
    server = ldap3.Server("fake", get_info=ldap3.OFFLINE_AD_2012_R2)
    seed = ldap3.Connection(server, client_strategy=ldap3.MOCK_SYNC)
    seed.strategy.add_entry(BASE, {"objectClass": "domain"})
    seed.strategy.add_entry(SVC_DN, {"objectClass": "user", "userPassword": BIND_PASSWORD})
    seed.strategy.add_entry(ALICE_DN, {
        "objectClass": "user", "mail": "alice@corp.example",
        "userPrincipalName": "alice@corp.example", "displayName": "Alice Example",
        "memberOf": [ANALYSTS], "userPassword": PASSWORD,
    })

    def _open(cfg, user, password):
        conn = ldap3.Connection(
            server, user=user or None, password=password or None,
            client_strategy=ldap3.MOCK_SYNC, raise_exceptions=True,
        )
        conn.open()
        conn.bind()
        return conn

    monkeypatch.setattr(ldap, "_open", _open)
    return seed.strategy


@pytest.fixture
def ldap_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, auth_method=OrgSSOConfig.AUTH_LDAP,
        ldap_server_uri="ldaps://dc.corp.example:636", ldap_user_search_base=BASE,
        ldap_bind_dn=SVC_DN, ldap_bind_password=BIND_PASSWORD,
        email_domains=["corp.example"],
    )


def _unreachable(monkeypatch):
    def boom(cfg, user, password):
        raise LDAPSocketOpenError("socket connection error while opening: timed out")
    monkeypatch.setattr(ldap, "_open", boom)


def _no_secret_in_trail():
    for row in AuditLog.objects.all():
        blob = json.dumps(row.metadata)
        assert PASSWORD not in blob and BIND_PASSWORD not in blob, row.action


class TestAuthenticate:
    def test_success_returns_entry(self, directory, ldap_cfg):
        assert ldap.authenticate(ldap_cfg, "alice@corp.example", PASSWORD) == {
            "dn": ALICE_DN, "email": "alice@corp.example",
            "name": "Alice Example", "groups": [ANALYSTS],
        }

    def test_wrong_password(self, directory, ldap_cfg):
        assert ldap.authenticate(ldap_cfg, "alice@corp.example", "nope") is None

    def test_unknown_login(self, directory, ldap_cfg):
        assert ldap.authenticate(ldap_cfg, "nobody@corp.example", PASSWORD) is None

    def test_empty_password_never_touches_the_directory(self, ldap_cfg, monkeypatch):
        """An empty simple bind is an anonymous bind and would succeed."""
        _unreachable(monkeypatch)
        assert ldap.authenticate(ldap_cfg, "alice@corp.example", "") is None

    def test_ambiguous_login_rejected(self, directory, ldap_cfg):
        directory.add_entry(f"cn=alice2,ou=people,{BASE}", {
            "objectClass": "user", "mail": "alice@corp.example", "userPassword": PASSWORD,
        })
        assert ldap.authenticate(ldap_cfg, "alice@corp.example", PASSWORD) is None

    def test_service_bind_refused_is_a_directory_error(self, directory, ldap_cfg):
        ldap_cfg.ldap_bind_password = "wrong"
        with pytest.raises(ldap.LDAPError) as exc:
            ldap.authenticate(ldap_cfg, "alice@corp.example", PASSWORD)
        assert "invalidCredentials" in str(exc.value)
        assert PASSWORD not in str(exc.value) and "wrong" not in str(exc.value)

    def test_anonymous_service_bind(self, directory, ldap_cfg):
        ldap_cfg.ldap_bind_dn = ""
        ldap_cfg.ldap_bind_password = ""
        assert ldap.authenticate(ldap_cfg, "alice@corp.example", PASSWORD)["dn"] == ALICE_DN

    def test_unreachable_is_a_directory_error(self, ldap_cfg, monkeypatch):
        _unreachable(monkeypatch)
        with pytest.raises(ldap.LDAPError, match="LDAPSocketOpenError"):
            ldap.authenticate(ldap_cfg, "alice@corp.example", PASSWORD)

    def test_login_is_escaped_in_the_filter(self, ldap_cfg):
        assert ldap._filter(ldap_cfg, "a*)(") == (
            "(|(mail=a\\2a\\29\\28)(userPrincipalName=a\\2a\\29\\28))"
        )

    def test_tls_always_verifies(self, ldap_cfg):
        server = ldap._server(ldap_cfg)
        assert server.ssl is True and server.tls.validate == ssl.CERT_REQUIRED
        assert server.tls.ca_certs_data is None
        ldap_cfg.ldap_ca_cert = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
        assert ldap._server(ldap_cfg).tls.ca_certs_data == ldap_cfg.ldap_ca_cert

    def test_never_follows_referrals_and_always_uses_tls(self, ldap_cfg, monkeypatch):
        """A followed referral would re-bind with the same credentials to
        whatever host the directory named, over ldap:// and without the CA;
        a plain ldap:// bind would send the password in clear."""
        built = []

        class Spy:
            def __init__(self, server, **kwargs):
                built.append(kwargs)
                self.calls = []

            def open(self):
                self.calls.append("open")

            def start_tls(self):
                self.calls.append("start_tls")

            def bind(self):
                self.calls.append("bind")

        monkeypatch.setattr(ldap3, "Connection", Spy)
        assert ldap._open(ldap_cfg, SVC_DN, BIND_PASSWORD).calls == ["open", "bind"]
        ldap_cfg.ldap_server_uri = "ldap://dc.corp.example:389"
        assert ldap._open(ldap_cfg, SVC_DN, BIND_PASSWORD).calls == ["open", "start_tls", "bind"]
        assert built and all(kw["auto_referrals"] is False for kw in built)

    def test_connection_test(self, directory, ldap_cfg):
        assert ldap.test_connection(ldap_cfg) == (
            f"Connected to ldaps://dc.corp.example:636 as {SVC_DN}; search base {BASE} found."
        )
        ldap_cfg.ldap_user_search_base = "dc=nope"
        with pytest.raises(ldap.LDAPError, match="noSuchObject"):
            ldap.test_connection(ldap_cfg)


class TestRouting:
    def test_ldap_row_is_enabled_without_oidc_fields(self, ldap_cfg):
        assert list(sso.enabled_configs()) == [ldap_cfg]
        assert sso.config_for_email_domain("x@corp.example") == ldap_cfg
        assert not [
            a for a in OrgSSOAdapter().list_apps(None, provider="openid_connect")
            if str(a.provider_id).startswith("oidc-")
        ]
        assert sso.config_for_provider("oidc-demo") is None

    def test_ldap_row_needs_server_and_base(self, ldap_cfg):
        ldap_cfg.ldap_user_search_base = ""
        ldap_cfg.save()
        assert list(sso.enabled_configs()) == []


@pytest.mark.usefixtures("directory")
class TestDirectoryLogin:
    # Tests that start without alice's account pull in org_admin: login_view
    # sends everyone to /setup while the instance has no account at all.
    def _post(self, client, email="alice@corp.example", password=PASSWORD):
        return client.post("/login", {"email": email, "password": password})

    def test_member_signs_in_with_directory_credentials(self, client, ldap_cfg, make_user, org):
        make_user("alice@corp.example", org=org)
        resp = self._post(client)
        assert resp.status_code == 302 and resp.url == "/"
        assert "_auth_user_id" in client.session
        assert AuditLog.objects.get(action="auth.login").metadata["method"] == "ldap"
        _no_secret_in_trail()

    def test_no_password_asks_for_the_directory_password(self, client, ldap_cfg, make_user, org):
        make_user("alice@corp.example", org=org)
        resp = self._post(client, password="")
        assert resp.status_code == 200 and b"directory password" in resp.content

    def test_group_dns_drive_permission_groups(self, client, ldap_cfg, make_user, make_group, attach_group, org):
        user = make_user("alice@corp.example", org=org)
        analysts, managers = make_group("Analysts"), make_group("Managers")
        attach_group(user, managers)  # mapped, but alice is not in that DN any more
        # Directories return DNs in their own casing; the mapping compares case-insensitively.
        ldap_cfg.group_map = {ANALYSTS.upper(): analysts.pk, MANAGERS: managers.pk}
        ldap_cfg.save()
        assert self._post(client).status_code == 302
        assert PermissionGroupMembership.objects.filter(user=user, group=analysts).exists()
        assert not PermissionGroupMembership.objects.filter(user=user, group=managers).exists()

    def test_existing_non_member_denied(self, client, ldap_cfg, make_user):
        make_user("alice@corp.example")  # a portal account, no membership
        resp = self._post(client)
        assert resp.status_code == 403 and b"not a member" in resp.content
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.outcome == "denied" and row.org_id == ldap_cfg.org_id
        assert row.metadata["reason_code"] == REASON_EXISTING_NON_MEMBER
        assert row.metadata["provider"] == "ldap"
        assert "_auth_user_id" not in client.session
        _no_secret_in_trail()

    def test_auto_provision_creates_the_member(self, client, ldap_cfg, make_group, org, org_admin):
        ldap_cfg.auto_provision = True
        ldap_cfg.save()
        ldap_cfg.default_groups.add(make_group("Everyone"))
        assert self._post(client).status_code == 302
        user = User.objects.get(email="alice@corp.example")
        assert user.name == "Alice Example" and not user.has_usable_password()
        assert OrgMembership.objects.filter(user=user, org=org).exists()
        assert user.permission_group_memberships.count() == 1
        assert AuditLog.objects.get(action="member.provisioned").metadata["provider"] == "ldap"

    def test_auto_provision_off_denies_new_accounts(self, client, ldap_cfg, org_admin):
        resp = self._post(client)
        assert resp.status_code == 403
        assert AuditLog.objects.get(action="auth.sso_denied").metadata["reason_code"] == REASON_AUTO_PROVISION_OFF
        assert not User.objects.filter(email="alice@corp.example").exists()

    def test_directory_address_outside_org_domains_denied(self, client, ldap_cfg, directory, org_admin):
        directory.add_entry(f"cn=bob,ou=people,{BASE}", {
            "objectClass": "user", "mail": "bob@other.io",
            "userPrincipalName": "bob@corp.example", "userPassword": "bobpw",
        })
        resp = self._post(client, email="bob@corp.example", password="bobpw")
        assert resp.status_code == 403
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == REASON_OUTSIDE_DOMAINS
        assert row.metadata["asserted_email"] == "bob@other.io"

    def test_enforce_sso_refuses_the_local_password(self, client, ldap_cfg, make_user, org):
        ldap_cfg.enforce_sso = True
        ldap_cfg.save()
        make_user("alice@corp.example", org=org)  # has LOCAL_PASSWORD
        resp = self._post(client, password=LOCAL_PASSWORD)
        assert resp.status_code == 200 and b"Invalid email or password" in resp.content
        assert "_auth_user_id" not in client.session
        assert AuditLog.objects.filter(action="auth.login_failed").exists()
        assert self._post(client).status_code == 302  # the directory password still works

    def test_local_password_fallback_when_allowed(self, client, ldap_cfg, make_user, org):
        make_user("alice@corp.example", org=org)
        assert self._post(client, password=LOCAL_PASSWORD).status_code == 302
        assert AuditLog.objects.get(action="auth.login").metadata["method"] == "password"
        assert not AuditLog.objects.filter(action="auth.login_failed").exists()

    def test_no_fallback_when_password_login_off(self, client, ldap_cfg, make_user, org):
        ldap_cfg.allow_password_login = False
        ldap_cfg.save()
        make_user("alice@corp.example", org=org)
        resp = self._post(client, password=LOCAL_PASSWORD)
        assert resp.status_code == 200 and "_auth_user_id" not in client.session

    def test_disabled_account_cannot_sign_in(self, client, ldap_cfg, make_user, org):
        user = make_user("alice@corp.example", org=org)
        user.is_active = False
        user.save()
        assert self._post(client).status_code == 200
        assert "_auth_user_id" not in client.session

    def test_directory_outage_is_generic_and_audited(self, client, ldap_cfg, make_user, org, monkeypatch):
        from apps.accounts import throttle

        make_user("alice@corp.example", org=org)
        _unreachable(monkeypatch)
        recorded = []
        monkeypatch.setattr(throttle, "record_failure", lambda email, request: recorded.append(email))
        resp = self._post(client)
        assert resp.status_code == 200 and b"Sign-in is unavailable" in resp.content
        assert recorded == ["alice@corp.example"]  # an outage is not a free retry
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.outcome == "failure"
        assert row.metadata["reason_code"] == REASON_DIRECTORY_ERROR
        assert "LDAPSocketOpenError" in row.metadata["error"]
        _no_secret_in_trail()

    def test_failures_count_against_the_throttle(self, client, ldap_cfg, make_user, org, monkeypatch):
        from apps.accounts import throttle

        make_user("alice@corp.example", org=org)
        recorded = []
        monkeypatch.setattr(throttle, "record_failure", lambda email, request: recorded.append(email))
        self._post(client, password="wrong-everywhere")
        assert recorded == ["alice@corp.example"]
        assert AuditLog.objects.filter(action="auth.login_failed").count() == 1

    def test_operator_keeps_local_password_during_an_outage(self, client, ldap_cfg, org, monkeypatch):
        """Same exemption operators have from enforce_sso: a dead directory
        must not lock out the people who can fix it."""
        User.objects.create_superuser(email="root@corp.example", password=LOCAL_PASSWORD)
        _unreachable(monkeypatch)
        resp = self._post(client, email="root@corp.example", password="wrong")
        assert resp.status_code == 200 and b"Invalid email or password" in resp.content
        resp = self._post(client, email="root@corp.example", password=LOCAL_PASSWORD)
        assert resp.status_code == 302 and "_auth_user_id" in client.session
        assert AuditLog.objects.get(action="auth.login").metadata["method"] == "password"

    def test_mfa_step_up_still_applies(self, client, ldap_cfg, make_user, org):
        user = make_user("alice@corp.example", org=org)
        device = mfa.begin_enrollment(user)
        mfa.confirm_enrollment(device, pyotp.TOTP(device.secret).now())
        device.last_used_step = 0  # confirmation spent the current step (see test_mfa)
        device.save(update_fields=["last_used_step"])
        resp = self._post(client)
        assert resp.status_code == 302 and resp.url == reverse("mfa-verify")
        assert "_auth_user_id" not in client.session
        resp = client.post(reverse("mfa-verify"), {"code": pyotp.TOTP(device.secret).now()})
        assert resp.status_code == 302 and "_auth_user_id" in client.session
        row = AuditLog.objects.get(action="auth.login")
        assert row.metadata["method"] == "ldap" and row.metadata["mfa"] == "totp"


SETTINGS_POST = {
    "enabled": "on", "auth_method": "ldap",
    "ldap_server_uri": "ldaps://dc.corp.example:636", "ldap_bind_dn": SVC_DN,
    "ldap_bind_password": BIND_PASSWORD, "ldap_user_search_base": BASE,
    "ldap_user_filter": "(mail={login})", "ldap_email_attr": "mail",
    "ldap_name_attr": "displayName", "ldap_group_attr": "memberOf",
    "email_domains_text": "corp.example", "default_org_role": "member",
    "groups_claim": "groups", "allow_password_login": "on",
}


class TestSettings:
    def _post(self, c, org, follow=False, **overrides):
        return c.post(f"/orgs/{org.slug}/settings/sso", {**SETTINGS_POST, **overrides}, follow=follow)

    def test_saves_a_directory_config_without_discovery(self, login, org_admin, org, monkeypatch):
        def never(issuer):
            raise AssertionError("discovery must not run for a directory")
        monkeypatch.setattr(sso, "discover", never)
        assert self._post(login(org_admin), org).status_code == 302
        cfg = OrgSSOConfig.objects.get(org=org)
        assert cfg.is_ldap and cfg.ldap_bind_password == BIND_PASSWORD
        assert cfg.ldap_user_filter == "(mail={login})"
        assert AuditLog.objects.get(action="sso.update").metadata["auth_method"] == "ldap"
        # Blank bind password on re-save keeps the stored one.
        self._post(login(org_admin), org, ldap_bind_password="")
        cfg.refresh_from_db()
        assert cfg.ldap_bind_password == BIND_PASSWORD

    def test_group_map_keys_on_a_dn(self, login, org_admin, org, make_group):
        """A DN is full of '=' -- the line splits at the last one, not the first."""
        group = make_group("Analysts")
        resp = self._post(login(org_admin), org, group_map_text=f"{ANALYSTS} = Analysts")
        assert resp.status_code == 302
        assert OrgSSOConfig.objects.get(org=org).group_map == {ANALYSTS: group.pk}

    def test_validation(self, login, org_admin, org):
        c = login(org_admin)
        resp = self._post(c, org, ldap_server_uri="")
        assert resp.status_code == 200 and b"Required to enable" in resp.content
        resp = self._post(c, org, ldap_server_uri="http://dc")
        assert resp.status_code == 200 and b"ldaps://host:636" in resp.content
        resp = self._post(c, org, ldap_user_filter="(mail=x)")
        assert resp.status_code == 200 and b"must contain {login}" in resp.content
        assert not OrgSSOConfig.objects.filter(org=org).exists()

    def test_page_renders_both_sources(self, login, org_admin, org, ldap_cfg):
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/sso").content.decode()
        assert 'data-reveal="oidc"' in html and 'data-reveal="ldap"' in html
        assert "Test directory connection" in html

    def test_connection_test_action(self, login, org_admin, org, ldap_cfg, monkeypatch):
        url = f"/orgs/{org.slug}/settings/sso"
        monkeypatch.setattr(ldap, "test_connection", lambda cfg: "Connected to it.")
        resp = login(org_admin).post(url, {"action": "test-ldap"}, follow=True)
        assert b"Connected to it." in resp.content
        assert AuditLog.objects.get(action="sso.ldap.test").outcome == "success"

        def refuse(cfg):
            raise ldap.LDAPError("LDAPSocketOpenError: timed out")
        monkeypatch.setattr(ldap, "test_connection", refuse)
        resp = login(org_admin).post(url, {"action": "test-ldap"}, follow=True)
        assert b"Directory connection failed: LDAPSocketOpenError: timed out" in resp.content
        assert AuditLog.objects.filter(action="sso.ldap.test", outcome="failure").exists()

    def test_connection_test_needs_a_saved_directory(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/sso", {"action": "test-ldap"}, follow=True
        )
        assert b"Save a directory configuration first" in resp.content
