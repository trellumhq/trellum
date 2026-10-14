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
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext
    from trellum.validation import validate_report

    project = tmp_path_factory.mktemp("metric-detail")
    metric = {"name": "gross_revenue", "label": "Gross Revenue", "agg": "sum",
              "column": "revenue", "format": "currency", "dataset": "daily",
              "description": "Synthetic daily revenue for metric browser interactions.", "tags": ["revenue"]}
    (project / "metrics.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "datasets": {"daily": {"provider": "fixture:daily", "dimensions": ["platform", "region"],
                               "time": {"column": "event_date", "grain": "day"}},
                     "snapshot": {"provider": "fixture:snapshot", "dimensions": ["platform"], "time": "none"},
                     "orders": {"provider": "fixture:orders", "dimensions": ["platform"],
                                "time": {"column": "order_date", "grain": "day"}}},
        "metrics": [metric, {**metric, "name": "net_revenue", "label": "Net Revenue"},
                    {**metric, "name": "balance", "label": "Balance", "time_agg": "last", "tags": ["finance"]},
                    {**metric, "name": "inventory", "label": "Inventory", "dataset": "snapshot"},
                    {**metric, "name": "orders", "label": "Orders", "dataset": "orders", "tags": ["usage"]}],
    }), encoding="utf-8")
    rows = pd.DataFrame([
        {"event_date": day.strftime("%Y-%m-%d"), "platform": f"Platform {i}: a long series label",
         "region": f"Region {i % 3}", "revenue": 10 + i + (600 if day_index == 6 else day_index * 2)}
        for day_index, day in enumerate(pd.date_range("2026-09-01", periods=40)) for i in range(12)
    ])
    patch = pytest.MonkeyPatch()
    patch.setattr("trellum.project._project_root_override", str(project))
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
            ctx.metrics = lambda ids, **kwargs: (
                rows[rows.event_date.isin(sorted(rows.event_date.unique())[::2])]
                .rename(columns={"event_date": "order_date"}).copy() if ids == ["orders"] else rows.copy())
            MetricsReport().generate(ctx)
            validation = validate_report(ctx)
            assert validation.summary["fail"] == 0, validation.checks
            assert validation.summary["warn"] == 0, [(c.id, c.message) for c in validation.checks if c.level == "warn"]
            html, _ = render_report(ctx, auto_refresh=False, validation=validation)
            ids.append(re.findall(r'<div[^>]+data-stack-toggle="([^"]+)"', Path(html).read_text(encoding="utf-8")))
        assert ids[0] and ids[0] == ids[1], "Saved breakdown URLs must survive equivalent rebuilds"
    finally:
        patch.undo()
        invalidate_metrics_cache()
    return project / "output"


