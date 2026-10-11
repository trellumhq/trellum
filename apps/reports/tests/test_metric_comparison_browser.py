"""Comparison operates on real generated reports over a synthetic HTTP host."""
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.template import Context, Template

from apps.reports.tests import test_metrics_detail_browser as detail

ROOT = detail.ROOT
comparison_report = detail.generated_metrics
comparison_browser = detail.metrics_browser

PAGE = "/s/demo/default/metrics/compare"
REPORT = "/s/demo/default/r/metrics/"


def comparison_html(query, *, unavailable=False):
    params = parse_qs(query)
    state = {key: params.get(key, [""])[0] for key in ("a", "b", "from", "to", "split_a", "split_b")}
    metrics = [{"row": SimpleNamespace(name=name, label=label, format="currency"),
                "spec": "sum(revenue)", "grain": "day", "time_agg": "last" if name == "balance" else "sum",
                "current": True, "embed_url": REPORT + "?only=metric-" + name}
               for name, label in [("gross_revenue", "Gross Revenue"), ("net_revenue", "Net Revenue"), ("balance", "Balance"), ("inventory", "Inventory")]]
    cards = [{**next(m for m in metrics if m["row"].name == state[slot]), "slot": slot}
             if state[slot] else {"slot": slot} for slot in ("a", "b")]
    if unavailable:
        for card in cards:
            card["embed_url"] = ""
    source = (ROOT / "templates/reports/metric_comparison.html").read_text(encoding="utf-8")
    content = source.split('{% block content %}')[1].split('{% endblock %}')[0]
    rendered = Template('{% load static %}' + content).render(Context({
        "cards": cards, "metrics": metrics, "state": state, "metrics_report_built": not unavailable,
        "comparison_url": PAGE, "catalog_url": "/catalog", "studio": SimpleNamespace(name="Demo")}))
    return '<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/ui.css"></head><body class="mgmt"><main>' + rendered + '</main></body></html>'


def catalog_html():
    source = (ROOT / "templates/reports/metrics.html").read_text(encoding="utf-8")
    content = source.split('{% block content %}')[1].split('{% endblock %}')[0]
    metrics = [{"row": SimpleNamespace(name=name, label=label, version=1, format="currency"),
                "spec": "sum(revenue)", "embed_url": REPORT + "?only=metric-" + name,
                "chart_url": REPORT + "?only=metric-" + name, "overview_url": REPORT + "#metric-" + name}
               for name, label in [("gross_revenue", "Gross Revenue"), ("net_revenue", "Net Revenue"), ("balance", "Balance")]]
    rendered = Template('{% load docs static %}' + content).render(Context({
        "metrics": metrics, "defined_count": 3, "metrics_report_built": True, "metrics_report_url": REPORT,
        "org": SimpleNamespace(slug="demo"), "studio": SimpleNamespace(slug="default", name="Demo")}))
    return '<!doctype html><html><head><meta charset="utf-8"></head><body class="mgmt"><main>' + rendered + '</main></body></html>'


@pytest.fixture
def open_comparison(comparison_browser, comparison_report):
    contexts, servers = [], []

    def open_page(query="a=gross_revenue&b=net_revenue", *, width=1440, unavailable=False, failed=False, delay=False, catalog=False):
        context = comparison_browser.new_context(viewport={"width": width, "height": 900})
        contexts.append(context)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                from trellum.rendering.cdn import serve_vendor_request
                if serve_vendor_request(self):
                    return
                parts = urlsplit(self.path)
                if delay and parts.path.endswith("data.json"):
                    time.sleep(0.5)
                second_failed = failed == "second" and "metric-net_revenue" in parts.query
                if parts.path == REPORT and not (failed is True or second_failed):
                    self.send_response(302)
                    self.send_header("Location", REPORT + "index.html?" + parts.query)
                    self.end_headers()
                    return
                if parts.path == PAGE:
                    content = comparison_html(parts.query, unavailable=unavailable).encode()
                    content_type = "text/html"
                elif parts.path == "/catalog":
                    content = catalog_html().encode()
                    content_type = "text/html"
                elif parts.path.startswith(REPORT) and (failed is True or second_failed):
                    self.send_error(503)
                    return
                else:
                    base = ROOT / "static" if parts.path.startswith("/static/") else comparison_report
                    prefix = "/static/" if parts.path.startswith("/static/") else REPORT
                    target = base / parts.path.removeprefix(prefix)
                    if not target.resolve().is_relative_to(base.resolve()) or not target.is_file():
                        self.send_error(404)
                        return
                    content = target.read_bytes()
                    content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        page.goto(f"http://127.0.0.1:{server.server_port}" + ("/catalog" if catalog else f"{PAGE}?{query}"))
        return page, errors

    yield open_page
    for context in contexts:
        context.close()
    for server in servers:
        server.shutdown()
        server.server_close()


