"""Every picker search answers through one helper: the response contract, the
permission gate and the selection rules hold identically on all six endpoints."""
import pytest
from django.urls import reverse

from apps.core.choice_requests import choice_id, search_page, selected_by_id
from apps.orgs.models import OrgMembership
from apps.reports.models import MetricDefinition, Report

pytestmark = pytest.mark.django_db

PER_PAGE_KEYS = {"page", "per_page", "total", "has_previous", "has_next"}
PAGES_KEYS = {"page", "pages", "total", "has_next", "has_previous"}


class TestHelpers:
    @pytest.mark.parametrize("value, prefix, expected", [
        ("42", "", 42), ("id:42", "id:", 42), ("42", "id:", 42), (None, "", None), ("", "", None),
        ("0", "", None), ("-4", "", None), ("4x", "", None), (" 4", "", None), ("١٢", "", None),
        ("9" * 19, "", None), ("9223372036854775807", "", 9223372036854775807),
        ("9223372036854775808", "", None), ("id:", "id:", None),
    ])
    def test_choice_id(self, value, prefix, expected):
        assert choice_id(value, prefix=prefix) == expected

    def test_search_page_filters_across_fields_and_clamps_the_page(self, org, make_user):
        for i in range(30):
            make_user(f"find-{i:02}@example.test", org=org)
        rows = OrgMembership.objects.filter(org=org).order_by("user__email")
        first = search_page(rows, "FIND-", fields=("user__email", "user__name"), page="1")
        assert first.paginator.count == 30 and len(first.object_list) == 25 and first.has_next()
        assert len(search_page(rows, "find-", fields=("user__email",), page="2").object_list) == 5
        assert search_page(rows, "find-", fields=("user__email",), page="99").number == 2  # past the end
        assert search_page(rows, "find-", fields=("user__email",), page="x").number == 1  # not a number
        assert search_page(rows, "%", fields=("user__email",)).paginator.count == 0  # literal, not a wildcard
        assert search_page(rows, "", fields=("user__email",)).paginator.count == 30

    def test_selected_by_id_keeps_the_callers_scope(self, org, make_user, other_org):
        mine = make_user("mine@example.test", org=org)
        theirs = make_user("theirs@example.test", org=other_org)
        rows = OrgMembership.objects.filter(org=org)
        assert selected_by_id(rows, mine.pk, field="user_id").user_id == mine.pk
        assert selected_by_id(rows, theirs.pk, field="user_id") is None
        assert selected_by_id(rows, "not-a-number", field="user_id") is None


@pytest.fixture
def many_reports(studio_tree):
    return [Report.objects.create(studio=studio_tree, slug=f"pick-{i:02}", name=f"Pick {i:02}") for i in range(30)]


class TestDeliveryReportChoices:
    def test_contract_search_selection_and_header(self, login, org_admin, studio_tree, org, many_reports):
        client = login(org_admin)
        url = reverse("api-delivery-report-choices", args=[org.slug, studio_tree.slug])
        data = client.get(url, {"selected": f"id:{many_reports[-1].pk}"}).json()
        assert set(data) == {"results", "selected", "pagination"}
        assert set(data["pagination"]) == PER_PAGE_KEYS and len(data["results"]) == 25
        assert data["selected"]["value"] == str(many_reports[-1].pk) and set(data["selected"]) == {"value", "label", "hint"}
        response = client.get(url, {"q": "pick 07", "selected": str(many_reports[0].pk)})
        assert response.headers["Cache-Control"] == "private, no-store"
        found = response.json()
        assert [row["value"] for row in found["results"]] == [str(many_reports[7].pk)]
        assert found["selected"]["value"] == str(many_reports[0].pk)  # not narrowed by the search
        assert client.get(url, {"selected": "9" * 30}).json()["selected"] is None
        assert client.post(url, {}).status_code == 405

    def test_requires_a_studio_role_and_hides_other_studios(self, client, member, org, studio_tree, many_reports, login):
        url = reverse("api-delivery-report-choices", args=[org.slug, studio_tree.slug])
        assert client.get(url).status_code in {302, 401, 403, 404}
        assert login(member).get(url).status_code in {403, 404}  # no studio role

    def test_global_form_needs_a_signed_in_person(self, client, org_admin, many_reports, login):
        assert client.get("/api/delivery-reports").status_code == 404
        data = login(org_admin).get("/api/delivery-reports").json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PER_PAGE_KEYS
        assert data["results"][0]["hint"].count(" / ") == 1  # organization / studio, only where studios mix


