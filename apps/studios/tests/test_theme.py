"""Per-studio theme setters (apps.studios.views.theme_set / theme_set_
default) -- see apps/core/tests/test_theme_resolution.py for the resolver
these feed, apps/core/tests/test_mgmt_theme.py for the tab-rail page-level
behavior (picker placement, lock note), and
apps/studios/tests/test_appearance_settings.py for the Studio settings ->
Appearance page theme_set_default now also serves."""
import pytest

from apps.core import roles
from apps.core.models import AuditLog
from apps.studios.models import StudioMembership, StudioPreference

pytestmark = pytest.mark.django_db


@pytest.fixture
def prefix(org, studio):
    return f"/s/{org.slug}/{studio.slug}"


class TestViewerThemeSet:
    def test_saves_the_override_for_an_existing_member(
        self, login, member, studio, prefix, grant_studio
    ):
        grant_studio(member, studio, roles.VIEWER)
        resp = login(member).post(f"{prefix}/theme", {"theme": "ocean", "next": prefix + "/"})
        assert resp.status_code == 302
        assert resp["Location"] == prefix + "/"
        assert StudioPreference.objects.get(user=member, studio=studio).theme == "ocean"
        assert StudioMembership.objects.get(user=member, studio=studio).role == roles.VIEWER

    def test_empty_resets_to_the_studio_default(
        self, login, member, studio, prefix, grant_studio
    ):
        grant_studio(member, studio, roles.VIEWER)
        StudioPreference.objects.create(user=member, studio=studio, theme="ocean")
        login(member).post(f"{prefix}/theme", {"theme": ""})
        assert not StudioPreference.objects.filter(user=member, studio=studio).exists()

    def test_rejects_an_unregistered_theme_name(
        self, login, member, studio, prefix, grant_studio
    ):
        grant_studio(member, studio, roles.VIEWER)
        login(member).post(f"{prefix}/theme", {"theme": "not-a-real-theme"})
        assert not StudioPreference.objects.filter(user=member, studio=studio).exists()

    def test_org_admin_theme_does_not_create_a_membership(
        self, login, org_admin, studio, prefix
    ):
        # org_admin has studio access via is_org_admin, with no
        # StudioMembership row at all -- persisting a personal preference
        # must not silently grant more than they already had.
        assert not StudioMembership.objects.filter(user=org_admin, studio=studio).exists()
        login(org_admin).post(f"{prefix}/theme", {"theme": "nord"})
        assert not StudioMembership.objects.filter(user=org_admin, studio=studio).exists()
        assert StudioPreference.objects.get(user=org_admin, studio=studio).theme == "nord"

    def test_org_admin_demotion_removes_access_after_theme_change(
        self, login, org_admin, org, studio, prefix
    ):
        from apps.orgs.models import OrgMembership

        login(org_admin).post(f"{prefix}/theme", {"theme": "nord"})
        membership = OrgMembership.objects.get(user=org_admin, org=org)
        membership.role = roles.ORG_MEMBER
        membership.save(update_fields=["role"])

        assert not StudioMembership.objects.filter(user=org_admin, studio=studio).exists()
        assert login(org_admin).post(f"{prefix}/theme", {"theme": "money"}).status_code == 404
        assert StudioPreference.objects.get(user=org_admin, studio=studio).theme == "nord"

    def test_selected_report_group_scope_survives_theme_and_revocation(
        self, login, member, org, studio_tree, report_row, prefix, settings
    ):
        from apps.core.permissions import effective_roles
        from apps.orgs.models import PermissionGroup, PermissionGroupGrant, PermissionGroupMembership
        from apps.reports.models import ReportPermissionGrant

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        group = PermissionGroup.objects.create(org=org, name="Selected report viewers")
        grant = PermissionGroupGrant.objects.create(
            group=group,
            studio=studio_tree,
            role=roles.VIEWER,
            viewer_scope=PermissionGroupGrant.REPORT_SCOPE_SELECTED,
        )
        PermissionGroupMembership.objects.create(user=member, group=group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)

        assert effective_roles(member, org).report_scope_for(studio_tree) == frozenset({report_row.pk})
        login(member).post(f"{prefix}/theme", {"theme": "nord"})
        assert not StudioMembership.objects.filter(user=member, studio=studio_tree).exists()
        assert effective_roles(member, org).report_scope_for(studio_tree) == frozenset({report_row.pk})

        PermissionGroupMembership.objects.filter(user=member, group=group).delete()
        assert effective_roles(member, org).report_scope_for(studio_tree) == frozenset()

    def test_rejects_when_the_org_has_locked_theme_overrides(
        self, login, member, org, studio, prefix, grant_studio
    ):
        grant_studio(member, studio, roles.VIEWER)
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        login(member).post(f"{prefix}/theme", {"theme": "nord"})
        assert not StudioPreference.objects.filter(user=member, studio=studio).exists()

    def test_get_is_not_allowed(self, login, member, studio, prefix, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        assert login(member).get(f"{prefix}/theme").status_code == 405

    def test_anonymous_is_redirected_to_login(self, client, studio, prefix):
        resp = client.post(f"{prefix}/theme", {"theme": "nord"})
        assert resp.status_code == 302
        assert "/login" in resp["Location"]

    def test_member_without_any_grant_gets_404(self, login, member, studio, prefix):
        # Same "hide its existence" rule as every other studio route.
        resp = login(member).post(f"{prefix}/theme", {"theme": "nord"})
        assert resp.status_code == 404

    def test_refuses_protocol_relative_next(self, login, member, studio, prefix, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        resp = login(member).post(
            f"{prefix}/theme", {"theme": "nord", "next": "//evil.example/"}
        )
        assert resp["Location"] == prefix + "/"

    def test_not_audited(self, login, member, studio, prefix, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        c = login(member)  # login itself is audited; count AFTER it lands
        before = AuditLog.objects.count()
        c.post(f"{prefix}/theme", {"theme": "nord"})
        assert AuditLog.objects.count() == before


class TestStudioDefaultThemeSet:
    def test_studio_admin_can_set_it(self, login, org_admin, studio, prefix):
        resp = login(org_admin).post(f"{prefix}/theme/default", {"theme": "money"})
        assert resp.status_code == 302
        studio.refresh_from_db()
        assert studio.theme == "money"

    def test_empty_resets_to_the_org_default(self, login, org_admin, studio, prefix):
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        login(org_admin).post(f"{prefix}/theme/default", {"theme": ""})
        studio.refresh_from_db()
        assert studio.theme == ""

    def test_rejects_an_unregistered_theme_name(self, login, org_admin, studio, prefix):
        login(org_admin).post(f"{prefix}/theme/default", {"theme": "not-a-real-theme"})
        studio.refresh_from_db()
        assert studio.theme == ""

    def test_non_admin_member_is_forbidden(self, login, member, studio, prefix, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        resp = login(member).post(f"{prefix}/theme/default", {"theme": "money"})
        assert resp.status_code == 403
        studio.refresh_from_db()
        assert studio.theme == ""

    def test_is_audited(self, login, org_admin, studio, prefix):
        login(org_admin).post(f"{prefix}/theme/default", {"theme": "money"})
        assert AuditLog.objects.filter(action="studio.theme_set").exists()

    def test_anonymous_is_redirected_to_login(self, client, studio, prefix):
        resp = client.post(f"{prefix}/theme/default", {"theme": "money"})
        assert resp.status_code == 302
        assert "/login" in resp["Location"]

    def test_get_is_not_allowed(self, login, org_admin, studio, prefix):
        assert login(org_admin).get(f"{prefix}/theme/default").status_code == 405
