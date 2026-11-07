"""Real generated Overview reports, native controls, downloads and captures."""

import base64
import csv
import json
import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from trellum.testing.test_overview_composition import build_overview, expected_values


def _hydrate(page):
    page.evaluate("document.fonts.ready")
    for canvas in page.locator("canvas:visible").all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))


def _series(page, ids):
    return page.evaluate("ids => ids.map(id => ({labels: _chartInstances[id].data.labels, "
                         "values: _chartInstances[id].data.datasets[0].data}))", ids)


def _assert_values(page, ids, expected):
    page.wait_for_function("([id,n]) => window._chartInstances[id].data.labels.length === n",
                           arg=[ids[0], len(expected["days"])])
    actual = _series(page, ids)
    for item, vector in zip(actual, expected["series"]):
        assert item["labels"] == expected["days"]
        assert item["values"] == pytest.approx(vector)
    formats = ["currency", "number", "currency", "percent", "currency", "currency"]
    for cid, mean, fmt in zip(ids, expected["means"], formats):
        # Only formatting uses the runtime; every expected number comes from Python.
        text = page.evaluate("([n,f]) => getFormatter(f)(n)", [mean, fmt])
        expect(page.locator(f'[data-chart-summary="{cid}"] .fw-chart-summary-value')).to_have_text(text)
        expect(page.locator(f'[data-chart-summary="{cid}"] .fw-chart-summary-label')).to_have_text("Daily avg")
    kpi_formats = ["currency", "number", "currency", "percent", "currency", "currency", "percent"]
    expected_text = [page.evaluate("([n,f]) => f === 'percent' ? (n*100).toFixed(1)+'%' : getFormatter(f)(n)",
                                   [value, fmt]) for value, fmt in zip(expected["kpis"], kpi_formats)]
    assert page.locator(".fw-kpi-grid").first.locator(".fw-kpi-value").all_text_contents() == expected_text
    # Currency ratios must be ordinary USD/install, never multiplied by 100.
    assert float(expected_text[4].replace("$", "")) == pytest.approx(expected["kpis"][4], abs=.005)
    assert float(expected_text[5].replace("$", "")) == pytest.approx(expected["kpis"][5], abs=.005)
    doughnuts = page.evaluate("""() => Array.from(document.querySelectorAll('.fw-doughnut canvas')).map(el => {
        const c=_chartInstances[el.id]; return {labels:c.data.labels, values:c.data.datasets[0].data};
    })""")
    for result, column in zip(doughnuts, ("cost", "installs", "d7_revenue")):
        assert set(result["labels"]) == {r["acquisition_type"] for r in expected["facts"]}
        for label, value in zip(result["labels"], result["values"]):
            assert value == pytest.approx(sum(r[column] for r in expected["facts"] if r["acquisition_type"] == label))


