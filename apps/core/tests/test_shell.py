"""Global shell: switcher visibility, active nav, and its presence on both
halves of the product."""
import pytest

from apps.core import roles
from apps.core.context_processors import shell
from trellum import __version__

pytestmark = pytest.mark.django_db


class TestContextProcessor:
    def _ctx(self, client, user, path="/"):
        # Run the processor against a real request object.
        from django.test import RequestFactory

        request = RequestFactory().get(path)
        request.user = user
        return shell(request)

    def test_member_sees_only_their_orgs(self, client, member, org, other_org):
        ctx = self._ctx(client, member)
        assert [o.slug for o in ctx["shell_orgs"]] == [org.slug]

    def test_superuser_sees_all_orgs(self, client, superuser, org, other_org):
        ctx = self._ctx(client, superuser)
        assert {o.slug for o in ctx["shell_orgs"]} == {org.slug, other_org.slug}

    def test_single_org_becomes_active_with_visible_studios(
        self, client, member, org, studio, grant_studio
    ):
        grant_studio(member, studio, roles.VIEWER)
        ctx = self._ctx(client, member)
        assert ctx["shell_org"] == org
        assert [s.slug for s in ctx["shell_studios"]] == [studio.slug]

    def test_member_without_grants_sees_no_studios(self, client, member, org, studio):
        ctx = self._ctx(client, member)
        assert ctx["shell_studios"] == []

    def test_anonymous_gets_public_source_url(self, client, db):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert shell(request) == {
            "trellum_source_url": (
                f"https://github.com/trellumhq/trellum/tree/v{__version__}"
            )
        }

    def test_named_console_route_supplies_mobile_page_title(self, member, org):
        from django.test import RequestFactory
        from django.urls import resolve

        request = RequestFactory().get(f"/orgs/{org.slug}/settings/members")
        request.user = member
        request.resolver_match = resolve(request.path)
        assert shell(request)["console_page_title"] == "Members"


