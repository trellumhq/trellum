import pytest
from django.test import Client

from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import ReportPermissionGrant, ShareLink

pytestmark = pytest.mark.django_db


@pytest.fixture
def audience_ready(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True


def _url(org, studio, report):
    return f"/s/{org.slug}/{studio.slug}/r/{report.slug}/access"


def test_audience_changes_are_audited_and_leave_public_links(
    login, org_admin, org, studio, report_row, audience_ready
):
    link = ShareLink.objects.create(report=report_row, created_by=org_admin)
    client = login(org_admin)
    url = _url(org, studio, report_row)
    for audience in ("private", "studio"):
        assert client.post(url, {"action": "set_audience", "audience": audience}).status_code == 302
        report_row.refresh_from_db()
        assert report_row.audience == audience
    entries = list(AuditLog.objects.filter(action="report.audience_set").order_by("pk"))
    assert [entry.metadata for entry in entries] == [
        {"prior_audience": "studio", "audience": "private"},
        {"prior_audience": "private", "audience": "studio"},
    ]
    link.refresh_from_db()
    assert not link.revoked_at


def test_audience_change_requires_admin_and_csrf(
    login, member, org_admin, org, studio, report_row, grant_studio, audience_ready
):
    grant_studio(member, studio, roles.DEVELOPER)
    data = {"action": "set_audience", "audience": "private"}
    url = _url(org, studio, report_row)
    assert login(member).post(url, data).status_code == 403
    client = Client(enforce_csrf_checks=True)
    client.force_login(org_admin)
    assert client.post(url, data).status_code == 403
    report_row.refresh_from_db()
    assert report_row.audience == "studio"


def test_invalid_audience_does_not_save(login, org_admin, org, studio, report_row, audience_ready):
    response = login(org_admin).post(
        _url(org, studio, report_row), {"action": "set_audience", "audience": "public"}, follow=True
    )
    assert b"Choose a valid audience" in response.content
    report_row.refresh_from_db()
    assert report_row.audience == "studio"
    assert not AuditLog.objects.filter(action="report.audience_set").exists()


@pytest.mark.parametrize("cdn_model", ["portal", "edge-external"])
def test_audience_change_is_blocked_when_scoped_access_unavailable(
    login, org_admin, org, studio, report_row, settings, cdn_model
):
    settings.TRELLUM_REPORT_ACCESS_MODEL = cdn_model
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = cdn_model == "edge-external"
    response = login(org_admin).post(
        _url(org, studio, report_row), {"action": "set_audience", "audience": "private"}
    )
    assert response.status_code == 302
    report_row.refresh_from_db()
    assert report_row.audience == "studio"


@pytest.mark.parametrize("default_viewer", [False, True])
def test_private_all_viewer_can_be_explicitly_added_and_removed(
    login, org_admin, org, studio, report_row, make_group, audience_ready, default_viewer
):
    report_row.audience = "private"
    report_row.save(update_fields=["audience"])
    group = make_group("Viewers", default_studio_role=roles.VIEWER if default_viewer else "")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role=roles.VIEWER)
    client = login(org_admin)
    url = _url(org, studio, report_row)
    rows = client.get(url).context["rows"]
    assert not rows[0]["full_reason"]
    assert client.post(url, {"action": "grant_report", "group_id": group.pk}).status_code == 302
    grant.refresh_from_db()
    assert grant.viewer_scope == "all"
    assert ReportPermissionGrant.objects.filter(grant=grant, report=report_row).exists()
    client.post(url, {"action": "ungrant_report", "group_id": group.pk})
    assert not ReportPermissionGrant.objects.filter(grant=grant, report=report_row).exists()
    grant.refresh_from_db()
    assert grant.viewer_scope == "all"


@pytest.mark.parametrize("management", ["developer", "admin", "org_admin"])
def test_private_management_group_access_is_inherited(
    login, org_admin, org, studio, report_row, make_group, audience_ready, management
):
    report_row.audience = "private"
    report_row.save(update_fields=["audience"])
    group = make_group("Management", org_role=roles.ORG_ADMIN) if management == "org_admin" else make_group(
        "Management", grants=[(studio, management)]
    )
    response = login(org_admin).get(_url(org, studio, report_row))
    assert response.context["rows"][0]["full_reason"]
    assert b"Inherited" in response.content
    assert not ReportPermissionGrant.objects.filter(grant__group=group).exists()


