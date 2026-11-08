"""Explicit opt-in resource bound and removal checks for native event UI."""
import json
from pathlib import Path

import pytest

from trellum.testing.test_adaptive_events import assert_layout, build, ids
from trellum.testing.test_line_chart_events import _canvas, _open


@pytest.mark.heavy
def test_adaptive_dense_population_and_capture_bound(tmp_path):
    from playwright.sync_api import sync_playwright

    build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 900}, has_touch=True)
        _open(page, tmp_path)
        cid, root = ids(page, "Dense events")
        # 1,200 eligible declarations keep a marker each and stay reachable; do
        # not silently sample the source population to satisfy a drawing or
        # capture limit.
        elapsed = page.evaluate("""() => {
            _reportData._events=Array.from({length:1200},(_,i)=>({date:'2026-10-'+String(i%31+1).padStart(2,'0'),
                label:'Event '+i+' · <b>literal long name</b>', type:'release',studio:'shared'}));
            const start=performance.now();_refreshAllAnnotations();return performance.now()-start;
        }""")
        assert elapsed < 3000
        assert_layout(page, "Dense events", 1200)
        root.get_by_role("button", name="All 1200 events").click()
        rows = root.locator(".fw-event-panel li")
        assert rows.count() == 1200
        assert root.locator(".fw-event-panel b").count() == 0
        rows.last.scroll_into_view_if_needed()
        rows.last.locator("button").focus()
        identity = rows.last.locator("button").get_attribute("data-event-focus")
        page.evaluate("_reportData._events.reverse();_refreshAllAnnotations()")
        assert page.evaluate("document.activeElement.dataset.eventFocus") == identity
        root.get_by_role("button", name="Close event list").click()
        # The actual native section capture rejects the expanded clone before
        # allocating an oversized bitmap; the screen keeps its bounded list.
        error = _canvas(page, "Dense events").evaluate("""async el => {
            try {await fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'));return '';}
            catch(e){return e.message;}
        }""")
        assert "16,384px / 32 megapixel" in error
        evidence = Path(".data-test/adaptive-events")
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "resources.json").write_text(json.dumps({"events": 1200, "refresh_ms": elapsed, "capture_error": error}, indent=2))
        assert not root.locator(".fw-event-panel").is_visible()
        assert root.locator(".fw-event-export-key").evaluate("el=>el.hidden")
        assert page.locator("iframe.html2canvas-container").count() == 0
        # Canvas-only capture retains its scope even for this event population.
        capture = _canvas(page, "Dense events").evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
        assert len(capture["image"]["data_base64"]) > 1000
        # Static removal and bound removal both release their owned DOM and
        # listeners even when callers replace a canvas without destroying it.
        for name in ("Sparse events", "Dense events"):
            removed_id = _canvas(page, name).get_attribute("id")
            _canvas(page, name).evaluate("el=>el.remove()")
            page.wait_for_function("id=>!window._chartInstances[id]", arg=removed_id)
            assert page.locator(f"#{removed_id}-adaptive-events").count() == 0
        browser.close()
