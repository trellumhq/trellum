"""The system page measures and shows health by itself.

Real login, real HTTP and a real supervised coordinator child; nothing is
mocked. Only the coordinator's own tick is driven by the test, because no worker
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
from apps.core.models import OpsState
from apps.core.operator_probe_worker import ProbeSupervisor

from .browser_login import sign_in
from .test_operator_probe_process import probe_process_env

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


def test_system_page_measures_and_updates_without_a_click(probe_process_env, live_server, settings, tmp_path):
    actor = get_user_model().objects.create_user(
        email="system-browser@portal.local", password="Operator-browser-42", is_operator_flag=True)

    def measure():
        ProbeSupervisor(probe_process_env).tick()

    def waiting_for_the_coordinator():
        row = OpsState.objects.filter(key=observations.SLOT).first()
        active = row and row.payload.get("active")
        return bool(active and active["kind"] == "full-health" and active["state"] == "accepted")

    def let_the_last_check_age():
        """A minute-old check, admitted 28 s ago: the next admission must wait about 3 s."""
        health = OpsState.objects.get(key="operator.health")
        health.payload["completed_at"] = (timezone.now() - timedelta(seconds=70)).isoformat()
        health.save(update_fields=["payload"])
        slot = OpsState.objects.get(key=observations.SLOT)
        slot.payload["admitted"] = {"full-health": (timezone.now() - timedelta(seconds=28)).isoformat()}
        slot.save(update_fields=["payload"])

    def admitted(page):
        for _ in range(150):
            if in_thread(waiting_for_the_coordinator):
                return
            page.wait_for_timeout(100)
        raise AssertionError("the page did not ask for a measurement by itself")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Waiting for a running measurement or the 30 s gap is not an error: no
        # refused request may reach the console.
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

        status = page.locator("[data-observation-status]")
        checks = page.locator("[data-observation-checks]")
        # Nothing to press: the page measures on its own.
        expect(page.get_by_role("button", name="Refresh coordinator diagnostics")).to_have_count(0)
        expect(page.get_by_role("link", name="Read latest observation")).to_have_count(0)
        expect(status).to_have_text("Checking…")
        admitted(page)
        in_thread(measure)
        expect(checks.locator("tr", has_text="database")).to_be_visible(timeout=15000)
        expect(status).to_have_text("Checked just now")
        expect(status).to_have_attribute("title", re.compile(r"^Checked .+ on coordinator "))
        # The checks carry their real detail, shown as the page always showed them.
        expect(checks.locator("tr", has_text="database")).to_contain_text("PostgreSQL")
        expect(checks.locator("tr", has_text="database").locator(".ui-badge.ok")).to_have_text("ok")
        assert "Passed on coordinator" not in page.content()
        assert "Observed" not in page.inner_text("main")
        page.screenshot(path=str(Path(os.environ.get("OPERATOR_BROWSER_ARTIFACTS", tmp_path)) / "sys-system-1440.png"), full_page=True)

        # A minute later the page asks again. The server's thirty seconds between
        # measurements is a quiet wait, never an error to click away.
        in_thread(let_the_last_check_age)
        page.reload()
        expect(status).to_have_text("Checked 1 minute ago")
        assert "Wait thirty seconds" not in page.inner_text("main")
        admitted(page)
        in_thread(measure)
        expect(status).to_have_text("Checked just now", timeout=15000)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        context.close()
        browser.close()
