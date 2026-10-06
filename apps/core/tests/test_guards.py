"""The view decorators enforce the 401/403/404 policy on real URLs."""
import pytest

from apps.core import roles

pytestmark = pytest.mark.django_db


class TestUnauthenticated:
    def test_org_page_redirects_to_login(self, client, org, member):
        # /orgs/<slug>/ is a legacy alias for the hub now; the chain must
        # still end at the login page for anonymous users. (member fixture:
        # with zero users the chain would legitimately end at /setup.)
        resp = client.get(f"/orgs/{org.slug}/", follow=True)
        assert resp.redirect_chain[-1][0].startswith("/login")

    def test_home_redirects_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302

    def test_api_returns_401_json(self, client):
        resp = client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json() == {"authenticated": False}


class TestOrgGuard:
    def test_non_member_sees_nothing_of_the_org(self, login, make_user, org):
        # The hub never 404s, but a non-member must get zero org content —
        # and the decorated settings pages still hide existence with 404.
        user = make_user("outsider@else.com")
        c = login(user)
        resp = c.get(f"/orgs/{org.slug}/", follow=True)
        assert resp.status_code == 200
        assert org.name not in resp.content.decode()
        assert c.get(f"/orgs/{org.slug}/settings/members").status_code == 404

    def test_unknown_org_hidden(self, login, member, org):
        c = login(member)
        # Hub falls back to the member's own org; settings 404.
        resp = c.get("/orgs/does-not-exist/", follow=True)
        assert resp.status_code == 200
        assert org.name in resp.content.decode()
        assert c.get("/orgs/does-not-exist/settings/members").status_code == 404

    def test_member_can_view_org_home(self, login, member, org):
        resp = login(member).get(f"/orgs/{org.slug}/", follow=True)
        assert resp.status_code == 200
        assert org.name in resp.content.decode()

    def test_member_cannot_open_admin_pages(self, login, member, org):
        c = login(member)
        for page in ("members", "groups", "invites"):
            resp = c.get(f"/orgs/{org.slug}/settings/{page}")
            assert resp.status_code == 403, page

    def test_org_admin_can_open_admin_pages(self, login, org_admin, org):
        c = login(org_admin)
        for page in ("members", "groups", "invites"):
            assert c.get(f"/orgs/{org.slug}/settings/{page}").status_code == 200, page

    def test_group_granted_admin_can_open_admin_pages(
        self, login, member, org, make_group, attach_group
    ):
        attach_group(member, make_group("Admins", org_role=roles.ORG_ADMIN))
        c = login(member)
        assert c.get(f"/orgs/{org.slug}/settings/members").status_code == 200

    def test_inactive_org_locked_out(self, login, org_admin, org):
        org.is_active = False
        org.save()
        c = login(org_admin)
        resp = c.get(f"/orgs/{org.slug}/", follow=True)
        assert org.name not in resp.content.decode()  # hub hides it
        assert c.get(f"/orgs/{org.slug}/settings/members").status_code == 404


class TestStudioGuard:
    def test_member_without_grant_gets_404(self, login, member, org, studio):
        resp = login(member).get(f"/s/{org.slug}/{studio.slug}/settings/members")
        assert resp.status_code == 404

    def test_viewer_gets_403_on_admin_page(self, login, member, org, studio, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        resp = login(member).get(f"/s/{org.slug}/{studio.slug}/settings/members")
        assert resp.status_code == 403

    def test_studio_admin_ok(self, login, member, org, studio, grant_studio):
        grant_studio(member, studio, roles.ADMIN)
        resp = login(member).get(f"/s/{org.slug}/{studio.slug}/settings/members")
        assert resp.status_code == 200

    def test_org_admin_implicitly_studio_admin(self, login, org_admin, org, studio):
        resp = login(org_admin).get(f"/s/{org.slug}/{studio.slug}/settings/members")
        assert resp.status_code == 200

    def test_group_default_role_grants_access(
        self, login, member, org, studio, make_group, attach_group
    ):
        attach_group(member, make_group("Studio admins", default_studio_role=roles.ADMIN))
        resp = login(member).get(f"/s/{org.slug}/{studio.slug}/settings/members")
        assert resp.status_code == 200

    def test_cross_org_studio_404(self, login, member, other_org, other_studio):
        resp = login(member).get(f"/s/{other_org.slug}/{other_studio.slug}/settings/members")
        assert resp.status_code == 404


class TestSuperuser:
    def test_superuser_reaches_everything(self, login, superuser, org, studio):
        c = login(superuser)
        assert c.get(f"/orgs/{org.slug}/", follow=True).status_code == 200
        assert c.get(f"/orgs/{org.slug}/settings/members").status_code == 200
        assert c.get(f"/s/{org.slug}/{studio.slug}/settings/members").status_code == 200

    def test_non_superuser_cannot_create_org(self, login, org_admin):
        resp = login(org_admin).post("/orgs/create", {"name": "X", "slug": "x"})
        assert resp.status_code == 403

    def test_superuser_creates_org(self, login, superuser):
        resp = login(superuser).post("/orgs/create", {"name": "New Org", "slug": "new-org"})
        assert resp.status_code == 302
        from apps.orgs.models import Organization

        assert Organization.objects.filter(slug="new-org").exists()
