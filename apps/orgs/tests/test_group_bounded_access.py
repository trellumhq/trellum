"""Whole-catalog discovery and off-page draft safety for group access."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant, ShareLink
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


def url(org, group, tab):
    return f"/orgs/{org.slug}/settings/groups/{group.pk}?tab={tab}"


def test_access_pages_studios_and_opens_offpage_editor(login, org_admin, org, make_group):
    studios = Studio.objects.bulk_create([Studio(org=org, name=f"Studio {i:02}", slug=f"bounded{i:02}") for i in range(50)])
    group = make_group("Bounded")
    client = login(org_admin)
    response = client.get(url(org, group, "access"))
    assert len(response.context["studio_rows"]) == 25
    assert len(response.context["access_rows"]) == 1
    response = client.get(url(org, group, "access"), {"tab": "access", "studio": studios[-1].pk, "studio_page": 2})
    assert response.context["access_rows"][0]["studio"].pk == studios[-1].pk
    response = client.get(url(org, group, "access"), {"tab": "access", "studio_q": "49"})
    assert [r["studio"].pk for r in response.context["studio_rows"]] == [studios[-1].pk]


def test_native_browse_and_failed_save_retain_offpage_selection(login, org_admin, org, studio, make_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"bounded{i:03}", name=f"Report {i:03}") for i in range(100)])
    group = make_group("Bounded")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.create(grant=grant, report=reports[-1])
    client = login(org_admin)
    payload = {"report_page": "2", "action": "browse_access", "studio": studio.pk, "role": "viewer", "viewer_scope": "selected", "reports": [reports[-1].pk, reports[0].pk]}
    response = client.post(url(org, group, "access") + "&report_page=1", payload)
    assert response.status_code == 200
    assert response.context["report_page"].number == 2
    assert list(grant.report_grants.values_list("report_id", flat=True)) == [reports[-1].pk]
    form = response.context["access_rows"][0]["form"]
    assert set(map(str, form["reports"].value())) == {str(reports[0].pk), str(reports[-1].pk)}
    assert len(form.fields["reports"].choices) == 27
    payload.update(action="studio_access", role="wrong")
    response = client.post(url(org, group, "access") + "&studio_page=20", payload)
    assert response.status_code == 200
    assert response.context["access_rows"][0]["studio"].pk == studio.pk
    assert b"Select a valid choice" in response.content
    assert set(map(str, response.context["access_rows"][0]["form"]["reports"].value())) == {str(reports[0].pk), str(reports[-1].pk)}
    payload.update(role="viewer")
    assert client.post(url(org, group, "access"), payload).status_code == 302
    assert set(grant.report_grants.values_list("report_id", flat=True)) == {reports[-1].pk, reports[0].pk}


def test_foreign_selected_draft_retained_without_foreign_label(login, org_admin, org, studio, other_studio, make_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    foreign = Report.objects.create(studio=other_studio, slug="secret", name="Secret foreign report")
    group = make_group("Bounded")
    response = login(org_admin).post(url(org, group, "access"), {"action": "studio_access", "studio": studio.pk, "role": "viewer", "viewer_scope": "selected", "reports": [foreign.pk]})
    assert response.status_code == 200
    assert foreign.name.encode() not in response.content
    assert str(foreign.pk) in response.context["access_rows"][0]["form"]["reports"].value()
    assert "Selected report is unavailable" in response.content.decode()
    assert not group.grants.exists()


def test_member_choices_are_complete_and_scope_safe(login, org_admin, org, other_org, make_group, make_user, attach_group):
    group = make_group("Bounded")
    users = [make_user(f"choice{i:03}@example.test", org=org) for i in range(60)]
    attach_group(users[0], group)
    foreign = make_user("foreign-secret@example.test", org=other_org)
    endpoint = f"/orgs/{org.slug}/settings/groups/{group.pk}/member-choices"
    client = login(org_admin)
    response = client.get(endpoint, {"q": "choice", "page": 2, "selected": users[-1].pk})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "private, no-store"
    assert len(response.json()["results"]) == 25
    assert response.json()["selected"]["value"] == users[-1].pk
    assert client.get(endpoint, {"selected": foreign.pk}).json()["selected"] is None
    assert client.get(endpoint, {"q": "choice059"}).json()["results"][0]["value"] == users[-1].pk
    assert client.get(endpoint, {"kind": "effective"}).json()["results"][0]["value"] == users[0].pk


def test_report_choices_validate_org_and_preserve_missing_labels(login, org_admin, org, studio, other_studio, make_group):
    group = make_group("Bounded")
    reports = Report.objects.bulk_create([Report(studio=studio, name=f"Report {i:03}", slug=f"choice{i:03}", present_in_scan=True) for i in range(60)])
    reports[-1].present_in_scan = False
    reports[-1].save(update_fields=["present_in_scan"])
    endpoint = f"/orgs/{org.slug}/settings/groups/{group.pk}/studios/{studio.pk}/report-choices"
    client = login(org_admin)
    data = client.get(endpoint, {"page": 2, "selected": reports[-1].pk}).json()
    assert len(data["results"]) == 25
    assert data["selected"][0]["label"].endswith("(missing from repository)")
    foreign_endpoint = endpoint.replace(f"/studios/{studio.pk}/", f"/studios/{other_studio.pk}/")
    assert client.get(foreign_endpoint).status_code == 404


def test_effective_sources_and_reports_are_complete_paged_search(login, org_admin, org, studio, member, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    groups = [make_group(f"Source {i:03}", default_studio_role=roles.VIEWER) for i in range(60)]
    for group in groups:
        attach_group(member, group)
    reports = Report.objects.bulk_create([Report(studio=studio, name=f"Report {i:03}", slug=f"effective{i:03}", audience="studio") for i in range(60)])
    private = Report.objects.create(studio=studio, slug="private", name="Private secret", audience="private")
    client = login(org_admin)
    response = client.get(url(org, groups[0], "effective"), {"tab": "effective", "member": member.pk, "studio": studio.pk})
    assert len(response.context["source_page"].object_list) == 25
    assert response.context["source_total"] == 60
    assert response.context["effective_report_page"].paginator.count == 60
    assert private.name.encode() not in response.content
    response = client.get(url(org, groups[0], "effective"), {"tab": "effective", "member": member.pk, "studio": studio.pk, "source_q": "059", "report_q": "059"})
    assert response.context["source_page"].object_list == ["Source 059: default Viewer on every studio"]
    assert list(response.context["effective_report_page"].object_list) == [reports[-1]]


def test_report_assignments_batch_and_links_page_without_tokens(login, org_admin, org, studio, report_row, make_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    for i in range(50):
        group = make_group(f"Assigned {i:03}")
        grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    links = ShareLink.objects.bulk_create([ShareLink(report=report_row, token=f"secret-token-{i:03}", created_by=org_admin) for i in range(60)])
    with CaptureQueriesContext(connection) as queries:
        response = login(org_admin).get(f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access")
    assert all(row["selected"] for row in response.context["rows"])
    assert len(response.context["public_links"]) == 25 and response.context["link_total"] == 60
    assert all(link.token.encode() not in response.content for link in links)
    selected_exists = [q for q in queries if 'SELECT 1 AS "a" FROM "reports_reportpermissiongrant"' in q["sql"]]
    assert not selected_exists


def test_new_choices_require_org_admin_and_next_request_revocation(login, org_admin, member, org, studio, make_group):
    from apps.orgs.models import OrgMembership
    group = make_group("Bounded")
    endpoints = [f"/orgs/{org.slug}/settings/groups/{group.pk}/member-choices", f"/orgs/{org.slug}/settings/groups/{group.pk}/studios/{studio.pk}/report-choices"]
    viewer = login(member)
    for endpoint in endpoints:
        assert viewer.get(endpoint).status_code == 403
    admin = login(org_admin)
    for endpoint in endpoints:
        assert admin.get(endpoint).status_code == 200
    OrgMembership.objects.filter(user=org_admin, org=org).update(role="member")
    for endpoint in endpoints:
        assert admin.get(endpoint).status_code == 403


def test_native_browse_member_preserves_offpage_selection_without_add(login, org_admin, org, make_group, make_user):
    group = make_group("Bounded")
    people = [make_user(f"native{i:03}@example.test", org=org) for i in range(60)]
    response = login(org_admin).post(url(org, group, "members") + "&member_page=1", {"action": "browse_member", "member_page": "2", "member_q": "native", "user_id": people[-1].pk})
    assert response.context["addable_page"].number == 2
    assert len(response.context["addable_members"]) == 26
    assert response.context["selected_addable"].user_id == people[-1].pk
    assert not group.memberships.exists()


@pytest.mark.parametrize("identity,group_status,report_status", [("org-admin", 200, 200), ("group-admin", 200, 200), ("studio-admin", 403, 200), ("developer", 403, 403), ("viewer", 403, 403), ("member", 403, 404), ("outsider", 404, 404), ("superuser", 200, 200)])
def test_group_roles_all_native_paths(identity, group_status, report_status, login, org_admin, member, superuser, make_user, org, studio, report_row, make_group, attach_group, grant_studio):
    target = make_group("Target")
    user = member
    if identity == "org-admin":
        user = org_admin
    elif identity == "superuser":
        user = superuser
    elif identity == "outsider":
        user = make_user("outside@example.test")
    elif identity == "group-admin":
        attach_group(member, make_group("Admin source", org_role="admin"))
    elif identity in {"studio-admin", "developer", "viewer"}:
        grant_studio(member, studio, "admin" if identity == "studio-admin" else identity)
    client = login(user)
    base = f"/orgs/{org.slug}/settings/groups/{target.pk}"
    paths = [base, base + "?tab=members", base + "?tab=access", base + "?tab=effective", base + "/member-choices", base + f"/studios/{studio.pk}/report-choices"]
    for path in paths:
        assert client.get(path).status_code == group_status, path
    assert client.get(f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access").status_code == report_status


def test_choice_api_key_org_scope_and_methods(client, org_admin, org, other_org, studio, make_group):
    from apps.accounts.models import ApiKey
    group = make_group("Target")
    endpoints = [f"/orgs/{org.slug}/settings/groups/{group.pk}/member-choices", f"/orgs/{org.slug}/settings/groups/{group.pk}/studios/{studio.pk}/report-choices"]
    _, token = ApiKey.mint(user=org_admin, org=org, name="Read", scopes=ApiKey.READ)
    for path in endpoints:
        response = client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}")
        assert response.status_code == 403 and response.json()["error"] == "session_required"
        assert client.post(path, HTTP_AUTHORIZATION=f"Bearer {token}").status_code in {403, 405}
    from apps.orgs.models import OrgMembership
    OrgMembership.objects.create(user=org_admin, org=other_org, role="admin")
    _, foreign_token = ApiKey.mint(user=org_admin, org=other_org, name="Wrong org", scopes=ApiKey.READ)
    for path in endpoints:
        response = client.get(path, HTTP_AUTHORIZATION=f"Bearer {foreign_token}")
        assert response.status_code == 403 and response.json()["error"] == "session_required"


def test_large_json_report_read_is_csrf_protected_complete_and_never_saves(org_admin, org, studio, make_group, settings):
    from django.test import Client
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"large{i:04}", name=f"Report {i:04}", present_in_scan=True) for i in range(1000)])
    group = make_group("Large selection")
    page = url(org, group, "access")
    endpoint = f"/orgs/{org.slug}/settings/groups/{group.pk}/studios/{studio.pk}/report-choices"
    client = Client(enforce_csrf_checks=True); client.force_login(org_admin)
    client.get(page)
    body = {"selected": [str(r.pk) for r in reports], "page": "2", "q": ""}
    assert client.post(endpoint, body, content_type="application/json").status_code == 403
    csrf = client.cookies["csrftoken"].value
    response = client.post(endpoint, body, content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 200
    assert len(response.json()["results"]) == 25 and len(response.json()["selected"]) == 1000
    assert response.json()["pagination"]["page"] == 2
    assert response.headers["Cache-Control"] == "private, no-store"
    assert not group.grants.exists()
    assert client.post(endpoint, {"selected": "1"}, content_type="application/json", HTTP_X_CSRFTOKEN=csrf).status_code == 400


def test_json_report_invalid_selected_ids_do_not_leak_or_crash(login, org_admin, org, studio, other_studio, make_group):
    group = make_group("Scope")
    own = Report.objects.create(studio=studio, slug="own", name="Own report")
    foreign = Report.objects.create(studio=other_studio, slug="foreign", name="Hidden foreign report")
    endpoint = f"/orgs/{org.slug}/settings/groups/{group.pk}/studios/{studio.pk}/report-choices"
    response = login(org_admin).post(endpoint, {"selected": [str(own.pk), str(foreign.pk), '9' * 5000, '-1', 'bad'], "q": ""}, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["selected"] == [{"value": str(own.pk), "label": "Own report · Report"}]
    assert foreign.name.encode() not in response.content
