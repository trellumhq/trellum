"""Actual login/HTTP diagnostic UI; isolated PG and a real supervised child."""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connections
from playwright.sync_api import expect, sync_playwright

from apps.core.operator_probe_worker import ProbeSupervisor

from .test_operator_probe_process import probe_process_env

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("chromium", 390), ("webkit", 1440), ("webkit", 390)])
@pytest.mark.parametrize("javascript", [False, True])
def test_scoped_system_native_and_enhanced(engine, width, javascript, probe_process_env, live_server, settings):
    actor = get_user_model().objects.create_user(email="system-browser@portal.local", password="Operator-browser-42", is_operator_flag=True)
    def measure():
        try:
            ProbeSupervisor(probe_process_env).tick()
        finally:
            connections.close_all()
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        context = browser.new_context(java_script_enabled=javascript, viewport={"width": width, "height": 900})
        page = context.new_page()
        def static(route):
            root = (settings.BASE_DIR / "static").resolve()
            path = (root / urlparse(route.request.url).path.split("/static/", 1)[1]).resolve()
            if path.is_relative_to(root) and path.is_file(): route.fulfill(path=str(path))
            else: route.continue_()
        page.route("**/static/**", static)
        page.goto(live_server.url + "/login?next=/system")
        page.get_by_label("Email", exact=True).fill(actor.email)
        page.get_by_label("Password", exact=True).fill("Operator-browser-42")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.wait_for_url("**/system")
        expect(page.get_by_role("heading", name="System health", exact=True)).to_be_visible()
        expect(page.locator('[data-observation-status]')).to_contain_text("No completed observation")
        page.get_by_role("button", name="Refresh coordinator diagnostics", exact=True).click()
        expect(page.locator('[data-observation-status]')).to_contain_text("accepted")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(measure).result(timeout=15)
        if javascript:
            expect(page.locator('[data-observation-status]')).to_contain_text("completed", timeout=15000)
        else:
            page.get_by_role("link", name="Read latest observation", exact=True).click()
        expect(page.locator('[data-observation-checks]')).to_contain_text("coordinator")
        expect(page.get_by_text("Web-local mounts", exact=False)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        context.close(); browser.close()
