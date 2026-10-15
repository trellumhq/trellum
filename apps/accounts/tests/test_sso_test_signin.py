"""The org admin's "Test sign-in": a dry run of the SSO callback / directory
login that reports what would happen and changes nothing."""
import json
import time
from unittest import mock

import ldap3
import pytest
from allauth.core import context
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import signing
from django.test import RequestFactory

from apps.accounts import ldap, sso, sso_test
from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs import domains
from apps.orgs.models import (
    OrgMembership,
    OrgSSOConfig,
    PermissionGroup,
    PermissionGroupMembership,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

ISSUER = "https://keycloak.internal/realms/acme"
PAGE = "/orgs/demo/settings/sso"
NUDGE = "Single sign-on hasn't been tested yet. Use Test sign-in before enforcing it."


@pytest.fixture(autouse=True)
def _no_domain_verification(settings, monkeypatch):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    monkeypatch.setattr(sso, "discover", lambda issuer: {})


@pytest.fixture
def cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url=ISSUER, client_id="cid", client_secret="s3cr3t",
        email_domains=["demo.example"],
    )


@pytest.fixture
def admin_client(login, org_admin):
    return login(org_admin)


def _start(client):
    return client.post(PAGE, {"action": "test-signin"})


def _callback(client, admin, *, email="sso.user@demo.example", uid="oid-9", **claims):
    """Run an OIDC callback for the browser behind ``client`` through allauth's
    real complete_social_login. Returns (response, request)."""
    request = RequestFactory().get("/accounts/oidc/oidc-demo/login/callback/")
    request.session = client.session
    request.user = admin
    request._messages = FallbackStorage(request)
    provider = get_adapter().get_provider(request, "oidc-demo")
    extra = {"sub": uid, "email": email, "email_verified": True, **claims}
    sociallogin = provider.sociallogin_from_response(request, {"userinfo": extra})
    with context.request_context(request):
        response = complete_social_login(request, sociallogin)
    request.session.save()
    return response, request


def _result(cfg):
    cfg.refresh_from_db()
    return {c["label"]: c for c in cfg.last_test_summary["checks"]}


class TestStart:
    def test_org_admin_post_stores_marker_and_redirects_to_idp(self, admin_client, cfg):
        resp = _start(admin_client)
        assert resp.status_code == 302
        assert resp.url.endswith("/oidc-demo/login/")
        assert sso_test.SESSION_MARKER in admin_client.session

    def test_get_does_not_start(self, admin_client, cfg):
        resp = admin_client.get(PAGE, {"action": "test-signin"})
        assert resp.status_code == 200
        assert sso_test.SESSION_MARKER not in admin_client.session

    def test_member_cannot_start(self, login, member, cfg):
        client = login(member)
        resp = _start(client)
        assert resp.status_code in (302, 403) and "oidc-demo" not in getattr(resp, "url", "")
        assert sso_test.SESSION_MARKER not in client.session

    def test_anonymous_cannot_start(self, client, cfg):
        resp = _start(client)
        assert "oidc-demo" not in getattr(resp, "url", "")

    def test_requires_csrf(self, org_admin, cfg):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        assert strict.login(username=org_admin.email, password="pw-Str0ng-pw")
        assert strict.post(PAGE, {"action": "test-signin"}).status_code == 403

    def test_needs_enabled_oidc_config(self, admin_client, cfg):
        cfg.enabled = False
        cfg.save()
        resp = _start(admin_client)
        assert "oidc-demo" not in resp.url
        assert sso_test.SESSION_MARKER not in admin_client.session

    def test_idp_prompts_for_account(self, settings):
        assert settings.SOCIALACCOUNT_PROVIDERS["openid_connect"]["AUTH_PARAMS"]["prompt"] == "select_account"