def ready(page):
    page.locator('iframe[data-ready="1"]').nth(1).wait_for()
    for handle in page.locator('iframe[data-ready="1"]').element_handles():
        handle.content_frame().locator('#fwLoading').wait_for(state='hidden')


def frame(page, slot):
    return page.frame_locator(f'[data-slot="{slot}"] iframe')


def test_catalog_selection_limit_clear_replace_and_open(open_comparison):
    page, errors = open_comparison(catalog=True)
    page.get_by_text('Metric definitions and report usage', exact=True).click()
    choices = page.locator('.met-compare-choice')
    assert page.locator('#metCompareOpen').is_disabled()
    choices.nth(0).check()
    assert page.locator('#metModal').is_hidden()
    assert page.locator('#metCompareOpen').is_disabled()
    choices.nth(1).check()
    assert choices.nth(2).is_disabled()
    choices.nth(0).uncheck()
    choices.nth(2).check()
    page.get_by_role('button', name='Clear selection').click()
    assert choices.nth(1).is_enabled() and not choices.nth(1).is_checked()
    choices.nth(0).check()
    choices.nth(1).check()
    with page.expect_navigation():
        page.locator('#metCompareOpen').click()
    ready(page)
    assert parse_qs(urlsplit(page.url).query)["a"] == ["gross_revenue"]
    assert parse_qs(urlsplit(page.url).query)["b"] == ["net_revenue"]
    assert errors == []


def test_shared_dates_independent_splits_history_reload(open_comparison, tmp_path):
    page, errors = open_comparison()
    ready(page)
    page.evaluate("history.replaceState({...history.state, unrelatedMarker:'retained'}, '', location.href)")
    frame(page, "a").get_by_role("button", name="Platform", exact=True).click()
    page.wait_for_timeout(100)
    frame(page, "b").get_by_role("button", name="Region", exact=True).click()
    page.wait_for_timeout(100)
    page.locator('#mcFrom').fill("2026-09-12")
    page.locator('#mcTo').fill("2026-09-24")
    page.get_by_role("button", name="Apply dates to both").click()
    assert page.evaluate("history.state.unrelatedMarker") == "retained"
    for slot, split, count in [("a", "Platform", 12), ("b", "Region", 3)]:
        assert frame(page, slot).locator('[data-stack-toggle] .active:visible').inner_text() == split
        actual = page.locator(f'[data-slot="{slot}"] iframe').element_handle().content_frame()
        chart = actual.evaluate("Object.values(window._chartInstances).find(c => c.canvas.offsetHeight > 0).data")
        assert len(chart["datasets"]) == count
        assert chart["labels"][0] == "2026-09-12"
        assert chart["labels"][-1] == "2026-09-24"
    query = parse_qs(urlsplit(page.url).query)
    assert query["split_a"] == ["Platform"] and query["split_b"] == ["Region"]
    page.go_back()
    assert page.evaluate("history.state.unrelatedMarker") == "retained"
    assert page.locator('#mcFrom').input_value() == "2026-09-01"
    page.go_forward()
    assert page.locator('#mcFrom').input_value() == "2026-09-12"
    page.screenshot(path=str(tmp_path / "metric-comparison-desktop.png"), full_page=True)
    page.reload()
    ready(page)
    assert frame(page, "a").locator('[data-stack-toggle] .active:visible').inner_text() == "Platform"
    assert frame(page, "b").locator('[data-stack-toggle] .active:visible').inner_text() == "Region"
    assert page.locator('#mcFrom').input_value() == "2026-09-12"
    assert errors == []


