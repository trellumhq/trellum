"""Rendered grant editor uses the shared pickers and save contract."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.conf import settings
from playwright.sync_api import sync_playwright, expect
from apps.assistant.tests.test_source_grants_views import grant_editor  # noqa: F401


@pytest.mark.django_db
def test_editor_pickers_preserve_draft_focus_and_scroll(grant_editor, viewer):
    client, source, path = grant_editor
    html = client.get(path + f"?source={source.pk}").content.decode()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, saves = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        def route(route):
            parsed = urlparse(route.request.url)
            if parsed.path.startswith("/static/"):
                asset = settings.BASE_DIR / parsed.path.lstrip("/")
                if asset.is_file():
                    route.fulfill(path=str(asset))
                else:
                    route.fulfill(status=204)
            elif route.request.method == "POST":
                saves.append(route)
            elif parsed.path == path:
                route.fulfill(body=html, content_type="text/html")
            else:
                route.fulfill(status=204)
        page.route("**/*", route)
        try:
            page.goto("http://portal.test" + path + f"?source={source.pk}")
            expect(page.locator("h1")).to_have_text("Assistant source access")
            page.locator('[data-choice-picker] .ui-picker-trigger').click()
            expect(page.locator('[data-choice-picker] .ui-picker-popup')).to_be_visible()
            page.keyboard.press("Escape")
            picker = page.locator('[data-recipient-picker]')
            picker.locator('.ui-picker-trigger').click()
            picker.locator('input[name="users"][value="' + str(viewer.pk) + '"]').check()
            picker.get_by_role("button", name="Done", exact=True).click()
            page.locator('#id_relations').fill('analytics.sales')
            # The shared handler sends once; later edits stay dirty and retain focus.
            page.locator('#source-grant-form button[type="submit"]').click()
            page.wait_for_function("document.querySelector('#source-grant-form').getAttribute('aria-busy')==='true'")
            page.locator('#id_relations').fill('analytics.sales\nanalytics.returns')
            page.locator('#id_relations').focus()
            before = page.evaluate('scrollY')
            saves[0].fulfill(json={"ok": True, "message": "Source access saved.", "values": {"revision": 1}})
            expect(page.locator('#id_revision')).to_have_value('1')
            expect(page.locator('#id_relations')).to_have_value('analytics.sales\nanalytics.returns')
            expect(page.locator('#id_relations')).to_be_focused()
            assert abs(page.evaluate('scrollY') - before) < 2
            assert page.locator('#source-grant-form.is-dirty').count() == 1
            page.locator('#source-grant-form button[type="submit"]').click()
            page.wait_for_function("document.querySelector('#source-grant-form').getAttribute('aria-busy')==='true'")
            saves[1].fulfill(status=400, json={"ok": False, "message": "Correct the relation.", "errors": {"relations": ["Invalid relation"]}})
            expect(page.locator('#id_relations')).to_have_value('analytics.sales\nanalytics.returns')
            expect(page.get_by_text('Invalid relation', exact=True)).to_be_visible()
            artifacts = Path('/tmp/trellum-source-ui-review'); artifacts.mkdir(exist_ok=True)
            page.screenshot(path=str(artifacts / 'source-grants-desktop.png'), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            page.screenshot(path=str(artifacts / 'source-grants-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors
        finally:
            browser.close()