class TestCallbackInTestMode:
    def test_nothing_happens_and_admin_stays_logged_in(self, admin_client, cfg, org, org_admin):
        group = PermissionGroup.objects.create(org=org, name="Analysts")
        cfg.group_map = {"idp-analysts": group.pk}
        cfg.auto_provision = True
        cfg.save()
        _start(admin_client)
        logins = AuditLog.objects.filter(action="auth.login").count()
        users, links, memberships = User.objects.count(), SocialAccount.objects.count(), OrgMembership.objects.count()

        response, request = _callback(admin_client, org_admin, groups=["idp-analysts"])

        assert response.status_code == 302 and response.url == PAGE
        assert User.objects.count() == users
        assert SocialAccount.objects.count() == links
        assert OrgMembership.objects.count() == memberships
        assert not PermissionGroupMembership.objects.exists()
        assert request.session["_auth_user_id"] == str(org_admin.pk)
        # ... and the browser really is still the admin.
        assert admin_client.get(PAGE).status_code == 200
        assert AuditLog.objects.filter(action="auth.login").count() == logins

    def test_existing_member_is_not_linked_or_synced(self, admin_client, cfg, org, org_admin, make_user):
        member = make_user("sso.user@demo.example", org=org)
        group = PermissionGroup.objects.create(org=org, name="Analysts")
        cfg.group_map = {"idp-analysts": group.pk}
        cfg.save()
        _start(admin_client)
        _callback(admin_client, org_admin, email=member.email, groups=["idp-analysts"])
        assert not SocialAccount.objects.exists()
        assert not PermissionGroupMembership.objects.filter(user=member).exists()
        checks = _result(cfg)
        assert checks["Admission outcome"]["detail"] == f"Would sign in the existing member {member.email}."
        assert checks["Permission groups that would be granted"]["detail"] == "Analysts"

    def test_result_is_persisted_and_shown(self, admin_client, cfg, org_admin):
        cfg.auto_provision = True
        cfg.save()
        _start(admin_client)
        _, request = _callback(admin_client, org_admin)
        cfg.refresh_from_db()
        assert cfg.last_test_at is not None
        assert cfg.last_test_by == org_admin
        assert cfg.last_test_ok is True
        assert sso_test.SESSION_RESULT in request.session
        body = admin_client.get(PAGE).content.decode()
        assert "Identity provider reached" in body
        assert f"by {org_admin.email}" in body
        # Shown from the session once, then from the persisted row.
        assert "Identity provider reached" in admin_client.get(PAGE).content.decode()

    def test_audit_row_has_outcome_and_summary_but_no_secrets(self, admin_client, cfg, org_admin):
        cfg.auto_provision = True
        cfg.save()
        _start(admin_client)
        _callback(admin_client, org_admin)
        row = AuditLog.objects.get(action="sso.test_signin")
        assert row.outcome == "success"
        assert row.actor == org_admin and row.org == cfg.org
        assert row.metadata["asserted_email"] == "sso.user@demo.example"
        assert row.metadata["checks"]
        blob = json.dumps(row.metadata)
        assert "s3cr3t" not in blob and "access_token" not in blob

    def test_failed_test_audits_failure(self, admin_client, cfg, org_admin):
        _start(admin_client)
        _callback(admin_client, org_admin, email="x@elsewhere.example")
        assert AuditLog.objects.get(action="sso.test_signin").outcome == "failure"
        cfg.refresh_from_db()
        assert cfg.last_test_ok is False


