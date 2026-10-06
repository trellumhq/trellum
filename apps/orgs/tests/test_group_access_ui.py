import re

import pytest

from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant, ShareLink

pytestmark = pytest.mark.django_db


def _group_url(org, group, tab=""):
    suffix = f"?tab={tab}" if tab else ""
    return f"/orgs/{org.slug}/settings/groups/{group.pk}{suffix}"


@pytest.fixture
def scoped_access_ready(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True


class TestGroupScreens:
    def test_listing_uses_a_dedicated_new_form_and_searches(
        self, login, org_admin, org, make_group
    ):
        make_group("Finance")
        make_group("Operations")
        html = (
            login(org_admin)
            .get(f"/orgs/{org.slug}/settings/groups?q=fin")
            .content.decode()
        )
        assert "Finance" in html
        assert "Operations" not in html
        assert f"/orgs/{org.slug}/settings/groups/new" in html
        assert 'id="new-group"' not in html

    def test_new_form_creates_a_group(self, login, org_admin, org):
        response = login(org_admin).post(
            f"/orgs/{org.slug}/settings/groups/new",
            {
                "name": "Analysts",
                "description": "",
                "org_role": "",
                "default_studio_role": "",
            },
        )
        assert response.status_code == 302
        assert org.permission_groups.filter(name="Analysts").exists()

    def test_detail_has_four_sections_and_mobile_selector(
        self, login, org_admin, org, make_group
    ):
        group = make_group("Analysts")
        response = login(org_admin).get(_group_url(org, group))
        html = response.content.decode()
        for label in ("Overview", "Members", "Access", "Effective access"):
            assert label in html
        assert 'class="group-tab-select"' in html
        assert response.context["console_page_title"] == group.name

    def test_access_forms_use_unique_ids_for_each_studio(
        self, login, org_admin, org, studio, studio2, make_group
    ):
        Report.objects.create(studio=studio, slug="first", name="First")
        Report.objects.create(studio=studio2, slug="second", name="Second")
        group = make_group("Analysts")

        html = login(org_admin).get(_group_url(org, group, "access")).content.decode()

        expected_ids = {
            f"id_studio_{studio.pk}_role",
            f"id_studio_{studio.pk}_reports_0",
            f"id_studio_{studio2.pk}_role",
            f"id_studio_{studio2.pk}_reports_0",
        }
        rendered_ids = re.findall(r'id="(id_studio_[^"]+)"', html)
        assert len(rendered_ids) == len(set(rendered_ids))
        assert expected_ids <= set(rendered_ids)
        for control_id in expected_ids:
            assert html.count(f'id="{control_id}"') == 1
        assert f'for="id_studio_{studio.pk}_reports_0"' in html
        assert f'for="id_studio_{studio2.pk}_reports_0"' in html

    def test_members_are_searchable_and_add_choices_exclude_existing(
        self, login, org_admin, org, member, make_user, make_group, attach_group
    ):
        group = make_group("Analysts")
        attach_group(member, group)
        addable = make_user("addable@example.com", org=org)
        html = (
            login(org_admin)
            .get(_group_url(org, group, "members") + "&q=member")
            .content.decode()
        )
        assert member.email in html
        assert f'<option value="{member.pk}">' not in html
        assert f'<option value="{addable.pk}">' in html

    def test_selected_reports_save_and_switching_to_all_clears_rows(
        self, login, org_admin, org, studio, report_row, make_group, scoped_access_ready
    ):
        second = Report.objects.create(studio=studio, slug="second", name="Second")
        group = make_group("Analysts")
        client = login(org_admin)
        url = _group_url(org, group, "access")
        response = client.post(
            url,
            {
                "action": "studio_access",
                "studio": studio.pk,
                "role": roles.VIEWER,
                "viewer_scope": "selected",
                "reports": [report_row.pk, second.pk],
            },
        )
        assert response.status_code == 302
        grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
        assert grant.viewer_scope == "selected"
        assert set(grant.report_grants.values_list("report_id", flat=True)) == {
            report_row.pk,
            second.pk,
        }

        client.post(
            url,
            {
                "action": "studio_access",
                "studio": studio.pk,
                "role": roles.VIEWER,
                "viewer_scope": "all",
                "reports": [report_row.pk],
            },
        )
        grant.refresh_from_db()
        assert grant.viewer_scope == "all"
        assert not grant.report_grants.exists()

    def test_non_viewer_accepts_the_disabled_scope_control(
        self, login, org_admin, org, studio, make_group
    ):
        group = make_group("Developers")
        response = login(org_admin).post(
            _group_url(org, group, "access"),
            {
                "action": "studio_access",
                "studio": studio.pk,
                "role": roles.DEVELOPER,
            },
        )

        assert response.status_code == 302
        grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
        assert (grant.role, grant.viewer_scope) == (roles.DEVELOPER, "all")

    def test_foreign_report_rejects_the_whole_access_change(
        self,
        login,
        org_admin,
        org,
        studio,
        other_studio,
        report_row,
        make_group,
        scoped_access_ready,
    ):
        foreign = Report.objects.create(
            studio=other_studio, slug="foreign", name="Foreign"
        )
        group = make_group("Analysts")
        response = login(org_admin).post(
            _group_url(org, group, "access"),
            {
                "action": "studio_access",
                "studio": studio.pk,
                "role": roles.VIEWER,
                "viewer_scope": "selected",
                "reports": [report_row.pk, foreign.pk],
            },
        )
        assert response.status_code == 200
        assert b"Select a valid choice" in response.content
        assert not PermissionGroupGrant.objects.filter(group=group).exists()

    def test_readiness_blocks_selected_but_keeps_all_report_grants_editable(
        self, login, org_admin, org, studio, report_row, make_group
    ):
        group = make_group("Analysts")
        client = login(org_admin)
        url = _group_url(org, group, "access")
        response = client.post(
            url,
            {
                "action": "studio_access",
                "studio": studio.pk,
                "role": roles.VIEWER,
                "viewer_scope": "selected",
                "reports": [report_row.pk],
            },
        )
        assert response.status_code == 200
        assert b"TRELLUM_REPORT_SCOPED_ACCESS_READY is not enabled" in response.content
        assert not PermissionGroupGrant.objects.filter(group=group).exists()

        response = client.post(
            url,
            {
                "action": "studio_access",
                "studio": studio.pk,
                "role": roles.VIEWER,
                "viewer_scope": "all",
            },
        )
        assert response.status_code == 302
        assert (
            PermissionGroupGrant.objects.get(group=group, studio=studio).viewer_scope
            == "all"
        )

    def test_effective_preview_names_every_contributing_source(
        self,
        login,
        org_admin,
        org,
        studio,
        member,
        report_row,
        make_group,
        attach_group,
        scoped_access_ready,
    ):
        selected = make_group("Selected analysts")
        selected_grant = PermissionGroupGrant.objects.create(
            group=selected, studio=studio, role=roles.VIEWER, viewer_scope="selected"
        )
        ReportPermissionGrant.objects.create(grant=selected_grant, report=report_row)
        broad = make_group("Developers", default_studio_role=roles.DEVELOPER)
        attach_group(member, selected)
        attach_group(member, broad)
        html = (
            login(org_admin)
            .get(_group_url(org, selected, "effective") + f"&member={member.pk}")
            .content.decode()
        )
        assert "Selected analysts: Viewer for 1 selected report" in html
        assert "Developers: default Developer on every studio" in html
        assert "all reports" in html


class TestUnrestrictedManagement:
    @pytest.mark.parametrize("path", ["groups", "sso", "security"])
    def test_management_pages_remain_editable_with_retired_flags(
        self, login, org_admin, org, path, settings
    ):
        settings.TRELLUM_LICENCE = "expired.or.malformed"
        settings.TRELLUM_FEATURES = {
            "permission_groups": False,
            "sso": False,
            "org_security_policy": False,
        }
        html = (
            login(org_admin).get(f"/orgs/{org.slug}/settings/{path}").content.decode()
        )
        assert "Read-only" not in html
        assert "requires a licence" not in html

    def test_existing_group_detail_remains_writable_with_retired_flags(
        self, login, org_admin, org, make_group, settings
    ):
        settings.TRELLUM_LICENCE = "expired.or.malformed"
        settings.TRELLUM_FEATURES = {"permission_groups": False}
        group = make_group("Existing")
        client = login(org_admin)
        response = client.get(_group_url(org, group))
        assert response.status_code == 200
        assert (
            client.post(
                _group_url(org, group),
                {
                    "action": "edit",
                    "name": "Changed",
                    "description": "",
                    "org_role": "",
                    "default_studio_role": "",
                },
            ).status_code
            == 302
        )
        group.refresh_from_db()
        assert group.name == "Changed"


class TestReportAccess:
    def test_json_probe_is_admin_only(
        self, login, org_admin, member, org, studio, report_row, grant_studio
    ):
        url = f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access"
        response = login(org_admin).get(url, HTTP_ACCEPT="application/json")
        assert response.json() == {"can_manage": True, "url": url}
        grant_studio(member, studio, roles.VIEWER)
        assert login(member).get(url, HTTP_ACCEPT="application/json").status_code == 403

    def test_readiness_explains_and_blocks_report_assignment(
        self, login, org_admin, org, studio, report_row, make_group
    ):
        group = make_group("Analysts")
        url = f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access"
        client = login(org_admin)
        html = client.get(url).content.decode()
        assert "Selected-report access is read-only" in html
        assert "TRELLUM_REPORT_SCOPED_ACCESS_READY is not enabled" in html
        assert (
            client.post(
                url, {"action": "grant_report", "group_id": group.pk}
            ).status_code
            == 302
        )
        assert not PermissionGroupGrant.objects.filter(group=group).exists()

    def test_studio_admin_adds_and_removes_only_the_selected_report(
        self,
        login,
        member,
        org,
        studio,
        report_row,
        make_group,
        grant_studio,
        scoped_access_ready,
    ):
        grant_studio(member, studio, roles.ADMIN)
        group = make_group("Analysts")
        url = f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access"
        client = login(member)
        assert (
            client.post(
                url, {"action": "grant_report", "group_id": group.pk}
            ).status_code
            == 302
        )
        grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
        assert (grant.role, grant.viewer_scope) == (roles.VIEWER, "selected")
        assert grant.report_grants.filter(report=report_row).exists()
        assert AuditLog.objects.filter(
            action="group.report_grant", target_id=str(report_row.pk)
        ).exists()

        client.post(url, {"action": "ungrant_report", "group_id": group.pk})
        grant.refresh_from_db()
        assert grant.viewer_scope == "selected"
        assert not grant.report_grants.exists()

    def test_broader_group_is_read_only_and_public_tokens_are_not_rendered(
        self, login, member, org, studio, report_row, make_group, grant_studio
    ):
        grant_studio(member, studio, roles.ADMIN)
        group = make_group("Developers", grants=[(studio, roles.DEVELOPER)])
        link = ShareLink.objects.create(report=report_row, created_by=member)
        url = f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access"
        client = login(member)
        html = client.get(url).content.decode()
        assert "Inherited" in html
        assert "Public links are separate" in html
        assert link.token not in html
        client.post(url, {"action": "ungrant_report", "group_id": group.pk})
        grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
        assert (grant.role, grant.viewer_scope) == (roles.DEVELOPER, "all")

    def test_group_from_another_org_is_rejected(
        self,
        login,
        member,
        other_org,
        org,
        studio,
        report_row,
        make_group,
        grant_studio,
    ):
        grant_studio(member, studio, roles.ADMIN)
        foreign = make_group("Foreign", org_=other_org)
        response = login(member).post(
            f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access",
            {"action": "grant_report", "group_id": foreign.pk},
        )
        assert response.status_code == 404
        assert not PermissionGroupGrant.objects.filter(group=foreign).exists()


def test_retired_flags_do_not_block_report_access_writes(
    login, org_admin, org, studio, report_row, make_group, scoped_access_ready, settings
):
    settings.TRELLUM_LICENCE = "expired.or.malformed"
    settings.TRELLUM_FEATURES = {"permission_groups": False}
    group = make_group("Analysts")
    url = f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access"
    client = login(org_admin)
    assert client.get(url, HTTP_ACCEPT="application/json").json()["can_manage"] is True
    assert (
        client.post(url, {"action": "grant_report", "group_id": group.pk}).status_code
        == 302
    )
    grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
    assert grant.report_grants.filter(report=report_row).exists()
