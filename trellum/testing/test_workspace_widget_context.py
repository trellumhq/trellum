"""Context isolation across reports using the shared bottom review widget."""

import json

import pytest

from trellum.analysis_inbox import import_pending_capture
from trellum.testing.test_workspace_journey import _pane
from trellum.testing.test_workspace_journey import (
    workspace_browser_project as workspace_browser_project,
)


def _select(frame):
    button = frame.locator("#fwrvSelect")
    button.wait_for()
    if not button.evaluate("el => el.classList.contains('on')"):
        button.click()


def _queue(frame, target, prompt):
    _select(frame)
    target.click()
    frame.locator("#fwrvPop").wait_for(state="visible")
    frame.locator("#fwrvPopText").fill(prompt)
    frame.locator("#fwrvPopQueue").click()


@pytest.mark.parametrize("width", [1360, 390])
def test_mixed_widget_requests_keep_their_original_page(workspace_browser_project, width):
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
            source.locator("#fwLoading").wait_for(state="hidden")
            _queue(source, source.get_by_role("heading", name="September conversion", exact=True), "Add this section to the analysis")
            try:
                page.wait_for_function("document.getElementById('queue-count').textContent === '1'")
            except Exception as error:
                state = page.evaluate("""() => {
                  const frame = document.getElementById('fw-workspace-report');
                  const child = frame && frame.contentWindow;
                  const doc = child && child.document;
                  const chart = doc && doc.querySelector('canvas');
                  const chartState = chart && child._chartInstances && child._chartInstances[chart.id];
                  return {
                    queueCount: document.getElementById('queue-count')?.textContent,
                    queueList: document.getElementById('queue-list')?.innerText,
                    widgetError: doc?.getElementById('fwrvWorkspaceError')?.textContent,
                    popVisible: !!doc?.getElementById('fwrvPop') && !doc.getElementById('fwrvPop').hidden,
                    chartReady: !!chartState,
                    canvasSize: chart && [chart.width, chart.height],
                    sendStatus: document.getElementById('send-status')?.textContent
                  };
                }""")
                raise AssertionError(f"Source selection did not queue: {state}; page errors: {errors}") from error
            page.get_by_label("Source report", exact=True).select_option("source-two")
            source.get_by_role("heading", name="Source Two", exact=True).wait_for()
            source.locator("#fwrvQueueBtn").click()
            assert "Source One" in source.locator("#fwrvList").inner_text()
            _pane(page, "Analysis")
            analysis = page.frame_locator("#fw-workspace-analysis")
            analysis.locator("#fwLoading").wait_for(state="hidden")
            _queue(analysis, analysis.get_by_role("heading", name="Findings", exact=True), "Rename this analysis slide")
            page.wait_for_function("document.getElementById('queue-count').textContent === '2'")
            analysis.locator("#fwrvBarSend").click()
            page.wait_for_function("document.getElementById('queue-count').textContent === '0'")
            status, batches = review.poll(0, "decision")
            assert status == "feedback" and len(batches) == 1
            items = batches[0]["items"]
            assert [item["origin_slug"] for item in items] == ["source-one", "decision"]
            assert [item["default_target_slug"] for item in items] == ["decision", "decision"]
            assert [item["component"]["kind"] for item in items] == ["Section", "Heading"]
            assert review.poll(0, "source-one")[0] == "waiting"
            assert not errors
        finally:
            browser.close()


def test_standalone_report_keeps_original_widget_and_source_queue(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, review, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1360, "height": 844})
            page.goto(origin + "/source-one/index.html")
            page.locator("#fwrvSelect").wait_for()
            assert page.evaluate("typeof window._fwWorkspaceChild") == "undefined"
            assert page.evaluate("!!window._fwReviewInit")
            page.locator("#fwLoading").wait_for(state="hidden")
            _queue(page, page.get_by_role("heading", name="September conversion", exact=True), "Change this report section")
            page.locator("#fwrvBarSend").click()
            page.wait_for_function("document.getElementById('fwrvCount').textContent === '0'")
            status, batches = review.poll(0, "source-one")
            assert status == "feedback" and len(batches) == 1
            assert not batches[0].get("workspace")
            assert batches[0]["slug"] == "source-one"
            assert review.poll(0, "decision")[0] == "waiting"
        finally:
            browser.close()


