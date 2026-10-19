"""Real-browser group pages: the per-studio access cards save through the shared settings form."""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db(transaction=True)


def _static(settings):
    def route(request_route):
        asset = settings.BASE_DIR / "static" / urlparse(request_route.request.url).path.split("/static/", 1)[1]
        if asset.is_file():
            request_route.fulfill(path=str(asset))
        else:
            request_route.continue_()
    return route


# The picker search/save logic does not depend on width or engine, but the
# narrow popup is a different layout and WebKit a different engine: one run of
# each (desktop Chromium, mobile WebKit) covers both.
@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("webkit", 412)])
def test_group_access_card_picks_searches_and_saves(engine, width, live_server, login, org_admin, org, studio, make_group, settings, tmp_path):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.DATA_DIR = tmp_path / "data"
    reports = Report.objects.bulk_create([Report(studio=studio, name=f"Report {i:03}", slug=f"browser{i:03}", present_in_scan=True) for i in range(60)])
    group = make_group("Browser group")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.create(grant=grant, report=reports[-1])
    client = login(org_admin)
    host = live_server.url
    path = f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access"

    def assignments():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: set(ReportPermissionGrant.objects.filter(grant_id=grant.pk).values_list("report_id", flat=True))).result()

    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        page.route("**/static/**", _static(settings))
        page.goto(host + path)
        form = page.locator("[data-studio-access]").first
        expect(form).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        picker = form.locator("[data-recipient-picker]")
        picker.locator(".ui-picker-trigger").click()
        # Every report is already in the list; the browser filters instantly.
        expect(picker.locator(".recipient-picker-option:visible")).to_have_count(60)
        assert picker.get_by_role("button", name="Next", exact=True).count() == 0
        assert picker.locator(".ui-picker-pager").count() == 0
        picker.locator(".ui-picker-search").fill("Report 000")
        expect(picker.locator(".recipient-picker-option:visible")).to_have_count(1)
        picker.get_by_role("checkbox", name="Report 000", exact=False).check()
        picker.locator(".ui-picker-search").fill("059")
        expect(picker.get_by_role("checkbox", name="Report 059", exact=False)).to_be_visible()
        expect(picker.get_by_role("checkbox", name="Report 000", exact=False)).not_to_be_visible()
        page.keyboard.press("Escape")
        assert page.locator("[name=reports]:checked").count() == 2
        assert assignments() == {reports[-1].pk}
        save = form.get_by_role("button", name="Save changes", exact=True)
        save.scroll_into_view_if_needed()
        saved_y = page.evaluate("scrollY")
        save.click()
        expect(form.locator("[data-settings-dirty-status]")).to_contain_text("Studio access updated.")
        expect(save).to_be_focused()
        assert abs(page.evaluate("scrollY") - saved_y) <= 2
        expect(page).to_have_url(host + path)
        assert assignments() == {reports[0].pk, reports[-1].pk}
        # An unauthorized report identity stays a failed draft and writes nothing.
        form.evaluate("f => { const c=document.createElement('input'); c.type='checkbox'; c.name='reports'; c.value='999999999'; c.checked=true; f.append(c); TrellumSettingsForms.trackControls(f,[c]); c.dispatchEvent(new Event('input',{bubbles:true})); }")
        form.get_by_role("button", name="Save changes", exact=True).click()
        expect(form.locator("[data-settings-error]")).to_contain_text("Select a valid choice")
        assert assignments() == {reports[0].pk, reports[-1].pk}
        context.close()
        browser.close()


# A scrollWidth check: width changes the layout, the engine does not.
@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("chromium", 412)])
def test_group_pages_do_not_overflow_with_long_content(engine, width, live_server, login, org_admin, member, org, studio, make_group, attach_group, settings, tmp_path):
    from apps.accounts.models import User
    from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupMembership
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.DATA_DIR = tmp_path / "data"
    group = make_group("Long group name for the permission group overflow check")
    attach_group(member, group)
    sources = PermissionGroup.objects.bulk_create([PermissionGroup(org=org, name=f"Source group with a long descriptive name {i:03}", default_studio_role="viewer") for i in range(30)])
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(group=source, user=member) for source in sources])
    available = User.objects.bulk_create([User(email=f"available{i:03}.with.a.rather.long.address@example.test") for i in range(60)])
    OrgMembership.objects.bulk_create([OrgMembership(org=org, user=user, role="member") for user in available])
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"long{i:03}", name=f"Report {i:03} with a long descriptive title", audience="studio", present_in_scan=True) for i in range(60)])
    client = login(org_admin)
    host = live_server.url
    base = f"/orgs/{org.slug}/settings/groups/{group.pk}"
    paths = [f"/orgs/{org.slug}/settings/groups", base, base + "?tab=members", base + "?tab=access", base + f"?tab=effective&member={member.pk}", f"/s/{org.slug}/{studio.slug}/r/{reports[0].slug}/access"]
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        page.route("**/static/**", _static(settings))
        for index, path in enumerate(paths):
            page.goto(host + path)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
            assert page.locator("h1").count() == 1
            if index == 4:
                assert page.locator(".effective-source").first.locator("li").count() == 30
        context.close()
        browser.close()
