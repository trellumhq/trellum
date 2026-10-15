"""The operator pages look and behave as they always did, on a live server.

Real login and PostgreSQL, a real supervised coordinator child for the
uploaded-files figure, and no API mocks. Static assets alone are served from
this checkout. Run only in a disposable PostgreSQL environment.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, connections
from playwright.sync_api import expect, sync_playwright

from apps.core import operator_observations as observations
from apps.core.models import AuditLog, OpsState
from apps.core.operator_probe_worker import ProbeSupervisor
from apps.core.tests.test_operator_probe_process import probe_process_env
from apps.datasources.models import DataSource
from apps.orgs.models import Organization, OrgMembership, OrgQuota
from apps.runner.models import WorkerHeartbeat
from apps.studios.models import Studio

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


@pytest.fixture
def console_data(probe_process_env, settings):
    assert connection.vendor == "postgresql", "operator browser gate requires disposable PostgreSQL"
    settings.TRELLUM_IMPERSONATION_ENABLED = True
    operator = get_user_model().objects.create_user(
        email="browser-operator@portal.local", password="Operator-browser-42", is_operator_flag=True,
    )
    organizations = Organization.objects.bulk_create([
        Organization(slug=f"browser-org-{i:03}", name=f"Browser organization {i:03}") for i in range(60)
    ])
    target = organizations[-1]
    Studio.objects.bulk_create([
        Studio(org=target, slug=f"browser-studio-{i:03}", name=f"Browser studio {i:03}") for i in range(50)
    ])
    users = get_user_model().objects.bulk_create([
        get_user_model()(email=f"browser-person-{i:03}@portal.local", name=f"Person {i:03}")
        for i in range(100)
    ])
    OrgMembership.objects.bulk_create([OrgMembership(org=target, user=user) for user in users])
    WorkerHeartbeat.objects.bulk_create([
        WorkerHeartbeat(worker_id=f"browser-worker-{i:03}", role="runner") for i in range(100)
    ])
    target.datasources_dir.mkdir(parents=True)
    (target.datasources_dir / "file.csv").write_bytes(b"x" * 2 * 1048576)
    DataSource.objects.create(org=target, name="file", type="file", config={"upload": True, "path": "file.csv"})
    return operator, target


def test_operator_pages_keep_their_layout_and_measure_uploads_by_themselves(
    console_data, probe_process_env, live_server, settings, tmp_path,
):
    operator, org = console_data
    host = live_server.url
    artifacts = Path(os.environ.get("OPERATOR_BROWSER_ARTIFACTS", tmp_path))

    def measure():
        ProbeSupervisor(probe_process_env).tick()

    def waiting_for_the_coordinator():
        row = OpsState.objects.filter(key=observations.SLOT).first()
        active = row and row.payload.get("active")
        return bool(active and active["kind"] == "upload-usage" and active["state"] == "accepted")

    def admitted(page):
        for _ in range(150):
            if in_thread(waiting_for_the_coordinator):
                return
            page.wait_for_timeout(100)
        raise AssertionError("the page did not ask for a measurement by itself")

    def no_controls(page):
        # The tables carry no search box, filter, pager or apply button.
        for locator in (page.locator("input[type=search]"), page.locator("main select:not([name=pool])"),
                        page.get_by_role("button", name="Search", exact=False),
                        page.get_by_role("link", name="Next", exact=False), page.get_by_role("link", name="Previous", exact=False)):
            expect(locator).to_have_count(0)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def static(route):
            root = (settings.BASE_DIR / "static").resolve()
            path = (root / urlparse(route.request.url).path.split("/static/", 1)[1]).resolve()
            if path.is_relative_to(root) and path.is_file():
                route.fulfill(path=str(path))
            else:
                route.continue_()
        page.route("**/static/**", static)

        page.goto(host + "/login?next=/operator/orgs/")
        page.get_by_label("Email", exact=True).fill(operator.email)
        page.get_by_label("Password", exact=True).fill("Operator-browser-42")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.wait_for_url("**/operator/orgs/")

        # Organizations: every row, as before.
        expect(page.get_by_role("heading", name="Organizations", exact=True)).to_be_visible()
        expect(page.locator(".ui-table tbody tr")).to_have_count(60)
        no_controls(page)
        page.screenshot(path=str(artifacts / "sys-operator-orgs-1440.png"), full_page=True)

        # One organization: every studio and member, and the uploaded-files figure
        # appears and updates without anyone pressing anything.
        page.get_by_role("link", name=org.slug, exact=True).click()
        expect(page.get_by_role("heading", name=org.name, exact=True)).to_be_visible()
        expect(page.locator("form[action$='/pool']")).to_have_count(50)
        expect(page.locator("form[action*='/impersonate/']")).to_have_count(100)
        expect(page.get_by_text("browser-person-099@portal.local", exact=True)).to_be_visible()
        no_controls(page)
        usage = page.locator("[data-settings-refresh=quota-usage]")
        expect(page.get_by_role("button", name="Measure uploaded files")).to_have_count(0)
        expect(page.get_by_role("link", name="Read latest observation")).to_have_count(0)
        expect(usage.locator("[data-observation-status]")).to_have_text("· measuring…")
        admitted(page)
        in_thread(measure)
        expect(usage.locator("[data-observation-value]")).to_have_text("2.0 MB", timeout=15000)
        expect(usage.locator("[data-observation-status]")).to_have_text("· measured just now")
        assert "Storage has not been measured" not in page.content()

        # Saving the quota refreshes the usage panel; the figure stays and nothing
        # is measured again, because the last measurement is fresh.
        quota = page.locator("form[data-settings-save]")
        field = quota.get_by_label("Max concurrent builds", exact=True)
        field.fill("7")
        field.press("Enter")
        expect(quota).to_have_attribute("data-settings-state", "saved")
        expect(usage.locator("[data-observation-value]")).to_have_text("2.0 MB")
        page.wait_for_timeout(1500)
        assert in_thread(lambda: OrgQuota.objects.get(org_id=org.pk).max_concurrent_runs) == 7
        assert in_thread(lambda: AuditLog.objects.filter(action="operator.usage.refresh", org_id=org.pk).count()) == 1
        assert in_thread(lambda: AuditLog.objects.filter(action="operator.org.quota", org_id=org.pk).count()) == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(artifacts / "sys-operator-org-detail-1440.png"), full_page=True)

        # Fleet: every live worker in one plain table.
        page.goto(host + "/operator/")
        expect(page.get_by_role("heading", name="Fleet", exact=True)).to_be_visible()
        expect(page.get_by_text("browser-worker-099", exact=True)).to_be_visible()
        expect(page.locator(".ui-table").first.locator("tbody tr")).to_have_count(101)
        no_controls(page)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(artifacts / "sys-operator-fleet-1440.png"), full_page=True)
        assert not errors
        context.close()
        browser.close()
