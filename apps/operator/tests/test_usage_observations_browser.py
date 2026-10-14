from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connections
from playwright.sync_api import expect, sync_playwright

from apps.core.operator_probe_worker import ProbeSupervisor
from apps.core.models import AuditLog
from apps.datasources.models import DataSource
from apps.orgs.models import OrgQuota

from apps.core.tests.test_operator_probe_process import probe_process_env

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("chromium", 390), ("webkit", 1440), ("webkit", 390)])
@pytest.mark.parametrize("javascript", [False, True])
def test_usage_actual_process_preserves_quota_draft_and_native_fallback(engine, width, javascript, probe_process_env, org, live_server, settings):
    actor = get_user_model().objects.create_user(email="usage-browser@portal.local", password="Operator-browser-42", is_operator_flag=True)
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "file.csv").write_bytes(b"x" * 2 * 1048576)
    DataSource.objects.create(org=org, name="file", type="file", config={"upload": True, "path": "file.csv"})
    def measure():
        try: ProbeSupervisor(probe_process_env).tick()
        finally: connections.close_all()
    def state():
        try: return OrgQuota.objects.get(org_id=org.pk).max_storage_mb, AuditLog.objects.filter(action="operator.usage.refresh", org_id=org.pk).count()
        finally: connections.close_all()
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
        path = f"/operator/orgs/{org.slug}/"
        page.goto(live_server.url + "/login?next=" + path)
        page.get_by_label("Email", exact=True).fill(actor.email)
        page.get_by_label("Password", exact=True).fill("Operator-browser-42")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.wait_for_url("**" + path)
        quota = page.locator('form[data-settings-save]')
        quota.get_by_label("Max uploaded files (MB)", exact=True).fill("4096")
        quota.get_by_role("button", name="Save quota", exact=True).click()
        if javascript: expect(quota).to_have_attribute("data-settings-state", "saved")
        else: page.wait_for_url("**" + path)
        expect(page.locator('[data-observation-value]')).to_contain_text("Unknown")
        if javascript:
            quota.get_by_label("Notes", exact=True).fill("Preserved quota draft")
        page.get_by_role("button", name="Measure uploaded files", exact=True).click()
        expect(page.locator('[data-observation-status]')).to_contain_text("accepted")
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(measure).result(timeout=15)
        if javascript:
            expect(page.locator('[data-observation-value]')).to_contain_text("2.0 MB", timeout=15000)
            expect(quota.get_by_label("Notes", exact=True)).to_have_value("Preserved quota draft")
            quota.get_by_label("Notes", exact=True).fill("")
        else:
            page.get_by_role("link", name="Read latest observation", exact=True).click()
            expect(page.locator('[data-observation-value]')).to_contain_text("2.0 MB")
        page.reload()
        expect(page.locator('[data-observation-value]')).to_contain_text("last observed")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(state).result() == (4096, 1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        context.close(); browser.close()
