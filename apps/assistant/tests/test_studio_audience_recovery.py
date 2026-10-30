"""An unavailable Private item cannot disable permitted report conversations."""
import pytest

from apps.assistant.models import AssistantSession
from apps.assistant.tools import AssistantToolbox
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("present", [True, False])
def test_studio_viewer_retains_bound_session_without_private_or_full_history(
    login, viewer, org, studio_tree, report_row, prefix, settings, present,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    private = Report.objects.create(
        studio=studio_tree, slug="private-analysis", audience="private", present_in_scan=present,
    )
    hidden = AssistantSession.objects.create(
        user=viewer, org=org, studio=studio_tree, scope="full", title="Full studio history",
    )
    private_session = AssistantSession.objects.create(
        user=viewer, org=org, studio=studio_tree, scope="report", report=private,
    )
    client = login(viewer)
    response = client.post(f"{prefix}/sessions", {"report": report_row.slug}, content_type="application/json")
    assert response.status_code == 201
    assert response.json()["scope"] == "report"
    session_id = response.json()["id"]
    assert [s["id"] for s in client.get(f"{prefix}/sessions").json()["sessions"]] == [session_id]
    for denied in (hidden, private_session):
        assert client.get(f"{prefix}/sessions/{denied.pk}").status_code == 404
    assert client.post(f"{prefix}/sessions", {"report": private.slug}, content_type="application/json").status_code == 404
    bound = AssistantToolbox(studio_tree, actor=viewer, scope="report", report=report_row)
    assert bound._access_error(report_row.slug) is None
    assert bound._access_error(private.slug) is not None
    assert AssistantToolbox(studio_tree, actor=viewer, scope="full")._access_error() is not None


def test_available_probe_allows_only_accessible_report(
    login, viewer, studio_tree, report_row, prefix, settings, assistant_config,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    private = Report.objects.create(studio=studio_tree, slug="private-analysis", audience="private")
    client = login(viewer)
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/r/"
    assert client.get(f"{prefix}/available", {"path": base + report_row.slug + "/"}).json()["available"]
    assert not client.get(f"{prefix}/available", {"path": base + private.slug + "/"}).json()["available"]


def test_mcp_keeps_studio_reports_and_hides_private_items(
    client, viewer, org, studio_tree, report_row, settings,
):
    from apps.assistant.tests.test_mcp import key_for, call

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    private = Report.objects.create(studio=studio_tree, slug="private-analysis", name="Private secret", audience="private")
    _, secret = key_for(viewer, org)
    url = f"/s/{org.slug}/{studio_tree.slug}/mcp"
    listed = call(client, url, secret, "list_reports")["content"][0]["text"]
    assert report_row.slug in listed
    assert private.slug not in listed and private.name not in listed
    assert call(client, url, secret, "get_report_details", {"slug": private.slug})["isError"]
