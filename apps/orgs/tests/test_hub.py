"""The hub (/) and the scope-labeled settings layout."""
import re

import pytest
from django.utils import timezone

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


class TestHub:
    def test_hub_shows_active_org_studios_and_settings_button(
        self, login, org_admin, org, studio
    ):
        html = login(org_admin).get("/").content.decode()
        assert org.name in html
        assert f"/s/{org.slug}/{studio.slug}/" in html
        # The overview IS the org surface: the shared sidebar is present.
        assert f"/orgs/{org.slug}/settings/members" in html
        assert f"/orgs/{org.slug}/settings/studios" in html  # + New studio

    def test_member_sees_no_admin_buttons(self, login, member, org, studio, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        html = login(member).get("/").content.decode()
        assert f"/s/{org.slug}/{studio.slug}/" in html
        assert f"/orgs/{org.slug}/settings/members" not in html

    def test_selected_viewer_home_counts_and_last_run_exclude_hidden_reports(
        self, login, member, studio_tree, report_row, make_group, attach_group, settings
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        hidden = Report.objects.create(studio=studio_tree, slug="hidden")
        group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        Run.objects.create(
            report=report_row,
            studio=studio_tree,
            slug=report_row.slug,
            status=Run.SUCCESS,
            created_at=timezone.now() - timezone.timedelta(hours=1),
        )
        Run.objects.create(
            report=hidden,
            studio=studio_tree,
            slug=hidden.slug,
            status=Run.ERROR,
            created_at=timezone.now(),
        )

        response = login(member).get("/")
        card = response.context["cards"][0]
        assert card["report_count"] == 1
        assert card["last_run"].report_id == report_row.pk

    def test_org_query_switches_and_sticks(self, login, make_user, org, other_org):
        from apps.orgs.models import OrgMembership

        user = make_user("both@worlds.com", org=org)
        OrgMembership.objects.create(user=user, org=other_org, role=roles.ORG_MEMBER)
        c = login(user)
        assert other_org.name in c.get(f"/?org={other_org.slug}").content.decode()
        # Session remembers the choice on the next plain visit.
        assert other_org.name in c.get("/").content.decode()

    def test_admin_home_carries_the_full_org_sidebar(self, login, org_admin, org):
        # No standalone Settings menu in the bar anymore: the org surface's
        # sidebar carries every settings category, right on the overview.
        html = login(org_admin).get("/").content.decode()
        assert "⚙ Settings" not in html
        assert f"/orgs/{org.slug}/settings/sso" in html
        assert f"/orgs/{org.slug}/settings/audit" in html

    def test_member_home_sidebar_has_studios_only(self, login, member, org):
        html = login(member).get("/").content.decode()
        assert "⚙ Settings" not in html
        assert f"/orgs/{org.slug}/settings/sso" not in html

    def test_empty_org_shows_cta_for_admin(self, login, org_admin, org):
        """An admin looking at an empty org is told to make a studio.

        On a brand-new org the getting-started checklist carries that call to
        action (and the empty state stands down so there is only one); once the
        checklist is dismissed the empty state takes it back. Either way the
        admin is never left staring at 'no studios' with nothing to click —
        which is what this test exists to guarantee.
        """
        c = login(org_admin)
        assert "Create your first studio" in c.get("/").content.decode()

        org.onboarding_dismissed_at = timezone.now()
        org.save(update_fields=["onboarding_dismissed_at"])
        assert "Create the first studio" in c.get("/").content.decode()


class TestSettingsScope:
    def test_org_settings_carry_org_scope(self, login, org_admin, org):
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        assert "scope-org" in html  # teal layer wrapper
        assert "scope-banner org" in html  # blast-radius banner
        assert "every studio" in html
        assert 'data-console-nav-group="org-data"' in html
        assert 'data-console-nav-group="org-security"' in html
        assert f'href="/orgs/{org.slug}/settings/sso"' in html
        assert re.search(
            rf'<a\b[^>]*class="[^"]*\bactive\b[^"]*"[^>]*'
            rf'href="/orgs/{re.escape(org.slug)}/settings/members"[^>]*'
            r'aria-current="page"',
            html,
        )

    def test_studio_settings_carry_studio_scope(self, login, org_admin, org, studio_tree):
        html = login(org_admin).get(
            f"/s/{org.slug}/{studio_tree.slug}/settings/repo"
        ).content.decode()
        assert "scope-studio" in html  # violet layer wrapper
        assert "scope-banner studio" in html
        assert "apply only to" in html
        assert 'data-console-nav-group="studio-settings"' in html
        assert f'href="/s/{org.slug}/{studio_tree.slug}/settings/datasources"' in html
        assert 'data-console-nav-group="org-workspace"' in html
        assert f'href="/?org={org.slug}"' in html


class TestStudiosSection:
    def test_rename_display_name(self, login, org_admin, org, studio):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/studios",
            {"action": "rename", "studio_id": studio.pk, "name": "Casino v2",
             "description": "renamed"},
        )
        studio.refresh_from_db()
        assert studio.name == "Casino v2"
        assert studio.slug == "casino"  # slug untouched

    def test_create_and_delete_from_settings(self, login, org_admin, org, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        c = login(org_admin)
        resp = c.post(
            f"/orgs/{org.slug}/studios/new", {"slug": "fresh", "name": "Fresh"}
        )
        assert resp.url.endswith("/settings/studios")
        from apps.studios.models import Studio

        assert Studio.objects.filter(org=org, slug="fresh").exists()
        c.post(f"/orgs/{org.slug}/studios/fresh/delete", {"confirm_slug": "fresh"})
        assert not Studio.objects.filter(org=org, slug="fresh").exists()

    def test_member_cannot_open_studios_settings(self, login, member, org):
        assert login(member).get(f"/orgs/{org.slug}/settings/studios").status_code == 403
