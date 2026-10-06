"""Personal API keys (internal planning ticket #002): mint/verify, the Bearer middleware
(apps.accounts.api_keys), scope, org confinement, CSRF, the chat-endpoint
refusal, audit attribution, and the two pages."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import ApiKey
from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs.models import OrgMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio, roles.DEVELOPER)
    return user


@pytest.fixture
def prefix(org, studio):
    return f"/s/{org.slug}/{studio.slug}"


def bearer(secret: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}


def mint(user, org, **kw):
    return ApiKey.mint(user=user, org=org, name=kw.pop("name", "test"), **kw)


class TestModel:
    def test_mint_format_and_verify(self, developer, org):
        key, secret = mint(developer, org)
        assert secret.startswith("trellum_pk_")
        assert len(secret) == len("trellum_pk_") + 8 + 1 + 32
        assert secret[11:19] == key.prefix
        assert secret not in key.key_hash and len(key.key_hash) == 64
        assert key.is_active and not key.can_write
        assert ApiKey.authenticate(secret) == key
        assert ApiKey.authenticate(secret[:-4] + "XXXX") is None
        assert ApiKey.authenticate("trellum_pk_") is None

    def test_write_scope(self, developer, org):
        key, _ = mint(developer, org, scopes=ApiKey.READ_WRITE)
        assert key.can_write

    def test_secrets_are_unique(self, developer, org):
        secrets = {mint(developer, org)[1] for _ in range(5)}
        assert len(secrets) == 5


class TestBearerAuth:
    def test_key_obeys_selected_reports_and_revocation(
        self, client, member, org, prefix, report_row, make_group, attach_group, settings
    ):
        from apps.orgs.models import PermissionGroupGrant
        from apps.reports.models import Report, ReportPermissionGrant

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        group = make_group("Report readers", grants=[(report_row.studio, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        assignment = ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        Report.objects.create(studio=report_row.studio, slug="private-report", name="Private report")
        _, secret = mint(member, org)
        headers = bearer(secret)

        response = client.get(f"{prefix}/api/registry", **headers)
        assert [row["id"] for row in response.json()["reports"]] == [report_row.pk]
        assert client.get(f"{prefix}/r/private-report/data.json", **headers).status_code == 404
        assert client.get(f"{prefix}/operations", **headers).status_code == 403
        assignment.delete()
        assert client.get(f"{prefix}/api/registry", **headers).json()["reports"] == []
        assert client.get(f"{prefix}/r/{report_row.slug}/", **headers).status_code == 404

    def test_read_key_reads(self, client, developer, org, prefix):
        _, secret = mint(developer, org)
        resp = client.get(f"{prefix}/api/registry", **bearer(secret))
        assert resp.status_code == 200
        assert resp.wsgi_request.user == developer

    def test_no_key_no_session_is_401(self, client, prefix):
        assert client.get(f"{prefix}/api/registry").status_code == 401

    @pytest.mark.parametrize(
        "break_it",
        ["revoked", "expired", "inactive_user", "non_member", "org_disabled", "wrong_secret"],
    )
    def test_dead_keys_are_401(self, client, developer, org, prefix, break_it):
        key, secret = mint(developer, org)
        if break_it == "revoked":
            key.revoke()
        elif break_it == "expired":
            ApiKey.objects.filter(pk=key.pk).update(
                expires_at=timezone.now() - timedelta(minutes=1)
            )
        elif break_it == "inactive_user":
            developer.is_active = False
            developer.save()
        elif break_it == "non_member":
            OrgMembership.objects.filter(user=developer, org=org).delete()
        elif break_it == "org_disabled":
            org.api_keys_enabled = False
            org.save()
        elif break_it == "wrong_secret":
            secret = secret[:-4] + "XXXX"
        resp = client.get(f"{prefix}/api/registry", **bearer(secret))
        assert resp.status_code == 401
        assert resp.json() == {"error": "invalid_api_key"}

    def test_bad_key_never_falls_through_to_the_session(self, login, developer, org, prefix):
        key, secret = mint(developer, org)
        key.revoke()
        c = login(developer)
        assert c.get(f"{prefix}/api/registry").status_code == 200
        assert c.get(f"{prefix}/api/registry", **bearer(secret)).status_code == 401

    def test_read_key_on_post_is_403(self, client, developer, org, prefix):
        _, secret = mint(developer, org)
        resp = client.post(f"{prefix}/api/system/cache/clear", **bearer(secret))
        assert resp.status_code == 403
        assert resp.json() == {"error": "write_scope_required"}

    def test_write_key_posts_without_csrf(self, developer, org, prefix):
        _, secret = mint(developer, org, scopes=ApiKey.READ_WRITE)
        resp = Client(enforce_csrf_checks=True).post(
            f"{prefix}/api/system/cache/clear", **bearer(secret)
        )
        assert resp.status_code == 200

    def test_session_post_still_needs_csrf(self, developer, prefix):
        c = Client(enforce_csrf_checks=True)
        c.force_login(developer)
        assert c.post(f"{prefix}/api/system/cache/clear").status_code == 403

    def test_session_request_unaffected(self, login, developer, prefix):
        resp = login(developer).get(f"{prefix}/api/registry")
        assert resp.status_code == 200
        assert getattr(resp.wsgi_request, "api_key", None) is None

    def test_no_session_cookie_for_a_bearer_request(self, client, developer, org, prefix):
        _, secret = mint(developer, org)
        resp = client.get(f"{prefix}/api/registry", **bearer(secret))
        assert "sessionid" not in resp.cookies

    def test_key_never_crosses_orgs(
        self, client, developer, org, other_org, other_studio, grant_studio, prefix
    ):
        OrgMembership.objects.create(user=developer, org=other_org, role=roles.ORG_ADMIN)
        grant_studio(developer, other_studio, roles.ADMIN)
        _, secret = mint(developer, org)
        other = f"/s/{other_org.slug}/{other_studio.slug}/api/registry"
        assert client.get(other, **bearer(secret)).status_code == 404
        assert client.get(f"{prefix}/api/registry", **bearer(secret)).status_code == 200

    def test_role_still_applies_under_a_write_key(self, client, make_user, grant_studio, org, studio, prefix):
        viewer = make_user("viewer@demo.example", org=org)
        grant_studio(viewer, studio, roles.VIEWER)
        _, secret = mint(viewer, org, scopes=ApiKey.READ_WRITE)
        assert client.post(f"{prefix}/api/system/cache/clear", **bearer(secret)).status_code == 403

    def test_chat_endpoints_are_session_only(self, client, developer, org, prefix):
        _, secret = mint(developer, org, scopes=ApiKey.READ_WRITE)
        for method in ("get", "post"):
            resp = getattr(client, method)(f"{prefix}/api/assistant/sessions", **bearer(secret))
            assert resp.status_code == 403
            assert resp.json() == {"error": "session_required"}

    def test_audit_rows_carry_the_key_id(self, client, developer, org, prefix):
        key, secret = mint(developer, org, scopes=ApiKey.READ_WRITE)
        client.post(f"{prefix}/api/system/cache/clear", **bearer(secret))
        row = AuditLog.objects.get(action="cache.clear")
        assert row.actor == developer
        assert row.metadata["api_key"] == key.pk

    def test_last_used_is_stamped_at_most_once_a_minute(self, client, developer, org, prefix):
        key, secret = mint(developer, org)
        url = f"{prefix}/api/registry"
        assert key.last_used_at is None
        client.get(url, **bearer(secret))
        key.refresh_from_db()
        assert key.last_used_at is not None

        recent = timezone.now() - timedelta(seconds=30)
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=recent)
        client.get(url, **bearer(secret))
        key.refresh_from_db()
        assert key.last_used_at == recent

        old = timezone.now() - timedelta(seconds=61)
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=old)
        client.get(url, **bearer(secret))
        key.refresh_from_db()
        assert key.last_used_at > old


class TestMyKeysPage:
    URL = "/me/api-keys"

    def test_requires_login(self, client, db):
        assert client.get(self.URL).status_code == 302

    def test_nav_link(self, login, developer):
        assert self.URL in login(developer).get("/").content.decode()

    def test_create_shows_the_secret_once_and_audits(self, login, developer, org):
        c = login(developer)
        resp = c.post(self.URL, {"action": "create", "name": "laptop", "org": org.pk, "scopes": "read"})
        assert resp.status_code == 302
        key = ApiKey.objects.get(user=developer, org=org, name="laptop")
        assert key.expires_at is None and not key.can_write

        page = c.get(self.URL).content.decode()
        assert f"trellum_pk_{key.prefix}_" in page  # the full secret, once
        assert "only time it is shown" in page
        assert "curl -H" in page
        again = c.get(self.URL).content.decode()
        assert f"trellum_pk_{key.prefix}_" not in again
        assert f"trellum_pk_{key.prefix}…" in again  # the list shows the prefix

        row = AuditLog.objects.get(action="apikey.create")
        assert row.actor == developer and row.org == org
        assert row.target_id == str(key.pk)
        assert row.metadata["prefix"] == key.prefix
        assert "api_key" not in row.metadata  # created over a session, not a key

    def test_create_shows_the_setup_portal_line_for_each_studio(self, login, developer, org, studio):
        from apps.core.instance import base_url

        c = login(developer)
        c.post(self.URL, {"action": "create", "name": "laptop", "org": org.pk, "scopes": "read"})
        key = ApiKey.objects.get(user=developer)
        page = c.get(self.URL).content.decode()
        line = (f"trellum setup portal --url {base_url()}/s/{org.slug}/{studio.slug} "
                f"--key trellum_pk_{key.prefix}_")
        assert line in page
        assert "trellum setup portal" not in c.get(self.URL).content.decode()  # once, like the secret

    def test_create_with_write_scope_and_expiry(self, login, developer, org):
        tomorrow = timezone.localdate() + timedelta(days=1)
        login(developer).post(
            self.URL,
            {"action": "create", "name": "ci", "org": org.pk, "scopes": "read,write",
             "expires_at": tomorrow.isoformat()},
        )
        key = ApiKey.objects.get(user=developer)
        assert key.can_write and key.is_active
        assert timezone.localtime(key.expires_at).date() == tomorrow

    def test_past_expiry_is_rejected(self, login, developer, org):
        resp = login(developer).post(
            self.URL,
            {"action": "create", "name": "x", "org": org.pk, "scopes": "read",
             "expires_at": timezone.localdate().isoformat()},
        )
        assert resp.status_code == 200
        assert "Pick a date after today" in resp.content.decode()
        assert not ApiKey.objects.exists()

    def test_cannot_mint_for_an_org_you_are_not_in(self, login, developer, other_org):
        resp = login(developer).post(
            self.URL, {"action": "create", "name": "x", "org": other_org.pk, "scopes": "read"}
        )
        assert resp.status_code == 200
        assert not ApiKey.objects.exists()

    def test_cannot_mint_when_the_org_has_keys_off(self, login, developer, org):
        org.api_keys_enabled = False
        org.save()
        c = login(developer)
        assert "switched off" in c.get(self.URL).content.decode()
        c.post(self.URL, {"action": "create", "name": "x", "org": org.pk, "scopes": "read"})
        assert not ApiKey.objects.exists()

    def test_list_and_revoke(self, login, developer, org, prefix):
        key, secret = mint(developer, org, name="laptop")
        c = login(developer)
        page = c.get(self.URL).content.decode()
        assert "laptop" in page and key.prefix in page and "active" in page

        resp = c.post(self.URL, {"action": "revoke", "key_id": key.pk})
        assert resp.status_code == 302
        key.refresh_from_db()
        assert key.revoked_at is not None
        assert AuditLog.objects.filter(action="apikey.revoke", target_id=str(key.pk)).exists()
        assert Client().get(f"{prefix}/api/registry", **bearer(secret)).status_code == 401
        assert "revoked" in c.get(self.URL).content.decode()
        # Already revoked: nothing to revoke again.
        assert c.post(self.URL, {"action": "revoke", "key_id": key.pk}).status_code == 404

    def test_cannot_revoke_someone_elses_key(self, login, developer, org_admin, org):
        key, _ = mint(org_admin, org)
        assert login(developer).post(self.URL, {"action": "revoke", "key_id": key.pk}).status_code == 404
        key.refresh_from_db()
        assert key.revoked_at is None


class TestOrgKeysPage:
    @staticmethod
    def url(org):
        return f"/orgs/{org.slug}/settings/api-keys"

    def test_member_is_403(self, login, developer, org):
        assert login(developer).get(self.url(org)).status_code == 403

    def test_lists_every_key_in_the_org_only(
        self, login, org_admin, developer, org, other_org, make_user
    ):
        mint(developer, org, name="dev-laptop")
        outsider = make_user("out@rival.example", org=other_org)
        mint(outsider, other_org, name="rival-key")
        page = login(org_admin).get(self.url(org)).content.decode()
        assert "dev-laptop" in page and developer.email in page
        assert "rival-key" not in page
        assert "tl-console-nav-link active" in page

    def test_admin_revokes_a_members_key(self, login, org_admin, developer, org, prefix):
        key, secret = mint(developer, org)
        resp = login(org_admin).post(self.url(org), {"action": "revoke", "key_id": key.pk})
        assert resp.status_code == 302
        key.refresh_from_db()
        assert key.revoked_at is not None
        row = AuditLog.objects.get(action="apikey.revoke")
        assert row.actor == org_admin and row.org == org and row.target_id == str(key.pk)
        assert Client().get(f"{prefix}/api/registry", **bearer(secret)).status_code == 401

    def test_cannot_revoke_across_orgs(self, login, org_admin, org, other_org, make_user):
        outsider = make_user("out@rival.example", org=other_org)
        key, _ = mint(outsider, other_org)
        assert login(org_admin).post(self.url(org), {"action": "revoke", "key_id": key.pk}).status_code == 404

    def test_toggle_off_stops_every_key_and_on_restores(
        self, login, org_admin, developer, org, prefix
    ):
        _, secret = mint(developer, org)
        c = login(org_admin)
        c.post(self.url(org), {"action": "toggle"})  # unchecked box
        org.refresh_from_db()
        assert not org.api_keys_enabled
        assert Client().get(f"{prefix}/api/registry", **bearer(secret)).status_code == 401
        assert AuditLog.objects.get(action="org.api_keys_set").metadata["enabled"] is False

        c.post(self.url(org), {"action": "toggle", "api_keys_enabled": "1"})
        org.refresh_from_db()
        assert org.api_keys_enabled
        assert Client().get(f"{prefix}/api/registry", **bearer(secret)).status_code == 200