def test_switch_to_studio_removes_invalid_all_viewer_item_assignment(
    login, org_admin, org, studio, report_row, make_group, audience_ready
):
    report_row.audience = "private"
    report_row.save(update_fields=["audience"])
    group = make_group("Viewers", grants=[(studio, roles.VIEWER)])
    grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    login(org_admin).post(_url(org, studio, report_row), {"action": "set_audience", "audience": "studio"})
    assert not ReportPermissionGrant.objects.filter(grant=grant, report=report_row).exists()
    grant.refresh_from_db()
    assert grant.viewer_scope == "all"


def test_analysis_uses_analysis_labels_and_links_defaults(
    login, org_admin, org, studio, report_row, make_group, audience_ready
):
    report_row.kind = "analysis"
    report_row.audience = "private"
    report_row.save(update_fields=["kind", "audience"])
    make_group("Viewers")
    html = login(org_admin).get(_url(org, studio, report_row)).content.decode()
    assert "Analysis audience" in html
    assert "Report access" not in html
    assert "Add analysis" in html
    assert "No access to this analysis" in html
    assert "this analysis through rebuilds" in html
    assert "#default-audiences" in html
    assert "Studio audience</option>" in html
    assert "Add report" not in html


def test_saving_all_viewer_studio_role_preserves_private_assignment(
    login, org_admin, org, studio, report_row, make_group, audience_ready
):
    report_row.audience = "private"
    report_row.save(update_fields=["audience"])
    group = make_group("Viewers", grants=[(studio, roles.VIEWER)])
    grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    response = login(org_admin).post(
        f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access",
        {"action": "studio_access", "studio": studio.pk, "role": roles.VIEWER, "viewer_scope": "all"},
    )
    assert response.status_code == 302
    assert ReportPermissionGrant.objects.filter(grant=grant, report=report_row).exists()


def test_private_group_assignment_rejects_wrong_org(
    login, org_admin, org, other_org, studio, report_row, make_group, audience_ready
):
    report_row.audience = "private"
    report_row.save(update_fields=["audience"])
    foreign = make_group("Foreign", org_=other_org)
    response = login(org_admin).post(
        _url(org, studio, report_row), {"action": "grant_report", "group_id": foreign.pk}
    )
    assert response.status_code == 404
    assert not PermissionGroupGrant.objects.filter(group=foreign).exists()


def test_private_effective_preview_lists_explicit_assignment(
    login, org_admin, org, studio, report_row, member, make_group, attach_group, audience_ready
):
    report_row.audience = "private"
    report_row.save(update_fields=["audience"])
    group = make_group("Viewers", grants=[(studio, roles.VIEWER)])
    grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    attach_group(member, group)
    client = login(org_admin)
    url = f"/orgs/{org.slug}/settings/groups/{group.pk}"
    response = client.get(url + f"?tab=effective&member={member.pk}")
    assert not response.context["effective_rows"][0]["full"]
    assert response.context["effective_rows"][0]["reports"] == [report_row]
    assert b"explicit Private assignments" in response.content
    html = client.get(url + "?tab=access").content.decode()
    assert _url(org, studio, report_row) in html
    assert "Explicit Private assignments" in html


def test_setting_private_restricts_direct_and_all_studio_viewers(
    login, org_admin, org, studio, report_row, member, make_group, attach_group, grant_studio, audience_ready
):
    from apps.core.report_access import can_view_report

    grant_studio(member, studio, roles.VIEWER)
    group = make_group("Viewers", default_studio_role=roles.VIEWER, grants=[(studio, roles.VIEWER)])
    attach_group(member, group)
    client = login(org_admin)
    url = _url(org, studio, report_row)
    assert can_view_report(member, report_row)
    client.post(url, {"action": "set_audience", "audience": "private"})
    report_row.refresh_from_db()
    assert not can_view_report(member, report_row)
    client.post(url, {"action": "grant_report", "group_id": group.pk})
    assert can_view_report(member, report_row)
    client.post(url, {"action": "ungrant_report", "group_id": group.pk})
    assert not can_view_report(member, report_row)
