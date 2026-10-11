"""Metric detail against a real MetricsReport build, with synthetic rows only."""
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest
import yaml
from django.template import Context, Template

ROOT = Path(__file__).resolve().parents[3]
SIZES = [(1440, 900), (1366, 768), (1024, 600), (800, 600), (390, 844), (844, 390)]
REPORT_PATH = "/reports/metrics/"


@pytest.fixture(scope="module")
def generated_metrics(tmp_path_factory):
    from trellum.metrics import invalidate_metrics_cache
    from trellum.metrics_report import MetricsReport
    from trellum.project import get_project_root, set_project_root
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext
    from trellum.validation import validate_report

    project = tmp_path_factory.mktemp("metric-detail")
    metric = {"name": "gross_revenue", "label": "Gross Revenue", "agg": "sum",
              "column": "revenue", "format": "currency", "dataset": "daily",
              "description": "Synthetic daily revenue for metric browser interactions."}
    (project / "metrics.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "datasets": {"daily": {"provider": "fixture:daily", "dimensions": ["platform", "region"],
                               "time": {"column": "event_date", "grain": "day"}},
                     "snapshot": {"provider": "fixture:snapshot", "dimensions": ["platform"], "time": "none"}},
        "metrics": [metric, {**metric, "name": "net_revenue", "label": "Net Revenue"},
                    {**metric, "name": "balance", "label": "Balance", "time_agg": "last"},
                    {**metric, "name": "inventory", "label": "Inventory", "dataset": "snapshot"}],
    }), encoding="utf-8")
    rows = pd.DataFrame([
        {"event_date": day.strftime("%Y-%m-%d"), "platform": f"Platform {i}: a long series label",
         "region": f"Region {i % 3}", "revenue": 10 + i}
        for day in pd.date_range("2026-09-01", periods=40) for i in range(12)
    ])
    original = get_project_root()
    set_project_root(str(project))
    invalidate_metrics_cache()
    try:
        config = {"name": "Metrics", "slug": "metrics", "version": "0.1.0",
                             "description": "Synthetic daily revenue by platform and region. This browser fixture exercises shared date filtering and per-metric breakdowns with forty days of generated rows.",
                             "schedule": {"cron": "0 8 * * *"}, "tags": ["metrics"],
                             "studio": "default", "category": "Metrics"}
        # Snapshot observations have no time axis or shared date filter.
        config["validation"] = {"suppress_per_dataset": {"snapshot": ["ds-no-filterbar"]}}
        ids = []
        for output in ("output", "rebuilt"):
            ctx = ReportContext(config, "metrics", str(project / output))
            ctx.metrics = lambda *args, **kwargs: rows.copy()
            MetricsReport().generate(ctx)
            validation = validate_report(ctx)
            assert validation.summary["fail"] == 0, validation.checks
            assert validation.summary["warn"] == 0, [(c.id, c.message) for c in validation.checks if c.level == "warn"]
            html, _ = render_report(ctx, auto_refresh=False, validation=validation)
            ids.append(re.findall(r'<div[^>]+data-stack-toggle="([^"]+)"', Path(html).read_text(encoding="utf-8")))
        assert ids[0] and ids[0] == ids[1], "Saved breakdown URLs must survive equivalent rebuilds"
    finally:
        set_project_root(original)
        invalidate_metrics_cache()
    return project / "output"