def test_last_snapshot_hides_static_kpi_dates_empty_and_mobile(open_comparison, tmp_path):
    page, errors = open_comparison("a=gross_revenue&b=balance&split_b=missing", width=390)
    ready(page)
    assert frame(page, "b").locator('.fw-kpi-grid').first.is_hidden()
    assert "Snapshot metrics" in page.locator('main').inner_text()
    assert "Requested split is unavailable" in page.locator('[data-slot="b"] .mc-range').inner_text()
    page.locator('#mcFrom').fill("2026-09-12")
    page.locator('#mcTo').fill("2026-09-24")
    page.get_by_role("button", name="Apply dates to both").click()
    actual = page.locator('[data-slot="b"] iframe').element_handle().content_frame()
    assert actual.evaluate("Object.values(window._chartInstances).find(c => c.canvas.offsetHeight > 0).data.labels[0]") == "2026-09-12"
    assert page.locator('[data-slot="b"]').bounding_box()["y"] > page.locator('[data-slot="a"]').bounding_box()["y"]
    page.screenshot(path=str(tmp_path / "metric-comparison-mobile.png"), full_page=True)
    page.locator('#mcFrom').fill("2027-01-01")
    page.locator('#mcTo').fill("2027-01-02")
    page.get_by_role("button", name="Apply dates to both").click()
    assert page.get_by_text("No data in the shared date range.", exact=False).count() == 2
    for width in [1440, 390, 1024]:
        page.set_viewport_size({"width": width, "height": 600})
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for handle in page.locator('iframe').element_handles():
            assert handle.content_frame().evaluate("document.documentElement.scrollHeight <= innerHeight + 2")
        heights = page.locator('iframe').evaluate_all("elements => elements.map(el => el.offsetHeight)")
        page.wait_for_timeout(400)
        assert page.locator('iframe').evaluate_all("elements => elements.map(el => el.offsetHeight)") == heights
    assert errors == []


def test_replace_remove_preserves_peer_and_delayed_ready_dates(open_comparison):
    page, errors = open_comparison(delay=True)
    page.locator('#mcFrom').fill("2026-09-13")
    page.locator('#mcTo').fill("2026-09-18")
    page.get_by_role("button", name="Apply dates to both").click()
    ready(page)
    frame(page, "b").get_by_role("button", name="Region", exact=True).click()
    page.wait_for_timeout(100)
    page.locator('#mc-a').select_option("balance")
    with page.expect_navigation():
        page.get_by_role("button", name="Update comparison").click()
    ready(page)
    assert frame(page, "b").locator('[data-stack-toggle] .active:visible').inner_text() == "Region"
    assert page.locator('#mcFrom').input_value() == "2026-09-13"
    page.locator('#mc-a').select_option("")
    with page.expect_navigation():
        page.get_by_role("button", name="Update comparison").click()
    page.locator('#mc-a').wait_for()
    assert page.locator('[data-slot="a"] iframe').count() == 0
    assert "split_b=Region" in page.url
    assert errors == []


@pytest.mark.parametrize("unavailable,failed", [(True, False), (False, True)])
def test_unavailable_build_keeps_selection_controls(open_comparison, unavailable, failed):
    page, errors = open_comparison(unavailable=unavailable, failed=failed)
    if failed:
        page.get_by_text("This metric’s chart is unavailable", exact=False).first.wait_for()
    else:
        assert page.get_by_text("has not been built yet", exact=False).count() == 2
    assert page.locator('#mc-a').is_visible()
    assert page.get_by_role("link", name="Metrics catalog", exact=False).is_visible()
    assert errors == []