class TestResultLines:
    def _run(self, admin_client, cfg, org_admin, **kw):
        _start(admin_client)
        _callback(admin_client, org_admin, **kw)
        return _result(cfg)

    def test_all_pass_for_new_account(self, admin_client, cfg, org_admin):
        cfg.auto_provision = True
        cfg.save()
        c = self._run(admin_client, cfg, org_admin)
        assert c["Identity provider reached"]["level"] == "pass"
        assert c["Email claim found"]["detail"] == "sso.user@demo.example"
        assert c["Email domain routes to single sign-on"]["level"] == "pass"
        assert c["Email verified by the identity provider"]["level"] == "pass"
        assert c["Admission outcome"]["detail"] == "Would create a new account for sso.user@demo.example."

    def test_missing_email(self, admin_client, cfg, org_admin):
        _start(admin_client)
        _callback(admin_client, org_admin, email="")
        c = _result(cfg)
        assert c["Email claim found"]["level"] == "fail"

    def test_domain_unlisted(self, admin_client, cfg, org_admin):
        c = self._run(admin_client, cfg, org_admin, email="a@elsewhere.example")
        line = c["Email domain routes to single sign-on"]
        assert line["level"] == "fail" and "not in this organization's email domains" in line["detail"]
        assert c["Admission outcome"]["level"] == "fail"

    def test_domain_unverified(self, admin_client, cfg, org_admin, settings, org):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
        assert domains.enforcement_enabled()
        c = self._run(admin_client, cfg, org_admin)
        line = c["Email domain routes to single sign-on"]
        assert line["level"] == "fail" and "listed but not verified" in line["detail"]

    def test_email_not_verified(self, admin_client, cfg, org_admin):
        cfg.auto_provision = True
        cfg.save()
        c = self._run(admin_client, cfg, org_admin, email_verified=False)
        assert c["Email verified by the identity provider"]["level"] == "fail"
        assert "did not verify the email" in c["Admission outcome"]["detail"]

    def test_refused_existing_non_member(self, admin_client, cfg, org_admin, make_user):
        make_user("sso.user@demo.example")  # account, but not in the org
        c = self._run(admin_client, cfg, org_admin)
        assert c["Admission outcome"]["detail"].startswith("Would be refused: ")
        assert "not a member" in c["Admission outcome"]["detail"]

    def test_refused_when_auto_provision_off(self, admin_client, cfg, org_admin):
        c = self._run(admin_client, cfg, org_admin)
        assert "does not auto-create accounts" in c["Admission outcome"]["detail"]

    def test_already_linked_account_signs_in(self, admin_client, cfg, org_admin, make_user, org):
        user = make_user("sso.user@demo.example", org=org)
        SocialAccount.objects.create(user=user, provider="oidc-demo", uid="oid-9", extra_data={})
        c = self._run(admin_client, cfg, org_admin, email_verified=False)
        assert c["Admission outcome"]["detail"] == "Would sign in the existing linked account."

    def test_group_preview_grants_and_removes(self, admin_client, cfg, org_admin, make_user, org):
        member = make_user("sso.user@demo.example", org=org)
        keep = PermissionGroup.objects.create(org=org, name="Analysts")
        drop = PermissionGroup.objects.create(org=org, name="Admins")
        PermissionGroupMembership.objects.create(user=member, group=drop)
        cfg.group_map = {"g-analysts": keep.pk, "g-admins": drop.pk}
        cfg.save()
        c = self._run(admin_client, cfg, org_admin, groups=["g-analysts", "unmapped"])
        assert c["Groups claim found"]["detail"] == "2 group(s) in claim 'groups'"
        assert c["Permission groups that would be granted"]["detail"] == "Analysts"
        assert c["Permission groups that would be removed"]["detail"] == "Admins"
        assert PermissionGroupMembership.objects.filter(user=member, group=drop).exists()

    def test_group_claim_missing_warns(self, admin_client, cfg, org_admin, org):
        group = PermissionGroup.objects.create(org=org, name="Analysts")
        cfg.group_map = {"g": group.pk}
        cfg.save()
        cfg.auto_provision = True
        cfg.save()
        c = self._run(admin_client, cfg, org_admin)
        assert c["Groups claim"]["level"] == "warn"
        assert not any(x["level"] == "fail" for x in c.values())  # a warning is not a failure
        assert "Admission outcome" in c


