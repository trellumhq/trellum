"""Native adaptive annotation semantics, interaction and export clone integration."""
from __future__ import annotations

import base64
import dataclasses
import importlib.util
import re
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


_LAYOUT = """title => {
    const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
    const a=c.chartArea, ms=c._fwEventMarkers||[], named=ms.filter(m=>m.named);
    const hit=(p,q)=>p.x<q.x+q.w&&q.x<p.x+p.w&&p.y<q.y+q.h&&q.y<p.y+p.h;
    let nameOverlaps=0, nameOnMarker=0;
    named.forEach((m,i)=>{
        named.slice(i+1).forEach(n=>{if(hit(m.name,n.name))nameOverlaps++;});
        ms.forEach(o=>{if(o!==m&&hit(m.name,{x:o.left,y:o.top,w:o.width,h:o.height}))nameOnMarker++;});
    });
    return {id:c.canvas.id, width:c.width, height:c.height, count:ms.length, named:named.length,
        text:named.map(m=>m.text), nameOverlaps, nameOnMarker,
        markerOutside:ms.filter(m=>m.left+m.width/2<a.left-.01||m.left+m.width/2>a.right+.01||m.top<a.top||m.top+m.height>a.bottom).length,
        nameOutside:named.filter(m=>m.name.x<a.left-.01||m.name.x+m.name.w>a.right+.01).length,
        indexes:ms.map(m=>m.events[0].index),
        area:{left:a.left,right:a.right,top:a.top,bottom:a.bottom},
        annotations:Chart.registry.plugins.get('annotation').getAnnotations(c).map(e=>({id:e.options.id,borderWidth:e.options.borderWidth,dash:e.options.borderDash,border:e.options.borderColor,labelDisplay:e.options.label?.display})),
        datasets:c.data.datasets.map(d=>({values:d.data}))};
}"""
_TYPE_TEXT = re.compile(r"·\s*(campaign|release|incident|event|ab_test|power_bet)\b", re.I)


def layout(page, title):
    return page.evaluate(_LAYOUT, title)


def ids(page, title):
    cid = _canvas(page, title).get_attribute("id")
    return cid, page.locator(f"#{cid}-adaptive-events")


def adaptive_geometry(state):
    """Compare native geometry; the owned quiet line style is the intended change."""
    result = _geometry(state)
    result["annotations"] = [
        {k: v for k, v in a.items() if k not in ("border", "borderWidth", "dash")} if a["id"].startswith("ev_line_") else a
        for a in result["annotations"]]
    return result


def assert_layout(page, title, count, zoomed=False):
    cid, root = ids(page, title)
    state = layout(page, title)
    native = [a for a in state["annotations"] if a["id"].startswith("ev_")]
    # Every eligible native line/range remains, with exactly one in-plot marker.
    assert state["count"] == len(set(state["indexes"])) == count
    if zoomed:
        assert {int(a["id"].rsplit("_", 1)[1]) for a in native} >= set(state["indexes"])
    else:
        assert len(native) == count
    assert state["markerOutside"] == state["nameOutside"] == state["nameOverlaps"] == state["nameOnMarker"] == 0, state
    assert not any(a["labelDisplay"] for a in native)
    assert not page.evaluate("id=>!!window._chartInstances[id]._fwAnnotationDetails", cid)
    # The old external rail, wrapping key and type text are gone, and nothing
    # reserves a bulky row beside the unchanged plot.
    assert page.locator(f"#{cid}-event-rail, #{cid}-adaptive-events .fw-event-key").count() == 0
    assert root.bounding_box()["height"] <= (36 if page.viewport_size["width"] <= 600 else 32) or root.locator(".fw-event-panel").is_visible() or root.locator(".fw-event-detail").is_visible()
    assert not _TYPE_TEXT.search(root.inner_text())
    assert state["height"] == 220
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    return state


