from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.alerts.tests.test_views import _form

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("success", [True, False])
def test_rendered_alert_edit_preserves_scroll_focus_draft_and_duplicate_submit(rule, login, success):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    html = html.replace('<form method="post" id="alertForm"',
                        '<div style="height:900px"></div><form method="post" id="alertForm"')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("https://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        pending = []
        page.route("https://example.test" + url, lambda route: pending.append(route))
        page.locator('[name="notification_policy"]').select_option("transition")
        page.locator('[name="instructions"]').fill("EUR net drain over 48 hours")
        page.locator('[name="instructions"]').focus()
        page.evaluate("window.marker = document; window.scrollTo(0, 850)")
        before = page.evaluate("scrollY")
        page.locator("#alertForm").evaluate("f => { f.requestSubmit(); f.requestSubmit(); }")
        page.wait_for_timeout(50)
        assert len(pending) == 1
        if success:
            pending[0].fulfill(json={"ok": True, "message": "Saved."})
            expect(page.locator("#alertForm [data-settings-dirty-status]")).to_have_text("Saved.")
        else:
            pending[0].fulfill(status=400, json={"ok": False, "message": "Invalid draft.",
                                               "errors": {"instructions": ["Specify coverage."]}})
            expect(page.locator('[name="instructions"]')).to_have_attribute("aria-invalid", "true")
        assert page.evaluate("document === window.marker")
        assert abs(page.evaluate("scrollY") - before) < 2
        assert page.evaluate("document.activeElement.name") == "instructions"
        assert page.locator('[name="notification_policy"]').input_value() == "transition"
        assert page.locator('[name="instructions"]').input_value() == "EUR net drain over 48 hours"
        browser.close()


def test_enhanced_alert_edit_and_native_save_share_validation(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    client = login(rule.created_by)
    enhanced = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}
    response = client.post(url, _form(rule, notification_policy="transition"), **enhanced)
    assert response.status_code == 200 and response.json()["ok"]
    rule.refresh_from_db()
    assert rule.notification_policy == "transition"
    response = client.post(url, _form(rule, notification_policy="invalid"), **enhanced)
    assert response.status_code == 400 and "notification_policy" in response.json()["errors"]
    native = client.post(url, _form(rule, notification_policy="invalid"))
    assert native.status_code == 200 and "notification_policy" in native.context["form"].errors
    rule.refresh_from_db()
    assert rule.notification_policy == "transition"


def test_canceling_delivery_reconciliation_sends_no_request(rule, login):
    from apps.alerts.tests.test_condition_semantics import unresolved_claim
    unresolved_claim(rule)
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("https://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://example.test" + url)
        requests = []
        page.on("request", lambda request: requests.append(request.method))
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.get_by_role("button", name="Confirm nothing was accepted").click()
        assert "POST" not in requests and page.url == "https://example.test" + url
        browser.close()
