"""Real-route catalog smoke in both browser engines and native mode.

The comprehensive Gunicorn/PostgreSQL performance and lifecycle gate runs
separately with its serialized runtime lease.
"""
import pytest

from apps.reports.models import Report

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
@pytest.mark.parametrize("width", (1440, 390))
def test_native_and_enhanced_catalog_keep_authorized_pages(
    live_server, login, studio, org_admin, engine, width,
):
    from playwright.sync_api import expect, sync_playwright

    Report.objects.bulk_create([
        Report(studio=studio, slug=f"r-{index:03}", name=f"Report {index:03}",
               category=f"Category {index % 3}") for index in range(31)
    ])
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"a-{index:03}", kind=Report.KIND_ANALYSIS,
               name=f"Analysis {index:03}") for index in range(31)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        try:
            for javascript in (False, True):
                context = browser.new_context(java_script_enabled=javascript,
                                              viewport={"width": width, "height": 900})
                context.add_cookies([
                    {"name": name, "value": cookie.value, "url": live_server.url}
                    for name, cookie in client.cookies.items()
                ])
                page = context.new_page()
                errors = []
                requests = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.goto(live_server.url + base + "/", wait_until="domcontentloaded")
                expect(page.locator('[data-catalog-ready="true"]')).to_be_visible()
                assert page.locator('[data-catalog-rows] [data-catalog-id]').count() == 25
                assert page.locator('html').evaluate('(node) => node.scrollWidth <= innerWidth + 1')
                page.goto(live_server.url + base + "/analyses?page=2", wait_until="domcontentloaded")
                expect(page.locator('[data-catalog-ready="true"]')).to_be_visible()
                assert page.locator('[data-catalog-rows] [data-catalog-id]').count() == 6
                assert not any('/api/registry' in url for url in requests)
                assert not errors
                context.close()
        finally:
            browser.close()
