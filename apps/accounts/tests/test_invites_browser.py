"""Rendered invitation controls and in-place resend feedback."""
import re
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


def _inline_assets(html):
    def style(match):
        path = ROOT / "static" / match.group(1)
        return f"<style>{path.read_text()}</style>" if path.exists() else match.group(0)

    def script(match):
        path = ROOT / "static" / match.group(1)
        return f"<script>{path.read_text()}</script>" if path.exists() else match.group(0)

    html = re.sub(r'<link href="/static/([^"]+)" rel="stylesheet">', style, html)
    return re.sub(r'<script src="/static/([^"]+)"(?: defer)?></script>', script, html)


@pytest.mark.django_db
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_invitation_resend_keeps_neighboring_draft(width, mode, login, org_admin, org, monkeypatch):
    from apps.accounts.models import Invitation

    monkeypatch.setattr("apps.core.instance.mail_is_configured", lambda: True)
    send = Mock(return_value=1)
    monkeypatch.setattr("django.core.mail.send_mail", send)
    invite = Invitation.objects.create(org=org, email="invitee@example.test")
    client = login(org_admin)
    path = f"/orgs/{org.slug}/settings/invites"
    html = _inline_assets(client.get(path).content.decode())
    endpoint = f"{path}/{invite.pk}/resend"
    headers = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}
    success = client.post(endpoint, **headers)
    send.return_value = 0
    cache.delete(f"invite-resend:{invite.pk}")  # the resend cooldown is covered in test_invites.py
    failure = client.post(endpoint, **headers)
    responses = [success, failure]
    requests = []

    def serve(route):
        if route.request.method == "POST":
            requests.append(route.request.url)
            response = responses[len(requests) - 1]
            route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
        elif route.request.resource_type == "document":
            route.fulfill(body=html, content_type="text/html")
        else:
            route.fulfill(status=204)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
        page.add_init_script(f"localStorage.setItem('mgmt-theme', '{mode}')")
        page.route("http://invitations.test/**", serve)
        page.goto(f"http://invitations.test{path}", wait_until="domcontentloaded")
        choice = page.get_by_role("checkbox", name="Send invitation by email")
        expect(choice).to_be_checked()
        choice.focus()
        page.keyboard.press("Space")
        expect(choice).not_to_be_checked()
        page.get_by_label("Email", exact=True).fill("neighbor-draft@example.test")
        button = page.get_by_role("button", name="Resend email")
        button.scroll_into_view_if_needed()
        button.focus()
        before = page.evaluate("window.scrollY")
        button.evaluate("button => { button.click(); button.click(); }")
        expect(page.get_by_text("Invitation email submitted to the email provider. Inbox delivery is not confirmed.", exact=True)).to_be_visible()
        expect(page.get_by_label("Email", exact=True)).to_have_value("neighbor-draft@example.test")
        expect(choice).not_to_be_checked()
        assert page.url.endswith(path)
        assert abs(page.evaluate("window.scrollY") - before) < 2
        expect(button).to_be_focused()
        button.click()
        expect(page.get_by_text(re.compile("Invitation email could not be confirmed by the email provider"))).to_be_visible()
        expect(page.get_by_label("Email", exact=True)).to_have_value("neighbor-draft@example.test")
        assert len(requests) == 2
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=f"/tmp/invitation-controls-{width}-{mode}.png", full_page=True)
        browser.close()
