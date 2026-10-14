from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connections
from playwright.sync_api import expect, sync_playwright

from apps.core.operator_probe_worker import ProbeSupervisor
from apps.core.models import AuditLog, OpsState
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


@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("webkit", 390)])
@pytest.mark.parametrize("release_at", ["pending", "accepted", "completed", "unconfirmed"])
def test_delayed_real_summary_retains_measurement_without_replay(engine, width, release_at, probe_process_env, org, live_server, settings):
    """Deliver a pre-admission Django summary after a real usage admission.

    Only delivery is held: quota persistence, admission/audit, authorized
    status lookup and the supervised measurement all use their real paths.
    """
    actor = get_user_model().objects.create_user(email="usage-race@portal.local", password="Operator-browser-42", is_operator_flag=True)
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "file.csv").write_bytes(b"x" * 2 * 1048576)
    DataSource.objects.create(org=org, name="file", type="file", config={"upload": True, "path": "file.csv"})
    path = f"/operator/orgs/{org.slug}/"

    def measure():
        try:
            ProbeSupervisor(probe_process_env).tick()
        finally:
            connections.close_all()

    def persisted():
        try:
            active = OpsState.objects.get(key="operator.probe").payload["active"]
            return (active["id"], OrgQuota.objects.get(org_id=org.pk).max_storage_mb,
                    AuditLog.objects.filter(action="operator.usage.refresh", org_id=org.pk).count())
        finally:
            connections.close_all()

    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        held = {}
        admissions = []

        def static(route):
            root = (settings.BASE_DIR / "static").resolve()
            asset = (root / urlparse(route.request.url).path.split("/static/", 1)[1]).resolve()
            if asset.is_relative_to(root) and asset.is_file():
                route.fulfill(path=str(asset))
            else:
                route.continue_()

        reads = []

        def delay_summary(route):
            # Hold only the one pre-admission read; any later legitimate read
            # (e.g. a retry once focus leaves the region) uses the real path.
            if route.request.method == "GET" and not route.request.is_navigation_request():
                reads.append(route.request.url)
                if "summary" not in held:
                    response = route.fetch()
                    assert response.status == 200
                    assert "No completed observation." in response.text()
                    assert "data-observation-id=" not in response.text()
                    held["summary"] = (route, response)
                    page.evaluate("window.summaryHeld = true")
                    return
            route.continue_()

        def delay_admission(route):
            admissions.append(route.request.method)
            response = route.fetch()
            assert response.status == 202
            held["admission"] = (route, response)
            page.evaluate("window.admissionHeld = true")

        page.route("**/static/**", static)
        page.goto(live_server.url + "/login?next=" + path)
        page.get_by_label("Email", exact=True).fill(actor.email)
        page.get_by_label("Password", exact=True).fill("Operator-browser-42")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.wait_for_url("**" + path)
        page.route("**" + path, delay_summary)
        page.route("**" + path + "usage/refresh", delay_admission)
        page.evaluate("""document.addEventListener('settings:updated', event => {
            if (event.detail.key === 'quota-usage') window.summaryReplaced = true;
        })""")
        quota = page.locator('form[data-settings-save]')
        quota.get_by_label("Max uploaded files (MB)", exact=True).fill("4096")
        quota.get_by_role("button", name="Save quota", exact=True).click()
        expect(quota).to_have_attribute("data-settings-state", "saved")
        page.wait_for_function("window.summaryHeld === true")
        summary_csrf = page.evaluate("""html => new DOMParser().parseFromString(html, 'text/html')
            .querySelector('form[data-observation-refresh] [name=csrfmiddlewaretoken]').value""", held["summary"][1].text())
        # Submit through the real form handler without focusing the button: a
        # pointer click that later blurs inside the summary region legitimately
        # retries (and supersedes) the in-flight read, which is not the stale
        # delivery under test.
        page.evaluate("""() => {
            const form = document.querySelector('form[data-observation-refresh]');
            form.requestSubmit(form.querySelector('button[type=submit]'));
        }""")
        page.wait_for_function("window.admissionHeld === true")
        operation_id = held["admission"][1].json()["action"]["id"]
        quota.get_by_label("Notes", exact=True).fill("Preserved neighboring draft")
        page.evaluate("window.positionBeforeSummary = scrollY; window.heightBeforeSummary = document.documentElement.scrollHeight")
        form = page.locator('form[data-observation-refresh]')

        if release_at == "pending":
            expect(form).to_have_attribute("aria-busy", "true")
            held["summary"][0].fulfill(response=held["summary"][1])
            expect(quota.locator('[data-settings-dirty-status]')).to_contain_text("Summary update waits")
            expect(page.locator('[data-observation-status]')).to_contain_text("Requesting measurement")
            held["admission"][0].fulfill(response=held["admission"][1])
        elif release_at == "unconfirmed":
            expect(page.locator('[data-observation-status]')).to_contain_text("outcome is unconfirmed", timeout=15000)
            expect(form).not_to_have_attribute("aria-busy", "true")
            held["summary"][0].fulfill(response=held["summary"][1])
            page.wait_for_function("window.summaryReplaced === true")
            expect(page.locator('[data-observation-status]')).to_contain_text("outcome is unconfirmed")
            expect(form.locator('[name=csrfmiddlewaretoken]')).to_have_value(summary_csrf)
            expect(page.get_by_role("button", name="Measure uploaded files", exact=True)).to_be_enabled()
        else:
            held["admission"][0].fulfill(response=held["admission"][1])

        if release_at != "unconfirmed":
            expect(page.locator('[data-observation-status]')).to_contain_text("accepted")
            expect(page.locator('[data-operator-observation]')).to_have_attribute("data-observation-id", operation_id)
            if release_at == "accepted":
                held["summary"][0].fulfill(response=held["summary"][1])
                expect(quota.locator('[data-settings-dirty-status]')).to_contain_text("Summary update waits")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(measure).result(timeout=15)
            expect(page.locator('[data-observation-value]')).to_contain_text("2.0 MB", timeout=15000)
            expect(form).not_to_have_attribute("aria-busy", "true")
            if release_at == "completed":
                nonce = form.locator('[name=request_key]').input_value()
                held["summary"][0].fulfill(response=held["summary"][1])
                page.wait_for_function("window.summaryReplaced === true")
                expect(form.locator('[name=request_key]')).to_have_value(nonce)
                expect(form.locator('[name=csrfmiddlewaretoken]')).to_have_value(summary_csrf)
            expect(page.locator('[data-operator-observation]')).to_have_attribute("data-observation-id", operation_id)
            expect(page.locator('[data-observation-value]')).to_contain_text("2.0 MB")
            expect(page.locator('[data-observation-status]')).to_contain_text("completed")
            expect(page.get_by_role("button", name="Measure uploaded files", exact=True)).to_be_enabled()

        expect(quota.get_by_label("Notes", exact=True)).to_have_value("Preserved neighboring draft")
        expect(quota.get_by_label("Notes", exact=True)).to_be_focused()
        # Replacing the summary must not jump the page. Browser scroll anchoring may
        # shift scrollY by at most the height the real measurement adds/removes.
        assert page.evaluate("""() => Math.abs(scrollY - window.positionBeforeSummary)
            <= Math.max(2, Math.abs(document.documentElement.scrollHeight - window.heightBeforeSummary))""")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(persisted).result() == (operation_id, 4096, 1)
        assert len(admissions) == 1
        assert len(reads) == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        context.close(); browser.close()
