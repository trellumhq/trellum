"""Browser journeys for contextual feedback and frozen evidence in one queue."""

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from trellum.analysis_inbox import import_pending_capture
from trellum.cli.commands.analysis import new_analysis
from trellum.rendering.cdn import serve_vendor_request
from trellum.review import http, inject
from trellum.review.state import ReviewState
from trellum.runner.execute import run_report
from trellum.testing.test_analysis_journey import _GENERATOR


@pytest.fixture
def workspace_browser_project(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FW_NOW", "2026-09-30")
    monkeypatch.setenv("FW_UPDATE_CHECK", "0")
    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    for slug, name in (("source-one", "Source One"), ("source-two", "Source Two")):
        report = tmp_path / "reports" / slug
        report.mkdir(parents=True)
        (report / "report.yaml").write_text(json.dumps({
            "slug": slug, "name": name, "theme": "trellum light",
            "studio": "Example", "category": "Conversion",
            "description": "How do synthetic conversion counts differ by channel and region? Daily demo counts for checking filtered September evidence.",
            "annotations": False,
        }))
        (report / "generator.py").write_text(_GENERATOR)
        run_report(str(report))
    destination = new_analysis("decision", format="presentation")
    run_report(str(destination))
    output = tmp_path / "output"
    review = ReviewState()
    review.bind_dir(str(output))
    monkeypatch.setattr(http, "STATE", review)
    monkeypatch.setattr(inject, "STATE", review)
    review.start_analysis("decision")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if (http.handle_review_request(self, str(output))
                    or serve_vendor_request(self)
                    or inject.maybe_serve_injected_html(self, str(output))):
                return
            super().do_GET()

        def do_POST(self):
            if not http.handle_review_request(self, str(output)):
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", review, destination
    server.shutdown()
    server.server_close()
    thread.join()


def _pane(page, name):
    dialog = page.locator("#queue-dialog")
    if name == "Queue" and dialog.count() and dialog.is_visible():
        return
    if name != "Queue" and dialog.count() and dialog.is_visible():
        page.locator("#queue-close").click()
    tab = page.locator('[data-tab="' + name.lower() + '"]')
    if tab.is_visible():
        tab.click()


def _workspace_select(page, on=True):
    button = page.locator("#global-select")
    if (button.get_attribute("aria-pressed") == "true") != on:
        button.click()


def _send(page):
    _pane(page, "Queue")
    page.locator("#send").click()


def _queue_in_widget(page, frame, prompt):
    _workspace_select(page)
    frame.get_by_role("heading", name="September conversion", exact=True).click()
    pop = frame.locator("#fwrvPop")
    pop.wait_for(state="visible")
    frame.locator("#fwrvPopText").fill(prompt)
    frame.locator("#fwrvPopQueue").click()


@pytest.mark.parametrize("width", [1360, 390])
def test_embedded_widget_selection_shares_queue_across_sources(workspace_browser_project, width):
    from playwright.sync_api import sync_playwright

    origin, review, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 844})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = page.frame_locator("#fw-workspace-report")
            source.get_by_role("heading", name="Source One", exact=True).wait_for()
            select = page.locator("#global-select")
            assert select.is_visible()
            assert not source.locator("#fwrvBar").is_visible()
            assert not source.locator("#fwrvPanel").is_visible()
            source.locator("#fwLoading").wait_for(state="hidden")
            page.screenshot(path=f"/tmp/trellum-workspace-widget-{width}.png", full_page=True)
            if width < 768:
                box = select.bounding_box()
                assert box and box["y"] + box["height"] <= 844
            _queue_in_widget(page, source, "Add this conversion section to the analysis")
            page.wait_for_function("document.querySelector('#queue-list').textContent.includes('Snapshot ready')")
            assert "Source One · Section" in page.locator("#queue-list").inner_text()
            _pane(page, "Analysis")
            analysis = page.frame_locator("#fw-workspace-analysis")
            analysis.get_by_role("heading", name="Findings", exact=True).wait_for()
            # Selection follows the visible document through the sole parent control.
            _workspace_select(page)
            page.wait_for_function("document.getElementById('fw-workspace-analysis').contentDocument.getElementById('fwrvSelect').classList.contains('on')")
            analysis.get_by_role("heading", name="Findings", exact=True).click()
            analysis.locator("#fwrvPop").wait_for(state="visible")
            analysis.locator("#fwrvPopText").fill("Clarify this finding")
            analysis.locator("#fwrvPopQueue").click()
            page.wait_for_function("document.querySelector('#queue-count').textContent === '2'", timeout=3000)
            _pane(page, "Queue")
            assert "Source One · Section" in page.locator("#queue-list").inner_text()
            assert "Decision · Heading" in page.locator("#queue-list").inner_text()
            page.locator("#queue-close").click()
            _workspace_select(page, False)
            page.wait_for_function("!document.getElementById('fw-workspace-report').contentWindow.document.getElementById('fwrvSelect').classList.contains('on')")
            _send(page)
            page.wait_for_function("document.querySelector('#queue-count').textContent === '0'")
            status, batches = review.poll(0, "decision")
            assert status == "feedback" and len(batches) == 1
            item = batches[0]["items"][0]
            assert [item["origin_slug"] for item in batches[0]["items"]] == ["source-one", "decision"]
            assert [item["default_target_slug"] for item in batches[0]["items"]] == ["decision", "decision"]
            assert [item["component"]["kind"] for item in batches[0]["items"]] == ["Section", "Heading"]
            assert not errors
        finally:
            browser.close()


