"""Table-driven tests for the effective-permission engine.

Sources of a studio role: direct StudioMembership, group per-studio grant,
group default_studio_role, org admin (direct or via group), superuser.
Effective role = MAX over all sources. No org membership = invisible org.
"""
import pytest
from django.core.exceptions import ValidationError

from apps.core import roles
from apps.core.permissions import effective_roles, visible_studios
from apps.core.report_access import bulk_can_view_report, can_view_report, visible_reports
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


class TestOrgLevel:
    def test_non_member_sees_nothing(self, make_user, org, studio):
        user = make_user("outsider@else.com")  # no org membership
        er = effective_roles(user, org)
        assert not er.is_member
        assert er.role_for(studio) is None
        assert list(visible_studios(user, org)) == []

    def test_direct_org_admin(self, org_admin, org, studio, studio2):
        er = effective_roles(org_admin, org)
        assert er.is_member and er.is_org_admin
        assert er.role_for(studio) == roles.ADMIN
        assert er.role_for(studio2) == roles.ADMIN
        assert set(visible_studios(org_admin, org)) == {studio, studio2}

    def test_plain_member_has_no_studio_access(self, member, org, studio):
        er = effective_roles(member, org)
        assert er.is_member and not er.is_org_admin
        assert er.role_for(studio) is None
        assert list(visible_studios(member, org)) == []

    def test_group_org_admin(self, member, org, studio, make_group, attach_group):
        attach_group(member, make_group("Demo Admin", org_role=roles.ORG_ADMIN))
        er = effective_roles(member, org)
        assert er.is_org_admin
        assert er.role_for(studio) == roles.ADMIN

    def test_superuser_is_admin_everywhere(self, superuser, org, other_org, studio, other_studio):
        assert effective_roles(superuser, org).role_for(studio) == roles.ADMIN
        assert effective_roles(superuser, other_org).role_for(other_studio) == roles.ADMIN

    def test_inactive_org_locks_everyone_out(self, org_admin, org, studio):
        org.is_active = False
        org.save()
        er = effective_roles(org_admin, org)
        assert not er.is_member
        assert er.role_for(studio) is None


class TestStudioSources:
    @pytest.mark.parametrize("role", [roles.VIEWER, roles.DEVELOPER, roles.ADMIN])
    def test_direct_membership(self, member, org, studio, studio2, grant_studio, role):
        grant_studio(member, studio, role)
        er = effective_roles(member, org)
        assert er.role_for(studio) == role
        assert er.role_for(studio2) is None
        assert set(visible_studios(member, org)) == {studio}

    def test_group_per_studio_grant(self, member, org, studio, studio2, make_group, attach_group):
        attach_group(member, make_group("Casino BAs", grants=[(studio, roles.DEVELOPER)]))
        er = effective_roles(member, org)
        assert er.role_for(studio) == roles.DEVELOPER
        assert er.role_for(studio2) is None

    def test_group_default_studio_role_covers_future_studios(
        self, member, org, studio, make_group, attach_group
    ):
        attach_group(member, make_group("Demo BA", default_studio_role=roles.VIEWER))
        er = effective_roles(member, org)
        assert er.role_for(studio) == roles.VIEWER
        # A studio created AFTER the group grant is still covered.
        from apps.studios.models import Studio

        new_studio = Studio.objects.create(org=org, slug="brand-new", name="New")
        assert effective_roles(member, org).role_for(new_studio) == roles.VIEWER
        assert new_studio in set(visible_studios(member, org))

    def test_max_wins_across_sources(
        self, member, org, studio, make_group, attach_group, grant_studio
    ):
        grant_studio(member, studio, roles.VIEWER)  # direct: viewer
        attach_group(member, make_group("Devs", grants=[(studio, roles.DEVELOPER)]))
        attach_group(member, make_group("Everyone", default_studio_role=roles.VIEWER))
        er = effective_roles(member, org)
        assert er.role_for(studio) == roles.DEVELOPER  # max of viewer/developer/viewer

    def test_direct_beats_weaker_group(self, member, org, studio, make_group, attach_group, grant_studio):
        grant_studio(member, studio, roles.ADMIN)
        attach_group(member, make_group("Viewers", grants=[(studio, roles.VIEWER)]))
        assert effective_roles(member, org).role_for(studio) == roles.ADMIN


