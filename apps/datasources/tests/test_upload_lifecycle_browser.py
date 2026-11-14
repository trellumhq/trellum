"""Rendered file controls keep uploads and clears on the current page."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from apps.datasources.models import DataSource


@pytest.mark.django_db
@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_rendered_file_replace_and_clear(login, org_admin, studio_tree, width):
    ds = DataSource.objects.create(studio=studio_tree, name="budget", type="file",
                                   config={"upload": True, "path": "data-sources/files/budget.xlsx"})
    target = studio_tree.project_root / ds.config["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old workbook")
    client = login(org_admin)
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}"
    url = base + "/settings/datasources"
    rendered = client.get(url)
    assert rendered.status_code == 200
    removed = client.post(base + "/api/datasources/budget/upload", {"action": "remove"})
    assert removed.status_code == 200
    ds.refresh_from_db()
    assert ds.config['path'] == '' and not target.exists()
    root = Path(__file__).resolve().parents[3]
    artifacts = root / ".artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / ".gitignore").write_text("*\n")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            path = urlparse(route.request.url).path
            if path.startswith("/static/"):
                asset = root / "static" / path.removeprefix("/static/")
                if asset.is_file():
                    route.fulfill(path=str(asset))
                else:
                    route.fulfill(status=404, body="")
            elif path.startswith("/_vendor/") or path == "/api/assistant/widget.js":
                route.fulfill(body="")
            elif route.request.method == "POST":
                route.fulfill(status=removed.status_code, content_type="application/json", body=removed.content)
            else:
                route.fulfill(content_type="text/html", body=rendered.content)

        page.route("**/*", serve)
        page.goto("http://datasources.test" + url)
        row = page.locator('#ds-row-budget')
        assert row.locator('[data-ds-filename]').text_content() == "budget.xlsx"
        assert row.get_by_text('Replace file', exact=True).is_visible()
        assert row.get_by_role('button', name='Remove file', exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(artifacts / f"upload-controls-{width}.png"), full_page=True)
        row.get_by_role('button', name='Remove file', exact=True).click()
        page.wait_for_function("document.querySelector('[data-ds-filename]').textContent === ''")
        assert row.get_by_text('Upload file', exact=True).is_visible()
        assert row.locator('[data-ds-remove]').is_hidden()
        assert not row.locator('[data-ds-download]').count()
        assert page.url.endswith(url)
        page.screenshot(path=str(artifacts / f"upload-empty-{width}.png"), full_page=True)
        assert not errors
        browser.close()