class TestMarker:
    def _marker(self, client, org, admin, *, ts=None, org_pk=None):
        session = client.session
        session[sso_test.SESSION_MARKER] = signing.dumps(
            {"org": org_pk or org.pk, "uid": admin.pk, "ts": ts or time.time(), "n": "x"},
            salt=sso_test.SALT,
        )
        session.save()

    def test_single_use(self, admin_client, cfg, org, org_admin, make_user):
        make_user("sso.user@demo.example", org=org)
        _start(admin_client)
        first, _ = _callback(admin_client, org_admin)
        assert first.url == PAGE
        # Second callback: no marker -> an ordinary login.
        assert sso_test.SESSION_MARKER not in admin_client.session
        second, _ = _callback(admin_client, org_admin)
        assert AuditLog.objects.filter(action="sso.test_signin").count() == 1
        assert second.url != PAGE

    def test_expired_marker_is_an_ordinary_login(self, admin_client, cfg, org, org_admin):
        _start(admin_client)
        with mock.patch("django.core.signing.time.time", return_value=time.time() + sso_test.TTL_SECONDS + 5):
            response, _ = _callback(admin_client, org_admin)
        assert not AuditLog.objects.filter(action="sso.test_signin").exists()
        # Ordinary flow: auto_provision is off and nobody is linked -> refused.
        assert response.status_code == 403
        assert sso_test.SESSION_MARKER not in admin_client.session

    def test_tampered_marker_is_ignored(self, admin_client, cfg, org_admin):
        session = admin_client.session
        session[sso_test.SESSION_MARKER] = "forged"
        session.save()
        response, _ = _callback(admin_client, org_admin)
        assert response.status_code == 403
        assert not OrgSSOConfig.objects.filter(last_test_at__isnull=False).exists()

    def test_marker_for_another_admin_is_not_honoured(self, admin_client, cfg, org, org_admin, make_user):
        other = make_user("other@demo.example", org=org, org_role=roles.ORG_ADMIN)
        self._marker(admin_client, org, other)
        response, _ = _callback(admin_client, org_admin)
        assert response.status_code == 403
        assert not AuditLog.objects.filter(action="sso.test_signin").exists()

    def test_marker_for_another_org_is_not_honoured(self, admin_client, cfg, org, org_admin, other_org):
        self._marker(admin_client, org, org_admin, org_pk=other_org.pk)
        response, _ = _callback(admin_client, org_admin)
        assert response.status_code == 403
        assert not AuditLog.objects.filter(action="sso.test_signin").exists()

    def test_marker_on_logged_out_session_is_not_honoured(self, client, cfg, org, org_admin):
        self._marker(client, org, org_admin)
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get("/")
        request.session = client.session
        request.user = AnonymousUser()
        assert sso_test.consume(request, cfg) is False
        assert sso_test.SESSION_MARKER not in request.session  # burnt regardless


class TestNonTestCallbackUnchanged:
    def test_ordinary_login_still_logs_member_in(self, client, cfg, org, make_user):
        member = make_user("sso.user@demo.example", org=org)
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get("/accounts/oidc/oidc-demo/login/callback/")
        request.session = client.session
        request.user = AnonymousUser()
        request._messages = FallbackStorage(request)
        provider = get_adapter().get_provider(request, "oidc-demo")
        sociallogin = provider.sociallogin_from_response(
            request, {"userinfo": {"sub": "u1", "email": member.email, "email_verified": True}},
        )
        with context.request_context(request):
            complete_social_login(request, sociallogin)
        assert SocialAccount.objects.filter(user=member).exists()
        assert request.session["_auth_user_id"] == str(member.pk)
        assert not AuditLog.objects.filter(action="sso.test_signin").exists()


class TestNudge:
    def test_warns_when_enforced_and_untested(self, admin_client, cfg):
        cfg.enforce_sso = True
        cfg.save()
        assert NUDGE in admin_client.get(PAGE).content.decode()

    def test_warns_when_password_login_off(self, admin_client, cfg):
        cfg.allow_password_login = False
        cfg.save()
        assert NUDGE in admin_client.get(PAGE).content.decode()

    def test_quiet_after_successful_test(self, admin_client, cfg):
        cfg.enforce_sso = True
        cfg.last_test_ok = True
        cfg.save()
        assert NUDGE not in admin_client.get(PAGE).content.decode()

    def test_still_warns_after_failed_test(self, admin_client, cfg):
        cfg.enforce_sso = True
        cfg.last_test_ok = False
        cfg.save()
        assert NUDGE in admin_client.get(PAGE).content.decode()

    def test_quiet_when_not_enforcing(self, admin_client, cfg):
        assert NUDGE not in admin_client.get(PAGE).content.decode()

    def test_saving_enforcement_shows_warning_without_blocking(self, admin_client, org):
        resp = admin_client.post(PAGE, {
            "enabled": "on", "issuer_url": ISSUER, "client_id": "cid", "enforce_sso": "on",
            "allow_password_login": "on", "email_domains_text": "demo.example",
            "default_org_role": "member", "groups_claim": "groups",
        }, follow=True)
        assert resp.status_code == 200
        assert OrgSSOConfig.objects.get(org=org).enforce_sso is True
        assert NUDGE in resp.content.decode()