def _catalog_html(*, built=True, embed=True):
    source = (ROOT / "templates/reports/metrics.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"):source.index("</style>") + len("</style>")]
    modal = source[source.index('<div class="met-overlay"'):source.index('<script src="{% static')]
    modal = Template(modal).render(Context({"metrics_report_built": built}))
    return f"""<!doctype html><html data-theme="light"><head>
    <link rel="stylesheet" href="/_vendor/inter.css">
    <link rel="stylesheet" href="/static/themes.generated.css">
    <link rel="stylesheet" href="/static/ui.css">
    <link rel="stylesheet" href="/static/console-shell.css">
    {style}</head><body class="mgmt"><main>
    <div class="met-card" data-metric="gross_revenue" data-label="Gross Revenue"
      data-embed="{REPORT_PATH + '?only=metric-gross_revenue' if embed else ''}"
      data-chart="{REPORT_PATH + '?only=metric-gross_revenue' if embed else ''}"
      data-overview="{REPORT_PATH + '#metric-gross_revenue' if embed else ''}" data-reason="No dataset">
      <button class="met-open">Gross Revenue</button>
      <div class="met-fact met-desc">Synthetic metric description. {'Details about the metric. ' * 14}</div>
    </div>{modal}<script src="/static/metrics_overlay.js"></script></main></body></html>"""


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def metrics_browser(request):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = getattr(pw, request.param).launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def open_metric(metrics_browser, generated_metrics):
    contexts = []
    servers = []

    def open_page(size=(1440, 900), *, built=True, embed=True, failed=False, zoom=1):
        # Browser zoom reduces the available CSS viewport; exercise that layout
        # and pixel density together without engine-specific browser chrome.
        context = metrics_browser.new_context(
            viewport={"width": int(size[0] / zoom), "height": int(size[1] / zoom)},
            device_scale_factor=zoom,
        )
        context.add_init_script("""if (window === top) {
            window.activeMetricObservers = 0;
            const Original = window.ResizeObserver;
            window.ResizeObserver = class extends Original {
                observe(target, options) { super.observe(target, options); window.activeMetricObservers++; }
                disconnect() { super.disconnect(); window.activeMetricObservers--; }
            };
        }""")
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
                path = urlsplit(self.path).path
                if path == REPORT_PATH and not failed:
                    query = urlsplit(self.path).query
                    self.send_response(302)
                    self.send_header("Location", REPORT_PATH + "index.html" + ("?" + query if query else ""))
                    self.end_headers()
                    return
                status, content_type = 200, "text/html"
                if path == "/catalog":
                    content = _catalog_html(built=built, embed=embed).encode("utf-8")
                elif path.startswith("/reports/metrics/") and failed:
                    status, content = 503, b"Build unavailable"
                else:
                    if path.startswith("/static/"):
                        base = ROOT / "static"
                        target = base / path.removeprefix("/static/")
                    elif path.startswith("/reports/metrics/"):
                        base = generated_metrics
                        target = base / path.removeprefix("/reports/metrics/")
                    else:
                        self.send_error(404)
                        return
                    if not target.resolve().is_relative_to(base.resolve()) or not target.is_file():
                        self.send_error(404)
                        return
                    content = target.read_bytes()
                    content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        page.goto(f"http://127.0.0.1:{server.server_port}/catalog")
        page.locator(".met-open").click()
        if embed and not failed:
            page.frame_locator("#metModal iframe").locator("canvas").first.wait_for(state="visible")
            page.wait_for_timeout(350)
        return page, errors

    yield open_page
    for context in contexts:
        context.close()
    for server in servers:
        server.shutdown()
        server.server_close()


def _geometry(page):
    return page.evaluate("""() => {
        const m = document.getElementById('metModal'), b = document.getElementById('metModalBody');
        const f = b.querySelector('iframe'), d = f.contentDocument;
        const chart = d.querySelector('.fw-chart-container'), toggle = d.querySelector('[data-stack-toggle]');
        return {modal: m.getBoundingClientRect().toJSON(), frame: f.getBoundingClientRect().toJSON(),
            outerOverflow: b.scrollHeight - b.clientHeight, innerOverflow: d.documentElement.scrollHeight - f.clientHeight,
            chartHeight: chart.getBoundingClientRect().height, innerWidth: d.documentElement.scrollWidth,
            frameWidth: f.clientWidth, toggleBottom: toggle.getBoundingClientRect().bottom};
    }""")


@pytest.mark.parametrize("size", SIZES)
def test_metric_modal_geometry(open_metric, size):
    page, errors = open_metric(size)
    frame = page.frame_locator("#metModal iframe")
    frame.get_by_role("button", name="Platform", exact=True).click()
    page.wait_for_timeout(350)
    geometry = _geometry(page)
    assert geometry["modal"]["top"] >= -1
    assert geometry["modal"]["bottom"] <= size[1] + 1
    assert geometry["innerWidth"] <= geometry["frameWidth"] + 1
    assert geometry["innerOverflow"] <= 2, geometry
    assert geometry["toggleBottom"] <= geometry["frame"]["height"] + 1
    assert errors == []


def test_metric_page_retains_dates_and_split_and_overview_link(open_metric):
    page, errors = open_metric()
    frame = page.frame_locator("#metModal iframe")
    frame.locator(".fw-date-trigger").click()
    frame.locator('.fw-date-input[data-edge="min"]').fill("2026-09-12")
    frame.locator('.fw-date-input[data-edge="min"]').dispatch_event("change")
    frame.locator('.fw-date-input[data-edge="max"]').fill("2026-09-24")
    frame.locator('.fw-date-input[data-edge="max"]').dispatch_event("change")
    frame.get_by_role("button", name="Platform", exact=True).click()
    page.wait_for_timeout(100)
    current = parse_qs(urlsplit(page.locator("iframe").element_handle().content_frame().url).query)
    assert current["only"] == ["metric-gross_revenue"]
    assert current["event_date"] == ["2026-09-12..2026-09-24"]
    split_key = next(k for k in current if k.startswith("t."))
    assert current[split_key] == ["Platform"]
    assert parse_qs(urlsplit(page.locator("#metModalFull").get_attribute("href")).query) == current
    overview = urlsplit(page.locator("#metModalOverview").get_attribute("href"))
    assert "only" not in parse_qs(overview.query)
    assert overview.fragment == "metric-gross_revenue"

    with page.expect_navigation():
        page.locator("#metModalFull").click()
    page.locator("canvas").first.wait_for(state="visible")
    assert page.locator("body.fw-only").count() == 1
    assert page.locator("#metric-net_revenue").is_hidden()
    assert page.locator('[data-stack-toggle] .fw-toggle-btn.active').first.inner_text() == "Platform"
    assert page.locator('.fw-date-input[data-edge="min"]').input_value() == "2026-09-12"
    assert page.locator('.fw-date-input[data-edge="max"]').input_value() == "2026-09-24"
    page.wait_for_function("Object.values(window._chartInstances)[0]?.data.datasets.length === 12")
    assert page.evaluate("Object.values(window._chartInstances)[0].data.datasets.length") == 12
    page.reload()
    page.locator("canvas").first.wait_for(state="visible")
    assert parse_qs(urlsplit(page.url).query) == current
    assert page.locator('[data-stack-toggle] .fw-toggle-btn.active').first.inner_text() == "Platform"
    page.go_back()
    page.locator(".met-open").wait_for()
    page.go_forward()
    page.locator("canvas").first.wait_for(state="visible")
    assert parse_qs(urlsplit(page.url).query) == current
    assert errors == []


