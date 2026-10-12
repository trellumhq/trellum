"""End-to-end local analysis capture, editing, and review workflow."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trellum.cli import main
from trellum.review import handle_review_request, maybe_serve_injected_html
from trellum.rendering.cdn import serve_vendor_request
from trellum.runner.execute import run_report


_SOURCE = '''from trellum import BaseReport
from trellum.components import BarChart, DataSource, FilterBar
import pandas as pd

class SourceReport(BaseReport):
    def generate(self, ctx):
        df = pd.DataFrame({"channel": ["Web", "Mobile", "Web", "Mobile"],
                           "region": ["NL", "NL", "DE", "DE"],
                           "conversion": [12, 8, 11, 9]})
        ctx.add_section("", [DataSource("conversions", df),
            FilterBar("conversions", df, filters=[{"column": "region", "label": "Region"}])])
        ctx.add_section("September conversion", [BarChart(df, x="channel",
            y="conversion", title="Conversion by channel", dataset_id="conversions")])
'''


def _json_request(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_local_analysis_capture_import_and_slide_review(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FW_UPDATE_CHECK", "0")
    source = tmp_path / "reports" / "conversion-source"
    source.mkdir(parents=True)
    (source / "report.yaml").write_text(
        "slug: conversion-source\nname: Conversion overview\ntheme: trellum light\n",
        encoding="utf-8",
    )
    (source / "generator.py").write_text(_SOURCE, encoding="utf-8")
    run_report(str(source))

    assert main(["analysis", "new", "presentation", "--format", "presentation"]) == 0
    presentation = tmp_path / "reports" / "presentation"
    output_root = tmp_path / "output"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_root), **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if (handle_review_request(self, str(output_root))
                    or serve_vendor_request(self)
                    or maybe_serve_injected_html(self, str(output_root))):
                return
            super().do_GET()

        def do_POST(self):
            if not handle_review_request(self, str(output_root)):
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        active = _json_request(origin + "/_fw/review/analysis/start", "POST", {"slug": "presentation"},
                               {"Origin": origin})
        assert active["active"] and active["slug"] == "presentation"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1360, "height": 960})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/conversion-source/", wait_until="networkidle")
                page.wait_for_function("window._chartInstances && Object.keys(window._chartInstances).length")
                page.evaluate("fw.filterEngine.setFilter('conversions', 'analysis-region', 'region', 'equals', 'NL')")
                page.wait_for_function("Object.values(window._chartInstances)[0].data.datasets[0].data.reduce((a,b) => a+b, 0) === 20")
                page.evaluate("void fw.captureForAnalysis()")
                page.locator(".fw-chart-container.fw-analysis-selectable").click()
                page.get_by_label("Note for your agent (optional)").fill("Investigate the filtered result")
                page.get_by_role("dialog").get_by_role("button", name="Send to agent", exact=True).click()
                page.get_by_text("Capture sent", exact=False).wait_for(state="visible")

                polled = _json_request(origin + "/_fw/review/poll?timeout=0&slug=presentation")
                capture_id = polled["batches"][0]["items"][0]["data"]["capture_id"]
                assert polled["batches"][0]["note"] == "Investigate the filtered result"
                assert main(["analysis", "import", str(presentation), "--inbox", capture_id,
                             "--name", "conversion"]) == 0
                provenance = json.loads((presentation / "evidence" / "conversion.json").read_text(encoding="utf-8"))
                assert provenance["filters"]["conversions"]["analysis-region"]["value"] == "NL"
                assert (presentation / "evidence" / "conversion.png").is_file()

                slides_path = presentation / "slides.yaml"
                slides_path.write_text(json.dumps({"version": 1, "slides": [
                    {"id": "opening", "layout": "title", "title": "September findings", "text": "Reporting period: September 2026."},
                    {"id": "finding", "layout": "image-text", "title": "Conversion by channel", "text": "Investigate the filtered result.", "image": "evidence/conversion.png", "alt": "Filtered synthetic conversion chart"},
                ]}), encoding="utf-8")

                def no_warehouse(*_args, **_kwargs):
                    raise AssertionError("Analysis builds must not query a warehouse")

                monkeypatch.setattr("trellum.report.ReportContext.get_connection", no_warehouse)
                run_report(str(presentation))
                preview = output_root / "presentation" / "index.html"
                good_preview = hashlib.sha256(preview.read_bytes()).hexdigest()
                slides_path.write_text("version: 1\nslides: [\n", encoding="utf-8")
                with pytest.raises(Exception):
                    run_report(str(presentation))
                assert hashlib.sha256(preview.read_bytes()).hexdigest() == good_preview
                slides_path.write_text(json.dumps({"version": 1, "slides": [
                    {"id": "opening", "layout": "title", "title": "September findings", "text": "Reporting period: September 2026."},
                    {"id": "finding", "layout": "image-text", "title": "Conversion by channel", "text": "Investigate the filtered result.", "image": "evidence/conversion.png", "alt": "Filtered synthetic conversion chart"},
                ]}), encoding="utf-8")
                run_report(str(presentation))

                page.goto(origin + "/presentation/", wait_until="networkidle")
                assert page.locator("#slide-finding").is_visible()
                page.click("#fwrvSelect")
                slide = page.locator("#slide-finding")
                slide.hover()
                slide.click()
                page.wait_for_selector("#fwrvPop", state="visible")
                page.fill("#fwrvPopText", "Keep this slide tied to the evidence")
                page.click("#fwrvPopQueueSend")
                page.wait_for_selector("#fwrvPop", state="hidden")
                reviewed = _json_request(origin + "/_fw/review/poll?timeout=0&slug=presentation")
                item = reviewed["batches"][0]["items"][0]
                assert item["prompt"] == "Keep this slide tied to the evidence"
                assert item["section"]["id"] == "slide-finding"
                assert item["section"]["title"] == "Conversion by channel"

                slides_path.write_text(slides_path.read_text(encoding="utf-8").replace(
                    "Investigate the filtered result.", "The filtered result is stable."), encoding="utf-8")
                run_report(str(presentation))
                page.reload(wait_until="networkidle")
                assert page.locator("#slide-finding").inner_text().find("The filtered result is stable.") >= 0
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_analysis_session_cli_validates_kind_and_prints_urls(tmp_path, monkeypatch, capsys):
    from trellum.cli.commands import analysis_session

    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "decision"
    report.mkdir()
    (report / "report.yaml").write_text("kind: analysis\nslug: decision\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(analysis_session, "_cmd_review_start", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(analysis_session, "_review_port_or_explain", lambda _output: 8765)
    monkeypatch.setattr(analysis_session, "_review_http",
                        lambda port, method, path, body: (calls.append((port, method, path, body)) or (200, {"active": True})))
    assert main(["analysis", "session", "decision", "--no-browser"]) == 0
    output = capsys.readouterr().out
    assert (8765, "POST", "/_fw/review/analysis/start", {"slug": "decision"}) in calls
    assert "http://localhost:8765/decision/index.html" in output
    assert "http://localhost:8765/" in output

    (report / "report.yaml").write_text("kind: report\nslug: decision\n", encoding="utf-8")
    assert main(["analysis", "session", "decision", "--no-browser"]) == 1
    assert "kind: analysis" in capsys.readouterr().err