def _assert_geometry(page, ids, width):
    for cid in ids:
        assert page.locator(f"#{cid}").bounding_box()["height"] == 160
        assert page.locator(f'[data-chart-summary="{cid}"]').is_visible()
    grids = page.locator(".fw-kpi-grid")
    boxes = grids.evaluate_all("""rows => rows.map(row => {
        const base=row.getBoundingClientRect(); return Array.from(row.children).map(c=>{
            const r=c.getBoundingClientRect();return [r.x-base.x,r.y-base.y,r.width,r.height];});
    })""")
    assert boxes[0] == boxes[1]  # Same-content, ordinary current-runtime row.
    assert len(boxes[0]) == 7
    assert len({b[1] for b in boxes[0]}) == (1 if width == 1280 else 4)
    assert page.locator(".fw-kpi-extra,.fw-kpi-more-btn").count() == 0
    assert page.locator(".fw-doughnut-center-value").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_overview_native_filter_series_geometry_exports_and_themes(tmp_path, width):
    output = tmp_path / "native"
    build_overview(output, reference=True)
    evidence = Path(os.environ.get("TRELLUM_OVERVIEW_EVIDENCE", "/tmp/trellum-overview-composition")) / str(width)
    evidence.mkdir(parents=True, exist_ok=True)
    shutil.copytree(output, evidence / "native", dirs_exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(output)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
                errors, warnings, failed_requests = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else
                        warnings.append(msg.text) if msg.type == "warning" else None)
                page.on("requestfailed", lambda req: failed_requests.append(req.url))
                page.on("response", lambda response: failed_requests.append(response.url) if response.status >= 400 else None)
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                _hydrate(page)
                section = page.locator(".fw-section").filter(has=page.get_by_role(
                    "heading", name="Overview — synthetic acquisition cohorts", exact=True)).first
                detail = page.locator("#overview-daily-detail")
                expect(detail).to_have_class("fw-section fw-collapsible fw-collapsed")
                lines = section.locator(".fw-line-compact canvas")
                assert lines.count() == 6
                ids = [c.get_attribute("id") for c in lines.all()]
                original = expected_values()
                _assert_values(page, ids, original)
                _assert_geometry(page, ids, width)
                # A narrow actual panel forces authored headers to wrap while
                # preserving every explicit compact canvas height.
                grid = lines.first.locator('xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," fw-grid ")][1]')
                grid.evaluate("el => el.style.width='240px'")
                _hydrate(page)
                assert grid.locator(".fw-chart-head").evaluate_all(
                    "els => els.some(el => el.getBoundingClientRect().height > 30)")
                for cid in ids:
                    assert page.locator(f"#{cid}").bounding_box()["height"] == 160
                grid.evaluate("el => el.style.width=''")

                bar = section.locator(".fw-filter-bar")
                bar.get_by_role("button", name="iOS", exact=True).click()
                _assert_values(page, ids, expected_values(platform="iOS"))
                bar.locator(".fw-date-trigger").click()
                bar.get_by_label("Start date", exact=True).fill("2026-10-15")
                page.keyboard.press("Escape")
                selected = expected_values(15, 28, "iOS")
                _assert_values(page, ids, selected)
                bar.get_by_role("button", name="Paid search", exact=True).click()
                _assert_values(page, ids, expected_values(15, 28, "iOS", "Paid search"))
                # Clear just the acquisition type, preserving date/platform.
                bar.locator(".fw-filter-item").filter(has=page.get_by_text(
                    "Overview: acquisition type", exact=True)).get_by_role("button", name="All", exact=True).click()
                _assert_values(page, ids, selected)
                detail.locator(".fw-collapsible-toggle").click()
                _hydrate(page)
                assert section.locator("canvas:visible").count() == 11
                detail_ids = [c.get_attribute("id") for c in detail.locator("canvas").all()]
                for cid in detail_ids:
                    assert page.locator(f"#{cid}").bounding_box()["height"] == (320 if width == 1280 else 180)
                cost_bars = page.evaluate("id => _chartInstances[id].data.datasets.map(d=>d.data)", detail_ids[0])
                assert cost_bars[0] == pytest.approx(selected["series"][0])
                assert cost_bars[1] == pytest.approx(selected["series"][2])
                # Use the existing detail legend, then restore the same series.
                detail.locator(".fw-chart-legend").get_by_text("D7 revenue", exact=True).click()
                assert not page.evaluate("id => _chartInstances[id].isDatasetVisible(1)", detail_ids[0])
                detail.locator(".fw-chart-legend").get_by_text("D7 revenue", exact=True).click()
                assert page.evaluate("id => _chartInstances[id].isDatasetVisible(1)", detail_ids[0])
                detail.get_by_role("button", name="Percentage", exact=True).click()
                stack_id = detail_ids[1]
                stack = page.evaluate("id => _chartInstances[id].data.datasets.map(d=>d.data)", stack_id)
                assert len(stack) == 3
                for i in range(14):
                    assert sum(s[i] for s in stack) == pytest.approx(100)
                stack_labels = page.evaluate("id => _chartInstances[id].data.datasets.map(d=>d.label)", stack_id)
                for label, values in zip(stack_labels, stack):
                    for day, actual in zip(selected["days"], values):
                        day_facts = [r for r in selected["facts"] if r["cohort_date"] == day]
                        expected = 100 * sum(r["installs"] for r in day_facts if r["acquisition_type"] == label) / sum(r["installs"] for r in day_facts)
                        assert actual == pytest.approx(expected)
                # Native plotted lines keep straight interpolation, adaptive
                # markers and nearest-point interaction; use real pointer input.
                options = page.evaluate("""id => {const c=_chartInstances[id];return {
                    tension:c.data.datasets[0].tension, radiusType:typeof c.data.datasets[0].pointRadius,
                    mode:c.options.interaction.mode};} """, ids[0])
                assert options["tension"] == 0 and options["radiusType"] == "function"
                assert options["mode"] == "nearest"
                page.locator(f"#{ids[0]}").scroll_into_view_if_needed()
                point = page.evaluate("id => {const p=_chartInstances[id].getDatasetMeta(0).data[3];return {x:p.x,y:p.y}}", ids[0])
                box = page.locator(f"#{ids[0]}").bounding_box()
                page.mouse.move(box["x"] + point["x"], box["y"] + point["y"])
                page.wait_for_function("id => _chartInstances[id].tooltip.opacity > 0", arg=ids[0])
                assert page.evaluate("id => _chartInstances[id].tooltip.dataPoints[0].raw", ids[0]) == pytest.approx(selected["series"][0][3])

                # Every native chart CSV is downloaded, including normalized
                # stack values and the detailed cost/revenue grouped bars.
                csv_dir = evidence / "csv"
                csv_dir.mkdir(exist_ok=True)
                for index, button in enumerate(section.locator("button[data-csv-chart]").all()):
                    cid = button.get_attribute("data-csv-chart")
                    button.evaluate("el => el.scrollIntoView({block:'center'})")
                    with page.expect_download() as download:
                        button.click()
                    path = csv_dir / f"{index:02d}.csv"
                    download.value.save_as(str(path))
                    with path.open(encoding="utf-8-sig", newline="") as stream:
                        rows = list(csv.reader(stream))
                    assert len(rows) > 1 and rows[0]
                    if index < 3:
                        column = ("cost", "installs", "d7_revenue")[index]
                        assert len(rows) == 4
                        for label, value in rows[1:]:
                            assert float(value) == pytest.approx(sum(r[column] for r in selected["facts"] if r["acquisition_type"] == label))
                    if cid in ids:
                        vector = selected["series"][ids.index(cid)]
                        assert [r[0] for r in rows[1:]] == selected["days"]
                        assert [float(r[1]) for r in rows[1:]] == pytest.approx(vector)
                    elif cid == detail_ids[0]:
                        assert [r[0] for r in rows[1:]] == selected["days"]
                        assert [float(r[1]) for r in rows[1:]] == pytest.approx(selected["series"][0])
                        assert [float(r[2]) for r in rows[1:]] == pytest.approx(selected["series"][2])
                    elif cid == stack_id:
                        assert [r[0] for r in rows[1:]] == selected["days"]
                        for row in rows[1:]:
                            assert sum(map(float, row[1:])) == pytest.approx(100)
                    # Chromium throttles more than ten downloads per second;
                    # pace this eleven-control audit without replacing downloads.
                    page.wait_for_timeout(150)
                assert len(list(csv_dir.glob("*.csv"))) == 11

                for theme in ("trellum light", "trellum dark"):
                    page.locator("#fwThemeSelect").select_option(theme)
                    _hydrate(page)
                    _assert_values(page, ids, selected)
                    _assert_geometry(page, ids, width)
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(path=str(evidence / f"{theme.split()[-1]}-expanded-filtered.png"), full_page=True)
                # Native section/report capture uses the actual selected report.
                if width == 1280:
                    snapshot = section.evaluate("el => fw.captureElementSnapshotForAnalysis(el)")
                    (evidence / "native-section.json").write_text(json.dumps(snapshot, indent=2))
                    (evidence / "native-section.png").write_bytes(base64.b64decode(snapshot["image"]["data_base64"]))
                    for extension, function in (("png", "exportPNG"), ("pdf", "exportPDF")):
                        with page.expect_download(timeout=60000) as download:
                            page.evaluate(f"fw.{function}()")
                        path = evidence / f"native-report.{extension}"
                        download.value.save_as(str(path))
                        assert path.stat().st_size > 1000
                        assert path.read_bytes().startswith(b"%PDF-" if extension == "pdf" else b"\x89PNG")

                detail.locator(".fw-collapsible-toggle").click()
                expect(detail).to_have_class("fw-section fw-collapsible fw-collapsed")
                bar.locator(".fw-date-trigger").click()
                bar.locator('.fw-date-preset[data-days="all"]').click()
                # Both category toggles are back to All after clearing platform.
                page.keyboard.press("Escape")
                bar.locator(".fw-filter-item").filter(has=page.get_by_text(
                    "Overview: platform", exact=True)).get_by_role("button", name="All", exact=True).click()
                _hydrate(page)
                _assert_values(page, ids, original)
                _assert_geometry(page, ids, width)
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(evidence / "dark-collapsed-restored.png"), full_page=True)
                (evidence / "browser-diagnostics.json").write_text(json.dumps({
                    "errors": errors, "warnings": warnings, "failed_requests": failed_requests,
                    "selection": {"start": "2026-10-15", "end": "2026-10-28", "platform": "iOS"},
                    "filtered_series": selected["series"], "filtered_means": selected["means"],
                    "filtered_period_kpis": selected["kpis"], "line_options": options,
                }, indent=2))
                assert errors == [] and failed_requests == []
                # Native html2canvas capture may emit Chromium's readback
                # performance advice. Keep every warning in evidence; fail on
                # unrelated warnings rather than hiding them in a broad allowlist.
                assert all(w.startswith("Canvas2D: Multiple readback operations using getImageData")
                           for w in warnings), warnings
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