def _catalog_html(*, built=True, embed=True, workspace=False):
    source = (ROOT / "templates/reports/metrics.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"):source.index("</style>") + len("</style>")]
    start = source.index('<div class="met-overlay"')
    modal = source[start:source.index('<script src="{% static', start)]
    modal = Template(modal).render(Context({"metrics_report_built": built}))
    if workspace:
        modal = (f'<iframe id="metWorkspace" src="{REPORT_PATH}?workspace=1" title="Metrics chart workspace"></iframe>'
                 '<script src="/static/metrics_workspace.js"></script>')
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

    def open_page(size=(1440, 900), *, built=True, embed=True, failed=False, zoom=1, workspace=False):
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
                    content = _catalog_html(built=built, embed=embed, workspace=workspace).encode("utf-8")
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
        if workspace:
            page.frame_locator('#metWorkspace').locator('canvas').first.wait_for(state='visible')
            page.wait_for_timeout(350)
            return page, errors
        page.locator(".met-open").click()
        if embed and not failed:
            page.frame_locator("#metModal iframe").locator("#metric-gross_revenue canvas").wait_for(state="visible")
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
        const chart = d.querySelector('#metric-gross_revenue .fw-chart-container'), toggle = d.querySelector('#metric-gross_revenue [data-stack-toggle]');
        return {modal: m.getBoundingClientRect().toJSON(), frame: f.getBoundingClientRect().toJSON(),
            outerOverflow: b.scrollHeight - b.clientHeight, innerOverflow: d.documentElement.scrollHeight - f.clientHeight,
            chartHeight: chart.getBoundingClientRect().height, innerWidth: d.documentElement.scrollWidth,
            frameWidth: f.clientWidth, toggleBottom: toggle.getBoundingClientRect().bottom};
    }""")


def _wait_for_metric_fit(page, *, chart_height=None):
    # Fitting runs through animation frames and ResizeObserver callbacks.
    # Wait for their visible result rather than a fixed delay under CI load.
    page.wait_for_function("""expectedHeight => {
        const frame = document.querySelector('#metModal iframe');
        const doc = frame && frame.contentDocument;
        const chart = doc && doc.querySelector('#metric-gross_revenue .fw-chart-container');
        return chart && doc.fonts.status === 'loaded'
            && doc.documentElement.scrollHeight - frame.clientHeight <= 2
            && (expectedHeight === null || Math.abs(chart.getBoundingClientRect().height - expectedHeight) <= 1);
    }""", arg=chart_height, timeout=5000)


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
    page.locator("#metric-gross_revenue canvas").wait_for(state="visible")
    assert page.locator("body.fw-only").count() == 1
    assert page.locator('#metric-gross_revenue .fw-kpi-grid').is_visible()
    assert page.locator("#metric-net_revenue").is_hidden()
    assert page.locator('#metric-gross_revenue [data-stack-toggle] .fw-toggle-btn.active').inner_text() == "Platform"
    assert page.locator('.fw-date-input[data-edge="min"]').input_value() == "2026-09-12"
    assert page.locator('.fw-date-input[data-edge="max"]').input_value() == "2026-09-24"
    page.wait_for_function("window._chartInstances[document.querySelector('#metric-gross_revenue canvas').id]?.data.datasets.length === 12")
    assert page.evaluate("window._chartInstances[document.querySelector('#metric-gross_revenue canvas').id].data.datasets.length") == 12
    page.reload()
    page.locator("#metric-gross_revenue canvas").wait_for(state="visible")
    assert parse_qs(urlsplit(page.url).query) == current
    assert page.locator('#metric-gross_revenue [data-stack-toggle] .fw-toggle-btn.active').inner_text() == "Platform"
    page.go_back()
    page.locator(".met-open").wait_for()
    page.go_forward()
    page.locator("#metric-gross_revenue canvas").wait_for(state="visible")
    assert parse_qs(urlsplit(page.url).query) == current
    assert errors == []


def test_resize_reopen_disconnects_observer_and_restores_chart(open_metric):
    page, errors = open_metric()
    _wait_for_metric_fit(page)
    initial = _geometry(page)
    original = initial["chartHeight"]
    for size in [(390, 844), (844, 390), (800, 600), (1440, 900)] * 2:
        page.set_viewport_size({"width": size[0], "height": size[1]})
        _wait_for_metric_fit(page, chart_height=original if size == (1440, 900) else None)
        assert _geometry(page)["innerOverflow"] <= 2
    assert abs(_geometry(page)["chartHeight"] - original) <= 1
    for _ in range(3):
        assert page.evaluate("window.activeMetricObservers") == 1
        page.locator("#metModalClose").click()
        assert page.evaluate("window.activeMetricObservers") == 0
        assert page.locator(".met-open").evaluate("el => el === document.activeElement")
        assert page.locator("iframe").count() == 0
        page.locator(".met-open").click()
        page.frame_locator("iframe").locator("#metric-gross_revenue canvas").wait_for(state="visible")
        _wait_for_metric_fit(page, chart_height=original)
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


def test_overview_and_existing_fragment_keep_splits(open_metric):
    page, errors = open_metric()
    with page.expect_navigation():
        page.locator("#metModalOverview").click()
    assert urlsplit(page.url).fragment == "metric-gross_revenue"
    assert "only" not in parse_qs(urlsplit(page.url).query)
    assert page.locator("body.fw-only").count() == 0
    assert page.locator("#metric-net_revenue").is_visible()
    assert page.locator('#metric-gross_revenue [data-stack-toggle]').is_visible()
    assert errors == []


@pytest.mark.parametrize("size", [(1440, 900), (800, 600), (390, 844)])
def test_workspace_visibility_splits_and_single_scroll(open_metric, size):
    page, errors = open_metric(size, workspace=True)
    frame = page.locator('#metWorkspace').element_handle().content_frame()
    assert frame.locator('.fw-metric-block').evaluate_all('(els) => els.map(el => el.id)') == [
        'metric-balance', 'metric-gross_revenue', 'metric-inventory', 'metric-net_revenue', 'metric-orders']
    assert frame.locator('body.fw-metrics-embedded').count() == 1
    assert frame.locator('.fw-metric-block .fw-kpi-grid').evaluate_all(
        '(els) => els.length === 5 && els.every(el => getComputedStyle(el).display === "none")')
    assert frame.locator('.fw-metric-snapshot-note').is_hidden()
    assert frame.locator('#metric-gross_revenue [data-stack-toggle]').is_visible()
    toolbar = frame.locator('#fwMetricsWorkspace')
    layout = toolbar.evaluate('''el => {
        const buttons = [...el.querySelectorAll('button')];
        const rects = [...el.querySelectorAll('summary, button, #fwMetricsCount')]
            .map(item => item.getBoundingClientRect());
        return {width: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth,
            buttonHeights: buttons.map(button => button.getBoundingClientRect().height),
            inside: rects.every(rect => rect.left >= 0 && rect.right <= document.documentElement.clientWidth + 1)};
    }''')
    assert layout['width'] <= layout['clientWidth'] + 1, layout
    assert layout['inside'], layout
    if size[0] <= 600:
        assert min(layout['buttonHeights']) >= 44, layout
    frame.locator('#metric-gross_revenue').scroll_into_view_if_needed()
    frame.locator('#metric-gross_revenue [data-stack-toggle]').get_by_role('button', name='Platform', exact=True).click()
    assert page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => {
        const f=document.getElementById('metWorkspace'); resolve(f.contentDocument.documentElement.scrollHeight <= f.clientHeight + 2);
    }))''')
    summary = frame.locator('#fwMetricsWorkspace summary')
    toolbar = frame.locator('#fwMetricsWorkspace')
    geometry = lambda: toolbar.evaluate('''el => {
        const box = selector => {
            const rect = el.querySelector(selector).getBoundingClientRect();
            return {top: rect.top - el.getBoundingClientRect().top, height: rect.height};
        };
        return {summary: box('summary'), actions: box('.fw-metric-actions'), choices: box('.fw-metric-choices')};
    }''')
    collapsed_geometry = geometry()
    summary.focus()
    summary.press('Enter')
    assert frame.locator('#fwMetricsWorkspace details').evaluate('(el) => el.open')
    expanded_geometry = geometry()
    assert abs(expanded_geometry['actions']['top'] - collapsed_geometry['actions']['top']) <= 1
    if size[0] > 600:
        assert abs((expanded_geometry['actions']['top'] + expanded_geometry['actions']['height'] / 2)
                   - (expanded_geometry['summary']['top'] + expanded_geometry['summary']['height'] / 2)) <= 2
    else:
        assert expanded_geometry['actions']['top'] < expanded_geometry['choices']['top']
    assert frame.evaluate('''() => {
        const width = document.documentElement.clientWidth;
        return document.documentElement.scrollWidth <= width + 1 &&
            [...document.querySelectorAll('.fw-metric-choices label')].every(label =>
                label.getBoundingClientRect().left >= 0 && label.getBoundingClientRect().right <= width + 1);
    }''')
    frame.locator('input[value="metric-net_revenue"]').uncheck()
    assert frame.locator('#metric-net_revenue').is_hidden()
    page.reload()
    frame = page.locator('#metWorkspace').element_handle().content_frame()
    frame.locator('#metric-gross_revenue canvas').wait_for(state='visible')
    assert frame.locator('#metric-net_revenue').is_hidden()
    frame.get_by_role('button', name='Show all', exact=True).click()
    assert frame.locator('#metric-net_revenue').is_visible()
    assert frame.locator('#fwMetricsCount').inner_text() == '5 of 5 shown'
    frame.get_by_role('button', name='Hide all', exact=True).click()
    assert frame.locator('#metric-net_revenue').is_hidden()
    assert frame.locator('#fwMetricsEmpty').is_visible()
    assert frame.locator('#fwMetricsCount').inner_text() == '0 of 5 shown'
    page.reload()
    frame = page.locator('#metWorkspace').element_handle().content_frame()
    frame.locator('#fwMetricsEmpty').wait_for(state='visible')
    assert frame.locator('#metric-net_revenue').is_hidden()
    frame.get_by_role('button', name='Show all', exact=True).click()
    assert frame.locator('#metric-net_revenue').is_visible()
    page.wait_for_timeout(200)
    assert page.evaluate('''() => { const f = document.getElementById('metWorkspace'), d=f.contentDocument;
        return d.documentElement.scrollWidth <= f.clientWidth + 1 && d.documentElement.scrollHeight <= f.clientHeight + 2;
    }''')
    page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
    assert page.evaluate('window.activeMetricObservers') == 0
    page.evaluate("dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    assert page.evaluate('window.activeMetricObservers') == 1
    page.set_viewport_size({'width': 390 if size[0] > 390 else 1440, 'height': 844})
    frame.locator('#metric-gross_revenue [data-stack-toggle]').get_by_role('button', name='Platform', exact=True).click()
    assert page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => {
        const f=document.getElementById('metWorkspace'); resolve(f.contentDocument.documentElement.scrollHeight <= f.clientHeight + 2);
    }))''')
    assert errors == []


def _render_workspace_charts(frame):
    """Scroll each chart into the lazy renderer's observer margin and await it."""
    canvases = frame.locator('.fw-metric-block canvas')
    for index in range(canvases.count()):
        canvas = canvases.nth(index)
        canvas.scroll_into_view_if_needed()
        frame.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute('id'))


