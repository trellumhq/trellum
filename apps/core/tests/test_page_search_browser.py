"""Search lives in the page it searches; the header only jumps to a report.

Real Chromium against a live server: the catalog's own field, Ctrl+K, and the
header's "Jump to a report" list with its permissions and states.
"""
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.core import roles
from apps.reports.models import Report

pytestmark = pytest.mark.django_db(transaction=True)

JUMP = ".tl-console-jump"
MENU = "[data-console-jump-menu]"
FILTER = MENU + " .tl-console-scope-filter"


@pytest.fixture(autouse=True)
def scoped_access(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"


@pytest.fixture
def reports(studio):
    Report.objects.bulk_create([
        Report(studio=studio, slug="cart-funnel", name="Cart Funnel", category="Product"),
        Report(studio=studio, slug="conversion", name="Conversion", category="Product"),
        Report(studio=studio, slug="revenue", name="Revenue Overview", category="Finance"),
        Report(studio=studio, slug="where", name="Where we lose buyers", kind=Report.KIND_ANALYSIS),
        Report(studio=studio, slug="secret", name="Secret Plan", audience=Report.AUDIENCE_PRIVATE),
    ])


def _browse(live_server, client, *, width=1440, height=900):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_cookies([
        {"name": name, "value": cookie.value, "url": live_server.url} for name, cookie in client.cookies.items()
    ])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def close():
        context.close()
        browser.close()
        playwright.stop()

    return page, errors, close


def _open_catalog(page, live_server, base, path="/"):
    page.goto(live_server.url + base + path, wait_until="domcontentloaded")
    expect(page.locator('#content[data-catalog-ready="true"]')).to_be_attached()


def _studio_base(studio):
    return f"/s/{studio.org.slug}/{studio.slug}"


def test_invites_has_no_header_search_but_keeps_its_own(live_server, login, org_admin, org, studio, reports):
    client = login(org_admin)
    client.get(_studio_base(studio) + "/")  # remembers the studio, which used to add a header box everywhere
    page, errors, close = _browse(live_server, client)
    try:
        page.goto(live_server.url + f"/orgs/{org.slug}/settings/invites", wait_until="domcontentloaded")
        header = page.locator(".tl-console-header")
        assert header.locator("input:visible").count() == 0 and page.locator("#searchInput").count() == 0
        assert header.locator("[data-console-search], [data-console-search-toggle]").count() == 0
        own = page.locator("form.ui-list-toolbar input[name=q]")
        expect(own).to_be_visible()
        assert own.get_attribute("placeholder") == "Search pending invitations…"
        expect(page.locator(JUMP)).to_be_visible()
        # Ctrl+K goes to the field this page has; the jump list stays closed.
        page.keyboard.press("Control+k")
        assert own.evaluate("el => el === document.activeElement")
        expect(page.locator(MENU)).to_be_hidden()
        assert not errors
    finally:
        close()


def test_catalog_field_filters_instantly_and_ctrl_k_focuses_it(live_server, login, org_admin, studio, reports):
    client = login(org_admin)
    base = _studio_base(studio)
    client.get(base + "/")
    page, errors, close = _browse(live_server, client)
    try:
        _open_catalog(page, live_server, base)
        field = page.locator(".folder-rail #searchInput")
        expect(field).to_be_visible()
        assert page.locator(".tl-console-header #searchInput").count() == 0
        assert field.get_attribute("placeholder") == "Search reports…"
        # In the folder bar, before Sort and after the tabs, about 240px wide.
        # The catalog redraws the folder bar and fills the toolbar by script
        # after it reports ready, so measure all three in one step once each
        # is laid out, instead of reading boxes that a redraw can swap out.
        geometry = page.wait_for_function("""() => {
            const field = document.querySelector('.folder-rail #searchInput');
            const sort = document.querySelector('.catalog-toolbar select');
            const tabs = document.querySelector('#folderTabs');
            if (!field || !sort || !tabs) return null;
            const f = field.getBoundingClientRect(), s = sort.getBoundingClientRect(),
                t = tabs.getBoundingClientRect();
            if (!f.width || !s.width || !t.width) return null;
            return {fieldX: f.x, fieldWidth: f.width, sortX: s.x, tabsX: t.x};
        }""").json_value()
        assert round(geometry["fieldWidth"]) == 240
        assert geometry["fieldX"] + geometry["fieldWidth"] < geometry["sortX"]
        assert geometry["tabsX"] < geometry["fieldX"]

        page.keyboard.press("Control+k")
        assert field.evaluate("el => el === document.activeElement")
        expect(page.locator(MENU)).to_be_hidden()
        cards = page.locator("#content [data-slug]")
        expect(cards).to_have_count(4)  # reports only; the analysis is not here
        total = cards.count()
        field.press_sequentially("funnel")  # instant: no button, no wait for a pause
        expect(cards).to_have_count(1)
        assert page.locator("#catalogSearchCount").inner_text() == "1 of 4"
        field.fill("retention q3")
        expect(page.locator(".empty-state")).to_contain_text("No reports match “retention q3”.")
        assert page.locator("#catalogSearchCount").inner_text() == "0 of 4"
        page.keyboard.press("Escape")  # a field with text clears and applies at once
        assert field.input_value() == ""
        expect(cards).to_have_count(total)
        field.fill("retention q3")
        expect(page.locator(".empty-state")).to_be_visible()
        page.get_by_role("button", name="Clear search").click()
        expect(cards).to_have_count(total)
        assert field.input_value() == "" and field.evaluate("el => el === document.activeElement")
        expect(page.locator("#catalogSearchCount")).to_be_hidden()
        page.keyboard.press("Escape")  # an empty field gives up focus
        assert not field.evaluate("el => el === document.activeElement")
        assert not errors
    finally:
        close()


def test_jump_list_opens_a_report_in_the_console_host_and_back_returns(live_server, login, org_admin, studio, reports):
    client = login(org_admin)
    base = _studio_base(studio)
    client.get(base + "/")
    page, errors, close = _browse(live_server, client)
    try:
        _open_catalog(page, live_server, base)
        page.locator(JUMP + " > summary").click()  # the icon works on every page; Ctrl+K here focuses the field
        field = page.locator(FILTER)
        links = page.locator(MENU + " a:visible")
        expect(links).to_have_count(5)  # four reports and an analysis; an admin may open the private one too
        assert field.evaluate("el => el === document.activeElement")
        assert [t.split("\n")[0] for t in links.all_inner_texts()] == [
            "Cart Funnel", "Conversion", "Revenue Overview", "Secret Plan", "Where we lose buyers",
        ]
        assert links.nth(0).locator("span").inner_text() == "Report · Product"
        assert links.nth(4).locator("span").inner_text() == "Analysis"
        # Instant filtering, first match highlighted, arrows move, Enter opens.
        field.fill("product")
        expect(links).to_have_count(2)
        # The highlight is applied by script after each key; let expect() retry.
        highlighted = re.compile(r"\bis-highlighted\b")
        expect(links.nth(0)).to_have_class(highlighted)
        page.keyboard.press("ArrowDown")
        expect(links.nth(1)).to_have_class(highlighted)
        expect(links.nth(0)).not_to_have_class(highlighted)
        page.keyboard.press("Enter")
        expect(page.locator("[data-console-report-host]")).to_be_visible()
        assert page.locator(".tl-console-header").is_visible()
        assert page.locator(JUMP + " > summary").is_visible()  # still reachable from inside a report
        expect(page.locator(MENU)).to_be_hidden()
        assert "/r/conversion/" in page.url and "display=console" in page.url
        page.go_back()
        expect(page.locator("[data-console-catalog]")).to_be_visible()
        expect(page.locator("[data-console-report-host]")).to_be_hidden()
        assert "/r/" not in page.url
        assert not errors
    finally:
        close()


def test_ctrl_k_opens_the_jump_list_where_nothing_is_searchable(live_server, login, org_admin, studio, studio2, reports):
    client = login(org_admin)
    base = _studio_base(studio)
    client.get(base + "/")
    page, errors, close = _browse(live_server, client)
    try:
        page.goto(live_server.url + base + "/settings/members", wait_until="domcontentloaded")
        assert page.locator("[data-page-search]").count() == 0
        menu = page.locator(MENU)
        expect(menu).to_be_hidden()
        page.keyboard.press("Control+k")
        expect(menu).to_be_visible()
        assert page.locator(FILTER).evaluate("el => el === document.activeElement")
        links = page.locator(MENU + " a:visible")
        expect(links).to_have_count(5)
        # Reports and analyses are in; navigation, members and settings pages are not.
        assert "Where we lose buyers" in menu.inner_text() and "Settings" not in menu.inner_text()
        # No results: the shared empty state, nothing to open, Enter does nothing.
        page.keyboard.type("zzzz")
        expect(menu.locator("[data-jump-empty]")).to_have_text("No reports match.")
        expect(links).to_have_count(0)
        page.keyboard.press("Enter")
        assert page.url.endswith("/settings/members")
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        assert page.locator(JUMP + " > summary").evaluate("el => el === document.activeElement")
        # Closing forgets the query: the next Ctrl+K starts from the whole list.
        page.keyboard.press("Control+k")
        expect(menu).to_be_visible()
        assert page.locator(FILTER).input_value() == ""
        expect(links).to_have_count(5)
        # A studio with no reports says so instead of showing a blank list.
        page.goto(live_server.url + _studio_base(studio2) + "/settings/members", wait_until="domcontentloaded")
        page.keyboard.press("Control+k")
        expect(page.locator(MENU + " [data-jump-empty]")).to_have_text("No reports or analyses to open yet.")
        # Choosing a result opens that report's page, with Back returning here.
        page.goto(live_server.url + base + "/settings/members", wait_until="domcontentloaded")
        page.keyboard.press("Control+k")
        page.keyboard.type("cart")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/r/cart-funnel/index\.html\?display=console$"), wait_until="commit")
        page.go_back(wait_until="domcontentloaded")
        assert page.url.endswith("/settings/members")
        assert not errors
    finally:
        close()


def test_a_viewer_cannot_see_unauthorized_reports_in_the_jump_list(
    live_server, login, make_user, grant_studio, org, studio, reports
):
    viewer = make_user("viewer@demo.example", org=org)
    grant_studio(viewer, studio, roles.VIEWER)
    client = login(viewer)
    base = _studio_base(studio)
    client.get(base + "/")
    page, errors, close = _browse(live_server, client)
    try:
        # A viewer of a studio with a private report sees the catalog only; the icon works there.
        _open_catalog(page, live_server, base)
        page.locator(JUMP + " > summary").click()
        links = page.locator(MENU + " a:visible")
        expect(links).to_have_count(4)  # the private report is not offered
        assert "Secret Plan" not in page.locator(MENU).inner_text()
        page.keyboard.type("secret")
        expect(links).to_have_count(0)
        expect(page.locator(MENU + " [data-jump-empty]")).to_have_text("No reports match.")
        # The server refuses it too, whatever the browser asks.
        answer = page.evaluate(
            "fetch(%r + '/api/jump?q=secret', {credentials: 'same-origin'}).then(r => r.json())" % base
        )
        assert answer["results"] == []
        assert not errors
    finally:
        close()


def test_the_jump_slot_does_not_move_between_pages(live_server, login, org_admin, org, studio, reports):
    client = login(org_admin)
    base = _studio_base(studio)
    client.get(base + "/")
    page, errors, close = _browse(live_server, client)
    boxes = {}
    try:
        for name, path in (
            ("catalog", base + "/"), ("metrics", base + "/metrics"), ("invites", f"/orgs/{org.slug}/settings/invites"),
            ("members", base + "/settings/members"), ("analyses", base + "/analyses"),
        ):
            page.goto(live_server.url + path, wait_until="load")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(400)  # room for the assistant widget to settle into its slot
            boxes[name] = page.evaluate(
                """() => ['.tl-console-jump', '.tl-console-docs-link', '.tl-console-account', '.tl-console-assistant-slot']
                  .map(s => { const r = document.querySelector(s).getBoundingClientRect(); return [s, r.x, r.y, r.width, r.height]; })"""
            )
        reference = boxes["catalog"]
        for name, box in boxes.items():
            assert box == reference, (name, box, reference)
        assert not errors
    finally:
        close()
