"""Complete group screens, bounded choice endpoints and safe off-page identities."""
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


def test_access_lists_every_studio_and_caps_far_above_realistic_sizes(login, org_admin, org, make_group, monkeypatch):
    studios = Studio.objects.bulk_create([Studio(org=org, name=f"Studio {i:02}", slug=f"bounded{i:02}") for i in range(50)])
    group = make_group("Bounded")
    client = login(org_admin)
    response = client.get(url(org, group, "access"))
    assert [row["studio"].pk for row in response.context["access_rows"]][-1] == studios[-1].pk
    assert len(response.context["access_rows"]) >= 50
    assert response.content.decode().count("data-studio-access") >= 50
    assert b"Showing the first" not in response.content
    monkeypatch.setattr("apps.orgs.groups.presentation.STUDIO_LIMIT", 10)
    response = client.get(url(org, group, "access"))
    assert len(response.context["access_rows"]) == 10
    assert b"Showing the first 10 studios." in response.content


def test_failed_save_keeps_posted_selection_and_large_studios_stay_complete(login, org_admin, org, studio, make_group, settings, monkeypatch):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"bounded{i:03}", name=f"Report {i:03}") for i in range(100)])
    group = make_group("Bounded")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.create(grant=grant, report=reports[-1])
    client = login(org_admin)
    html = client.get(url(org, group, "access")).content.decode()
    assert html.count('type="checkbox" name="reports"') == 100
    assert "data-recipient-source" not in html
    monkeypatch.setattr("apps.orgs.groups.presentation.CHOICE_LIMIT", 10)
    response = client.get(url(org, group, "access"))
    html = response.content.decode()
    # The first reports plus every selected one are rendered; search is server-side beyond the cap.
    assert html.count('type="checkbox" name="reports"') == 11
    assert f'value="{reports[-1].pk}" checked' in html or 'checked' in html
    assert f"/studios/{studio.pk}/report-choices" in html
    assert response.context["access_rows"][0]["remote_reports"]
    monkeypatch.undo()
    payload = {"action": "studio_access", "studio": studio.pk, "role": "wrong", "viewer_scope": "selected", "reports": [reports[-1].pk, reports[0].pk]}
    response = client.post(url(org, group, "access"), payload)
    assert response.status_code == 200
    assert b"Select a valid choice" in response.content
    assert set(map(str, response.context["access_rows"][0]["form"]["reports"].value())) == {str(reports[0].pk), str(reports[-1].pk)}
    assert list(grant.report_grants.values_list("report_id", flat=True)) == [reports[-1].pk]
    payload.update(role="viewer")
    assert client.post(url(org, group, "access"), payload).status_code == 302
    assert set(grant.report_grants.values_list("report_id", flat=True)) == {reports[-1].pk, reports[0].pk}


