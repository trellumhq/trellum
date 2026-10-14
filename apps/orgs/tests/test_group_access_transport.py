"""Whole HTTP compact selection saves and draft isolation."""
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.models import AuditLog
from apps.orgs.groups.forms import StudioAccessForm
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def scoped_access(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"


def payload(studio, selected, editable=(), checked=(), **values):
    return {"action": "studio_access", "studio": str(studio.pk), "role": "viewer", "viewer_scope": "selected", "reports__selection": json.dumps({"version": 1, "selected": [str(pk) for pk in selected], "editable": [str(pk) for pk in editable]}), "reports": [str(pk) for pk in checked], **values}


def path(org, group):
    return f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access"


@pytest.mark.parametrize("count", [25, 100, 1000])
@pytest.mark.parametrize("enhanced", [False, True])
def test_compact_http_save_exact_sets_and_bounded_queries(count, enhanced, login, org_admin, org, studio, make_group):
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"compact-{i:04}", name=f"Report {i:04}") for i in range(count)])
    group = make_group("Compact")
    client = login(org_admin)
    kwargs = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"} if enhanced else {}
    with CaptureQueriesContext(connection) as queries:
        response = client.post(path(org, group), payload(studio, [r.pk for r in reports]), **kwargs)
    assert response.status_code == (200 if enhanced else 302)
    assert len(queries) <= 65
    if enhanced:
        assert response.json()["ok"] and len(response.content) <= 32768
    assert set(ReportPermissionGrant.objects.filter(grant__group=group).values_list("report_id", flat=True)) == {r.pk for r in reports}
    assert set(AuditLog.objects.filter(action="group.report_grant", metadata__group_id=group.pk).values_list("target_id", flat=True)) == {str(r.pk) for r in reports}
    assert AuditLog.objects.filter(action="group.grant", target_id=str(group.pk)).count() == 1


def test_native_browse_remove_and_uncheck_are_draft_only(login, org_admin, org, studio, make_group):
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"draft-{i:03}", name=f"Report {i:03}") for i in range(100)])
    group = make_group("Draft")
    client = login(org_admin)
    selected = [r.pk for r in reports]
    draft = payload(studio, selected, selected[:25], selected[1:25], action="browse_access")
    before = AuditLog.objects.count()
    response = client.post(path(org, group) + f"&report_page=2&remove_report={reports[-1].pk}", draft)
    assert response.status_code == 200
    form = response.context["access_rows"][0]["form"]
    assert set(form["reports"].value()) == {str(r.pk) for r in reports[1:-1]}
    assert len(form.fields["reports"].widget.page_values) == 25
    assert not group.grants.exists() and AuditLog.objects.count() == before
    assert b"Remove Report 098" in response.content
    assert response.content.count(b'type="checkbox"') == 25


@pytest.mark.parametrize("packet", ["{", "null", '{"version":1,"selected":["bad"],"editable":[]}'])
def test_malformed_packet_bound_render_never_clears(packet, login, org_admin, org, studio, make_group):
    group = make_group("Malformed")
    report = Report.objects.create(studio=studio, slug="retained")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.create(grant=grant, report=report)
    data = payload(studio, [])
    data["reports__selection"] = packet
    client = login(org_admin)
    for action in ("browse_access", "studio_access"):
        data["action"] = action
        response = client.post(path(org, group), data)
        form = response.context["access_rows"][0]["form"]
        assert "reports" in form.errors
        assert form["reports"].value().packet == packet
        assert b"data-compact-invalid" in response.content
        assert list(grant.report_grants.values_list("report_id", flat=True)) == [report.pk]


def test_packet_does_not_authorize_foreign_or_deleted_identity(login, org_admin, org, studio, other_studio, make_group):
    foreign = Report.objects.create(studio=other_studio, slug="hidden", name="Hidden foreign")
    deleted = Report.objects.create(studio=studio, slug="deleted").pk
    Report.objects.filter(pk=deleted).delete()
    group = make_group("Authority")
    for identity in (foreign.pk, deleted):
        response = login(org_admin).post(path(org, group), payload(studio, [identity]))
        assert "reports" in response.context["access_rows"][0]["form"].errors
        assert b"Hidden foreign" not in response.content
        assert not group.grants.exists()


def test_form_validation_retains_compact_metadata(org, studio):
    from django.http import QueryDict
    data = QueryDict(mutable=True)
    data.update(studio=str(studio.pk), role="viewer", viewer_scope="selected", reports__selection="{")
    form = StudioAccessForm(data, org=org)
    assert not form.is_valid()
    assert form["reports"].value().error and form["reports"].value().packet == "{"


def test_compact_csrf_and_role_denial_write_nothing(org_admin, member, org, studio, make_group):
    from django.test import Client
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


def test_compact_role_scope_and_readiness_keep_existing_service_contract(login, org_admin, org, studio, make_group, settings):
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
