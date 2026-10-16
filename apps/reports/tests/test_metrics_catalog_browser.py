"""One Chromium check of the Metrics catalog: usage and charts appear by themselves."""
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.reports.models import (
    MetricCatalogState, MetricDefinition, Report, ReportMetricClaim, ReportMetricPreview, ReportSourceSummary,
)

pytestmark = pytest.mark.django_db(transaction=True)
SHOTS = Path("/home/haarbal/projects/task-artifacts/portal-ux-d")


def _indexed(studio, slug, **values):
    report = Report.objects.create(studio=studio, slug=slug, name=slug.title(), tags=["metrics"] if slug == "metrics" else [])
    # Observed an hour ago: a worker that idled must not hide charts or usage.
    return ReportSourceSummary.objects.create(
        report=report, state="ready", quality="verified", schema_version=2,
        observed_at=timezone.now() - timedelta(hours=1), **values)


def test_usage_sparks_and_charts_show_without_any_button(live_server, login, studio, org_admin):
    from playwright.sync_api import expect, sync_playwright

    MetricCatalogState.objects.create(studio=studio, definition_revision=3)
    gross, net, spare = [MetricDefinition.objects.create(
        studio=studio, name=name, label=label, definition_hash="h", version=1, agg="sum", column="x",
        binding={"dataset": "daily"}, description="Synthetic metric.", tags=["revenue"] if name != "spare" else [])
        for name, label in (("gross", "Gross"), ("net", "Net"), ("spare", "Spare"))]
    generated = _indexed(studio, "metrics", workspace_version=1, preview_state="ready")
    other = _indexed(studio, "sales")
    for metric in (gross, net):
        ReportMetricClaim.objects.create(summary=generated, metric_name=metric.name, definition_hash="h", version=1)
        ReportMetricPreview.objects.create(summary=generated, metric_name=metric.name, points="0,30 40,10 80,20 140,4")
    ReportMetricClaim.objects.create(summary=other, metric_name=gross.name, definition_hash="old", version=1)

    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_cookies([{"name": name, "value": cookie.value, "url": live_server.url}
                                 for name, cookie in client.cookies.items()])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(live_server.url + base + "/metrics", wait_until="domcontentloaded")
            gross_card = page.locator('.met-card[data-metric="gross"]')
            # Usage, trend and chart state arrive on their own: no status line, no button.
            expect(gross_card.locator("[data-metric-usage] > b")).to_have_text("2 reports")
            expect(gross_card.locator(".met-spark")).to_have_count(1)
            expect(gross_card.locator("details.met-expand summary")).to_have_text("2 claiming reports — details")
            expect(page.locator('.met-card[data-metric="spare"] [data-metric-usage]')).to_contain_text("no reports claim this")
            expect(page.locator('.met-card[data-metric="spare"] .met-tags')).to_contain_text("unused")
            assert gross_card.get_attribute("data-chart-state") == "ready"
            for name in ("Retry report usage", "Search metrics", "Reload definitions"):
                assert page.get_by_role("button", name=name).count() == 0 and page.get_by_role("link", name=name).count() == 0
            assert page.locator("[data-summary-status], [data-summary-retry], form[data-metric-search]").count() == 0

            # The filter applies as you type, with no button.
            page.locator("#metSearch").fill("zzz")
            expect(page.locator("#metEmpty")).to_be_visible()
            page.locator("#metSearch").fill("net")
            expect(page.locator(".met-card:not([hidden])")).to_have_count(1)
            page.locator("#metSearch").fill("")

            # The detail panel frames the metric's own chart.
            gross_card.get_by_role("button", name="Gross", exact=True).click()
            expect(page.locator("#metModalBody iframe")).to_have_attribute("src", f"{base}/r/metrics/?only=metric-gross")
            page.screenshot(path=str(SHOTS / "uxd-metrics-catalog-modal.png"))
            page.locator("#metModalClose").click()
            page.screenshot(path=str(SHOTS / "uxd-metrics-catalog.png"), full_page=True)
            assert not errors
        finally:
            browser.close()


def test_every_claiming_report_is_inline_and_only_a_huge_list_gets_server_search(live_server, login, studio, org_admin, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    # The inline limit is 500; shrink it so a handful of claims crosses it.
    monkeypatch.setattr("apps.reports.metric_summaries.INLINE_LIMIT", 3)
    MetricCatalogState.objects.create(studio=studio, definition_revision=3)
    busy, quiet = [MetricDefinition.objects.create(
        studio=studio, name=name, label=name.title(), definition_hash="h", version=1, agg="sum", column="x",
        binding={"dataset": "daily"}, description="Synthetic metric.") for name in ("busy", "quiet")]
    for index, name in enumerate(("alpha", "bravo", "charlie", "delta", "echo")):
        summary = _indexed(studio, name)
        ReportMetricClaim.objects.create(summary=summary, metric_name=busy.name, definition_hash="h", version=1)
        if index < 2:
            ReportMetricClaim.objects.create(summary=summary, metric_name=quiet.name, definition_hash="h", version=1)

    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    client.get(base + "/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_cookies([{"name": name, "value": cookie.value, "url": live_server.url}
                                 for name, cookie in client.cookies.items()])
            page = context.new_page()
            page.goto(live_server.url + base + "/metrics", wait_until="domcontentloaded")
            quiet_card = page.locator('.met-card[data-metric="quiet"]')
            expect(quiet_card.locator("[data-metric-usage] > b")).to_have_text("2 reports")
            # Within the limit: every claiming report inline, no link to anywhere else.
            expect(quiet_card.locator("details.met-expand tr").filter(has_text="open report")).to_have_count(2)
            assert quiet_card.get_by_role("link", name="indexed claims").count() == 0
            busy_card = page.locator('.met-card[data-metric="busy"]')
            expect(busy_card.locator("[data-metric-usage] > b")).to_have_text("5 reports")
            expect(busy_card.locator("details.met-expand tr").filter(has_text="open report")).to_have_count(3)
            busy_card.locator("details.met-expand summary").click()
            busy_card.get_by_role("link", name="All 5 indexed claims →").click()
            page.wait_for_url("**/claims?*")
            expect(page.locator("tbody tr")).to_have_count(3)
            for name in ("Previous", "Next", "Search", "Reload Metrics definitions"):
                assert page.get_by_role("button", name=name).count() == 0 and page.get_by_role("link", name=name).count() == 0
            # Search as you type narrows on the server; no button to press.
            page.get_by_role("searchbox").fill("echo")
            expect(page.locator("tbody tr")).to_have_count(1)
            expect(page.locator("tbody")).to_contain_text("Echo")
            assert "q=echo" in page.url
        finally:
            browser.close()