class TestAlertReportChoices:
    def test_contract_and_developer_gate(self, login, org_admin, studio_tree, org, many_reports, grant_studio, member):
        url = reverse("studio-alert-report-choices", args=[org.slug, studio_tree.slug])
        client = login(org_admin)
        data = client.get(url, {"selected": str(many_reports[3].pk)}).json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PER_PAGE_KEYS
        assert data["pagination"]["per_page"] == 25 and data["selected"]["label"] == "Pick 03"
        assert [row["label"] for row in client.get(url, {"q": "pick 21"}).json()["results"]] == ["Pick 21"]
        assert client.get(url).headers["Cache-Control"] == "private, no-store"
        grant_studio(member, studio_tree, "viewer")
        assert login(member).get(url).status_code == 403


class TestStudioMemberChoices:
    def test_contract_and_admin_gate(self, login, org_admin, studio_tree, org, member, grant_studio):
        url = reverse("studio-member-choices-api", args=[org.slug, studio_tree.slug])
        data = login(org_admin).get(url, {"selected": member.pk}).json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PAGES_KEYS
        assert data["selected"] == {"value": str(member.pk), "label": member.email, "hint": ""}
        grant_studio(member, studio_tree, "developer")
        assert login(member).get(url).status_code == 403
        assert login(org_admin).get(url, {"selected": member.pk}).json()["selected"] is None  # already a member


class TestGroupChoices:
    def test_member_choices_contract_and_validation(self, login, org_admin, org, make_group, member):
        group = make_group("Pickers")
        url = reverse("org-group-member-choices", args=[org.slug, group.pk])
        data = login(org_admin).get(url, {"selected": member.pk}).json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PER_PAGE_KEYS
        assert data["selected"] == {"value": member.pk, "label": member.email, "hint": ""}  # a number, as before
        assert login(org_admin).get(url, {"kind": "nope"}).status_code == 400
        assert login(org_admin).get(url).headers["Cache-Control"] == "private, no-store"
        assert login(member).get(url).status_code in {403, 404}

    def test_report_choices_contract_get_and_json_post(self, login, org_admin, org, studio_tree, make_group, many_reports):
        group = make_group("Report pickers")
        url = reverse("org-group-report-choices", args=[org.slug, group.pk, studio_tree.pk])
        client = login(org_admin)
        data = client.get(url, {"selected": f"{many_reports[1].pk},{many_reports[2].pk}"}).json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PER_PAGE_KEYS
        assert [row["value"] for row in data["selected"]] == [str(many_reports[1].pk), str(many_reports[2].pk)]
        posted = client.post(url, {"q": "pick 11"}, content_type="application/json")
        assert [row["value"] for row in posted.json()["results"]] == [str(many_reports[11].pk)]
        assert posted.headers["Cache-Control"] == "private, no-store"
        assert client.post(url, {"bogus": "x"}, content_type="application/json").status_code == 400


class TestShellScopeChoices:
    def test_contract_and_authentication(self, client, login, org_admin, org, studio):
        url = "/api/shell/scopes/"
        assert client.get(url, {"kind": "org"}).status_code == 401
        data = login(org_admin).get(url, {"kind": "org", "selected": org.slug}).json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PAGES_KEYS
        assert data["selected"] == {"value": org.slug, "label": org.name, "hint": org.slug}
        assert login(org_admin).get(url, {"kind": "org"}).headers["Cache-Control"] == "private, no-store"
        assert login(org_admin).get(url, {"kind": "bogus"}).status_code == 400


class TestMetricChoices:
    def test_contract_and_validation(self, login, org_admin, org, studio_tree):
        for i in range(30):
            MetricDefinition.objects.create(studio=studio_tree, name=f"metric_{i:02}", label=f"Metric {i:02}", present_in_scan=True)
        url = reverse("studio-metric-choices", args=[org.slug, studio_tree.slug])
        client = login(org_admin)
        data = client.get(url, {"selected": "metric_29"}).json()
        assert set(data) == {"results", "selected", "pagination"} and set(data["pagination"]) == PAGES_KEYS
        assert data["selected"] == {"value": "metric_29", "label": "Metric 29", "hint": "metric_29"}
        assert len(data["results"]) == 25 and data["pagination"]["total"] == 30
        assert client.get(url, {"q": "metric 05"}).json()["results"][0]["value"] == "metric_05"
        assert client.get(url, {"page": "0"}).status_code == 400
        assert client.get(url, {"selected": "Bad Name"}).status_code == 400
        assert client.get(url).headers["Cache-Control"] == "private, no-store"