def test_foreign_selected_draft_is_rejected_without_foreign_label(login, org_admin, org, studio, other_studio, make_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    foreign = Report.objects.create(studio=other_studio, slug="secret", name="Secret foreign report")
    group = make_group("Bounded")
    response = login(org_admin).post(url(org, group, "access"), {"action": "studio_access", "studio": studio.pk, "role": "viewer", "viewer_scope": "selected", "reports": [foreign.pk]})
    assert response.status_code == 200
    assert foreign.name.encode() not in response.content
    assert b"Select a valid choice" in response.content
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


def test_effective_sources_and_reports_are_listed_in_full(login, org_admin, org, studio, member, make_group, attach_group, settings, monkeypatch):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    groups = [make_group(f"Source {i:03}", default_studio_role=roles.VIEWER) for i in range(60)]
    for group in groups:
        attach_group(member, group)
    reports = Report.objects.bulk_create([Report(studio=studio, name=f"Report {i:03}", slug=f"effective{i:03}", audience="studio") for i in range(60)])
    private = Report.objects.create(studio=studio, slug="private", name="Private secret", audience="private")
    client = login(org_admin)
    response = client.get(url(org, groups[0], "effective"), {"tab": "effective", "member": member.pk})
    row = next(r for r in response.context["effective_rows"] if r["studio"].pk == studio.pk)
    assert len(row["sources"]) == 60 and row["sources"][-1] == "Source 059: default Viewer on every studio"
    assert row["reports"] == reports and row["reports_count"] == 60
    assert private.name.encode() not in response.content
    html = response.content.decode()
    assert "member-choices" not in html
    # Choosing a member applies at once: no Preview, Search or Go button and no pager.
    form = html.split('id="effectiveMember"', 1)[1].split("</form>", 1)[0]
    assert 'onchange="this.form.submit()"' in form and "<button" not in form
    tab = html.split("<h2>Effective access</h2>", 1)[1].split("<script", 1)[0]
    for text in ("Search", "Previous", "Next", "ui-pager"):
        assert text not in tab, text
    monkeypatch.setattr("apps.orgs.groups.presentation.CHOICE_LIMIT", 5)
    response = client.get(url(org, groups[0], "effective"), {"tab": "effective", "member": member.pk})
    row = next(r for r in response.context["effective_rows"] if r["studio"].pk == studio.pk)
    assert len(row["reports"]) == 5 and row["reports_count"] == 60
    assert b"Showing the first 5 reports." in response.content


def test_report_assignments_batch_and_link_count_without_tokens(login, org_admin, org, studio, report_row, make_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    for i in range(50):
        group = make_group(f"Assigned {i:03}")
        grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    links = ShareLink.objects.bulk_create([ShareLink(report=report_row, token=f"secret-token-{i:03}", created_by=org_admin) for i in range(60)])
    with CaptureQueriesContext(connection) as queries:
        response = login(org_admin).get(f"/s/{org.slug}/{studio.slug}/r/{report_row.slug}/access")
    assert all(row["selected"] for row in response.context["rows"])
    assert response.context["link_total"] == 60 and "60</strong> saved links" in response.content.decode()
    assert b"Find link creator" not in response.content
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


def test_addable_members_are_all_listed_and_remote_only_beyond_the_cap(login, org_admin, org, make_group, make_user, monkeypatch):
    group = make_group("Bounded")
    people = [make_user(f"native{i:03}@example.test", org=org) for i in range(60)]
    client = login(org_admin)
    response = client.get(url(org, group, "members"))
    html = response.content.decode()
    assert all(f'value="{person.pk}"' in html for person in people)
    assert "member-choices" not in html and '<option value="">' not in html
    monkeypatch.setattr("apps.orgs.groups.presentation.CHOICE_LIMIT", 10)
    response = client.get(url(org, group, "members"))
    assert f"/settings/groups/{group.pk}/member-choices?kind=addable" in response.content.decode()
    assert len(response.context["addable_members"]) == 10
    assert not group.memberships.exists()


@pytest.mark.parametrize("identity,group_status,report_status", [("org-admin", 200, 200), ("group-admin", 200, 200), ("studio-admin", 403, 200), ("developer", 403, 403), ("viewer", 403, 403), ("member", 403, 404), ("outsider", 404, 404), ("superuser", 200, 200)])
def test_group_roles_all_paths(identity, group_status, report_status, login, org_admin, member, superuser, make_user, org, studio, report_row, make_group, attach_group, grant_studio):
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


def test_large_report_columns_never_transfer_for_presentation(login, org_admin, org, studio, member, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group("Large metadata", default_studio_role="viewer")
    attach_group(member, group)
    reports = Report.objects.bulk_create([Report(studio=studio, name=f"Large {i:03}", slug=f"blob{i:03}", config={"unused": "c" * 65536}, description="d" * 65536, tags=["t" * 65536]) for i in range(100)])
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.create(grant=grant, report=reports[-1])
    client = login(org_admin)
    paths = [url(org, group, "access"), url(org, group, "effective") + f"&member={member.pk}", f"/orgs/{org.slug}/settings/groups/{group.pk}/studios/{studio.pk}/report-choices?selected={reports[-1].pk}", f"/s/{org.slug}/{studio.slug}/r/{reports[0].slug}/access"]
    for path in paths:
        with CaptureQueriesContext(connection) as queries:
            response = client.get(path)
        assert response.status_code == 200
        report_selects = [query["sql"] for query in queries if query["sql"].lstrip().startswith("SELECT") and '"reports_report"' in query["sql"]]
        assert report_selects
        assert not any(f'"reports_report"."{field}"' in sql for field in ("config", "description", "tags") for sql in report_selects)
        if response.context and "access_rows" in response.context and response.context["access_rows"]:
            # Field validation still sees the complete studio and its reports.
            form = response.context["access_rows"][0]["form"]
            assert form.fields["reports"].queryset.count() == 100
            # Projected columns must not restrict valid off-page identities.
            selected = form.fields["reports"].clean([str(reports[-2].pk)])
            assert [row.pk for row in selected] == [reports[-2].pk]