def test_resize_reopen_disconnects_observer_and_restores_chart(open_metric):
    page, errors = open_metric()
    initial = _geometry(page)
    original = initial["chartHeight"]
    for size in [(390, 844), (844, 390), (800, 600), (1440, 900)] * 2:
        page.set_viewport_size({"width": size[0], "height": size[1]})
        page.wait_for_timeout(120)
        assert _geometry(page)["innerOverflow"] <= 2
    assert abs(_geometry(page)["chartHeight"] - original) <= 1
    for _ in range(3):
        assert page.evaluate("window.activeMetricObservers") == 1
        page.locator("#metModalClose").click()
        assert page.evaluate("window.activeMetricObservers") == 0
        assert page.locator(".met-open").evaluate("el => el === document.activeElement")
        assert page.locator("iframe").count() == 0
        page.locator(".met-open").click()
        page.frame_locator("iframe").locator("canvas").first.wait_for(state="visible")
        page.wait_for_timeout(120)
        assert abs(_geometry(page)["chartHeight"] - original) <= 1, (initial, _geometry(page))
    assert errors == []


@pytest.mark.parametrize("size,zoom", [((1440, 900), 2), ((390, 844), 1.25), ((320, 180), 1)])
def test_zoom_and_tiny_viewport_keep_controls_reachable(open_metric, size, zoom):
    page, errors = open_metric(size, zoom=zoom)
    geometry = _geometry(page)
    assert geometry["modal"]["bottom"] <= page.viewport_size["height"] + 1
    assert geometry["innerOverflow"] <= 2
    assert geometry["innerWidth"] <= geometry["frameWidth"] + 1
    frame = page.frame_locator("iframe")
    frame.locator(".fw-date-trigger").click()
    assert frame.locator(".fw-date-popover").is_visible()
    frame.get_by_role("button", name="7D", exact=True).click()
    assert _geometry(page)["innerOverflow"] <= 2
    assert frame.locator(".fw-date-preset.active").inner_text() == "7D"
    assert frame.locator('.fw-date-input[data-edge="min"]').input_value() == "2026-10-03"
    assert frame.locator('.fw-date-input[data-edge="max"]').input_value() == "2026-10-10"
    frame.locator(".fw-date-trigger").click()
    frame.get_by_role("button", name="Region", exact=True).click()
    assert frame.locator('#metric-gross_revenue [data-stack-toggle] .active').inner_text() == "Region"
    page.locator("#metModalClose").click()
    assert page.locator(".met-open").evaluate("el => el === document.activeElement")
    assert errors == []


@pytest.mark.parametrize("built,failed", [(False, False), (True, True)])
def test_unbuilt_or_failed_chart_keeps_accessible_exit(open_metric, built, failed):
    page, errors = open_metric((320, 180), built=built, embed=built, failed=failed)
    if built:
        page.locator(".met-modal-note").wait_for()
        assert "unavailable" in page.locator(".met-modal-note").inner_text()
        assert page.locator("iframe").is_hidden()
        assert page.locator("#metModalFull").is_visible()
    else:
        assert "has not been built" in page.locator(".met-modal-note").inner_text()
        assert page.locator("#metModalFull").is_hidden()
    page.keyboard.press("Escape")
    assert page.locator("#metModal").is_hidden()
    assert page.locator(".met-open").evaluate("el => el === document.activeElement")
    assert errors == []


def test_overview_and_existing_fragment_keep_compact_grid(open_metric):
    page, errors = open_metric()
    with page.expect_navigation():
        page.locator("#metModalOverview").click()
    assert urlsplit(page.url).fragment == "metric-gross_revenue"
    assert "only" not in parse_qs(urlsplit(page.url).query)
    assert page.locator("body.fw-only").count() == 0
    assert page.locator("#metric-net_revenue").is_visible()
    assert page.locator('[data-stack-toggle]').first.is_hidden()
    assert errors == []
