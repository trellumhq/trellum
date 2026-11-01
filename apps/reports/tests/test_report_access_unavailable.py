"""Presentation for report access configuration outages."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from apps.core import roles
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def scoped_access(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"


@pytest.fixture
def private_report(report_row):
    report_row.audience = Report.AUDIENCE_PRIVATE
    report_row.save(update_fields=["audience"])
    return report_row


@pytest.fixture
def selected_reader(private_report, member, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group("Selected readers", grants=[(private_report.studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    ReportPermissionGrant.objects.create(grant=grant, report=private_report)
    attach_group(member, group)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    return member


def _prefix(report):
    return f"/s/{report.studio.org.slug}/{report.studio.slug}"


@pytest.mark.parametrize(
    ("label", "viewport"),
    [
        pytest.param("desktop", {"width": 1440, "height": 1000}, id="desktop"),
        pytest.param(
            "mobile", {"width": 390, "height": 844}, marks=pytest.mark.heavy, id="mobile"
        ),
    ],
)
def test_html_report_block_has_console_shell_and_admin_actions(
    client, login, org_admin, private_report, tmp_path, label, viewport,
):
    prefix = _prefix(private_report)
    response = login(org_admin).get(f"{prefix}/r/{private_report.slug}/index.html?display=console")

    assert response.status_code == 503
    assert response["Content-Type"].startswith("text/html")
    assert response["Cache-Control"] == "private, no-store"
    assert b"Report access is unavailable" in response.content
    assert b"Private and selected report access is not configured" in response.content
    assert f"{prefix}/r/{private_report.slug}/access".encode() in response.content
    assert b"TRELLUM_REPORT_SCOPED_ACCESS_READY" in response.content

    # Exercise the actual Django error response and its former plain-text
    # presentation in Chromium at both viewport sizes for a direct comparison.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for variant, body, content_type in (
                ("notice", response.content, response["Content-Type"]),
                ("baseline", b"Selected report access is temporarily unavailable.", "text/plain"),
            ):
                page = browser.new_page(viewport=viewport)

                def fulfill_document(route):
                    if route.request.is_navigation_request():
                        route.fulfill(
                            status=503,
                            headers={"content-type": content_type},
                            body=body,
                        )
                    elif urlsplit(route.request.url).path.startswith("/static/"):
                        static_root = (Path(settings.BASE_DIR) / "static").resolve()
                        name = unquote(urlsplit(route.request.url).path.removeprefix("/static/"))
                        asset = (static_root / name).resolve()
                        if static_root in asset.parents and asset.is_file():
                            asset_type = (
                                mimetypes.guess_type(str(asset))[0]
                                or "application/octet-stream"
                            )
                            route.fulfill(body=asset.read_bytes(), content_type=asset_type)
                        else:
                            route.abort()
                    elif urlsplit(route.request.url).path.startswith("/_vendor/"):
                        route.fulfill(body="", content_type="text/css")
                    else:
                        route.abort()

                page.route("**/*", fulfill_document)
                page.goto("http://testserver/report-access-unavailable")
                if variant == "notice":
                    expect(page.get_by_role("heading", name="Report access is unavailable")).to_be_visible()
                    expect(page.get_by_role("link", name="Back to reports")).to_be_visible()
                    expect(page.get_by_role("link", name="Report access settings")).to_be_visible()
                else:
                    expect(page.get_by_text("Selected report access is temporarily unavailable.")).to_be_visible()
                page.screenshot(
                    path=str(tmp_path / f"report-access-{variant}-{label}.png"),
                    full_page=True,
                )
                page.close()
        finally:
            browser.close()


@pytest.mark.parametrize(
    ("access_model", "expected_detail"),
    [
        ("proxy", "TRELLUM_REPORT_SCOPED_ACCESS_READY"),
        ("edge-external", "edge-external"),
    ],
)
def test_operator_detail_is_only_shown_to_admins(
    client, login, private_report, org_admin, selected_reader,
    settings, access_model, expected_detail,
):
    settings.TRELLUM_REPORT_ACCESS_MODEL = access_model
    prefix = _prefix(private_report)

    admin_response = login(org_admin).get(f"{prefix}/r/{private_report.slug}/")
    assert admin_response.status_code == 503
    assert expected_detail.encode() in admin_response.content

    reader_response = login(selected_reader).get(
        f"{prefix}/r/{private_report.slug}/index.html"
    )
    assert reader_response.status_code == 503
    assert b"Operator detail" not in reader_response.content
    assert b"Report access settings" not in reader_response.content


def test_assets_and_live_query_keep_machine_readable_responses(
    client, login, org_admin, private_report,
):
    prefix = _prefix(private_report)
    client = login(org_admin)

    asset = client.get(f"{prefix}/r/{private_report.slug}/data.json")
    assert asset.status_code == 503
    assert asset["Content-Type"].startswith("text/plain")
    assert b"Private or selected-report access is not configured" in asset.content
    assert b"<html" not in asset.content
    assert private_report.slug.encode() not in asset.content
    assert asset["Cache-Control"] == "private, no-store"

    live = client.post(
        f"{prefix}/api/reports/{private_report.slug}/live-query",
        data="{}", content_type="application/json",
    )
    assert live.status_code == 503
    assert live["Content-Type"].startswith("application/json")
    assert live.json() == {
        "error": "selected_report_access_unavailable",
        "message": "Private or selected-report access is not configured. Contact your administrator.",
    }
    assert private_report.slug not in live.content.decode()
    assert live["Cache-Control"] == "private, no-store"


def test_users_without_report_access_still_get_404(
    client, login, private_report, member,
):
    client = login(member)
    prefix = f"{_prefix(private_report)}/r/{private_report.slug}"
    for path in (f"{prefix}/", f"{prefix}/index.html"):
        response = client.get(path)
        assert response.status_code == 404
        assert b"Report access is unavailable" not in response.content
