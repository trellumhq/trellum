"""Per-org OpenID Connect SSO: transient apps, login routing, the pre_social_login
gate, group sync, and the settings UI."""
import types

import pytest
import requests
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.adapters import OrgSSOAdapter
from apps.accounts import sso
from apps.core import roles
from apps.orgs.models import (
    OrgMembership,
    OrgSSOConfig,
    PermissionGroup,
    PermissionGroupMembership,
)

User = get_user_model()

pytestmark = pytest.mark.django_db

ISSUER = "https://keycloak.internal/realms/acme"


@pytest.fixture(autouse=True)
def _domain_verification_is_covered_by_its_own_suite(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False


@pytest.fixture
def sso_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org,
        enabled=True,
        issuer_url=ISSUER,
        client_id="client-abc",
        client_secret="entra-secret",
        email_domains=["demo.example"],
    )


@pytest.fixture
def rf_request():
    request = RequestFactory().get("/accounts/oidc/oidc-demo/login/callback/")
    request.session = {}
    return request


def _sociallogin(provider="oidc-demo", email="sso.user@demo.example", uid="oid-1", **claims):
    account = SocialAccount(
        provider=provider, uid=uid, extra_data={"email": email, **claims}
    )
    user = User(email=email)
    return SocialLogin(user=user, account=account)


class TestTransientApps:
    def test_list_apps_contains_org_app_with_decrypted_secret(self, sso_cfg):
        apps = OrgSSOAdapter().list_apps(None, provider="openid_connect")
        app = next(a for a in apps if a.provider_id == "oidc-demo")
        assert app.client_id == "client-abc"
        assert app.secret == "entra-secret"  # decrypted in memory only
        assert app.settings["server_url"] == ISSUER
        assert app.pk is None  # never persisted

    def test_disabled_or_incomplete_configs_excluded(self, sso_cfg, other_org):
        OrgSSOConfig.objects.create(
            org=other_org, enabled=True, client_id="", issuer_url=ISSUER
        )
        sso_cfg.enabled = False
        sso_cfg.save()
        apps = OrgSSOAdapter().list_apps(None, provider="openid_connect")
        assert not [a for a in apps if str(a.provider_id).startswith("oidc-")]

    def test_provider_urls_resolve(self, sso_cfg):
        assert reverse(
            "openid_connect_login", kwargs={"provider_id": "oidc-demo"}
        ).endswith("/oidc-demo/login/")
        assert "oidc-demo/login/callback/" in sso.redirect_uri_for_org(sso_cfg.org)

    def test_enabled_configs_need_an_issuer(self, sso_cfg, other_org):
        OrgSSOConfig.objects.create(org=other_org, enabled=True, client_id="cid")  # no issuer
        assert [c.org for c in sso.enabled_configs()] == [sso_cfg.org]


class TestLoginRouting:
    def test_sso_domain_redirects_to_provider(self, client, sso_cfg, member):
        resp = client.post("/login", {"email": "someone@demo.example", "password": ""})
        assert resp.status_code == 302
        assert "oidc-demo/login" in resp.url

    def test_password_login_still_allowed_when_org_permits(self, client, sso_cfg, make_user, org):
        user = make_user("dev@demo.example", org=org)
        resp = client.post("/login", {"email": user.email, "password": "pw-Str0ng-pw"})
        assert resp.status_code == 302 and "oidc-" not in resp.url

    def test_password_forced_to_sso_when_forbidden(self, client, sso_cfg, make_user, org):
        sso_cfg.allow_password_login = False
        sso_cfg.save()
        user = make_user("dev@demo.example", org=org)
        resp = client.post("/login", {"email": user.email, "password": "pw-Str0ng-pw"})
        assert resp.status_code == 302
        assert "oidc-demo/login" in resp.url

    def test_other_domains_untouched(self, client, sso_cfg, make_user, org):
        user = make_user("ext@partner.io", org=org)
        resp = client.post("/login", {"email": user.email, "password": "pw-Str0ng-pw"})
        assert resp.status_code == 302 and "oidc-" not in resp.url