def point(page, title, marker):
    canvas = _canvas(page, title)
    box = canvas.bounding_box()
    return canvas, {"x": marker["left"] + marker["width"] / 2, "y": marker["top"] + marker["height"] / 2}, box


def press(page, title, marker, phone):
    canvas, position, _ = point(page, title, marker)
    (canvas.tap if phone else canvas.click)(position=position)


def markers(page, title):
    return page.evaluate("t=>Object.values(_chartInstances).find(c=>c.canvas.dataset.fwTitle===t)._fwEventMarkers", title)


def browser_check(tmp_path, width):
    from playwright.sync_api import sync_playwright

    phone = width < 600
    artifacts = Path(".data-test/adaptive-events")
    artifacts.mkdir(parents=True, exist_ok=True)
    current, grouped = tmp_path / "adaptive", tmp_path / "grouped"
    build(current)
    build(grouped, "grouped")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=phone)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _open(page, current)
        control = browser.new_page(viewport={"width": width, "height": 900})
        _open(control, grouped)
        for name, count in (("Sparse events", 2), ("Dense events", 15), ("Weekly events", 10)):
            _canvas(page, name).scroll_into_view_if_needed()
            state = assert_layout(page, name, count)
            assert state["height"] == control.evaluate(_STATE, name)["height"]
            assert adaptive_geometry(page.evaluate(_STATE, name)) == adaptive_geometry(control.evaluate(_STATE, name))
        sid, sparse = ids(page, "Sparse events")
        did, dense = ids(page, "Dense events")
        qid, quiet = ids(page, "Quiet events")
        assert not quiet.is_visible() and page.locator(f"#{qid}-adaptive-events").count() == 0
        # Sparse short names fit beside their markers at any width, with no
        # phone breakpoint hiding them.
        sparse_state = layout(page, "Sparse events")
        assert sparse_state["text"] == ["Campaign", "Store update"], sparse_state
        # Dense charts keep every marker; only measured crowded names collapse.
        dense_state = layout(page, "Dense events")
        assert dense_state["named"] < dense_state["count"] == 15
        assert phone or dense_state["named"] > 0
        # Same-day events (two on 6 October) stay as separate visible markers.
        same = [m for m in markers(page, "Dense events") if m["events"][0]["event"]["date"] == "2026-10-06"]
        assert len(same) == 2 and same[0]["top"] != same[1]["top"] and same[0]["left"] == same[1]["left"]
        # Quiet native lines and ranges: only event line style changed.
        quiet_lines = [a for a in dense_state["annotations"] if a["id"].startswith("ev_line_")]
        assert quiet_lines and all(a["borderWidth"] == 1 and a["dash"] == [3, 4] for a in quiet_lines)
        assert any(a["id"].startswith("wd_") for a in dense_state["annotations"])
        # Tap/click a marker: exact name and ORIGINAL dates, highlighted native line.
        sparse_marker = markers(page, "Sparse events")[0]
        press(page, "Sparse events", sparse_marker, phone)
        detail = sparse.locator(".fw-event-detail")
        assert detail.is_visible() and "Campaign" in detail.inner_text() and "2026-08-27" in detail.inner_text()
        assert not _TYPE_TEXT.search(sparse.inner_text())
        assert page.evaluate("document.activeElement.dataset.eventFocus") == "details"
        selected = layout(page, "Sparse events")
        assert [a["borderWidth"] for a in selected["annotations"] if a["id"].startswith("ev_")].count(2) == 1
        _canvas(page, "Sparse events").scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / f"sparse-{width}.png"))
        page.keyboard.press("Escape")
        assert not detail.is_visible()
        assert sparse.locator(".fw-event-all").evaluate("el=>el===document.activeElement")
        assert all(a["borderWidth"] == 1 for a in layout(page, "Sparse events")["annotations"] if a["id"].startswith("ev_"))
        # Keyboard: every event reachable from the local All N events control.
        sparse.get_by_role("button", name="All 2 events").focus()
        page.keyboard.press("Enter")
        assert sparse.locator(".fw-event-panel li").count() == 2
        assert page.evaluate("document.activeElement.dataset.eventPick") is not None
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        assert "Store update" in detail.inner_text() and "2026-09-07" in detail.inner_text()
        assert not sparse.locator(".fw-event-panel").is_visible()
        page.keyboard.press("Escape")
        # Dense: All 15 events, bounded local scroller, original dates.
        _canvas(page, "Dense events").scroll_into_view_if_needed()
        dense.get_by_role("button", name="All 15 events").click()
        assert dense.locator(".fw-event-panel li").count() == 15
        scroller = dense.locator(".fw-adaptive-event-list")
        assert scroller.evaluate("el=>el.clientHeight<=168&&el.scrollHeight>el.clientHeight")
        last = dense.locator(".fw-event-panel li").last
        last.scroll_into_view_if_needed()
        assert "2026-10-18 – 2026-11-04" in last.inner_text()
        assert scroller.evaluate("el=>el.scrollTop>0")
        assert not _TYPE_TEXT.search(dense.inner_text())
        last.locator("button").focus()
        scroll_before = scroller.evaluate("el=>el.scrollTop")
        page.evaluate("window.renderAll();window.renderAll()")
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=did)
        assert dense.locator(".fw-event-panel").is_visible()
        assert page.evaluate("document.activeElement.dataset.eventFocus") == last.locator("button").get_attribute("data-event-focus")
        assert abs(scroller.evaluate("el=>el.scrollTop") - scroll_before) < 2
        assert page.locator(f"#{did}-adaptive-events").count() == 1
        dense.get_by_role("button", name="Close event list").click()
        assert dense.get_by_role("button", name="All 15 events").evaluate("el=>el===document.activeElement")
        # Source arrays, weekday lines and CSV remain unchanged.
        original = layout(page, "Dense events")
        page.evaluate("window._fwDownloadCsv=(headers,rows,name)=>window.adaptiveCsv={headers,rows,name}")
        page.locator(f"[data-csv-chart='{did}']").click()
        assert page.evaluate("adaptiveCsv.rows.map(r=>r[1])") == original["datasets"][0]["values"]
        page.locator('[data-anno-type="release"]').click()
        assert_layout(page, "Dense events", 3)
        page.locator('[data-anno-type="release"]').click()
        assert_layout(page, "Dense events", 15)
        # Existing mapping and scope semantics; original endpoints remain exact.
        wid, weekly = ids(page, "Weekly events")
        weekly.get_by_role("button", name="All 10 events").click()
        assert "2026-10-18 – 2026-11-04" in weekly.inner_text()
        weekly.get_by_role("button", name="Close event list").click()
        weekly_range = [m for m in markers(page, "Weekly events") if m["events"][0]["event"]["end_date"]][0]
        press(page, "Weekly events", weekly_range, phone)
        # Several weekly marks share this snapped position: the press offers
        # exactly those events, and choosing one shows its original dates.
        assert weekly.locator(".fw-event-panel li").count() > 1
        weekly.locator(".fw-event-panel button", has_text="Long event name").click()
        assert "Plotted at" in weekly.locator(".fw-event-detail").inner_text() and "2026-10-18 – 2026-11-04" in weekly.locator(".fw-event-detail").inner_text()
        weekly.get_by_role("button", name="Clear selected event").click()
        page.evaluate("""() => { _reportData._events[0].studio='elsewhere'; _currentScope='here'; _refreshAllActions=0; _refreshAllAnnotations(); }""")
        assert_layout(page, "Sparse events", 1)
        page.evaluate("_currentScope=null; _refreshAllAnnotations()")
        # Date/channel filters use the genuine shared filter engine. A narrow
        # window returns to readable names; a long name is marker-only on a phone.
        page.evaluate("fw.filterEngine.setFilter('adaptive_events','test-days','day','range',{min:'2026-10-12',max:'2026-10-14'})")
        returned = assert_layout(page, "Dense events", 1)
        assert returned["text"] == ["October release 12"]
        page.evaluate("fw.filterEngine.setFilter('adaptive_events','test-days','day','range',{min:'2026-10-18',max:'2026-10-20'})")
        long_state = assert_layout(page, "Dense events", 1)
        # The very long name fits only where the actual plot is wide enough.
        assert long_state["named"] == (0 if phone else 1)
        press(page, "Dense events", markers(page, "Dense events")[0], phone)
        assert "2026-11-04" in dense.locator(".fw-event-detail").inner_text()
        assert "Long event name" in dense.locator(".fw-event-detail").inner_text()
        _canvas(page, "Dense events").scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / f"long-range-{width}.png"))
        dense.get_by_role("button", name="Clear selected event").click()
        page.evaluate("fw.filterEngine.clearFilter('adaptive_events','test-days')")
        page.get_by_role("button", name="Partner", exact=True).click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]===41", arg=did)
        assert_layout(page, "Dense events", 15)
        page.get_by_role("button", name="All", exact=True).click()
        # Zoom uses the existing positioned bands, including either clipped edge.
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:17,max:20},'none')", did)
        assert_layout(page, "Dense events", 1, zoomed=True)
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", did)
        # Event presses never begin metric selection or drag; ordinary nearest
        # tooltips, metric clicks and plot drag zoom still work.
        page.evaluate("""id=>{const cfg=_reportData.components[id];cfg.crossFilter='channel';cfg.crossFilterMode='dataset';
            _fwRenderers.chart(id,cfg);window.eventCrossFilters=[];window._fwCrossFilter=(...args)=>eventCrossFilters.push(args);}""", did)
        _canvas(page, "Dense events").scroll_into_view_if_needed()
        press(page, "Dense events", markers(page, "Dense events")[3], phone)
        page.wait_for_timeout(150)
        assert page.evaluate('eventCrossFilters.length') == 0
        dense.get_by_role("button", name="Clear selected event").or_(dense.get_by_role("button", name="Close event list")).first.click()
        canvas = _canvas(page, 'Dense events')
        marker = markers(page, "Dense events")[3]
        box = canvas.bounding_box()
        # A drag that starts on a marker neither zooms nor selects a metric.
        zoom_before = page.evaluate("id=>_chartInstances[id].scales.x.min", did)
        page.mouse.move(box['x'] + marker['left'] + 4, box['y'] + marker['top'] + 4)
        page.mouse.down()
        page.mouse.move(box['x'] + marker['left'] + 100, box['y'] + marker['top'] + 4, steps=5)
        page.mouse.up()
        page.wait_for_timeout(150)
        assert page.evaluate("id=>_chartInstances[id].scales.x.min", did) == zoom_before
        assert page.evaluate('eventCrossFilters.length') == 0
        pt = page.evaluate("id=>{const p=_chartInstances[id].getDatasetMeta(0).data[15];return {x:p.x,y:p.y}}", did)
        page.mouse.move(box['x'] + pt['x'], box['y'] + pt['y'])
        page.wait_for_function("id=>_chartInstances[id].tooltip.dataPoints?.length===1", arg=did)
        page.mouse.click(box['x'] + pt['x'], box['y'] + pt['y'])
        page.wait_for_function('eventCrossFilters.length === 1', timeout=5000)
        page.mouse.move(box['x'] + 70, box['y'] + 150)
        page.mouse.down()
        page.mouse.move(box['x'] + 170, box['y'] + 150, steps=5)
        page.mouse.up()
        page.wait_for_function("id=>_chartInstances[id].scales.x.min>0", arg=did)
        page.evaluate("id=>{_chartInstances[id].resetZoom('none');delete _reportData.components[id].crossFilter;}", did)
        # Unrelated focus survives update; disappearing owned focus returns locally.
        page.locator("#fwThemeSelect").focus()
        page.evaluate("_refreshAllAnnotations()")
        assert page.evaluate("document.activeElement.id") == "fwThemeSelect"
        dense.get_by_role("button", name="All 15 events").focus()
        page.evaluate("fw.filterEngine.setFilter('adaptive_events','empty','day','range',{min:'2027-01-01',max:'2027-01-02'})")
        assert not dense.is_visible() and page.locator(f"#{did}-adaptive-events").count() == 0
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
        assert page.locator(f"#{did}-adaptive-events").count() == 1
        # Exercise the clone hook against genuine native PNG/PDF and section
        # captures, including the original screen disclosure and scroll state.
        page.evaluate("""() => {window.eventCaptureClones=[];
            const prepare=_fwPrepareEventCapture;
            window._fwPrepareEventCapture=(doc,root,scale)=>{prepare(doc,root,scale);
                eventCaptureClones.push({key:Array.from(root.querySelectorAll('.fw-event-export-key:not([hidden])')).map(el=>({text:el.innerText,count:el.querySelectorAll('li').length})),
                    panel:Array.from(root.querySelectorAll('.fw-event-panel:not([hidden]) li')).length});};
        }""")
        dense.get_by_role("button", name="All 15 events").click()
        dense.locator(".fw-event-panel li").last.scroll_into_view_if_needed()
        before = scroller.evaluate("el=>el.scrollTop")
        capture = _canvas(page, "Dense events").evaluate("el=>fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'))")
        (artifacts / f"section-{width}-open.png").write_bytes(base64.b64decode(capture["image"]["data_base64"]))
        assert scroller.evaluate("el=>el.scrollTop") == before
        assert page.evaluate("eventCaptureClones.at(-1).panel") == 15
        dense.get_by_role("button", name="Close event list").click()
        capture = _canvas(page, "Dense events").evaluate("el=>fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'))")
        (artifacts / f"section-{width}-closed.png").write_bytes(base64.b64decode(capture["image"]["data_base64"]))
        assert page.evaluate("eventCaptureClones.at(-1).key[0].count") == 15
        assert "Long event name" in page.evaluate("eventCaptureClones.at(-1).key[0].text")
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
        assert page.locator(f"#{did}-adaptive-events").count() == 0
        assert errors == [], errors
        browser.close()


