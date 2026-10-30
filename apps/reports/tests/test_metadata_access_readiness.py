"""Metadata endpoints use the same content availability guard as report bytes."""
import pytest

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("endpoint", ["validation", "metrics", "recipients", "schedules"])
def test_selected_metadata_fails_closed_before_storage(
    login, member, report_row, studio_tree, make_group, attach_group, settings, monkeypatch, endpoint,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save()
    attach_group(member, group)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    def forbidden(*args, **kwargs):
        pytest.fail("Blocked metadata must not read report storage")
    monkeypatch.setattr("apps.reports.views.storage.read_meta", forbidden)
    prefix = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/reports"
    client = login(member)
    assert client.get(f"{prefix}/{report_row.slug}/{endpoint}").status_code == 503
    hidden = Report.objects.create(studio=studio_tree, slug="hidden")
    assert client.get(f"{prefix}/{hidden.slug}/{endpoint}").status_code == 404


@pytest.mark.parametrize("present", [True, False])
def test_existing_private_analysis_does_not_block_studio_report_bytes(
    login, member, grant_studio, report_row, studio_tree, write_meta, settings, present,
):
    from apps.reports.models import OrgSharePolicy

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    grant_studio(member, studio_tree, roles.VIEWER)
    private = Report.objects.create(
        studio=studio_tree, slug="removed-private", kind="analysis",
        audience="private", present_in_scan=present,
    )
    OrgSharePolicy.objects.create(org=studio_tree.org, share_links_enabled=False)
    output = write_meta(report_row.slug, validation={"status": "passed"})
    (output / "index.html").write_text("<html><head></head><body>Permitted report content</body></html>")
    (output / "data.json").write_text('{"allowed": true}')
    prefix = f"/s/{studio_tree.org.slug}/{studio_tree.slug}"
    client = login(member)
    for asset in ("index.html?display=console", "data.json"):
        response = client.get(f"{prefix}/r/{report_row.slug}/{asset}")
        assert response.status_code == 200
        if response.streaming:
            assert b"allowed" in b"".join(response.streaming_content)
        else:
            assert b"Permitted report content" in response.content
        assert client.get(f"{prefix}/r/{private.slug}/{asset}").status_code == 404
    validation = client.get(f"{prefix}/api/reports/{report_row.slug}/validation")
    assert validation.status_code == 200
    assert validation.json()["status"] == "passed"
