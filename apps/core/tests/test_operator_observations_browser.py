"""The system page measures and shows health by itself.

Real login, real HTTP and a real measurement child process; nothing is mocked.
Only the coordinator's own tick is driven by the test, because no worker
process runs beside the live server.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connections
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from apps.core import operator_observations as observations
from apps.core.models import AuditLog, OpsState
from apps.runner.models import WorkerHeartbeat

from .browser_login import sign_in

pytestmark = pytest.mark.django_db(transaction=True)


def in_thread(function):
    """Playwright's sync API owns this thread's event loop; use the database elsewhere."""
    def run():
        try:
            return function()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result(timeout=30)


def test_system_page_measures_and_updates_without_a_click(operator_child, live_server, settings, tmp_path):
    actor = get_user_model().objects.create_user(
        email="system-browser@portal.local", password="Operator-browser-42", is_operator_flag=True)
    WorkerHeartbeat.objects.create(worker_id="system-browser-coordinator", role="coordinator")

    def measure():
        return observations.tick()

    def let_the_last_check_age():
        """A check from a minute ago, which the page's own requests keep wanted."""
        health = OpsState.objects.get(key=observations.HEALTH)
        old = (timezone.now() - timedelta(seconds=70)).isoformat()
        health.payload.update(completed_at=old, attempted_at=old)
        health.save(update_fields=["payload"])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        requests = []
        page.on("request", lambda request: requests.append((request.method, urlparse(request.url).path)))
        # Nothing the page does by itself may be refused or fail.
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

        def static(route):
            root = (settings.BASE_DIR / "static").resolve()
            path = (root / urlparse(route.request.url).path.split("/static/", 1)[1]).resolve()
            if path.is_relative_to(root) and path.is_file():
                route.fulfill(path=str(path))
            else:
                route.continue_()
        page.route("**/static/**", static)
        sign_in(page, live_server.url, actor.email, "Operator-browser-42", next_path="/system")
        page.wait_for_url("**/system")
        expect(page.get_by_role("heading", name="System health", exact=True)).to_be_visible()
        requests.clear()  # signing in posts; the page itself must not

        status = page.locator("[data-observation-status]")
        checks = page.locator("[data-observation-checks]")
        # Nothing to press: the page waits for the coordinator and shows what it measured.
        expect(page.locator("[data-operator-observation]").get_by_role("button")).to_have_count(0)
        expect(status).to_have_text("Checking…")
        assert in_thread(measure) == observations.HEALTH
        expect(checks.locator("tr", has_text="database")).to_be_visible(timeout=15000)
        expect(status).to_have_text("Checked just now")
        expect(status).to_have_attribute("title", re.compile(r"^Checked "))
        # The checks carry their real detail, shown as the page always showed them.
        expect(checks.locator("tr", has_text="database")).to_contain_text("PostgreSQL")
        expect(checks.locator("tr", has_text="database").locator(".ui-badge.ok")).to_have_text("ok")
        assert "Observed" not in page.inner_text("main")
        page.screenshot(path=str(Path(os.environ.get("OPERATOR_BROWSER_ARTIFACTS", tmp_path)) / "sys-system-1440.png"), full_page=True)

        # A minute later the figure is stale; the page's own requests make the
        # coordinator measure again, and the new figure replaces the old one.
        in_thread(let_the_last_check_age)
        page.reload()
        expect(status).to_have_text("Checked 1 minute ago")
        assert in_thread(measure) == observations.HEALTH
        expect(status).to_have_text("Checked just now", timeout=15000)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Only reads: the page never posted anything, and nothing was audited.
        assert not [method for method, path in requests if method != "GET"]
        assert in_thread(lambda: AuditLog.objects.filter(action__startswith="operator.diagnostics").count()) == 0
        assert not errors
        context.close()
        browser.close()