class TestPreSocialLoginGate:
    def _gate(self, request, login_obj):
        return OrgSSOAdapter().pre_social_login(request, login_obj)

    def test_domain_outside_allowlist_denied(self, sso_cfg, rf_request):
        login_obj = _sociallogin(email="attacker@gmail.com")
        with pytest.raises(ImmediateHttpResponse) as exc:
            self._gate(rf_request, login_obj)
        assert exc.value.response.status_code == 403

    def test_missing_email_denied(self, sso_cfg, rf_request):
        account = SocialAccount(provider="oidc-demo", uid="x", extra_data={})
        login_obj = SocialLogin(user=User(), account=account)
        with pytest.raises(ImmediateHttpResponse):
            self._gate(rf_request, login_obj)

    def test_unknown_provider_denied(self, rf_request, org):
        login_obj = _sociallogin(provider="oidc-ghost")
        with pytest.raises(ImmediateHttpResponse):
            self._gate(rf_request, login_obj)

    def test_existing_member_gets_linked(self, sso_cfg, rf_request, make_user, org, monkeypatch):
        member = make_user("sso.user@demo.example", org=org)
        login_obj = _sociallogin(email=member.email)
        connected = {}
        monkeypatch.setattr(
            SocialLogin, "connect", lambda self, req, user: connected.update(user=user)
        )
        self._gate(rf_request, login_obj)
        assert connected["user"] == member

    def test_existing_nonmember_denied(self, sso_cfg, rf_request, make_user):
        make_user("sso.user@demo.example")  # exists, but no org membership
        login_obj = _sociallogin()
        with pytest.raises(ImmediateHttpResponse) as exc:
            self._gate(rf_request, login_obj)
        assert b"not a member" in exc.value.response.content

    def test_new_user_denied_without_auto_provision(self, sso_cfg, rf_request):
        with pytest.raises(ImmediateHttpResponse) as exc:
            self._gate(rf_request, _sociallogin())
        assert b"invitation" in exc.value.response.content

    def test_new_user_passes_with_auto_provision(self, sso_cfg, rf_request, settings):
        sso_cfg.auto_provision = True
        sso_cfg.save()
        self._gate(rf_request, _sociallogin())  # no exception -> allauth signs up
        assert OrgSSOAdapter().is_open_for_signup(rf_request, _sociallogin()) is True


class TestProvisionAndGroupSync:
    def test_provision_membership_and_default_groups(self, sso_cfg, make_user, org, make_group):
        group = make_group("Demo BA", default_studio_role=roles.VIEWER)
        sso_cfg.default_groups.add(group)
        user = make_user("fresh@demo.example")  # no membership yet
        sso.provision_membership(user, sso_cfg)
        assert OrgMembership.objects.filter(user=user, org=org, role="member").exists()
        assert PermissionGroupMembership.objects.filter(user=user, group=group).exists()
        # Idempotent.
        sso.provision_membership(user, sso_cfg)
        assert OrgMembership.objects.filter(user=user, org=org).count() == 1

    def test_group_sync_add_and_remove(self, sso_cfg, member, make_group):
        ba = make_group("BA")
        admins = make_group("Admins", org_role=roles.ORG_ADMIN)
        sso_cfg.group_map = {"entra-ba-id": ba.pk, "entra-admin-id": admins.pk}
        sso_cfg.save()

        sso.sync_groups(member, sso_cfg, {"groups": ["entra-ba-id"]})
        assert PermissionGroupMembership.objects.filter(user=member, group=ba).exists()
        assert not PermissionGroupMembership.objects.filter(user=member, group=admins).exists()

        # The provider now says: admin yes, ba no -> memberships follow.
        sso.sync_groups(member, sso_cfg, {"groups": ["entra-admin-id"]})
        assert not PermissionGroupMembership.objects.filter(user=member, group=ba).exists()
        assert PermissionGroupMembership.objects.filter(user=member, group=admins).exists()

    def test_unmapped_portal_groups_untouched(self, sso_cfg, member, make_group, attach_group):
        manual = make_group("Manual")
        attach_group(member, manual)
        sso_cfg.group_map = {"x": make_group("Mapped").pk}
        sso_cfg.save()
        sso.sync_groups(member, sso_cfg, {"groups": []})
        assert PermissionGroupMembership.objects.filter(user=member, group=manual).exists()


    def test_groups_claim_name_is_configurable(self, sso_cfg, member, make_group):
        ba = make_group("BA")
        sso_cfg.groups_claim = "roles"
        sso_cfg.group_map = {"analyst": ba.pk}
        sso_cfg.save()
        sso.sync_groups(member, sso_cfg, {"roles": ["analyst"], "groups": []})
        assert PermissionGroupMembership.objects.filter(user=member, group=ba).exists()
        # A lone group arrives as a bare string from some IdPs; "groups" is ignored.
        sso.sync_groups(member, sso_cfg, {"roles": "other", "groups": ["analyst"]})
        assert not PermissionGroupMembership.objects.filter(user=member, group=ba).exists()


