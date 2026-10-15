"""The Reports/Analyses dashboard in real browsers: main's controls over bounded reads.

The comprehensive Gunicorn/PostgreSQL performance and lifecycle gate runs
separately with its serialized runtime lease.
"""
import pytest

from apps.reports.models import Report

pytestmark = pytest.mark.django_db(transaction=True)


def _open(playwright, engine, live_server, client, width):
    browser = getattr(playwright, engine).launch(headless=True)
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_cookies([
        {"name": name, "value": cookie.value, "url": live_server.url}
        for name, cookie in client.cookies.items()
    ])
    page = context.new_page()
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    return browser, context, page, errors, requests


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
@pytest.mark.parametrize("width", (1440, 390))
def test_dashboard_shows_every_report_with_instant_controls(
    live_server, login, studio, org_admin, engine, width,
):
    from playwright.sync_api import expect, sync_playwright

    Report.objects.bulk_create([
        Report(studio=studio, slug=f"r-{index:03}", name=f"Report {index:03}",
               category=f"Category {index % 3}") for index in range(61)
    ])
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"a-{index:03}", kind=Report.KIND_ANALYSIS,
               name=f"Analysis {index:03}") for index in range(7)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser, context, page, errors, requests = _open(playwright, engine, live_server, client, width)
        try:
            page.goto(live_server.url + base + "/", wait_until="domcontentloaded")
            expect(page.locator('#content[data-catalog-ready="true"]')).to_be_attached()
            # No page break: all 61 reports at once, like the full registry.
            assert page.locator('#content tr[data-slug]').count() == 61
            assert page.locator('html').evaluate('(node) => node.scrollWidth <= innerWidth + 1')
            # Main's controls only: no Apply/Search buttons, no catalog forms.
            assert page.get_by_role("button", name="Apply", exact=True).count() == 0
            assert page.get_by_role("button", name="Search", exact=True).count() == 0
            assert page.locator('#content form, .summary-bar form').count() == 0
            # Sorting applies at once.
            page.select_option('#listSortSelect', 'name')
            first = page.locator('#content tr[data-slug]').first.get_attribute('data-slug')
            assert first == 'r-000'
            # Search filters at once, over the whole studio.
            if width < 768:
                page.locator('[data-console-search-toggle]').click()
            page.fill('#searchInput', 'Report 060')
            expect(page.locator('#content tr[data-slug]')).to_have_count(1)
            page.fill('#searchInput', '')
            expect(page.locator('#content tr[data-slug]')).to_have_count(61)
            page.goto(live_server.url + base + "/analyses", wait_until="domcontentloaded")
            expect(page.locator('#content[data-catalog-ready="true"]')).to_be_attached()
            assert page.locator('#content tr[data-slug]').count() == 7
            sliced = [url for url in requests if '/api/registry' in url]
            assert sliced and all('page=' in url for url in sliced), sliced
            assert not errors
        finally:
            context.close()
            browser.close()


