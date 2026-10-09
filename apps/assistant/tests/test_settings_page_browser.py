"""Connection-test feedback distinguishes provider access from saved readiness."""
import json

import pytest
from playwright.sync_api import sync_playwright

from apps.assistant import llm
from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, TextBlock
from apps.orgs.models import OrgAssistantConfig


@pytest.mark.django_db
@pytest.mark.parametrize(
    "enabled,model,reason",
    [
        (True, "claude-future-9", "Set input and output prices for model 'claude-future-9' in AI settings to enforce budgets, or leave both budgets blank."),
        (False, "claude-sonnet-4-6", "The AI assistant is disabled for this organization."),
    ],
)
def test_successful_connection_shows_readiness_blocker(
    login, org_admin, org, monkeypatch, enabled, model, reason
):
    OrgAssistantConfig.objects.create(
        org=org, enabled=enabled, api_key="sk-test-browser-secret", model=model,
        monthly_budget_usd=10 if model == "claude-future-9" else None,
    )
    calls = []
    monkeypatch.setattr(
        llm, "_anthropic_client",
        lambda config: FakeClient([FakeResponse([TextBlock("OK")])], calls),
    )
    client = login(org_admin)
    url = f"http://portal.test/orgs/{org.slug}/settings/assistant"
    path = f"/orgs/{org.slug}/settings/assistant"
    html = client.get(path).content.decode()
    result = client.post(path + "/test").json()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def respond(route):
                if route.request.url == url:
                    route.fulfill(content_type="text/html", body=html)
                elif route.request.url == url + "/test":
                    route.fulfill(content_type="application/json", body=json.dumps(result))
                else:
                    # This check exercises the settings page's inline behavior.
                    route.abort()

            page.route("**/*", respond)
            page.goto(url)
            page.locator("#assistant-test").click()
            page.locator("#assistant-test-result").wait_for(state="visible")
            page.wait_for_function(
                "document.getElementById('assistant-test-message').textContent.endsWith('answered.')"
            )
            assert page.locator("#assistant-test-message").inner_text() == f"{model} answered."
            assert page.locator("#assistant-test-hints").inner_text() == reason
            assert "sk-test-browser-secret" not in page.content()
            assert calls and errors == []
        finally:
            browser.close()