# ── directory ────────────────────────────────────────────────────────────
BASE = "dc=corp,dc=example"
ALICE_DN = f"cn=alice,ou=people,{BASE}"
SVC_DN = f"cn=svc,ou=people,{BASE}"
ANALYSTS = f"cn=analysts,ou=groups,{BASE}"
PASSWORD = "s3cret-dir-pw"
BIND_PASSWORD = "svc-bind-pw"


@pytest.fixture
def directory(monkeypatch):
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
        conn.bind()
        return conn

    monkeypatch.setattr(ldap, "_open", _open)


@pytest.fixture
def ldap_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, auth_method=OrgSSOConfig.AUTH_LDAP,
        ldap_server_uri="ldaps://dc.corp.example:636", ldap_user_search_base=BASE,
        ldap_bind_dn=SVC_DN, ldap_bind_password=BIND_PASSWORD,
        email_domains=["corp.example"], auto_provision=True,
    )


def _directory_test(client, email="alice@corp.example", password=PASSWORD):
    return client.post(PAGE, {"action": "test-directory", "email": email, "password": password})


class TestDirectory:
    def test_success_creates_and_logs_in_nothing(self, admin_client, ldap_cfg, directory, org, org_admin):
        group = PermissionGroup.objects.create(org=org, name="Analysts")
        ldap_cfg.group_map = {ANALYSTS: group.pk}
        ldap_cfg.save()
        users = User.objects.count()
        resp = _directory_test(admin_client)
        assert resp.status_code == 302
        assert User.objects.count() == users
        assert not PermissionGroupMembership.objects.exists()
        assert admin_client.session["_auth_user_id"] == str(org_admin.pk)
        c = _result(ldap_cfg)
        assert c["Directory reached"]["level"] == "pass"
        assert c["Email attribute found"]["detail"] == "alice@corp.example"
        assert c["Admission outcome"]["detail"] == "Would create a new account for alice@corp.example."
        assert c["Permission groups that would be granted"]["detail"] == "Analysts"
        ldap_cfg.refresh_from_db()
        assert ldap_cfg.last_test_ok is True and ldap_cfg.last_test_by == org_admin

    def test_wrong_password_fails_without_leaking_it(self, admin_client, ldap_cfg, directory):
        _directory_test(admin_client, password="nope-nope")
        c = _result(ldap_cfg)
        assert c["Credentials accepted"]["level"] == "fail"
        row = AuditLog.objects.get(action="sso.test_signin")
        assert row.outcome == "failure"
        assert "nope-nope" not in json.dumps(row.metadata)
        assert PASSWORD not in json.dumps(row.metadata)
        assert "nope-nope" not in admin_client.get(PAGE).content.decode()

    def test_outside_domain_and_existing_non_member(self, admin_client, ldap_cfg, directory, make_user):
        make_user("alice@corp.example")  # exists, not in the org
        _directory_test(admin_client)
        c = _result(ldap_cfg)
        assert "not a member" in c["Admission outcome"]["detail"]
        ldap_cfg.email_domains = ["other.example"]
        ldap_cfg.save()
        _directory_test(admin_client)
        c = _result(ldap_cfg)
        assert c["Email domain routes to single sign-on"]["level"] == "fail"

    def test_directory_down(self, admin_client, ldap_cfg, monkeypatch):
        def boom(cfg, user, password):
            raise ldap.LDAPError("LDAPSocketOpenError: timed out")

        monkeypatch.setattr(ldap, "authenticate", boom)
        _directory_test(admin_client)
        assert _result(ldap_cfg)["Directory reached"]["level"] == "fail"

    def test_member_cannot_run_it(self, login, member, ldap_cfg, directory):
        client = login(member)
        _directory_test(client)
        assert not AuditLog.objects.filter(action="sso.test_signin").exists()

    def test_oidc_org_cannot_run_directory_test(self, admin_client, cfg):
        _directory_test(admin_client)
        assert not AuditLog.objects.filter(action="sso.test_signin").exists()

    def test_form_renders_for_directory(self, admin_client, ldap_cfg):
        body = admin_client.get(PAGE).content.decode()
        assert 'name="action" value="test-directory"' in body
        assert 'type="password"' in body