def test_opening_a_report_uses_the_console_host_and_back_restores_the_catalog_scroll(
    live_server, login, studio, org_admin,
):
    from playwright.sync_api import expect, sync_playwright

    Report.objects.bulk_create([
        Report(studio=studio, slug=f"r-{index:03}", name=f"Report {index:03}") for index in range(61)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser, context, page, errors, requests = _open(playwright, "chromium", live_server, client, 1440)
        try:
            page.goto(live_server.url + base + "/?view=list", wait_until="domcontentloaded")
            expect(page.locator('#content[data-catalog-ready="true"]')).to_be_attached()
            page.wait_for_load_state("networkidle")  # favorites and deliveries have repainted the rows
            link = page.locator('#content tr[data-slug="r-040"] a.list-title')
            link.scroll_into_view_if_needed()
            page.evaluate("window.scrollBy(0, 120)")
            page.wait_for_timeout(300)  # let the host save the catalog scroll
            saved = page.evaluate("window.scrollY")
            assert saved > 200
            link.click()
            # Inside the console host, shell kept; not a bare full-page report.
            expect(page.locator('[data-console-report-host]')).to_be_visible()
            assert page.locator('.tl-console-header').is_visible()
            assert '/r/r-040/' in page.url and 'display=console' in page.url
            page.go_back()
            expect(page.locator('[data-console-catalog]')).to_be_visible()
            expect(page.locator('[data-console-report-host]')).to_be_hidden()
            page.wait_for_function("y => Math.abs(window.scrollY - y) <= 24", arg=saved)
            assert not errors
        finally:
            context.close()
            browser.close()


def test_hidden_catalog_cancels_unfinished_reads_and_repeats_them_on_return(
    live_server, login, studio, org_admin,
):
    from playwright.sync_api import expect, sync_playwright

    Report.objects.bulk_create([
        Report(studio=studio, slug=f"r-{index:03}", name=f"Report {index:03}") for index in range(5)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser, context, page, errors, requests = _open(playwright, "chromium", live_server, client, 1440)
        held, calls = [], []

        def registry(route):
            calls.append(route.request.url)
            if len(calls) == 1:
                held.append(route)  # the first read never answers
            else:
                route.continue_()

        page.route("**/api/registry?*", registry)
        try:
            page.goto(live_server.url + base + "/", wait_until="domcontentloaded")
            page.wait_for_function("() => !document.querySelector('#content tr[data-slug]')")
            while not calls:
                page.wait_for_timeout(50)
            # The console report host hides the catalog: unfinished work stops.
            page.evaluate("document.dispatchEvent(new CustomEvent('trellum:catalog-hide'))")
            assert page.locator('#content tr[data-slug]').count() == 0
            # Returning repeats the read once and announces it for scroll restoration.
            page.evaluate("window.__loading = 0; addEventListener('trellum:catalog-loading', () => window.__loading++)")
            page.evaluate("document.dispatchEvent(new CustomEvent('trellum:catalog-return'))")
            expect(page.locator('#content tr[data-slug]')).to_have_count(5)
            assert page.evaluate("window.__loading") == 1
            expect(page.locator('#content[data-catalog-ready="true"]')).to_be_attached()
            assert len(calls) == 2
            # A return without interrupted work reads nothing.
            page.evaluate("document.dispatchEvent(new CustomEvent('trellum:catalog-hide'))")
            page.evaluate("document.dispatchEvent(new CustomEvent('trellum:catalog-return'))")
            page.wait_for_timeout(300)
            assert len(calls) == 2
            assert not errors
        finally:
            for route in held:
                try:
                    route.abort()
                except Exception:  # noqa: BLE001 - the page may already have dropped it
                    pass
            context.close()
            browser.close()


@pytest.mark.heavy
def test_a_studio_beyond_the_preloaded_rows_offers_show_more_and_searches_the_server(
    live_server, login, studio, org_admin,
):
    from playwright.sync_api import expect, sync_playwright

    Report.objects.bulk_create([
        Report(studio=studio, slug=f"r-{index:04}", name=f"Report {index:04}") for index in range(560)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser, context, page, errors, requests = _open(playwright, "chromium", live_server, client, 1440)
        try:
            page.goto(live_server.url + base + "/", wait_until="domcontentloaded")
            expect(page.locator('#content[data-catalog-ready="true"]')).to_be_attached()
            assert page.locator('#content tr[data-slug]').count() == 500
            expect(page.locator('.catalog-more')).to_contain_text('Showing 500 of 560')
            # A match beyond the preloaded rows is found by the server after the pause.
            page.fill('#searchInput', 'Report 0559')
            expect(page.locator('#content tr[data-slug]')).to_have_count(1)
            page.fill('#searchInput', '')
            page.locator('[data-catalog-more]').click()
            expect(page.locator('#content tr[data-slug]')).to_have_count(560)
            assert page.locator('.catalog-more').count() == 0
            assert not errors
        finally:
            context.close()
            browser.close()
