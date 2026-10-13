"""Real HTTP group pages, native draft browse and shared enhanced saves."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
from urllib.parse import urlparse

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("chromium", 390), ("webkit", 1440), ("webkit", 390), ("chromium", 720), ("chromium", 195), ("webkit", 720), ("webkit", 195)])
def test_group_draft_browse_save_and_native_discovery(engine, width, live_server, login, org_admin, org, studio, make_group, settings, tmp_path):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.DATA_DIR = tmp_path / "data"
    reports = Report.objects.bulk_create([Report(studio=studio, name=f"Report {i:03}", slug=f"browser{i:03}", present_in_scan=True) for i in range(60)])
    group = make_group("Browser group")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.create(grant=grant, report=reports[-1])
    client = login(org_admin)
    host = live_server.url
    path = f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access&studio={studio.pk}"
    def assignments():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: set(ReportPermissionGrant.objects.filter(grant_id=grant.pk).values_list("report_id", flat=True))).result()
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        def static(route):
            asset = settings.BASE_DIR / "static" / urlparse(route.request.url).path.split("/static/", 1)[1]
            if asset.is_file():
                route.fulfill(path=str(asset))
            else:
                route.continue_()
        page.route("**/static/**", static)
        page.goto(host + path)
        form = page.locator("[data-studio-access]")
        expect(form).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        picker = form.locator("[data-recipient-picker]")
        picker.locator(".ui-picker-trigger").click()
        picker.get_by_role("checkbox", name="Report 000", exact=False).check()
        page.keyboard.press("Escape")
        assert page.locator("[name=reports]:checked").count() == 2
        form.get_by_role("button", name="Next reports").click()
        expect(page).to_have_url(host + path + "&report_page=2")
        form = page.locator("[data-studio-access]")
        assert page.locator("[name=reports]:checked").count() == 2
        assert assignments() == {reports[-1].pk}
        form.get_by_role("button", name="Save changes", exact=True).click()
        expect(form.locator("[data-settings-dirty-status]")).to_contain_text("Studio access updated.")
        assert assignments() == {reports[0].pk, reports[-1].pk}
        # Actual validation failure: unauthorized report IDs stay a failed draft.
        form.evaluate("f => { const c=document.createElement('input'); c.type='checkbox'; c.name='reports'; c.value='999999999'; c.checked=true; f.append(c); TrellumSettingsForms.trackControls(f,[c]); c.dispatchEvent(new Event('input',{bubbles:true})); }")
        form.get_by_role("button", name="Save changes", exact=True).click()
        expect(form.locator('[data-settings-error]')).to_contain_text("Select a valid choice")
        assert page.locator('[name=reports]:checked').count() == 3
        assert assignments() == {reports[0].pk, reports[-1].pk}
        artifacts = os.environ.get("TRELLUM_TEST_ARTIFACT_DIR")
        if artifacts:
            target = Path(artifacts); target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f"group-access-{engine}-{width}.png"), full_page=True)
        context.close()
        native = browser.new_context(java_script_enabled=False, viewport={"width": width, "height": 900})
        native.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = native.new_page()
        page.route("**/static/**", static)
        page.goto(host + path)
        page.locator('[name="report_q"]').fill("059")
        page.get_by_role("button", name="Search reports", exact=True).click()
        expect(page.get_by_role("checkbox", name="Report 059", exact=False)).to_be_checked()
        assert page.locator("[name=reports]:checked").count() == 2
        assert assignments() == {reports[0].pk, reports[-1].pk}
        native.close()
        browser.close()
