"""Admins explicitly recover an unavailable Private audience with disclosure."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.orgs.models import PermissionGroupGrant
from apps.orgs.tests.test_group_bounded_access_browser import _static
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("width", [1440, pytest.param(390, marks=pytest.mark.heavy)])
def test_disabled_private_access_retains_confirmation_and_allows_recovery(
    width, live_server, login, org_admin, org, studio, make_group, settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    report = Report.objects.create(studio=studio, slug="finding", name="Private finding", audience="private")
    group = make_group("Assigned viewers", grants=[(studio, "viewer")])
    grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
    ReportPermissionGrant.objects.create(grant=grant, report=report)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    client = login(org_admin)
    host = live_server.url
    path = f"/s/{org.slug}/{studio.slug}/r/{report.slug}/access"

    def stored_audience():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: Report.objects.get(pk=report.pk).audience).result()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        page.route("**/static/**", _static(settings))
        page.goto(host + path)
        form = page.locator("#reportAudienceForm")
        expect(form.locator("select")).to_be_enabled()
        expect(page.get_by_role("button", name="Remove report", exact=True)).to_be_enabled()
        expect(page.get_by_text("New Private audiences and assignments are unavailable")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot_dir = Path(".data-test/audience-recovery")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / f"private-{width}.png"), full_page=True)
        page.get_by_role("button", name="Remove report", exact=True).click()
        expect(page.get_by_role("button", name="Add report", exact=True)).to_be_disabled()
        assert stored_audience() == "private"
        form.locator("select").select_option("studio")
        confirmations = []

        def cancel(dialog):
            confirmations.append(dialog.message)
            dialog.dismiss()

        page.once("dialog", cancel)
        form.get_by_role("button", name="Save audience").click()
        assert stored_audience() == "private"
        assert "Viewers with access to the whole studio" in confirmations[0]
        page.once("dialog", lambda dialog: dialog.accept())
        form.get_by_role("button", name="Save audience").click()
        expect(form.locator("[data-settings-dirty-status]")).to_contain_text("audience saved")
        expect(form.get_by_role("button", name="Save audience")).to_be_focused()
        expect(page).to_have_url(host + path)
        expect(form.locator('option[value="private"]')).to_be_disabled()
        assert stored_audience() == "studio"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(screenshot_dir / f"studio-{width}.png"), full_page=True)
        context.close()
        browser.close()
