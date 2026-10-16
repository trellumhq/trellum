"""Real Settings templates use the shared save contract without losing drafts."""
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.core.models import InstanceConfig
from apps.core.tests.test_email_connections_browser import _rendered_page

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


def _screenshot_after_first_frame(page, path):
    """Capture a review screenshot once the page has presented a frame.

    ``domcontentloaded`` resolves before Chromium has produced the page's first
    frame, and ``Page.captureScreenshot`` fails with "Unable to capture
    screenshot" when asked for one that early (even a static page does this a
    few percent of the time). Two animation frames means the current layout was
    committed and painted, so the capture is deterministic.
    """
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r())))")
    page.screenshot(path=path, full_page=True)


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [390, 1280])
def test_moved_forms_keep_drafts_focus_scroll_and_newer_edits(engine, width, login, superuser):
    client = login(superuser)
    settings_page = client.get("/instance/settings")
    html = _rendered_page(settings_page.content.decode())
    health = _rendered_page(client.get("/system").content.decode())
    requests, pending = [], []
    headers = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}
    instance_data = {"action": "identity_settings", "instance_name": "Browser saved"}
    security_data = {**settings_page.context["security_form"].initial, "action": "security_settings"}
    replies = [
        client.post("/instance/settings", instance_data, **headers),
        client.post("/instance/settings", {**security_data, "session_idle_minutes": "-2"}, **headers),
        client.post("/instance/settings", {**security_data, "session_idle_minutes": "82"}, **headers),
    ]
    completed = []

    def complete_save():
        route = pending.pop()
        response = replies[len(completed)]
        completed.append(response)
        route.fulfill(status=response.status_code, body=response.content, content_type="application/json")

    def serve(route):
        request = route.request
        if request.method == "POST":
            requests.append((request.post_data_buffer, request.headers["content-type"]))
            pending.append(route)
        elif request.resource_type == "document" or request.headers.get("accept") == "text/html":
            body = health if request.url.endswith("/system") else html
            route.fulfill(body=body, content_type="text/html")
        else:
            route.fulfill(status=204)

    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 800})
        page.route("http://settings.test/**", serve)
        page.goto("http://settings.test/instance/settings", wait_until="domcontentloaded")
        expect(page.locator("#instance-settings-form")).to_have_attribute("data-settings-initialized", "1")
        expect(page.locator('.tl-console-nav-link[href="/instance/settings"]')).to_have_attribute("aria-current", "page")
        expect(page.locator('[data-console-page-title]')).to_have_text("Instance settings")
        assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth + 1")
        if engine == "chromium":
            output = ROOT / ".artifacts" / "instance-settings"
            output.mkdir(parents=True, exist_ok=True)
            _screenshot_after_first_frame(page, output / f"settings-{width}.png")
        page.evaluate("window.documentIdentity = document")
        page.locator("#id_instance_name").fill("Browser saved")
        # A malformed, unsaved limits draft must neither block nor join Identity save.
        page.locator("#id_max_upload_mb").fill("-1")
        expect(page.locator("#limits-settings-form")).to_have_class("is-dirty")
        page.locator("#id_session_idle_minutes").fill("81")
        page.locator("#id_session_idle_minutes").focus()
        position = page.evaluate("scrollY")
        page.locator("#instance-settings-form").evaluate("f => f.requestSubmit(f.querySelector('[type=submit]'))")
        expect(page.locator("#instance-settings-form")).to_have_attribute("data-settings-state", "saving")
        complete_save()
        expect(page.locator("#instance-settings-form")).to_have_attribute("data-settings-state", "saved")
        expect(page.locator("#id_session_idle_minutes")).to_have_value("81")
        expect(page.locator("#id_max_upload_mb")).to_have_value("-1")
        expect(page.locator("#limits-settings-form")).to_have_class("is-dirty")
        assert page.locator("#id_session_idle_minutes").evaluate("e => e === document.activeElement")
        assert abs(page.evaluate("scrollY") - position) <= 2
        assert page.evaluate("document === window.documentIdentity")

        # Server validation must retain the submitted form and neighboring draft.
        page.locator("#id_session_idle_minutes").fill("-2")
        page.locator("#security-settings-form").evaluate("f => { f.noValidate = true; f.requestSubmit(f.querySelector('[type=submit]')); }")
        expect(page.locator("#security-settings-form")).to_have_attribute("data-settings-state", "saving")
        complete_save()
        expect(page.locator("#security-settings-form")).to_have_attribute("data-settings-state", "error")
        expect(page.locator("#id_session_idle_minutes")).to_have_attribute("aria-invalid", "true")
        expect(page.locator("#id_session_idle_minutes")).to_have_value("-2")
        expect(page.locator("#id_instance_name")).to_have_value("Browser saved")
        assert page.locator("#id_session_idle_minutes").evaluate("e => e === document.activeElement")

        # Save the snapshot, then edit before its delayed response arrives.
        page.locator("#id_session_idle_minutes").fill("82")
        position = page.evaluate("scrollY")
        page.locator("#security-settings-form").evaluate("f => f.requestSubmit(f.querySelector('[type=submit]'))")
        expect(page.locator("#security-settings-form")).to_have_attribute("data-settings-state", "saving")
        page.locator("#id_session_idle_minutes").fill("83")
        page.locator("#security-settings-form").evaluate("f => f.requestSubmit(f.querySelector('[type=submit]'))")
        assert len(requests) == 3
        assert len(pending) == 1
        complete_save()
        expect(page.locator("#security-settings-form")).to_have_attribute("data-settings-state", "saved")
        expect(page.locator("#id_session_idle_minutes")).to_have_value("83")
        expect(page.locator("#security-settings-form")).to_have_class("is-dirty")
        expect(page.locator("#security-settings-form [role=status]")).to_contain_text("Newer changes are still unsaved")
        assert abs(page.evaluate("scrollY") - position) <= 2
        assert page.evaluate("document === window.documentIdentity")

        # Explicit sign-out still confirms before issuing any request.
        dialogs = []

        def answer(dialog):
            # Dismiss the sign-out confirmation; leaving with unsaved drafts must
            # ask first (beforeunload), and the test then deliberately leaves.
            dialogs.append(dialog.type)
            dialog.accept() if dialog.type == "beforeunload" else dialog.dismiss()

        page.on("dialog", answer)
        signout = page.locator('form:has(input[value="sign_everyone_out"])')
        signout.evaluate("f => f.requestSubmit(f.querySelector('[type=submit]'))")
        assert len(requests) == 3
        page.goto("http://settings.test/system", wait_until="domcontentloaded")
        assert dialogs == ["confirm", "beforeunload"], dialogs
        assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth + 1")
        assert page.locator("table[data-mobile-cards]").first.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
        assert page.locator("#instance-settings-form").count() == 0
        assert page.get_by_role("link", name="Open Fleet").is_visible()
        if engine == "chromium":
            _screenshot_after_first_frame(page, output / f"health-{width}.png")
        browser.close()

    # Replay the browser's actual multipart submissions through the same authorized view.
    # Django ORM stays outside Playwright's event loop; no async safety bypass is needed.
    for (body, content_type), expected in zip(requests, replies, strict=True):
        response = client.generic("POST", "/instance/settings", body, content_type=content_type, **headers)
        assert response.status_code == expected.status_code
        assert response.json() == expected.json()
    assert InstanceConfig.load().instance_name == "Browser saved"
    assert InstanceConfig.load().session_idle_minutes == 82
