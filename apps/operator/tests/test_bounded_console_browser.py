"""Live Django/PostgreSQL operator discovery and quota saves; no API mocks.

Static assets alone are served from this checkout for pytest's live server.
Run only in a disposable PostgreSQL environment after runtime coordination.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import time
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, connections
from playwright.sync_api import expect, sync_playwright

from apps.core.models import AuditLog
from apps.orgs.models import Organization, OrgMembership, OrgQuota
from apps.runner.models import WorkerHeartbeat
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db(transaction=True)


def database_read(function):
    def read():
        try:
            return function()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result()


@pytest.fixture
def console_data(settings, tmp_path):
    assert connection.vendor == "postgresql", "operator browser gate requires disposable PostgreSQL"
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()
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
    WorkerHeartbeat.objects.create(worker_id="browser-coordinator", role="coordinator")
    return operator, target


@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("chromium", 390), ("webkit", 1440), ("webkit", 390)])
@pytest.mark.parametrize("javascript", [False, True])
def test_native_discovery_independent_sections_and_quota(
    engine, width, javascript, console_data, live_server, settings, tmp_path,
):
    operator, org = console_data
    host = live_server.url
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        context = browser.new_context(
            java_script_enabled=javascript, viewport={"width": width, "height": 900},
            reduced_motion="reduce",
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def tap(locator):
            if engine == "webkit" and not javascript:
                # Headless WebKit without JavaScript does not deliver the
                # animation frames Playwright's stability check waits for (this
                # also fails at the test's own introducing commit). Poll the
                # box until the stylesheet-driven layout settles, then click;
                # the expectations after each click still prove the navigation.
                previous, steady = None, 0
                for _ in range(100):
                    box = locator.bounding_box()
                    steady = steady + 1 if box is not None and box == previous else 0
                    if steady >= 3:
                        break
                    previous = box
                    time.sleep(0.1)
                locator.click(force=True)
            else:
                locator.click()

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
        expect(page.get_by_role("heading", name="Organizations", exact=True)).to_be_visible()
        expect(page.locator(".ui-table tbody tr")).to_have_count(25)
        tap(page.get_by_role("link", name="Next organizations", exact=True))
        expect(page.get_by_role("navigation", name="Organizations pages")).to_contain_text("Page 2 of 3")
        org_search = page.get_by_role("form", name="Search organizations")
        org_search.get_by_label("Search organizations", exact=True).fill("Browser organization 059")
        tap(org_search.get_by_role("button", name="Search organizations", exact=True))
        expect(page.locator(".ui-table tbody tr")).to_have_count(1)
        tap(page.get_by_role("link", name=org.slug, exact=True))
        expect(page.get_by_role("heading", name=org.name, exact=True)).to_be_visible()
        expect(page.get_by_role("navigation", name="Studios pages")).to_contain_text("50 matching")
        expect(page.get_by_role("navigation", name="Members pages")).to_contain_text("100 matching")
        tap(page.get_by_role("link", name="Next members", exact=True))
        tap(page.get_by_role("link", name="Next studios", exact=True))
        expect(page.get_by_role("navigation", name="Members pages")).to_contain_text("Page 2 of 4")
        expect(page.get_by_role("navigation", name="Studios pages")).to_contain_text("Page 2 of 2")
        member_search = page.get_by_role("form", name="Search members")
        member_search.get_by_label("Search members", exact=True).fill("Person 099")
        tap(member_search.get_by_role("button", name="Search members", exact=True))
        expect(page.get_by_role("navigation", name="Studios pages")).to_contain_text("Page 2 of 2")
        expect(page.get_by_role("navigation", name="Members pages")).to_contain_text("1 matching")
        expect(page.get_by_text("browser-person-099@portal.local", exact=True)).to_be_visible()
        expect(page.locator("[data-settings-refresh=quota-usage]")).to_contain_text("Storage has not been measured.")
        assert page.locator("form[action*='/impersonate/']").count() == 1
        assert page.locator("form[action$='/pool']").count() == 25

        quota = page.locator("form[data-settings-save]")
        field = quota.get_by_label("Max concurrent builds", exact=True)
        field.fill("7")
        field.focus()
        field.scroll_into_view_if_needed()
        if javascript:
            before = field.evaluate("node => node.getBoundingClientRect().top")
            field.press("Enter")
            expect(quota).to_have_attribute("data-settings-state", "saved")
            assert field.evaluate("node => node.getBoundingClientRect().top") == pytest.approx(before, abs=2)
            expect(field).to_be_focused()
            quota.get_by_label("Notes", exact=True).fill("Keep this draft")
            page.once("dialog", lambda dialog: dialog.dismiss())
            tap(page.get_by_role("link", name="Previous studios", exact=True))
            expect(quota.get_by_label("Notes", exact=True)).to_have_value("Keep this draft")
            expect(page.get_by_role("navigation", name="Studios pages")).to_contain_text("Page 2 of 2")
            # Clear the draft before the deliberate native departure.
            quota.get_by_label("Notes", exact=True).fill("")
        else:
            tap(quota.get_by_role("button", name="Save quota", exact=True))
            page.wait_for_url(host + f"/operator/orgs/{org.slug}/")
            expect(page.get_by_label("Max concurrent builds", exact=True)).to_have_value("7")
        assert database_read(lambda: OrgQuota.objects.get(org_id=org.pk).max_concurrent_runs) == 7
        assert database_read(lambda: AuditLog.objects.filter(action="operator.org.quota", org_id=org.pk).count()) == 1
        page.reload()
        expect(page.get_by_label("Max concurrent builds", exact=True)).to_have_value("7")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("document.querySelectorAll('*').length") <= 1500

        page.goto(host + "/operator/")
        expect(page.get_by_role("navigation", name="Workers pages")).to_contain_text("101 total")
        tap(page.get_by_role("link", name="Next workers", exact=True))
        worker_search = page.get_by_role("form", name="Search workers")
        worker_search.get_by_label("Search workers", exact=True).fill("browser-worker-099")
        tap(worker_search.get_by_role("button", name="Search workers", exact=True))
        expect(page.get_by_text("browser-worker-099", exact=True)).to_be_visible()
        expect(page.get_by_role("navigation", name="Workers pages")).to_contain_text("1 matching")
        expect(page.get_by_role("navigation", name="Workers pages")).to_contain_text("101 total")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        artifacts = Path(os.environ.get("OPERATOR_BROWSER_ARTIFACTS", tmp_path))
        artifacts.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(artifacts / f"fleet-{engine}-{width}-js{int(javascript)}.png"), full_page=True)
        assert not errors
        context.close()
        browser.close()
