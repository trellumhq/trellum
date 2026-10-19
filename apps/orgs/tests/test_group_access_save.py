"""Whole HTTP saves of the per-studio access cards: exact sets, audits, query bounds."""
import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.core.models import AuditLog
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def scoped_access(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"


def payload(studio, selected, **values):
    return {"action": "studio_access", "studio": str(studio.pk), "role": "viewer", "viewer_scope": "selected",
            "reports": [str(pk) for pk in selected], **values}


def path(org, group):
    return f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access"


# The query bound does not depend on size, so one small run and the largest are
# enough; 500 runs both the plain-redirect and the enhanced JSON response.
@pytest.mark.parametrize("count, enhanced", [(25, False), (500, False), (500, True)])
def test_http_save_exact_sets_and_bounded_queries(count, enhanced, login, org_admin, org, studio, make_group):
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"save-{i:04}", name=f"Report {i:04}") for i in range(count)])
    group = make_group("Save")
    client = login(org_admin)
    kwargs = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"} if enhanced else {}
    with CaptureQueriesContext(connection) as queries:
        response = client.post(path(org, group), payload(studio, [r.pk for r in reports]), **kwargs)
    assert response.status_code == (200 if enhanced else 302)
    assert len(queries) <= 65
    if enhanced:
        assert response.json()["ok"]
    else:
        assert response["Location"] == f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access"
    assert set(ReportPermissionGrant.objects.filter(grant__group=group).values_list("report_id", flat=True)) == {r.pk for r in reports}
    assert set(AuditLog.objects.filter(action="group.report_grant", metadata__group_id=group.pk).values_list("target_id", flat=True)) == {str(r.pk) for r in reports}
    assert AuditLog.objects.filter(action="group.grant", target_id=str(group.pk)).count() == 1


def test_unchecking_removes_only_unchecked_assignments(login, org_admin, org, studio, make_group):
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"draft-{i:03}", name=f"Report {i:03}") for i in range(10)])
    group = make_group("Draft")
    client = login(org_admin)
    assert client.post(path(org, group), payload(studio, [r.pk for r in reports])).status_code == 302
    before = AuditLog.objects.count()
    assert client.post(path(org, group), payload(studio, [r.pk for r in reports[1:-1]])).status_code == 302
    assert set(ReportPermissionGrant.objects.filter(grant__group=group).values_list("report_id", flat=True)) == {r.pk for r in reports[1:-1]}
    removed = [row for row in AuditLog.objects.order_by("id")[before:] if row.action == "group.report_ungrant"]
    assert {row.target_id for row in removed} == {str(reports[0].pk), str(reports[-1].pk)}


@pytest.mark.parametrize("identities", ["foreign", "deleted", "own_and_foreign"])
def test_foreign_or_deleted_identity_is_rejected_without_leaking(
    identities, login, org_admin, org, studio, other_studio, make_group
):
    foreign = Report.objects.create(studio=other_studio, slug="hidden", name="Hidden foreign")
    own = Report.objects.create(studio=studio, slug="own", name="Own report")
    deleted = Report.objects.create(studio=studio, slug="deleted").pk
    Report.objects.filter(pk=deleted).delete()
    selected = {
        "foreign": [foreign.pk],
        "deleted": [deleted],
        # One valid report must not let a foreign one through: the whole change is refused.
        "own_and_foreign": [own.pk, foreign.pk],
    }[identities]
    group = make_group("Authority")
    response = login(org_admin).post(path(org, group), payload(studio, selected))
    assert response.status_code == 200
    assert "reports" in response.context["access_rows"][0]["form"].errors
    assert b"Select a valid choice" in response.content
    assert b"Hidden foreign" not in response.content
    assert not group.grants.exists()
    assert not ReportPermissionGrant.objects.filter(grant__group=group).exists()


def test_csrf_and_role_denial_write_nothing(org_admin, member, org, studio, make_group):
    group = make_group("CSRF")
    report = Report.objects.create(studio=studio, slug="protected")
    client = Client(enforce_csrf_checks=True)
    client.force_login(org_admin)
    url = path(org, group)
    client.get(url)
    data = payload(studio, [report.pk])
    assert client.post(url, data).status_code == 403
    csrf = client.cookies["csrftoken"].value
    client.force_login(member)
    assert client.post(url, data, HTTP_X_CSRFTOKEN=csrf).status_code == 403
    assert not group.grants.exists()
    assert not AuditLog.objects.filter(action="group.grant", target_id=str(group.pk)).exists()


def test_role_scope_and_readiness_keep_existing_service_contract(login, org_admin, org, studio, make_group, settings):
    private = Report.objects.create(studio=studio, slug="private", audience=Report.AUDIENCE_PRIVATE)
    public = Report.objects.create(studio=studio, slug="public")
    group = make_group("Transitions")
    client = login(org_admin)
    url = path(org, group)
    assert client.post(url, payload(studio, [private.pk, public.pk])).status_code == 302
    assert client.post(url, payload(studio, [], viewer_scope="all")).status_code == 302
    assert set(ReportPermissionGrant.objects.filter(grant__group=group).values_list("report_id", flat=True)) == {private.pk}
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    response = client.post(url, payload(studio, []))
    assert response.status_code == 200 and response.context["access_rows"][0]["form"].non_field_errors()
    assert ReportPermissionGrant.objects.filter(grant__group=group, report=private).exists()
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    assert client.post(url, payload(studio, [], role="developer")).status_code == 302
    assert not ReportPermissionGrant.objects.filter(grant__group=group).exists()
    assert not PermissionGroupGrant.objects.filter(group=group, role="viewer").exists()
