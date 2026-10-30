"""Metadata endpoints use the same content availability guard as report bytes."""
import pytest

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("endpoint", ["validation", "metrics", "recipients"])
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