def test_unconfirmed_widget_send_keeps_newer_note(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, review, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1360, "height": 900})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            frame = page.frame_locator("#fw-workspace-report")
            _pane(page, "Reports")
            frame.get_by_role("heading", name="Source One", exact=True).wait_for()
            _pane(page, "Queue")
            note = page.locator("#chat-note")
            note.fill("First report note")
            submitted = []

            def lose_first_response(route):
                submitted.append(route.request.post_data_json)
                response = route.fetch()
                if len(submitted) == 1:
                    route.abort("failed")
                else:
                    route.fulfill(response=response)

            page.route("**/_fw/review/workspace/feedback", lose_first_response)
            page.locator("#send").click()
            page.locator("#send-status").get_by_text("Send not confirmed", exact=False).wait_for()
            note.fill("Newer report draft")
            page.locator("#send").click()
            page.locator("#send-status").get_by_text("Sent to the analysis session.", exact=True).wait_for()
            assert submitted[0] == submitted[1]
            assert note.input_value() == "Newer report draft"
            status, batches = review.poll(0, "decision")
            assert status == "feedback" and len(batches) == 1
            assert batches[0]["items"][0]["prompt"] == "First report note"
            assert batches[0]["items"][0]["origin_slug"] == "source-one"
        finally:
            browser.close()


def test_custom_canvas_selection_queues_frozen_evidence(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, review, destination = workspace_browser_project
    source_dir = destination.parent / "source-one"
    generator = _GENERATOR.replace("BarChart, DataSource, FilterBar", "BarChart, DataSource, FilterBar, RawHTML")
    generator += '''        ctx.add_section("Custom funnel", [RawHTML(
            html='<canvas id="custom-funnel" width="400" height="180"></canvas>',
            js="var c=document.getElementById('custom-funnel'); var g=c.getContext('2d'); g.fillStyle='navy'; g.fillRect(0,0,300,100);")])
'''
    (source_dir / "generator.py").write_text(generator)
    run_report(str(source_dir))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 960})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = page.frame_locator("#fw-workspace-report")
            source.locator("#custom-funnel").wait_for()
            source.locator("#fwLoading").wait_for(state="hidden")
            source.locator("#custom-funnel").evaluate(
                "() => fw.filterEngine.setFilter('conversions', 'region-selection', 'region', 'equals', 'NL')")
            _workspace_select(page)
            source.locator("#custom-funnel").click()
            source.locator("#fwrvPop").wait_for(state="visible")
            source.locator("#fwrvPopText").fill("Use this funnel in the analysis")
            source.locator("#fwrvPopQueue").click()
            page.wait_for_function("document.getElementById('queue-list').textContent.includes('Snapshot ready')")
            assert "Snapshot ready" in page.locator("#queue-list").inner_text()
            _send(page)
            page.wait_for_function("document.querySelectorAll('#queue-list .fw-queue-item').length === 0")
            status, batches = review.poll(0, "decision")
            assert status == "feedback"
            item = batches[0]["items"][0]
            assert item["component"]["kind"] == "RawHTML"
            assert item["capture_id"]
            import_pending_capture(str(destination), item["capture_id"], "custom-funnel")
            assert (destination / "evidence/custom-funnel.png").stat().st_size > 100
            provenance = json.loads((destination / "evidence/custom-funnel.json").read_text())
            assert provenance["filters"] == item["filters"]
            assert provenance["filters"]["conversions"]["region-selection"]["value"] == "NL"
        finally:
            browser.close()