def _document(issuer):
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
    }


class TestDiscovery:
    """discover() never leaves the process here: requests.get is replaced."""

    @staticmethod
    def _http(monkeypatch, status=200, body=None, exc=None):
        def fake_get(url, timeout):
            assert url == f"{ISSUER}/.well-known/openid-configuration"
            assert timeout
            if exc:
                raise exc
            return types.SimpleNamespace(status_code=status, json=lambda: body)
        monkeypatch.setattr(requests, "get", fake_get)

    def test_success_returns_the_document(self, monkeypatch):
        self._http(monkeypatch, body=_document(ISSUER))
        assert sso.discover(ISSUER + "/") == _document(ISSUER)  # trailing slash tolerated

    @pytest.mark.parametrize(
        "status,body,exc,fragment",
        [
            (404, None, None, "HTTP 404"),
            (200, _document("https://elsewhere.example"), None, "names the issuer"),
            (200, {"issuer": ISSUER, "token_endpoint": "t"}, None, "no authorization_endpoint"),
            (200, ["not", "an", "object"], None, "not a JSON object"),
            (200, None, requests.ConnectionError("refused"), "Could not fetch"),
        ],
    )
    def test_failures_are_readable(self, monkeypatch, status, body, exc, fragment):
        self._http(monkeypatch, status=status, body=body, exc=exc)
        with pytest.raises(ValueError, match=fragment):
            sso.discover(ISSUER)


class TestProviderPrefixMigration:
    def test_linked_identities_follow_the_prefix(self, make_user):
        from importlib import import_module

        from django.apps import apps as registry

        migration = import_module("apps.orgs.migrations.0015_sso_generic_oidc")
        user = make_user("linked@demo.example")
        SocialAccount.objects.create(user=user, provider="entra-demo", uid="oid-1")
        migration.forwards(registry, None)
        assert SocialAccount.objects.get(user=user).provider == "oidc-demo"
        migration.backwards(registry, None)
        assert SocialAccount.objects.get(user=user).provider == "entra-demo"


