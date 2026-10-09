"""Browser checks for the explicit-save report policy forms.

These render the real templates without database fixtures and intercept the API
calls in the page, so the tests cover the client-side save contract only.
"""

import json
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.template.loader import get_template
from django.test import RequestFactory

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error, sync_playwright  # noqa: E402


def _render(template_name, context):
    request = RequestFactory().get("/settings")
    request.org = SimpleNamespace(name="Acme", slug="acme")
    html = get_template(template_name).render({"request": request, "csrf_token": "test-csrf-token", **context})
    helper = (settings.BASE_DIR / "static" / "settings-forms.js").read_text(encoding="utf-8")
    return html + f"<script>{helper}</script>"


def with_browser(test):
    def run():
        with sync_playwright() as playwright:
            try:
                instance = playwright.chromium.launch(headless=True)
            except Error as exc:  # pragma: no cover - depends on local browser install
                pytest.skip(f"Chromium unavailable: {exc}")
            try:
                test(instance)
            finally:
                instance.close()
    return run


def _page(browser, html, responses):
    page = browser.new_page()
    page.set_default_timeout(5000)
    requests = []

    def route(route):
        request = route.request
        if request.resource_type == "document":
            route.fulfill(body=html, content_type="text/html")
            return
        if "/api/" not in request.url:
            route.fulfill(status=204)
            return
        assert request.method == "POST"
        assert request.headers["x-trellum-form"] == "1"
        assert request.headers["accept"] == "application/json"
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data;")
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + (request.post_data_buffer or b"")
        )
        fields = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode()
            for part in message.iter_parts()
        }
        assert fields.pop("csrfmiddlewaretoken") == "test-csrf-token"
        requests.append(fields)
        status, body = responses.pop(0)
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    page.route("http://settings.test/**", route)
    page.goto("http://settings.test/", wait_until="domcontentloaded")
    return page, requests


@with_browser
def test_share_edits_wait_for_save_and_send_one_payload(browser):
    html = _render(
        "reports/org_share_settings.html",
        {"org": SimpleNamespace(name="Acme", slug="acme"), "policy": SimpleNamespace(
            share_links_enabled=False, require_password=False,
            embed_links_enabled=False, max_expiry_days=None,
        )},
    )
    page, requests = _page(browser, html, [(200, {"ok": True})])
    page.locator("#reportSharePolicyToggle").check()
    page.locator("#reportSharePolicyRequirePassword").check()
    page.locator("#reportSharePolicyEmbed").check()
    page.locator("#reportSharePolicyMaxExpiry").fill("30")
    page.wait_for_timeout(50)
    assert requests == []
    assert page.locator('[data-settings-dirty-status]').text_content() == 'Unsaved changes'
    page.locator("#reportSharePolicyForm").evaluate("""form => {
      const button = form.querySelector('button[type=submit]');
      form.requestSubmit(button);
      form.requestSubmit(button);
    }""")
    page.wait_for_function("document.querySelector('[data-settings-status]').textContent === 'Saved.'")
    assert page.locator('[data-settings-dirty-status]').text_content() == 'Saved.'
    assert not page.locator('#reportSharePolicyForm').evaluate("form => form.classList.contains('is-dirty')")
    assert requests == [{
        "enabled": "on", "require_password": "on",
        "embed_links_enabled": "on", "max_expiry_days": "30",
    }]


@with_browser
def test_share_failure_keeps_edits_and_retry_succeeds(browser):
    html = _render(
        "reports/org_share_settings.html",
        {"org": SimpleNamespace(name="Acme", slug="acme"), "policy": SimpleNamespace(
            share_links_enabled=False, require_password=False,
            embed_links_enabled=False, max_expiry_days=None,
        )},
    )
    page, requests = _page(browser, html, [(400, {"error": "invalid max_expiry_days"}), (200, {"ok": True})])
    page.locator("#reportSharePolicyToggle").check()
    page.locator("#reportSharePolicyMaxExpiry").fill("45")
    page.locator("#reportSharePolicyForm button[type=submit]").click()
    page.wait_for_function("document.querySelector('[data-settings-status]').textContent.includes('invalid max_expiry_days')")
    assert page.locator("#reportSharePolicyToggle").is_checked()
    assert page.locator("#reportSharePolicyMaxExpiry").input_value() == "45"
    assert page.locator('#reportSharePolicyForm').evaluate("form => form.classList.contains('is-dirty')")
    assert requests == [{"enabled": "on", "max_expiry_days": "45"}]
    page.locator("#reportSharePolicyForm button[type=submit]").click()
    page.wait_for_function("document.querySelector('[data-settings-status]').textContent === 'Saved.'")
    assert len(requests) == 2 and requests[0] == requests[1]


@with_browser
def test_live_query_blank_and_invalid_input(browser):
    html = _render(
        "reports/org_live_query_settings.html",
        {"org": SimpleNamespace(name="Acme", slug="acme"),
         "rate_limit_per_minute": 30, "default_rate_limit_per_minute": 30},
    )
    page, requests = _page(browser, html, [(200, {"ok": True}), (200, {"ok": True})])
    field = page.locator("#liveQueryPolicyRateLimit")
    field.fill("12.5")
    page.locator("#liveQueryPolicyForm button[type=submit]").click()
    page.wait_for_timeout(50)
    assert requests == []
    field.fill("")
    page.locator("#liveQueryPolicyForm button[type=submit]").click()
    page.wait_for_function("document.querySelector('[data-settings-status]').textContent === 'Saved.'")
    assert requests == [{"rate_limit_per_minute": ""}]
    field.fill("0")
    page.locator("#liveQueryPolicyForm button[type=submit]").click()
    page.wait_for_function("document.querySelector('[data-settings-status]').textContent === 'Saved.'")
    assert requests[-1] == {"rate_limit_per_minute": "0"}