def test_workspace_hover_matches_dates_and_clears(open_metric):
    page, errors = open_metric(workspace=True)
    frame = page.locator('#metWorkspace').element_handle().content_frame()
    # Each chart must enter the observer margin; jumping to the last chart
    # does not guarantee that intermediate charts were initialized.
    _render_workspace_charts(frame)
    frame.locator('#metric-gross_revenue canvas').scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    def hover(index):
        xy = frame.evaluate('''(index) => {const c=window._chartInstances[document.querySelector('#metric-gross_revenue canvas').id];
            const r=c.canvas.getBoundingClientRect(); return {x:r.x+c.scales.x.getPixelForValue(index),y:r.y+c.getDatasetMeta(0).data[index].y};}''', index)
        box = page.locator('#metWorkspace').bounding_box()
        page.mouse.move(box['x'] + xy['x'], box['y'] + xy['y'])
        page.wait_for_timeout(100)
    hover(2)
    assert frame.evaluate('''() => { const c=window._chartInstances[document.querySelector('#metric-orders canvas').id];
        return Math.abs(c.$metricGuide-c.scales.x.getPixelForValue(1))<1; }''')
    assert frame.evaluate("window._chartInstances[document.querySelector('#metric-inventory canvas').id]?.$metricGuide == null")
    hover(1)
    assert frame.evaluate("window._chartInstances[document.querySelector('#metric-orders canvas').id].$metricGuide == null")
    hover(2)
    page.mouse.move(1, 1)
    assert frame.evaluate('Object.values(window._chartInstances).every(c => c.$metricGuide == null)')
    hover(2)
    frame.locator('.fw-date-trigger').click()
    frame.locator('.fw-date-input[data-edge="min"]').fill('2026-09-12')
    frame.locator('.fw-date-input[data-edge="min"]').dispatch_event('change')
    assert frame.evaluate('Object.values(window._chartInstances).every(c => c.$metricGuide == null)')
    # A touch drag must not leave a shared hover guide on mobile.
    frame.evaluate('''() => { const el=document.querySelector('#metric-gross_revenue canvas'), r=el.getBoundingClientRect();
        const touch={identifier:1,target:el,clientX:r.x+100,clientY:r.y+100};
        const ev=new Event('touchmove',{bubbles:true});
        Object.defineProperty(ev,'touches',{value:[touch]});
        el.dispatchEvent(ev); }''')
    page.wait_for_timeout(100)
    assert frame.evaluate('Object.values(window._chartInstances).every(c => c.$metricGuide == null)')
    assert errors == []


