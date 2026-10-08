import pytest
from django.test import Client

from apps.core import roles
from apps.core.models import AuditLog
from apps.reports.models import Report
from apps.studios.models import StudioRepo

pytestmark = pytest.mark.django_db


def _url(org, studio):
    return f"/s/{org.slug}/{studio.slug}/settings/repo"


def _defaults(report="studio", analysis="private"):
    return {
        "set_default_audiences": "1",
        "default_report_audience": report,
        "default_analysis_audience": analysis,
    }


def test_defaults_save_without_repo_and_leave_existing_items_unchanged(
    login, org_admin, org, studio, settings
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    existing = Report.objects.create(studio=studio, slug="existing", audience="studio")
    response = login(org_admin).post(_url(org, studio), _defaults())
    assert response.status_code == 302
    studio.refresh_from_db()
    existing.refresh_from_db()
    assert (studio.default_report_audience, studio.default_analysis_audience) == (
        "studio", "private"
    )
    assert existing.audience == "studio"
    assert not StudioRepo.objects.filter(studio=studio).exists()
    entry = AuditLog.objects.get(action="studio.default_audiences_set")
    assert entry.metadata == {
        "prior": {"default_report_audience": "studio", "default_analysis_audience": "studio"},
        "new": {"default_report_audience": "studio", "default_analysis_audience": "private"},
    }


@pytest.mark.parametrize("report,analysis", [("invalid", "private"), ("private", "invalid")])
def test_invalid_defaults_reject_both_values(
    login, org_admin, org, studio, settings, report, analysis
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    response = login(org_admin).post(_url(org, studio), _defaults(report, analysis))
    assert response.status_code == 200
    assert b"Select a valid choice" in response.content
    studio.refresh_from_db()
    assert (studio.default_report_audience, studio.default_analysis_audience) == ("studio", "studio")
    assert not StudioRepo.objects.filter(studio=studio).exists()
    assert not AuditLog.objects.filter(action="studio.default_audiences_set").exists()


def test_defaults_require_admin(login, member, org, studio, grant_studio):
    grant_studio(member, studio, roles.DEVELOPER)
    assert login(member).post(_url(org, studio), _defaults()).status_code == 403
    studio.refresh_from_db()
    assert studio.default_analysis_audience == "studio"


def test_defaults_require_csrf(org_admin, org, studio):
    client = Client(enforce_csrf_checks=True)
    client.force_login(org_admin)
    assert client.post(_url(org, studio), _defaults()).status_code == 403
    studio.refresh_from_db()
    assert studio.default_analysis_audience == "studio"


@pytest.mark.parametrize("cdn_model", ["portal", "edge-external"])
def test_defaults_reject_private_when_scoped_access_unavailable(
    login, org_admin, org, studio, settings, cdn_model
):
    settings.TRELLUM_REPORT_ACCESS_MODEL = cdn_model
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = cdn_model == "edge-external"
    response = login(org_admin).post(_url(org, studio), _defaults())
    assert response.status_code == 200
    studio.refresh_from_db()
    assert studio.default_analysis_audience == "studio"


def test_settings_describe_new_items_and_link_to_access(login, org_admin, org, studio):
    html = login(org_admin).get(_url(org, studio)).content.decode()
    assert "Default audiences" in html
    assert "Studio audience" in html
    assert "Existing items keep their audience" in html
    assert "choose Access in the Options menu" in html