class TestCrossOrgIsolation:
    def test_membership_does_not_leak_across_orgs(self, member, org, other_org, other_studio):
        er = effective_roles(member, other_org)
        assert not er.is_member
        assert er.role_for(other_studio) is None

    def test_group_grants_stay_inside_their_org(
        self, make_user, org, other_org, studio, make_group, attach_group
    ):
        # User is a member of BOTH orgs, but the admin group belongs to org A.
        user = make_user("both@worlds.com", org=org, org_role=roles.ORG_MEMBER)
        from apps.orgs.models import OrgMembership

        OrgMembership.objects.create(user=user, org=other_org, role=roles.ORG_MEMBER)
        attach_group(user, make_group("A-Admins", org_role=roles.ORG_ADMIN))
        assert effective_roles(user, org).is_org_admin is True
        assert effective_roles(user, other_org).is_org_admin is False

    def test_default_studio_role_does_not_cross_orgs(
        self, make_user, org, other_org, other_studio, make_group, attach_group
    ):
        user = make_user("both2@worlds.com", org=org)
        from apps.orgs.models import OrgMembership

        OrgMembership.objects.create(user=user, org=other_org, role=roles.ORG_MEMBER)
        attach_group(user, make_group("Demo BA", default_studio_role=roles.ADMIN))
        assert effective_roles(user, other_org).role_for(other_studio) is None


class TestReportVisibility:
    @pytest.fixture(autouse=True)
    def _enable_selected_access(self, settings):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True

    def _selected_grant(self, user, studio, make_group, attach_group, name="Selected"):
        group = make_group(name, grants=[(studio, roles.VIEWER)])
        grant = group.grants.get(studio=studio)
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(user, group)
        return grant

    def test_selected_groups_union_report_ids(
        self, member, org, studio_tree, report_row, make_group, attach_group
    ):
        other = Report.objects.create(studio=studio_tree, slug="other")
        first = self._selected_grant(member, studio_tree, make_group, attach_group)
        second = self._selected_grant(
            member, studio_tree, make_group, attach_group, name="Also selected"
        )
        ReportPermissionGrant.objects.create(grant=first, report=report_row)
        ReportPermissionGrant.objects.create(grant=second, report=other)

        resolved = effective_roles(member, org)
        assert resolved.role_for(studio_tree) == roles.VIEWER
        assert resolved.report_scope_for(studio_tree) == frozenset({report_row.pk, other.pk})
        assert not resolved.has_full_studio_visibility(studio_tree)
        assert can_view_report(member, report_row)
        assert set(visible_reports(member, Report.objects.all())) == {report_row, other}

    def test_empty_selected_scope_still_allows_studio_entry(
        self, member, org, studio_tree, make_group, attach_group
    ):
        self._selected_grant(member, studio_tree, make_group, attach_group)
        resolved = effective_roles(member, org)
        assert resolved.role_for(studio_tree) == roles.VIEWER
        assert resolved.report_scope_for(studio_tree) == frozenset()

    def test_empty_selected_parent_requires_operator_readiness(
        self, member, studio_tree, make_group, settings
    ):
        group = make_group("Pending")
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        with pytest.raises(ValidationError, match="TRELLUM_REPORT_SCOPED_ACCESS_READY"):
            PermissionGroupGrant.objects.create(
                group=group,
                studio=studio_tree,
                role=roles.VIEWER,
                viewer_scope=PermissionGroupGrant.REPORT_SCOPE_SELECTED,
            )

    def test_any_full_source_wins_over_selected(
        self, member, org, studio_tree, report_row, make_group, attach_group, grant_studio
    ):
        selected = self._selected_grant(member, studio_tree, make_group, attach_group)
        ReportPermissionGrant.objects.create(grant=selected, report=report_row)
        grant_studio(member, studio_tree, roles.VIEWER)

        resolved = effective_roles(member, org)
        assert resolved.report_scope_for(studio_tree) is None
        assert resolved.has_full_studio_visibility(studio_tree)

    def test_bulk_helper_matches_full_selected_and_ungranted_users(
        self,
        member,
        org,
        studio_tree,
        report_row,
        make_user,
        make_group,
        attach_group,
        grant_studio,
        settings,
    ):
        full = make_user("full@demo.example", org=org)
        hidden = make_user("hidden@demo.example", org=org)
        grant_studio(full, studio_tree, roles.VIEWER)
        selected = self._selected_grant(member, studio_tree, make_group, attach_group)
        self._selected_grant(hidden, studio_tree, make_group, attach_group, name="No reports")
        ReportPermissionGrant.objects.create(grant=selected, report=report_row)

        assert bulk_can_view_report([member, full, hidden], report_row) == {
            member.pk: True,
            full.pk: True,
            hidden.pk: False,
        }

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        assert effective_roles(member, org).report_scope_for(studio_tree) == frozenset(
            {report_row.pk}
        )
        assert not can_view_report(member, report_row)
        assert bulk_can_view_report([member, full], report_row) == {
            member.pk: False,
            full.pk: True,
        }
        assert list(visible_reports(member, Report.objects.filter(pk=report_row.pk))) == []
        assert list(visible_reports(full, Report.objects.filter(pk=report_row.pk))) == [report_row]

    def test_report_assignment_validates_parent_and_studio(
        self, member, studio_tree, studio2, report_row, make_group, attach_group
    ):
        all_reports = make_group("All", grants=[(studio_tree, roles.VIEWER)]).grants.get()
        with pytest.raises(ValidationError):
            ReportPermissionGrant.objects.create(grant=all_reports, report=report_row)

        selected = self._selected_grant(member, studio_tree, make_group, attach_group)
        foreign_report = Report.objects.create(studio=studio2, slug="foreign")
        with pytest.raises(ValidationError):
            ReportPermissionGrant.objects.create(grant=selected, report=foreign_report)

    def test_report_assignment_rejects_edge_external(
        self, member, studio_tree, report_row, make_group, attach_group, settings
    ):
        selected = self._selected_grant(member, studio_tree, make_group, attach_group)
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        with pytest.raises(ValidationError, match="edge-external"):
            ReportPermissionGrant.objects.create(grant=selected, report=report_row)

    def test_corrupt_cross_studio_bulk_row_never_grants_visibility(
        self, member, studio_tree, studio2, make_group, attach_group
    ):
        selected = self._selected_grant(member, studio_tree, make_group, attach_group)
        foreign_report = Report.objects.create(studio=studio2, slug="foreign")
        ReportPermissionGrant.objects.bulk_create(
            [ReportPermissionGrant(grant=selected, report=foreign_report)]
        )

        assert not can_view_report(member, foreign_report)
        assert list(visible_reports(member, Report.objects.filter(pk=foreign_report.pk))) == []
        assert bulk_can_view_report([member], foreign_report) == {member.pk: False}