def named_by_width(page, widths):
    result = []
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_function("""() => Object.values(_chartInstances).every(c => !c.canvas.isConnected ||
            Math.abs(c.width - c.canvas.parentElement.clientWidth) < 2)""")
        page.wait_for_timeout(150)
        result.append({t: layout(page, t) for t in ("Sparse events", "Dense events")})
    return result


def test_adaptive_names_follow_measured_width_in_both_directions(tmp_path):
    from playwright.sync_api import sync_playwright

    build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _open(page, tmp_path)
        wide, narrow, wide_again = named_by_width(page, (1280, 360, 1280))
        for title in wide:
            # Markers, native lines and heights never depend on name fit.
            assert wide[title]["count"] == narrow[title]["count"] == wide_again[title]["count"]
            assert narrow[title]["height"] == wide[title]["height"] == 220
            assert narrow[title]["markerOutside"] == narrow[title]["nameOverlaps"] == narrow[title]["nameOnMarker"] == 0
            assert narrow[title]["named"] <= wide[title]["named"]
            assert wide_again[title]["text"] == wide[title]["text"]
        # The actual width, not a breakpoint, moves names out and back in.
        assert wide["Sparse events"]["text"] == narrow["Sparse events"]["text"] == ["Campaign", "Store update"]
        assert wide["Dense events"]["named"] > narrow["Dense events"]["named"]
        assert wide_again["Dense events"]["named"] > narrow["Dense events"]["named"]
        browser.close()


def test_adaptive_events_chromium_desktop(tmp_path):
    browser_check(tmp_path, 1280)


@pytest.mark.heavy
def test_adaptive_events_chromium_phone(tmp_path):
    browser_check(tmp_path, 390)
