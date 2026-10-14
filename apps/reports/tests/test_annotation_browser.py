"""One real Chromium desktop pass over the annotation browser (native + enhanced)."""
import pytest

from .test_annotation_http import base, seeded  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_native_and_enhanced_browser_filter_and_page(live_server, login, seeded, org_admin):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    client = login(org_admin)
    client.get(base(seeded) + "/browse")
    url = live_server.url + base(seeded) + "/browse"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for javascript in (False, True):
                context = browser.new_context(java_script_enabled=javascript, viewport={"width": 1440, "height": 900})
                context.add_cookies([{"name": n, "value": c.value, "url": live_server.url} for n, c in client.cookies.items()])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url, wait_until="domcontentloaded")
                if not javascript:
                    assert page.locator("[data-event-id]").count() == 25
                    page.locator('button[data-type="campaign"]').click()  # native POST -> 303 -> GET
                    page.wait_for_url("**state=*")
                    assert "hidden" in page.locator('button[data-type="campaign"]').inner_text()
                    page.locator("[data-anno-next]").click()
                    assert page.locator("[data-event-id]").count() == 15
                else:
                    expect(page.locator('[data-anno-ready="true"]')).to_be_visible()
                    expect(page.locator("[data-anno-total]")).to_have_text("40 events")  # no saved intent: project defaults
                    page.locator('button[data-type="campaign"]').click()
                    expect(page.locator("[data-anno-total]")).to_have_text("60 events")
                    assert page.evaluate("JSON.parse(localStorage.getItem('anno-cal:%s/%s')).hiddenTypes.length" % (seeded.org.slug, seeded.slug)) == 0
                    page.locator('button[data-type="release"]').click()
                    expect(page.locator("[data-anno-total]")).to_have_text("40 events")
                    page.locator("[data-anno-next]").click()
                    expect(page.locator("[data-event-id]")).to_have_count(15)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.goto(url, wait_until="domcontentloaded")  # fresh page: saved intent is restored in bounded chunks
                    expect(page.locator("[data-anno-total]")).to_have_text("40 events")
                assert not errors
                context.close()
        finally:
            browser.close()
