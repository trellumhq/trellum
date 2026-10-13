"""Snapshot-only browser capture for the local analysis workspace."""
from __future__ import annotations

import base64
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from trellum.rendering.cdn import serve_vendor_request
from trellum.review import handle_review_request, maybe_serve_injected_html
from trellum.runner.execute import run_report
from trellum.testing.test_analysis_journey import _GENERATOR


def test_workspace_capture_returns_frozen_snapshot_without_delivery(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FW_UPDATE_CHECK", "0")
    source = tmp_path / "reports" / "conversion-source"
    source.mkdir(parents=True)
    (source / "report.yaml").write_text(json.dumps({
        "slug": "conversion-source", "name": "Conversion overview",
        "theme": "trellum light", "studio": "Example", "category": "Conversion",
        "description": "Synthetic data for an evidence capture.", "annotations": False,
    }), encoding="utf-8")
    generator = _GENERATOR.replace(
        "from trellum.components import BarChart, DataSource, FilterBar",
        "from trellum.components import BarChart, DataSource, FilterBar, RawHTML",
    ).replace(
        "dataset_id=\"conversions\")])",
        "dataset_id=\"conversions\")])\n"
        "        ctx.add_section(\"Canvas evidence\", [RawHTML("
        "html='<canvas id=\"raw-evidence\" width=\"400\" height=\"180\"></canvas>', "
        "js=\"var canvas = document.getElementById('raw-evidence'); "
        "var context = canvas.getContext('2d'); context.fillStyle = 'navy'; "
        "context.fillRect(20, 20, 300, 120);\")])",
    )
    (source / "generator.py").write_text(generator, encoding="utf-8")
    run_report(str(source))
    output_root = str(tmp_path / "output")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=output_root, **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if (handle_review_request(self, output_root)
                    or serve_vendor_request(self)
                    or maybe_serve_injected_html(self, output_root)):
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/conversion-source/?share_token=secret", wait_until="networkidle")
                page.wait_for_function("window._chartInstances && Object.keys(window._chartInstances).length > 0")
                page.evaluate("fw.filterEngine.setFilter('conversions', 'workspace-region', 'region', 'equals', 'NL')")
                page.wait_for_function("Object.values(window._chartInstances)[0].data.datasets[0].data.reduce((a,b) => a+b, 0) === 20")
                page.evaluate("window.workspaceAnchorDownloads = 0; HTMLAnchorElement.prototype.click = function() { if (this.download) window.workspaceAnchorDownloads++; }")
                result = page.locator(".fw-chart-container").evaluate("el => fw.captureElementSnapshotForAnalysis(el)")
                # Playwright serializes promises returned by evaluate.
                assert result["format"] == "trellum-analysis-capture"
                assert result["version"] == 1
                assert result["image"]["mime_type"] == "image/png"
                assert len(base64.b64decode(result["image"]["data_base64"])) > 2000
                assert result["source"]["report_slug"] == "conversion-source"
                assert result["source"]["component_id"]
                assert result["source"]["source_built_at"]
                assert result["source"]["url"] == origin + "/conversion-source/"
                assert result["source"]["filters"]["conversions"]["workspace-region"]["value"] == "NL"
                assert not page.locator("dialog").count()
                assert not page.locator(".fw-analysis-capture-ui").count()
                assert page.evaluate("window.workspaceAnchorDownloads") == 0
                assert not errors

                raw_canvas = page.locator(".fw-raw").filter(has=page.locator("#raw-evidence"))
                raw_snapshot = raw_canvas.evaluate("el => fw.captureElementSnapshotForAnalysis(el)")
                assert raw_snapshot["format"] == "trellum-analysis-capture"
                assert len(base64.b64decode(raw_snapshot["image"]["data_base64"])) > 500
                assert raw_snapshot["source"]["component_title"] == "Canvas evidence"
                assert page.evaluate("window.workspaceAnchorDownloads") == 0
                assert not page.locator("dialog").count()
                assert not page.locator(".fw-analysis-capture-ui").count()

                # A failed renderer rejects cleanly and restores controls hidden during capture.
                filter_bar = page.locator(".fw-filter-bar").first
                before = filter_bar.evaluate("el => el.style.display")
                failure = page.locator(".fw-chart-container").evaluate("""async el => {
                    const old = window.html2canvas;
                    window.html2canvas = () => Promise.reject(new Error('renderer failed'));
                    try { await fw.captureElementSnapshotForAnalysis(el); return null; }
                    catch (error) { return error.message; }
                    finally { window.html2canvas = old; }
                }""")
                assert failure == "renderer failed"
                assert filter_bar.evaluate("el => el.style.display") == before
                assert page.evaluate("window.workspaceAnchorDownloads") == 0
                assert not page.locator("dialog").count()
                assert not page.locator(".fw-analysis-capture-ui").count()
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