def test_failed_peer_establishes_dates_from_surviving_chart(open_comparison):
    page, errors = open_comparison(failed="second", delay=True)
    page.locator('iframe[data-ready="1"]').wait_for()
    page.wait_for_function("document.getElementById('mcFrom').value !== ''")
    assert page.locator('#mcFrom').input_value() == "2026-09-01"
    assert page.locator('#mcTo').input_value() == "2026-10-10"
    assert "unavailable" in page.locator('[data-slot="b"] .mc-range').inner_text()
    assert errors == []


def test_snapshot_dates_are_truthful_and_persisted_pageshow_resumes_size(open_comparison):
    page, errors = open_comparison("a=gross_revenue&b=inventory")
    ready(page)
    assert "Shared dates do not apply" in page.locator('[data-slot="b"] .mc-range').inner_text()
    assert "Filters: none" in page.locator('[data-slot="b"] .mc-range').inner_text()
    actual = page.locator('[data-slot="b"] iframe').element_handle().content_frame()
    before = actual.evaluate("Object.values(window._chartInstances).find(c => c.canvas.offsetHeight > 0).data.labels")
    page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true})); dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    page.set_viewport_size({"width": 390, "height": 600})
    page.locator('#mcFrom').fill("2027-01-01")
    page.locator('#mcTo').fill("2027-01-02")
    page.get_by_role("button", name="Apply dates to both").click()
    page.wait_for_timeout(150)
    assert actual.evaluate("Object.values(window._chartInstances).find(c => c.canvas.offsetHeight > 0).data.labels") == before
    assert actual.evaluate("document.documentElement.scrollHeight <= innerHeight + 2")
    assert errors == []


@pytest.mark.parametrize("width", [1440, 1024, 390])
def test_repeated_splits_and_dates_fit_generated_charts(open_comparison, width):
    page, errors = open_comparison(width=width)
    ready(page)
    page.wait_for_timeout(150)
    # A settled assertion misses the scrollbar painted during a legend rebuild.
    page.evaluate("""() => {
        window.comparisonResizeOverflow = [];
        window.comparisonResizeObserver = new ResizeObserver(() => {
            document.querySelectorAll('iframe').forEach(f => {
                const overflow = f.contentDocument.documentElement.scrollHeight - f.clientHeight;
                if (overflow > 2) comparisonResizeOverflow.push(overflow);
            });
        });
        document.querySelectorAll('iframe').forEach(f => comparisonResizeObserver.observe(f.contentDocument.body));
    }""")
    heights_by_split = {}
    for iteration, split in enumerate(["Platform", "Region", "Total"] * 3):
        frame(page, "a").get_by_role("button", name=split, exact=True).click()
        frame(page, "b").get_by_role("button", name=split, exact=True).click()
        page.locator('#mcFrom').fill("2026-09-12" if iteration % 2 else "2026-09-01")
        page.locator('#mcTo').fill("2026-09-24" if iteration % 2 else "2026-10-10")
        page.get_by_role("button", name="Apply dates to both").click()
        page.wait_for_timeout(150)
        geometry = page.locator('iframe').evaluate_all("""frames => frames.map(f => {
            const d = f.contentDocument;
            return {frameHeight: f.clientHeight, bodyHeight: d.body.scrollHeight,
                innerOverflow: d.documentElement.scrollHeight - f.clientHeight,
                visibleScrollers: [...d.querySelectorAll('*')].filter(el =>
                    el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 2 &&
                    ['auto', 'scroll'].includes(d.defaultView.getComputedStyle(el).overflowY))
                    .map(el => ({tag: el.tagName, cls: el.className, height: el.clientHeight, scroll: el.scrollHeight}))};
        })""")
        assert all(g["innerOverflow"] <= 2 and not g["visibleScrollers"] for g in geometry), (split, geometry)
        heights = [g["frameHeight"] for g in geometry]
        assert heights == heights_by_split.setdefault(split, heights), (split, geometry)
    assert page.evaluate("comparisonResizeOverflow") == []
    assert errors == []
