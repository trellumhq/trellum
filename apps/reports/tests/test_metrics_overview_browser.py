"""The Metrics Overview list as a person sees it: one instant filter box, one
usage block per card, no banners or manual controls. Real Django, PostgreSQL
and static assets behind a live server; Chromium desktop."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.core import roles
from apps.reports.models import Report
from apps.reports.scan import sync_studio_metrics, sync_studio_registry
from apps.reports.source_summary import reconcile

pytestmark = pytest.mark.django_db(transaction=True)

METRICS = [
    {"name": "gross_revenue", "label": "Gross Revenue", "description": "IAP plus ad revenue.",
     "format": "currency", "version": 2, "agg": "sum", "column": "total_revenue", "tags": ["revenue"]},
    {"name": "net_revenue", "label": "Net Revenue", "description": "Revenue after store fees.",
     "format": "currency", "version": 1, "agg": "sum", "column": "net_revenue"},
    {"name": "dau", "label": "DAU", "description": "Daily active users.",
     "format": "number", "version": 1, "agg": "sum", "column": "dau", "owner": "growth@demo.example"},
]


@pytest.fixture
def overview(studio_tree, member, grant_studio, login, write_metrics_yaml, write_report, write_meta, live_server, settings):
    """Two reports claim two of three metrics: one current, one stale."""
    from django.utils import timezone

    write_metrics_yaml(METRICS)
    sync_studio_metrics(studio_tree)
    current = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash
    write_report("insert-coin", name="Insert Coin")
    write_report("monetization", name="Monetization")
    sync_studio_registry(studio_tree)
    now = timezone.now().isoformat()
    write_meta("insert-coin", last_run=now, last_status="success", metrics_used=[
        {"id": "gross_revenue", "definition_hash": current, "version": 2},
        {"id": "net_revenue", "definition_hash": "old", "version": 1}])
    write_meta("monetization", last_run=now, last_status="success", metrics_used=[
        {"id": "gross_revenue", "definition_hash": current, "version": 2}])
    for report in Report.objects.filter(studio=studio_tree):
        reconcile(report.pk)
    grant_studio(member, studio_tree, roles.VIEWER)
    client = login(member)
    return (live_server.url + f"/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics",
            client.cookies["sessionid"].value, live_server.url)


def _open(playwright, overview, width=1440):
    url, session, origin = overview
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_cookies([{"name": "sessionid", "value": session, "url": origin}])
    page = context.new_page()
    page.goto(url)
    return browser, page


def test_overview_list_reads_like_the_catalog_always_did(overview):
    with sync_playwright() as playwright:
        browser, page = _open(playwright, overview)
        try:
            cards = page.locator(".met-card")
            expect(cards).to_have_count(3)
            # The filter box, and nothing else around it.
            search = page.locator("#metSearch")
            expect(search).to_have_attribute("placeholder", "Filter metrics…")
            expect(page.get_by_role("button", name="Search metrics")).to_have_count(0)
            expect(page.locator("#metCount")).to_have_text("")
            expect(page.locator(".page-sub")).to_contain_text("3 defined, 2 claimed by reports")

            both = cards.filter(has_text="Gross Revenue")
            expect(both.locator(".met-usage > b")).to_have_text("2 reports")
            expect(both.locator(".met-usage .st")).to_have_text("all current")
            summary = both.locator("details.met-expand summary")
            expect(summary).to_have_text("2 claiming reports — details")
            expect(both.locator("details.met-expand")).not_to_have_attribute("open", "")
            summary.click()
            expect(both.locator(".met-expand-table tr")).to_have_count(3)
            expect(both.locator(".met-expand-table .ui-chip.acc").first).to_have_text("current · v2")
            expect(both.get_by_role("link", name="open report")).to_have_count(2)

            stale = cards.filter(has_text="Net Revenue")
            expect(stale.locator(".met-usage > b")).to_have_text("1 report")
            expect(stale.locator(".met-usage .st")).to_contain_text("Insert Coin")
            expect(stale.locator(".met-usage .st b")).to_have_text("built against v1")
            expect(stale.locator(".met-expand-table .ui-chip.warn")).to_have_text("stale — built against v1")

            unused = cards.filter(has_text="DAU")
            expect(unused.locator(".met-usage")).to_have_text("no reports claim this")
            expect(unused.locator(".met-tags .ui-chip.mute")).to_have_text("unused")

            # Nothing about the read is announced, and nothing asks to be retried.
            body = page.locator("main").inner_text()
            for text in ("Report usage", "partial", "Retry", "indexed report claims", "matching definitions",
                         "Previous", "Next"):
                assert text not in body, text

            # Typing filters the cards already on the page, with no pause and no page request.
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            search.fill("gross")
            expect(page.locator(".met-card:not([hidden])")).to_have_count(1)
            expect(page.locator("#metCount")).to_have_text("1 of 3")
            search.press("Escape")
            expect(page.locator(".met-card:not([hidden])")).to_have_count(3)
            search.fill("nothing like this")
            expect(page.locator("#metEmpty")).to_have_text("No metric matches that filter.")
            assert [url for url in requests if "/api/metrics/summary" not in url] == []

            shots = os.environ.get("TRELLUM_SCREENSHOT_DIR")
            if shots:
                search.fill("")
                for width in (1440, 412):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.screenshot(path=str(Path(shots) / f"metrics-fixed-{width}.png"))
        finally:
            browser.close()


def test_a_catalog_larger_than_one_page_searches_on_the_server(overview, monkeypatch):
    """Past one page the cards on screen are not all of them: a pause in typing asks the server."""
    monkeypatch.setattr("apps.reports.metrics_catalog.METRIC_PAGE_SIZE", 2)
    with sync_playwright() as playwright:
        browser, page = _open(playwright, overview)
        try:
            expect(page.locator(".met-card")).to_have_count(2)
            expect(page.locator(".ui-pager")).to_contain_text("1–2 of 3")
            expect(page.get_by_role("button", name="Search metrics")).to_have_count(0)
            page.locator("#metSearch").fill("dau")
            page.wait_for_url(lambda url: "q=dau" in url)
            expect(page.locator(".met-card")).to_have_count(1)
            expect(page.locator(".met-card .met-name")).to_have_text("DAU")
            expect(page.locator(".ui-pager")).to_have_count(0)
            expect(page.locator("#metSearch")).to_be_focused()
        finally:
            browser.close()