class TestRoleHelpers:
    def test_rank_ordering(self):
        assert roles.rank(roles.VIEWER) < roles.rank(roles.DEVELOPER) < roles.rank(roles.ADMIN)
        assert roles.rank(None) == 0

    def test_max_role(self):
        assert roles.max_role(None, roles.VIEWER) == roles.VIEWER
        assert roles.max_role(roles.VIEWER, roles.ADMIN, roles.DEVELOPER) == roles.ADMIN
        assert roles.max_role(None, "", None) is None

    def test_at_least(self):
        assert roles.at_least(roles.ADMIN, roles.VIEWER)
        assert not roles.at_least(roles.VIEWER, roles.DEVELOPER)
        assert not roles.at_least(None, roles.VIEWER)


class TestBulkMatchesSingle:
    def test_bulk_role_for_studio_equals_effective_roles_for_every_source(
        self, make_user, org, studio, studio2, superuser, org_admin, member,
        make_group, attach_group, grant_studio,
    ):
        from apps.core.permissions import bulk_role_for_studio

        direct = make_user("direct@demo.example", org=org)
        grant_studio(direct, studio, roles.DEVELOPER)
        grouped = make_user("grouped@demo.example", org=org)
        attach_group(grouped, make_group("g", grants=[(studio, roles.VIEWER)]))
        defaulted = make_user("default@demo.example", org=org)
        attach_group(defaulted, make_group("d", default_studio_role=roles.ADMIN))
        stranger = make_user("stranger@else.example")
        users = [superuser, org_admin, member, direct, grouped, defaulted, stranger]

        for target in (studio, studio2):
            assert bulk_role_for_studio(users, target) == {
                u.pk: effective_roles(u, org).role_for(target) for u in users
            }
