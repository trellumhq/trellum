"""Published repository status reflects this instance's files immediately."""
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import OperationalError

from apps.core import roles
from apps.studios.models import StudioRepo
from apps.studios.startup import check_studio_files_at_startup

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def published_repo(studio):
    return StudioRepo.objects.create(
        studio=studio, repo_url="https://example.test/reports.git",
        last_synced_sha="a" * 40, remote_sha="a" * 40,
        publish_mode="manual", sync_interval_minutes=0,
    )


@pytest.mark.parametrize("suffix", ["/", "/analyses", "/operations", "/settings/repo"])
def test_page_opens_with_missing_files_status(client, org_admin, published_repo, suffix):
    studio = published_repo.studio
    client.force_login(org_admin)
    response = client.get(f"/s/{studio.org.slug}/{studio.slug}{suffix}")
    assert response.status_code == 200
    assert b"data-studio-files-status" in response.content
    assert b"Studio files missing." in response.content
    if suffix == "/settings/repo":
        assert b">Up to date<" not in response.content


def test_git_status_checks_files_without_mutating_repo(client, org_admin, published_repo):
    studio = published_repo.studio
    client.force_login(org_admin)
    response = client.get(f"/s/{studio.org.slug}/{studio.slug}/api/system/git/status")
    assert response.status_code == 200
    assert response.json()["files"]["state"] == "missing"
    assert response.json()["files"]["ready"] is False
    published_repo.refresh_from_db()
    assert published_repo.last_synced_sha == "a" * 40
    assert not published_repo.sync_requested
    assert not published_repo.last_error


def test_viewer_sees_generic_notice_without_git_access(client, member, grant_studio, published_repo):
    studio = published_repo.studio
    grant_studio(member, studio, roles.VIEWER)
    client.force_login(member)
    response = client.get(f"/s/{studio.org.slug}/{studio.slug}/")
    assert response.status_code == 200
    assert b"Studio files missing." in response.content
    assert published_repo.repo_url.encode() not in response.content
    assert client.get(f"/s/{studio.org.slug}/{studio.slug}/api/system/git/status").status_code == 403


def test_startup_records_missing_files_even_without_polling(published_repo):
    check_studio_files_at_startup()
    published_repo.refresh_from_db()
    assert published_repo.last_error.startswith("Studio files missing.")
    assert published_repo.last_synced_sha == "a" * 40
    assert not published_repo.sync_requested


def test_startup_preserves_operational_error(published_repo):
    published_repo.last_error = "Repository authentication failed"
    published_repo.save(update_fields=["last_error"])
    check_studio_files_at_startup()
    published_repo.refresh_from_db()
    assert published_repo.last_error == "Repository authentication failed"


def test_startup_clears_only_obsolete_file_status(published_repo):
    published_repo.last_error = "Studio files missing."
    published_repo.save(update_fields=["last_error"])
    with patch("apps.runner.studio_files.check_studio_files", return_value={
        "state": "ready", "ready": True, "message": "",
    }):
        check_studio_files_at_startup()
    published_repo.refresh_from_db()
    assert published_repo.last_error == ""


def test_startup_does_not_overwrite_a_concurrent_publish(published_repo):
    def inspect(repo):
        StudioRepo.objects.filter(pk=repo.pk).update(last_synced_sha="b" * 40)
        return {"state": "missing", "ready": False, "message": "Studio files missing."}

    with patch("apps.runner.studio_files.check_studio_files", side_effect=inspect):
        check_studio_files_at_startup()
    published_repo.refresh_from_db()
    assert published_repo.last_error == ""


def test_startup_tolerates_unavailable_database(caplog):
    with patch("apps.studios.models.StudioRepo.objects.exclude", side_effect=OperationalError):
        check_studio_files_at_startup()
    assert "database unavailable" in caplog.text


def test_web_entry_point_runs_file_check(settings):
    import runpy

    settings.CHECK_STUDIO_FILES_AT_STARTUP = True
    with patch("django.core.wsgi.get_wsgi_application"), patch(
        "apps.studios.startup.check_studio_files_at_startup",
    ) as check:
        runpy.run_module("trellum_portal.wsgi")
    check.assert_called_once_with()


@pytest.mark.parametrize("width,height", [
    (1440, 1000), pytest.param(390, 844, marks=pytest.mark.heavy),
])
def test_missing_files_notice_rendered_on_open(client, org_admin, published_repo, width, height):
    from playwright.sync_api import sync_playwright

    client.force_login(org_admin)
    studio = published_repo.studio
    prefix = f"/s/{studio.org.slug}/{studio.slug}"
    from apps.reports.models import Report

    Report.objects.create(studio=studio, slug="daily-summary", name="Daily summary")
    responses = {
        path: client.get(path) for path in (
            prefix + "/", prefix + "/settings/repo", prefix + "/api/registry",
        )
    }
    screenshots = ROOT / "artifacts" / "studio-files"
    screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})

        def serve(route):
            from urllib.parse import urlsplit

            path = urlsplit(route.request.url).path
            if path.startswith("/static/"):
                asset = ROOT / "static" / path.removeprefix("/static/")
                if asset.is_file():
                    route.fulfill(status=200, body=asset.read_bytes())
                    return
            if path in responses:
                response = responses[path]
                route.fulfill(status=response.status_code, content_type=response["Content-Type"], body=response.content)
                return
            route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("http://portal.test/**", serve)
        for name, suffix in (("reports", "/"), ("repository", "/settings/repo")):
            page.goto(f"http://portal.test{prefix}{suffix}")
            notice = page.locator("[data-studio-files-status]")
            assert notice.is_visible()
            assert "Studio files missing." in notice.inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(screenshots / f"{name}-missing-{width}.png"), full_page=True)
        browser.close()
