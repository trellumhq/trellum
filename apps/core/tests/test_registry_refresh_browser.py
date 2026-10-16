"""Real Django + PostgreSQL + Chromium: Operations 'Reload Registry' shows
Scheduled, then a terminal outcome once a worker runs the durable action."""
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from apps.core import roles
from apps.runner import maintenance_actions as maint
from apps.runner.models import StudioMaintenanceAction as Action

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.django_db(transaction=True)
def test_refresh_button_scheduled_then_terminal(
    live_server, studio_tree, make_user, org, grant_studio, login, write_report, monkeypatch,
):
    # Playwright's sync API runs an event loop on this thread; the ORM is used
    # here only to assert state, never concurrently.
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    dev = make_user("dev@demo.example", org=org)
    grant_studio(dev, studio_tree, roles.DEVELOPER)
    write_report("alpha")
    client = login(dev)
    url = f"{live_server.url}/s/{org.slug}/{studio_tree.slug}/operations"
    posts, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies([{
            "name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url,
        }])
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "registry/refresh" in r.url else None)
        page.goto(url, wait_until="domcontentloaded")
        button = page.locator('[data-ops-action="refresh"]')
        button.wait_for(timeout=15000)
        assert "Reload Registry" in button.inner_text()
        button.click()
        page.wait_for_function(
            "document.querySelector('[data-ops-action=refresh]').textContent.indexOf('Scheduled') >= 0",
            timeout=10000,
        )
        # 202 must never read as done: nothing succeeded yet, and the action is only admitted.
        action = Action.objects.get()
        assert action.state == "accepted"
        assert page.locator(".toast.success").count() == 0
        # Now a worker picks it up.
        worker = threading.Thread(target=lambda: maint.run_once("browser-worker"))
        worker.start()
        worker.join(30)
        page.wait_for_function(
            "document.querySelector('[data-ops-action=refresh]').textContent.indexOf('Reload Registry') >= 0",
            timeout=15000,
        )
        page.locator(".toast.success").first.wait_for(timeout=5000)
        assert "Registry refreshed" in page.locator(".toast.success").first.inner_text()
        output = ROOT / ".artifacts" / "registry-refresh"
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output / "operations-terminal.png")
        browser.close()
    action.refresh_from_db()
    assert action.state == "succeeded" and action.reports == 1
    assert len(posts) == 1, posts  # the POST was never replayed
    assert not errors, errors
