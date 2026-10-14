"""Native and JSON catalog entry points share bounded authorized results."""
import json

import pytest

from apps.reports.models import Report, ReportFavorite

pytestmark = pytest.mark.django_db


def test_native_and_json_catalog_use_same_pages_without_registry(login, studio, org_admin, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("The catalog must not enumerate the legacy registry")

    monkeypatch.setattr("apps.reports.views.build_registry_payload", forbidden)
    reports = Report.objects.bulk_create([
        Report(studio=studio, slug=f"report-{index:03}", name=f"Report {index:03}",
               category=f"Category {index % 4}", description="é & <needle> " * 60)
        for index in range(56)
    ])
    ReportFavorite.objects.bulk_create([
        ReportFavorite(user=org_admin, report=report) for report in reports[:30]
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    native = client.get(base + "/?view=list&page=2")
    payload = client.get(base + "/api/catalog?view=list&page=2").json()
    assert native.status_code == 200
    assert payload["pagination"]["total"] == 56
    assert len(payload["rows"]) == 25
    for row in payload["rows"]:
        assert f'data-catalog-id="{row["id"]}"'.encode() in native.content
    assert b"portal-catalog.js" in native.content
    assert b"static/portal.js" not in native.content
    assert b"Next" in native.content and b"Previous" in native.content
    assert client.get(base + "/api/catalog/personal?favorite_page=2").json()["pagination"]["total"] == 30
    assert client.get(base + "/api/catalog?tab=_favorites&page=2").json()["pagination"]["total"] == 30
    assert client.get(base + "/api/catalog/categories").json()["pagination"]["total"] == 4


def test_large_analysis_native_and_json_pages_are_complete(login, studio, org_admin):
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"analysis-{index:03}", kind=Report.KIND_ANALYSIS,
               name=f"Analysis {index:03}") for index in range(67)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    seen = []
    for page in (1, 2, 3):
        native = client.get(base + f"/analyses?page={page}")
        payload = client.get(base + f"/api/catalog?kind=analysis&page={page}").json()
        assert native.status_code == 200
        assert payload["pagination"]["total"] == 67
        seen += [row["id"] for row in payload["rows"]]
        for row in payload["rows"]:
            assert f'data-catalog-id="{row["id"]}"'.encode() in native.content
    assert len(seen) == len(set(seen)) == 67


def test_detail_fallback_is_authorized_and_bounds_native_html(login, studio, org_admin, member):
    report = Report.objects.create(studio=studio, slug="detail", description="Long text " * 200, tags=["tag"] * 40)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    owner = login(org_admin)
    detail = owner.get(base + f"/catalog/details/{report.pk}?metadata=0&description_page=2&tag_page=2")
    assert detail.status_code == 200
    assert b"Open report normally" in detail.content
    assert b"Next description" in detail.content or b"Previous description" in detail.content
    assert len(detail.content) <= 128 * 1024
    assert owner.get(base + f"/api/catalog/details/{report.pk}?metadata=0").json()["report_id"] == report.pk
    assert owner.get(base + "/api/catalog/indicators?id=not-an-id").status_code == 400
    assert owner.get(base + "/api/catalog?kind=invalid").status_code == 400
