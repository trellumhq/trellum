"""Native adaptive annotation semantics, interaction and export clone integration."""
from __future__ import annotations

import base64
import dataclasses
import importlib.util
from pathlib import Path

import pytest

from trellum.components import LineChart
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_line_chart_events import (
    _STATE,
    _canvas,
    _context,
    _extras,
    _geometry,
    _open,
)
from trellum.validation import validate_report


def example():
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_adaptive_events.py"
    spec = importlib.util.spec_from_file_location("adaptive_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(output, mode="adaptive"):
    output.mkdir(parents=True, exist_ok=True)
    module = example()
    ctx = _context(output, module.ANNOTATIONS)
    module.add_sections(ctx)
    if mode != "adaptive":
        for section in ctx.sections:
            for component in section["components"]:
                if isinstance(component, LineChart):
                    component.event_labels = mode
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert result.summary["warn"] == result.summary["fail"] == 0, [dataclasses.asdict(c) for c in result.checks if c.level in ("warn", "fail")]
    render_report(ctx, str(output), data=_extras(ctx.config), auto_refresh=False, validation=result, details=details)
    result.write(str(output))
    copy_vendor_tree(output)
    return ctx


def test_adaptive_standalone_example_has_zero_warn_fail(tmp_path):
    build(tmp_path)


def ids(page, title):
    cid = _canvas(page, title).get_attribute("id")
    return cid, page.locator(f"#{cid}-event-rail"), page.locator(f"#{cid}-adaptive-events")


def assert_layout(page, title, count):
    cid, rail, root = ids(page, title)
    state = page.evaluate(_STATE, title)
    assert sum(len(g["events"]) for g in state["markers"]) == count
    assert not any(a["labelDisplay"] for a in state["annotations"] if a["id"].startswith("ev_"))
    assert not page.evaluate("id=>!!window._chartInstances[id]._fwAnnotationDetails", cid)
    boxes = rail.locator("button").evaluate_all("els=>els.map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,w:r.width,h:r.height,sw:el.scrollWidth,cw:el.clientWidth,sh:el.scrollHeight,ch:el.clientHeight}})")
    bounds = rail.bounding_box()
    assert all(b["left"] >= bounds["x"] - 1 and b["right"] <= bounds["x"] + bounds["width"] + 1 for b in boxes)
    assert all(a["right"] + 5 <= b["left"] for a, b in zip(boxes, boxes[1:]))
    assert all(b["sw"] <= b["cw"] and b["sh"] <= b["ch"] for b in boxes)
    if page.viewport_size["width"] <= 600:
        assert all(b["w"] >= 44 and b["h"] >= 44 for b in boxes)
        assert bounds["height"] == 48
    else:
        assert bounds["height"] == 40
    assert state["height"] == 220
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    return state


def browser_check(tmp_path, width):
    from playwright.sync_api import sync_playwright

    artifacts = Path(".data-test/adaptive-events")
    artifacts.mkdir(parents=True, exist_ok=True)
    current, grouped = tmp_path / "adaptive", tmp_path / "grouped"
    build(current)
    build(grouped, "grouped")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=width < 600)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _open(page, current)
        control = browser.new_page(viewport={"width": width, "height": 900})
        _open(control, grouped)
        for name, count in (("Sparse events", 2), ("Dense events", 15), ("Weekly events", 10)):
            _canvas(page, name).scroll_into_view_if_needed()
            state = assert_layout(page, name, count)
            assert _geometry(state) == _geometry(control.evaluate(_STATE, name))
        sid, sparse_rail, sparse = ids(page, "Sparse events")
        did, dense_rail, dense = ids(page, "Dense events")
        qid, quiet_rail, quiet = ids(page, "Quiet events")
        assert not quiet_rail.is_visible() and not quiet.is_visible()
        assert sparse_rail.locator("button").count() == 2
        if width < 600:
            assert "Autumn campaign launch" in sparse.locator(".fw-event-key").inner_text()
            assert "Store update" in sparse.locator(".fw-event-key").inner_text()
        else:
            assert "Autumn campaign launch" in sparse_rail.inner_text()
        if width < 600:
            sparse_rail.locator("button").first.tap()
        else:
            sparse_rail.locator("button").first.focus()
            page.keyboard.press('Enter')
        assert sparse.locator(".fw-event-panel").is_visible()
        assert "2026-09-01" in sparse.locator(".fw-event-panel").inner_text()
        assert page.evaluate("document.activeElement.dataset.eventFocus") == "details"
        assert sparse_rail.locator("button").first.get_attribute("aria-expanded") == "true"
        selected = page.evaluate(_STATE, "Sparse events")
        assert selected["annotations"][0]["borderWidth"] == 4
        page.keyboard.press("Escape")
        assert not sparse.locator(".fw-event-panel").is_visible()
        assert sparse_rail.locator("button").first.evaluate("el=>el===document.activeElement")
        assert page.evaluate(_STATE, "Sparse events")["annotations"][0]["borderWidth"] == 2
        _canvas(page, "Sparse events").scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / f"sparse-{width}.png"))
        dense.get_by_role("button", name="View all 15 events").click()
        assert dense.locator(".fw-event-panel li").count() == 15
        scroller = dense.locator(".fw-adaptive-event-list")
        assert scroller.evaluate("el=>el.clientHeight<=280&&el.scrollHeight>el.clientHeight")
        last = dense.locator(".fw-event-panel li").last
        last.scroll_into_view_if_needed()
        assert "2026-10-18 – 2026-11-04" in last.inner_text()
        assert scroller.evaluate("el=>el.scrollTop>0")
        last.focus()
        scroll_before = scroller.evaluate("el=>el.scrollTop")
        page.evaluate("window.renderAll();window.renderAll()")
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=did)
        assert dense.locator(".fw-event-panel").is_visible()
        assert page.evaluate("document.activeElement.dataset.eventFocus") == last.get_attribute("data-event-focus")
        assert abs(scroller.evaluate("el=>el.scrollTop") - scroll_before) < 2
        assert page.locator(f"#{did}-adaptive-events").count() == 1
        if width < 600:
            dense.get_by_role("button", name="Close event details").tap()
        else:
            dense.get_by_role("button", name="Close event details").click()
        assert dense.get_by_role("button", name="View all 15 events").evaluate("el=>el===document.activeElement")
        # Source arrays, weekday lines and CSV remain unchanged.
        original = page.evaluate(_STATE, "Dense events")
        page.evaluate("window._fwDownloadCsv=(headers,rows,name)=>window.adaptiveCsv={headers,rows,name}")
        page.locator(f"[data-csv-chart='{did}']").click()
        assert page.evaluate("adaptiveCsv.rows.map(r=>r[1])") == original["datasets"][0]["values"]
        assert any(a["id"].startswith("wd_") for a in original["annotations"])
        page.locator('[data-anno-type="release"]').click()
        assert_layout(page, "Dense events", 3)
        page.locator('[data-anno-type="release"]').click()
        assert_layout(page, "Dense events", 15)
        # Existing mapping and scope semantics; original endpoints remain exact.
        wid, weekly_rail, weekly = ids(page, "Weekly events")
        weekly.get_by_role("button", name="View all 10 events").click()
        assert "Plot position uses the existing date buckets/clipping" in weekly.inner_text()
        assert "2026-10-18 – 2026-11-04" in weekly.inner_text()
        page.evaluate("""() => { _reportData._events[0].studio='elsewhere'; _currentScope='here'; _refreshAllAnnotations(); }""")
        assert_layout(page, "Sparse events", 1)
        page.evaluate("_currentScope=null; _refreshAllAnnotations()")
        # Date/channel filters use the genuine shared filter engine.
        page.evaluate("fw.filterEngine.setFilter('adaptive_events','test-days','day','range',{min:'2026-10-18',max:'2026-10-20'})")
        assert_layout(page, "Dense events", 1)
        dense_rail.locator("button").click()
        assert "2026-11-04" in dense.locator(".fw-event-panel").inner_text()
        assert dense.locator('.fw-event-key button').evaluate_all("els=>els.every(el=>el.scrollWidth<=el.clientWidth)")
        _canvas(page, "Dense events").scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / f"long-range-{width}.png"))
        page.evaluate("fw.filterEngine.clearFilter('adaptive_events','test-days')")
        page.get_by_role("button", name="Partner", exact=True).click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]===41", arg=did)
        assert_layout(page, "Dense events", 15)
        page.get_by_role("button", name="All", exact=True).click()
        # Zoom uses the existing positioned bands, including either clipped edge.
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:17,max:20},'none')", did)
        assert_layout(page, "Dense events", 1)
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", did)
        # Buttons live outside the canvas: no metric selection/drag begins on
        # them, while ordinary nearest tooltips and plot drag zoom still work.
        page.evaluate("""id=>{const cfg=_reportData.components[id];cfg.crossFilter='channel';cfg.crossFilterMode='dataset';
            _fwRenderers.chart(id,cfg);window.eventCrossFilters=[];window._fwCrossFilter=(...args)=>eventCrossFilters.push(args);}""", did)
        dense_rail.locator('button').first.click()
        assert page.evaluate('eventCrossFilters.length') == 0
        dense.get_by_role('button', name='Close event details').click()
        canvas = _canvas(page, 'Dense events')
        canvas.scroll_into_view_if_needed()
        point = page.evaluate("id=>{const p=_chartInstances[id].getDatasetMeta(0).data[15];return {x:p.x,y:p.y}}", did)
        box = canvas.bounding_box()
        page.mouse.move(box['x'] + point['x'], box['y'] + point['y'])
        page.wait_for_function("id=>_chartInstances[id].tooltip.dataPoints?.length===1", arg=did)
        page.mouse.click(box['x'] + point['x'], box['y'] + point['y'])
        # Chart.js dispatches native chart events on the next animation frame.
        page.wait_for_function('eventCrossFilters.length === 1', timeout=5000)
        assert page.evaluate('eventCrossFilters.length') == 1
        page.mouse.move(box['x'] + 70, box['y'] + 100)
        page.mouse.down()
        page.mouse.move(box['x'] + 170, box['y'] + 100, steps=5)
        page.mouse.up()
        page.wait_for_function("id=>_chartInstances[id].scales.x.min>0", arg=did)
        page.evaluate("id=>{_chartInstances[id].resetZoom('none');delete _reportData.components[id].crossFilter;}", did)
        # Unrelated focus survives update; disappearing owned focus returns locally.
        page.locator("#fwThemeSelect").focus()
        page.evaluate("_refreshAllAnnotations()")
        assert page.evaluate("document.activeElement.id") == "fwThemeSelect"
        dense_rail.locator("button").first.focus()
        page.evaluate("fw.filterEngine.setFilter('adaptive_events','empty','day','range',{min:'2027-01-01',max:'2027-01-02'})")
        assert not dense_rail.is_visible() and not dense.is_visible()
        assert page.evaluate("document.activeElement.id") == did
        page.evaluate("fw.filterEngine.clearFilter('adaptive_events','empty')")
        # Actual theme redraw, disposal and re-entry, without duplicate owners.
        for theme in ("trellum dark", "trellum light"):
            page.locator("#fwThemeSelect").select_option(theme)
            _canvas(page, "Dense events").scroll_into_view_if_needed()
            page.wait_for_function("id=>!document.getElementById(id)._fwPendingTheme", arg=did)
            assert_layout(page, "Dense events", 15)
            page.screenshot(path=str(artifacts / f"dense-{width}-{theme.split()[1]}.png"))
        _canvas(page, "Dense events").evaluate("""el=>{const id=el.id;_chartInstances[id].destroy();delete _chartInstances[id];
            const next=el.cloneNode();el.replaceWith(next);_fwRenderers.chart(id,_reportData.components[id]);}""")
        assert_layout(page, "Dense events", 15)
        assert page.locator(f"#{did}-adaptive-events").count() == page.locator(f"#{did}-event-rail").count() == 1
        # Exercise the clone hook against genuine native PNG/PDF and section
        # captures, including the original screen disclosure and scroll state.
        page.evaluate("""() => {window.eventCaptureClones=[];
            const prepare=_fwPrepareEventCapture;
            window._fwPrepareEventCapture=(doc,root,scale)=>{prepare(doc,root,scale);
                eventCaptureClones.push(Array.from(root.querySelectorAll('.fw-event-export-key:not([hidden])')).map(el=>({text:el.innerText,count:el.querySelectorAll('li').length})));};
        }""")
        dense.get_by_role("button", name="View all 15 events").click()
        dense.locator(".fw-event-panel li").last.scroll_into_view_if_needed()
        before = scroller.evaluate("el=>el.scrollTop")
        capture = _canvas(page, "Dense events").evaluate("el=>fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'))")
        (artifacts / f"section-{width}-open.png").write_bytes(base64.b64decode(capture["image"]["data_base64"]))
        assert scroller.evaluate("el=>el.scrollTop") == before
        dense.get_by_role("button", name="Close event details").click()
        capture = _canvas(page, "Dense events").evaluate("el=>fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'))")
        (artifacts / f"section-{width}-closed.png").write_bytes(base64.b64decode(capture["image"]["data_base64"]))
        assert page.evaluate("eventCaptureClones.at(-1)[0].count") == 15
        assert "Long event name" in page.evaluate("eventCaptureClones.at(-1)[0].text")
        for extension, command, magic in (("png", "fw.exportPNG()", b"\x89PNG"), ("pdf", "fw.exportPDF()", b"%PDF")):
            with page.expect_download() as downloaded:
                page.evaluate(command)
            file = artifacts / f"report-{width}.{extension}"
            downloaded.value.save_as(str(file))
            assert file.read_bytes().startswith(magic)
            assert not dense.locator(".fw-event-panel").is_visible()
        assert dense.locator(".fw-event-export-key").evaluate("el=>el.hidden")
        assert page.locator("iframe.html2canvas-container").count() == 0
        assert page.evaluate("""() => {const root=document.createElement('div');root.style.height='17000px';root.className='fw-adaptive-events';document.body.append(root);
            try{_fwPrepareEventCapture(document,root,1);return false;}catch(e){return /limit/.test(e.message);}finally{root.remove();}}""")
        # Replacing the authored presentation does not leave another owner's
        # disclosure or duplicate full-label fallback behind.
        for mode in ('grouped', 'adaptive', 'full', None, 'adaptive'):
            page.evaluate("""({id,mode})=>{const cfg=_reportData.components[id];
                if(mode)cfg.event_labels=mode;else delete cfg.event_labels;_fwRenderers.chart(id,cfg);}""", {'id': did, 'mode': mode})
            if mode == 'grouped':
                assert page.locator(f'#{did}-event-details').count() == 1
            elif mode == 'adaptive':
                assert_layout(page, 'Dense events', 15)
                assert page.locator(f'#{did}-event-details').count() == 0
            else:
                assert not page.evaluate(_STATE, 'Dense events')['markers']
                assert page.locator(f'#{did}-adaptive-events, #{did}-event-details').count() == 0
        page.evaluate("id=>_chartInstances[id].destroy()", did)
        assert page.locator(f"#{did}-adaptive-events, #{did}-event-rail").count() == 0
        assert errors == [], errors
        browser.close()


def test_adaptive_events_chromium_desktop(tmp_path):
    browser_check(tmp_path, 1280)


@pytest.mark.heavy
def test_adaptive_events_chromium_phone(tmp_path):
    browser_check(tmp_path, 390)