def test_workspace_guide_matches_native_tooltip_far_above_line(open_metric):
    page, errors = open_metric(workspace=True)
    frame = page.locator('#metWorkspace').element_handle().content_frame()
    _render_workspace_charts(frame)
    frame.locator('#metric-gross_revenue canvas').scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    xy = frame.evaluate('''() => { const c=window._chartInstances[document.querySelector('#metric-gross_revenue canvas').id];
        const r=c.canvas.getBoundingClientRect(); return {x:r.x+c.scales.x.getPixelForValue(2),y:r.y+c.chartArea.top+10}; }''')
    box = page.locator('#metWorkspace').bounding_box()
    page.mouse.move(box['x'] + xy['x'], box['y'] + xy['y'])
    page.wait_for_timeout(100)
    result = frame.evaluate('''() => {
        const source=window._chartInstances[document.querySelector('#metric-gross_revenue canvas').id];
        const point=source.tooltip.dataPoints[0], date=source.data.labels[point.dataIndex];
        const peers=['metric-net_revenue','metric-orders'].map(id => {
            const c=window._chartInstances[document.querySelector('#'+id+' canvas').id];
            const index=c.data.labels.indexOf(date);
            return index<0 ? c.$metricGuide == null : Math.abs(c.$metricGuide-c.scales.x.getPixelForValue(index))<1;
        });
        return {date:date,tooltipDate:point.label,index:point.dataIndex,guide:source.$metricGuide,
            sourceAligned:Math.abs(source.$metricGuide-source.scales.x.getPixelForValue(point.dataIndex))<1,peers:peers};
    }''')
    assert result['index'] != 2, 'Fixture must exercise native nearest XY selecting a different date'
    assert result['tooltipDate'] == result['date']
    assert result['sourceAligned'] and all(result['peers']), result
    assert errors == []