class TestSSOSettingsUI:
    @pytest.fixture(autouse=True)
    def _offline_discovery(self, monkeypatch):
        monkeypatch.setattr(sso, "discover", _document)

    @staticmethod
    def _post(c, org, follow=False, **overrides):
        data = {
            "enabled": "on", "issuer_url": ISSUER, "client_id": "cid",
            "email_domains_text": "demo.example", "default_org_role": "member",
            "groups_claim": "groups", "allow_password_login": "on",
        }
        data.update(overrides)
        return c.post(f"/orgs/{org.slug}/settings/sso", data, follow=follow)

    def test_issuer_url_is_required_to_enable(self, login, org_admin, org):
        resp = self._post(login(org_admin), org, issuer_url="")
        assert resp.status_code == 200 and b"is required to enable" in resp.content
        assert not OrgSSOConfig.objects.filter(org=org).exists()

    def test_discovery_failure_is_shown_and_nothing_saved(self, login, org_admin, org, monkeypatch):
        def refuse(issuer):
            raise ValueError(f"{issuer}/.well-known/openid-configuration answered HTTP 404.")
        monkeypatch.setattr(sso, "discover", refuse)
        resp = self._post(login(org_admin), org)
        assert resp.status_code == 200
        assert b"answered HTTP 404" in resp.content
        assert not OrgSSOConfig.objects.filter(org=org).exists()

    def test_discovered_endpoints_shown_after_save(self, login, org_admin, org):
        resp = self._post(login(org_admin), org, follow=True)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert f"{ISSUER}/protocol/openid-connect/auth" in html
        assert f"{ISSUER}/protocol/openid-connect/token" in html
        assert OrgSSOConfig.objects.get(org=org).issuer_url == ISSUER

    def test_disabling_never_needs_the_idp(self, login, org_admin, org, monkeypatch):
        OrgSSOConfig.objects.create(
            org=org, enabled=True, issuer_url=ISSUER, client_id="cid", enforce_sso=True,
        )

        def down(issuer):
            raise ValueError("IdP unreachable")
        monkeypatch.setattr(sso, "discover", down)
        resp = self._post(login(org_admin), org, enabled="")
        assert resp.status_code == 302
        assert OrgSSOConfig.objects.get(org=org).enabled is False

    def test_issuer_url_must_be_https(self, login, org_admin, org):
        from django.core.exceptions import ValidationError

        resp = self._post(login(org_admin), org, issuer_url="http://keycloak.internal/realms/acme")
        assert resp.status_code == 200 and b"must use https" in resp.content
        assert not OrgSSOConfig.objects.filter(org=org).exists()
        with pytest.raises(ValidationError):  # the model field refuses it too
            OrgSSOConfig._meta.get_field("issuer_url").run_validators("http://keycloak.internal")

    def test_trailing_slash_is_dropped(self, login, org_admin, org):
        self._post(login(org_admin), org, issuer_url=ISSUER + "/")
        assert OrgSSOConfig.objects.get(org=org).issuer_url == ISSUER

    def test_extra_scopes_commas_become_spaces(self, login, org_admin, org):
        self._post(login(org_admin), org, extra_scopes=" groups,  allatclaims ,")
        assert OrgSSOConfig.objects.get(org=org).extra_scopes == "groups allatclaims"

    def test_save_and_secret_kept_on_blank(self, login, org_admin, org, make_group):
        group = make_group("Demo BA")
        c = login(org_admin)
        url = f"/orgs/{org.slug}/settings/sso"
        resp = c.post(
            url,
            {
                "enabled": "on",
                "issuer_url": ISSUER,
                "client_id": "cid",
                "client_secret": "first-secret",
                "email_domains_text": "Demo.example\n@partner.io",
                "auto_provision": "on",
                "default_org_role": "member", "groups_claim": "groups",
                "default_groups": [group.pk],
                "group_map_text": f"abc-123 = {group.name}",
                "allow_password_login": "on",
            },
        )
        assert resp.status_code == 302
        cfg = OrgSSOConfig.objects.get(org=org)
        assert cfg.email_domains == ["demo.example", "partner.io"]
        assert cfg.group_map == {"abc-123": group.pk}
        assert cfg.client_secret == "first-secret"
        # Blank secret on re-save keeps the stored one.
        c.post(
            url,
            {
                "enabled": "on", "issuer_url": ISSUER, "client_id": "cid",
                "client_secret": "", "email_domains_text": "demo.example",
                "default_org_role": "member", "groups_claim": "groups", "allow_password_login": "on",
            },
        )
        cfg.refresh_from_db()
        assert cfg.client_secret == "first-secret"

    def test_bad_group_name_rejected(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/sso",
            {
                "enabled": "on", "issuer_url": ISSUER, "client_id": "c",
                "email_domains_text": "demo.example", "default_org_role": "member", "groups_claim": "groups",
                "group_map_text": "abc = Nope",
            },
        )
        assert resp.status_code == 200  # re-rendered with error
        assert not OrgSSOConfig.objects.filter(org=org).exists()

    def test_page_shows_redirect_uri(self, login, org_admin, org):
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/sso").content.decode()
        assert "oidc-demo/login/callback/" in html

    def test_member_cannot_open(self, login, member, org):
        assert login(member).get(f"/orgs/{org.slug}/settings/sso").status_code == 403