class TestShellRendering:
    def test_security_navigation_has_no_tier_markers(self):
        from django.conf import settings

        source = (settings.BASE_DIR / "templates" / "_org_nav.html").read_text()
        security = source.split('data-console-nav-group="org-security"', 1)[1]
        assert "capability_chip" not in security
        assert "tl-console-edition" not in security

    def test_management_page_has_shell_with_active_nav(self, login, org_admin, org):
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        assert 'id="globalShell"' in html
        assert 'tl-console-nav-link active' in html  # Members nav link is active
        assert '>Source code</a>' in html
        assert f"https://github.com/trellumhq/trellum/tree/v{__version__}" in html

    def test_studio_page_keeps_organization_navigation(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        nav = html.split('class="tl-console-nav"', 1)[1].split("</nav>", 1)[0]
        assert 'data-console-nav-group="studio-work"' in nav
        assert 'data-console-nav-group="org-workspace"' in nav
        assert 'data-console-nav-group="org-security"' in nav
        groups = [
            'data-console-nav-group="studio-work"',
            'data-console-nav-group="studio-settings"',
            'data-console-nav-group="org-workspace"',
            'data-console-nav-group="org-people"',
            'data-console-nav-group="org-data"',
            'data-console-nav-group="org-security"',
        ]
        assert [nav.index(group) for group in groups] == sorted(nav.index(group) for group in groups)

    def test_report_analytics_is_last_studio_work_link(self, login, org_admin, org, studio_tree, report_row):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        nav = html.split('data-console-nav-group="studio-work"', 1)[1].split(
            'data-console-nav-group="studio-settings"', 1
        )[0]
        assert ">Report Analytics<" in nav
        assert ">Annotations<" in nav
        assert nav.index(">Annotations<") < nav.index(">Report Analytics<")

    def test_dashboard_has_shell_and_studio_switcher(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert 'id="globalShell"' in html
        assert f"/s/{org.slug}/{studio_tree.slug}/" in html  # switcher entry
        # The deploy-hash footer is gone. Both spellings: the element was
        # removed under its old name, and must not return under the new one.
        for dead in ("bi-portal-version", "bi-portal-footer",
                     "trellum-portal-version", "trellum-portal-footer"):
            assert dead not in html

    def test_studio_switcher_lists_only_visible_studios(
        self, login, make_user, org, studio_tree, studio2, grant_studio
    ):
        user = make_user("limited@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        html = login(user).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert f"/s/{org.slug}/{studio_tree.slug}/" in html
        assert f"/s/{org.slug}/{studio2.slug}/" not in html

    def test_org_page_retains_studio_navigation_without_becoming_studio_scoped(
        self, login, org_admin, org, studio_tree, report_row
    ):
        studio_tree.theme = "dracula"
        studio_tree.save(update_fields=["theme"])
        client = login(org_admin)
        studio_html = client.get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "/api/assistant/widget.js" in studio_html

        html = client.get(f"/orgs/{org.slug}/settings/members").content.decode()
        nav = html.split('class="tl-console-nav"', 1)[1].split("</nav>", 1)[0]
        breadcrumbs = html.split('class="tl-console-breadcrumbs"', 1)[1].split(
            "</nav>", 1
        )[0]

        assert 'data-console-nav-group="studio-work"' in nav
        assert f"/s/{org.slug}/{studio_tree.slug}/settings/repo" in nav
        assert f'href="/s/{org.slug}/{studio_tree.slug}/" class="active"' in html
        assert f'action="/s/{org.slug}/{studio_tree.slug}/"' in html
        assert studio_tree.name not in breadcrumbs
        assert "scope-org" in html
        assert "data-studio-theme" not in html
        assert "/api/assistant/widget.js" not in html

    def test_explicit_org_switch_does_not_carry_another_orgs_studio(
        self,
        login,
        org_admin,
        org,
        other_org,
        other_studio,
        studio_tree,
        report_row,
        grant_studio,
    ):
        from apps.orgs.models import OrgMembership

        OrgMembership.objects.create(
            user=org_admin, org=other_org, role=roles.ORG_MEMBER
        )
        grant_studio(org_admin, other_studio, roles.VIEWER)
        client = login(org_admin)
        client.get(f"/s/{org.slug}/{studio_tree.slug}/")

        html = client.get("/", {"org": other_org.slug}).content.decode()
        nav = html.split('class="tl-console-nav"', 1)[1].split("</nav>", 1)[0]
        assert "Choose a studio" in html
        assert f"/s/{other_org.slug}/{other_studio.slug}/" in html
        assert f"/s/{org.slug}/{studio_tree.slug}/" not in html
        assert 'data-console-nav-group="studio-work"' not in nav

    def test_lost_studio_permission_clears_the_remembered_choice(
        self, login, member, org, studio_tree, report_row, grant_studio
    ):
        membership = grant_studio(member, studio_tree, roles.VIEWER)
        client = login(member)
        client.get(f"/s/{org.slug}/{studio_tree.slug}/")

        membership.delete()
        html = client.get("/").content.decode()
        assert 'data-console-nav-group="studio-work"' not in html
        assert org.slug not in client.session.get("active_studio_by_org", {})

    def test_deleted_studio_clears_the_remembered_choice(
        self, login, org_admin, org, studio_tree, report_row
    ):
        client = login(org_admin)
        client.get(f"/s/{org.slug}/{studio_tree.slug}/")

        studio_tree.delete()
        html = client.get("/").content.decode()
        assert 'data-console-nav-group="studio-work"' not in html
        assert org.slug not in client.session.get("active_studio_by_org", {})

    def test_inactive_organization_clears_its_remembered_choice(
        self,
        login,
        org_admin,
        org,
        other_org,
        studio_tree,
        report_row,
    ):
        from apps.orgs.models import OrgMembership

        OrgMembership.objects.create(
            user=org_admin, org=other_org, role=roles.ORG_MEMBER
        )
        client = login(org_admin)
        client.get(f"/s/{org.slug}/{studio_tree.slug}/")

        org.is_active = False
        org.save(update_fields=["is_active"])
        client.get("/", {"org": other_org.slug})
        assert org.slug not in client.session.get("active_studio_by_org", {})

    def test_retained_viewer_context_does_not_elevate_page_permissions(
        self, login, member, org, studio_tree, report_row, grant_studio
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        client = login(member)
        client.get(f"/s/{org.slug}/{studio_tree.slug}/")

        html = client.get("/", {"org": org.slug}).content.decode()
        nav = html.split('class="tl-console-nav"', 1)[1].split("</nav>", 1)[0]
        assert 'data-console-nav-group="studio-work"' in nav
        assert 'data-console-nav-group="studio-settings"' not in nav
        assert "/analytics" not in nav
        assert client.get(f"/orgs/{org.slug}/settings/members").status_code == 403

    def test_login_page_has_no_shell(self, client, member):
        html = client.get("/login").content.decode()
        assert 'id="globalShell"' not in html
        assert f"https://github.com/trellumhq/trellum/tree/v{__version__}" in html

    def test_studio_dropdown_is_purely_a_switcher(
        self, login, org_admin, org, studio_tree, report_row
    ):
        # The "This studio" page links (Experiments/Annotations/⚙ Settings)
        # moved into the studio tab bar; the dropdown only switches studios.
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        shell = html.split('<header class="tl-console-header"', 1)[1].split("</header>", 1)[0]
        assert "This studio" not in shell
        assert f"/s/{org.slug}/{studio_tree.slug}/experiments" not in shell
        assert f"/s/{org.slug}/{studio_tree.slug}/annotations" not in shell
        assert "/settings/repo" not in shell

    def test_shell_has_no_chat_button(
        self, login, org_admin, org, studio_tree, report_row
    ):
        # The chat view mode is deleted (apps/chat was removed); the
        # shell must not resurrect its button under either id or label.
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert 'id="viewChat"' not in html
        assert "AI report builder" not in html