def test_embedded_presentation_enters_navigates_and_exits(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(origin + "/_fw/review/workspace")
            analysis = page.frame_locator("#fw-workspace-analysis")
            analysis.get_by_role("button", name="Present", exact=True).wait_for()
            analysis.locator("#fwLoading").wait_for(state="hidden")
            assert page.locator("#fw-workspace-analysis").evaluate("el => el.allowFullscreen")

            # Selection mode owns document clicks and keyboard review shortcuts;
            # presentation keyboard checks use the normal viewing workflow.
            select = analysis.locator("#fwrvSelect")
            if select.evaluate("el => el.classList.contains('on')"):
                select.click()
            assert not select.evaluate("el => el.classList.contains('on')")
            opener = analysis.get_by_role("button", name="Present", exact=True)
            opener.focus()
            analysis.get_by_role("button", name="Present", exact=True).click()
            analysis.locator("body.fw-presenting").wait_for()
            assert analysis.locator(".fw-slide:visible").count() == 1

            # Arrow keys navigate in both directions; Home/End and the disabled
            # boundary controls keep the current slide within the deck.
            page.keyboard.press("ArrowRight")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 2 of 2"
            assert analysis.get_by_role("button", name="Next", exact=True).is_disabled()
            page.keyboard.press("ArrowLeft")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 1 of 2"
            page.keyboard.press("ArrowDown")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 2 of 2"
            page.keyboard.press("ArrowUp")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 1 of 2"
            page.keyboard.press("End")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 2 of 2"
            page.keyboard.press("Home")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 1 of 2"
            assert analysis.get_by_role("button", name="Previous", exact=True).is_disabled()

            # Tab and Shift+Tab stay within presentation controls, including
            # when focus starts on the slide heading.
            analysis.locator(".fw-slide:visible h2").focus()
            page.keyboard.press("Shift+Tab")
            assert analysis.get_by_role("button", name="Exit presentation", exact=True).evaluate("el => el === document.activeElement")
            page.keyboard.press("Tab")
            assert analysis.get_by_role("button", name="Next", exact=True).evaluate("el => el === document.activeElement")
            page.keyboard.press("Shift+Tab")
            assert analysis.get_by_role("button", name="Exit presentation", exact=True).evaluate("el => el === document.activeElement")
            analysis.get_by_role("button", name="Exit presentation", exact=True).focus()
            page.keyboard.press("Tab")
            assert analysis.get_by_role("button", name="Next", exact=True).evaluate("el => el === document.activeElement")

            # Editable fields retain arrow and Home/End keys for caret movement.
            analysis.locator(".fw-slide:visible").evaluate("el => { const input=document.createElement('input'); input.value='editable'; el.appendChild(input); }")
            field = analysis.locator(".fw-slide:visible input")
            field.focus()
            page.keyboard.press("Home")
            page.keyboard.press("ArrowRight")
            assert analysis.locator(".fw-presentation-position").inner_text() == "Slide 1 of 2"

            # The workspace iframe's fullscreen denial uses the in-window mode.
            analysis.locator(".fw-presentation").evaluate("el => { el.requestFullscreen = () => Promise.reject(new Error('denied')); }")
            page.keyboard.press("Escape")
            analysis.locator("body.fw-presenting").wait_for(state="hidden")
            assert analysis.locator(".fw-slide:visible").count() == 2
            assert opener.evaluate("el => el === document.activeElement")
            analysis.get_by_role("button", name="Present", exact=True).click()
            analysis.locator(".fw-presentation-notice").get_by_text("Fullscreen unavailable", exact=False).wait_for()
            page.emulate_media(media="print")
            assert not analysis.locator(".fw-presentation-controls").is_visible()
            page.emulate_media(media="screen")
            analysis.get_by_role("button", name="Exit presentation", exact=True).click()
            analysis.locator("body.fw-presenting").wait_for(state="hidden")
        finally:
            browser.close()


def test_failed_capture_can_queue_its_context_without_snapshot(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, review, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = page.frame_locator("#fw-workspace-report")
            source.locator("canvas").wait_for()
            source.locator("#fwLoading").wait_for(state="hidden")
            source.locator("canvas").evaluate("el => fw.captureElementSnapshotForAnalysis = () => Promise.reject(new Error('Snapshot unavailable'))")
            _queue(source, source.locator("canvas"), "Include this source context")
            page.wait_for_function("document.getElementById('queue-list').textContent.includes('Snapshot unavailable')")
            assert source.locator("#fwrvPopText").input_value() == "Include this source context"
            _pane(page, "Queue")
            page.get_by_role("button", name="Queue text without snapshot", exact=True).click()
            assert "Snapshot needs attention" not in page.locator("#queue-list").inner_text()
            _pane(page, "Reports")
            source.locator("#fwrvBarSend").click()
            page.wait_for_function("document.getElementById('queue-count').textContent === '0'")
            status, batches = review.poll(0, "decision")
            assert status == "feedback" and len(batches) == 1
            item = batches[0]["items"][0]
            assert item["origin_slug"] == "source-one"
            assert item["destination"] == "decision"
            assert not item.get("capture_id")
        finally:
            browser.close()


@pytest.mark.parametrize("width", [1360, 390])
def test_filtered_widget_capture_survives_source_switch(workspace_browser_project, width):
    from playwright.sync_api import sync_playwright

    origin, review, destination = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 844})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = page.frame_locator("#fw-workspace-report")
            source.locator("canvas").wait_for()
            source.locator("#fwLoading").wait_for(state="hidden")
            source.locator("canvas").evaluate("el => fw.filterEngine.setFilter('conversions','test-region','region','equals','NL')")
            page.wait_for_function("Object.values(document.getElementById('fw-workspace-report').contentWindow._chartInstances)[0].data.datasets[0].data.reduce((a,b)=>a+b,0) === 20")
            _queue(source, source.locator("canvas"), "Add this filtered chart to the analysis")
            page.wait_for_function("document.getElementById('queue-list').textContent.includes('Snapshot')")
            assert "Snapshot ready" in page.locator("#queue-list").inner_text()
            page.get_by_label("Source report", exact=True).select_option("source-two")
            source.get_by_role("heading", name="Source Two", exact=True).wait_for()
            assert not source.locator("canvas").evaluate("el => (fw.filterEngine.getFilterState('conversions')||{})['test-region']")
            source.locator("canvas").evaluate("el => fw.filterEngine.setFilter('conversions','test-region','region','equals','DE')")
            source.locator("#fwrvBarSend").click()
            page.wait_for_function("document.getElementById('queue-count').textContent === '0'")
            status, batches = review.poll(0, "decision")
            assert status == "feedback"
            item = batches[0]["items"][0]
            assert item["origin_slug"] == "source-one"
            assert item["default_target_slug"] == "decision"
            assert item.get("data") is None
            assert item["filters"]["conversions"]["test-region"]["value"] == "NL"
            import_pending_capture(str(destination), item["capture_id"], "original-filtered-chart")
            provenance = json.loads((destination / "evidence/original-filtered-chart.json").read_text())
            assert provenance["report_slug"] == "source-one"
            assert provenance["filters"]["conversions"]["test-region"]["value"] == "NL"
            assert "?" not in provenance["url"]
        finally:
            browser.close()
