"""Genuine vendored matrix paint, measured labels and report lifecycle checks."""
from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, HeatmapChart
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report


def _report(output):
    source = Path(__file__).resolve().parents[1] / "demo/reports/component-showcase/examples_heatmaps.py"
    spec = importlib.util.spec_from_file_location("heatmap_examples", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={"name": "Heatmap checks", "studio": "test", "category": "Test",
        "description": "Synthetic quantities by band, purchase frequency and platform; fixed observations for heatmap scales, missing coordinates and report filters."},
        slug="heatmap-checks", output_dir=str(output))
    module.add_sections(ctx)
    dense = pd.DataFrame([{"x": f"Column {i}", "y": "Measured", "v": 12345 + i}
                          for i in range(80)])
    uniform = pd.DataFrame({"x": ["A", "B"], "y": ["Same", "Same"], "v": [64, 64]})
    floor = pd.DataFrame({"x": ["A", "B"], "y": ["Small", "Small"], "v": [0.1, 0.5]})
    ctx.add_section("Heatmap sizing and scale edges", [
        HeatmapChart(dense, "x", "y", "v", "Narrow slots", static=True, log_scale=False),
        HeatmapChart(uniform, "x", "y", "v", "Uniform global", static=True),
        HeatmapChart(uniform, "x", "y", "v", "Uniform relative", static=True, normalize="col"),
        HeatmapChart(floor, "x", "y", "v", "Logarithmic floor", static=True, log_scale=True),
        HeatmapChart(uniform, "x", "y", "v", "Column relative", static=True, normalize="col"),
    ])
    coercion = pd.DataFrame({"x": ["B", "A", "B", "C", "A", "D"],
                             "y": ["two", "one", "two", "one", "one", "one"],
                             "v": [None, 4, 7, 0, None, 0], "kind": ["observed"] * 6})
    coercion = pd.concat([coercion, pd.DataFrame({
        "x": ["A", "B", "C", "D"], "y": ["two", "one", "two", "two"],
        "v": [3, 2, 6, 8], "kind": ["observed"] * 4})], ignore_index=True)
    ctx.add_section("Heatmap live coercion", [
        DataSource("heatmap_coercion", coercion),
        FilterBar("heatmap_coercion", coercion, filters=[{"column": "kind", "type": "toggle"}]),
        HeatmapChart(coercion, "x", "y", "v", "Live null and duplicate coordinates",
                     dataset_id="heatmap_coercion"),
    ])
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, result.to_dict()
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    return result


def _check(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    result = _report(tmp_path)
    (tmp_path / "validation.json").write_text(json.dumps(result.to_dict(), indent=2))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.add_init_script("""(() => {
              const original = CanvasRenderingContext2D.prototype.fillText;
              window.heatmapText = [];
              CanvasRenderingContext2D.prototype.fillText = function(text,x,y,...args) {
                if (this.canvas.id && this.textAlign === 'center' && this.textBaseline === 'middle') {
                  window.heatmapText.push({id:this.canvas.id,text:String(text),x,y,
                    width:this.measureText(text).width,color:this.fillStyle,font:this.font});
                }
                return original.call(this,text,x,y,...args);
              };
            })()""")

            def serve(route):
                path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
                if path.is_file():
                    route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                                  or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="Missing test asset")
            page.route("http://heatmap.test/**", serve)
            page.goto("http://heatmap.test/index.html", wait_until="networkidle")
            page.locator("#fwThemeSelect").select_option("trellum light")
            ids = {}
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                cid = canvas.get_attribute("id")
                page.wait_for_function("id => !!window._chartInstances[id]", arg=cid)
                ids[canvas.evaluate("el => el.parentElement.previousElementSibling.previousElementSibling.textContent")] = cid

            def chart(title):
                return ids[title]

            def key(title):
                return page.locator(f'[data-heatmap-key="{chart(title)}"]')

            def snapshot(title):
                return page.evaluate("""id => {
                  const c=window._chartInstances[id], a=c.chartArea;
                  return {area:{left:a.left,right:a.right,top:a.top,bottom:a.bottom,width:a.width},
                    height:c.height,data:c.data.datasets[0].data,
                    points:c.getDatasetMeta(0).data.map(e=>({x:e.x,y:e.y,width:e.width,height:e.height,
                      center:e.getCenterPoint(),color:e.options.backgroundColor})),
                    x:c.scales.x.getLabels(),y:c.scales.y.getLabels(),
                    labels:window.heatmapText.filter(t=>t.id===id && t.x>=a.left && t.x<=a.right && t.y>=a.top && t.y<=a.bottom)};
                }""", chart(title))

            def geometry(title):
                snap = snapshot(title)
                slot = snap["area"]["width"] / len(snap["x"])
                for point in snap["points"]:
                    assert point["width"] <= slot and point["height"] > 0
                    assert point["x"] >= snap["area"]["left"] - 0.1
                    assert point["x"] + point["width"] <= snap["area"]["right"] + 0.1
                    assert point["y"] >= snap["area"]["top"] - 0.1
                    assert point["y"] + point["height"] <= snap["area"]["bottom"] + 0.1
                return snap

            complete = geometry("Complete nonnegative observations")
            assert complete["height"] == 200
            assert [d["v"] for d in complete["data"]] == [10,35,80,140,25,100,220,410,50,180,450,820]
            assert "Global linear" in key("Complete nonnegative observations").inner_text()
            assert "10 – 820" in key("Complete nonnegative observations").inner_text()
            assert "automatic" in key("Wide range · automatic logarithmic colors").inner_text()
            assert "logarithmic" in key("Wide range · automatic logarithmic colors").inner_text()
            assert "Row-relative linear" in key("Unequal rows · relative colors").inner_text()
            assert "Low (0) – High (1)" in key("Unequal rows · relative colors").inner_text()
            assert "Column-relative" in key("Column relative").inner_text()
            assert "Uniform positive groups: midpoint" in key("Uniform relative").inner_text()
            assert "All positive cells: 64" in key("Uniform global").inner_text()
            assert "All observed cells: 0" in key("All-zero observations").inner_text()
            assert "Signed or nonfinite" in key("Signed values · legacy semantics").inner_text()
            assert key("Signed values · legacy semantics").locator('.fw-heatmap-gradient').count() == 0
            assert "Positive values ≤ 1: low color" in key("Logarithmic floor").inner_text()

            # Real fillText calls must be centered in their own rectangle, fit
            # horizontally, keep the theme font size and choose legible ink.
            for point, datum in zip(complete["points"], complete["data"]):
                labels = [t for t in complete["labels"] if t["text"] == str(datum["v"])
                          and abs(t["x"] - point["center"]["x"]) < 0.1
                          and abs(t["y"] - point["center"]["y"]) < 0.1]
                assert labels, (point, datum, complete["labels"])
                assert labels[-1]["width"] + 8 <= point["width"]
                assert labels[-1]["color"] in ("#000000", "#ffffff")
                rgb = [int(v) / 255 for v in re.findall(r"\d+", point["color"])]
                linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
                luminance = sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
                contrast = (luminance + 0.05) / 0.05 if labels[-1]["color"] == "#000000" else 1.05 / (luminance + 0.05)
                assert contrast >= 4.5
                assert "12px" in labels[-1]["font"]
            assert snapshot("Values off · tooltips retained")["labels"] == []
            assert snapshot("Narrow slots")["labels"] == []
            assert snapshot("Fixed two-stop palette")["points"][0]["color"] == "rgb(232,222,248)"
            assert snapshot("Fixed two-stop palette")["points"][-1]["color"] == "rgb(83,48,122)"
            assert snapshot("Null coercion and duplicate coordinates")["data"] == [
                {"x":"One","y":"Supplied","v":0}, {"x":"Two","y":"Supplied","v":80},
                {"x":"Three","y":"Supplied","v":25}]

            assert snapshot("Live null and duplicate coordinates")["data"] == [
                {"x":"A","y":"one","v":4}, {"x":"A","y":"two","v":3},
                {"x":"B","y":"one","v":2}, {"x":"B","y":"two","v":7},
                {"x":"C","y":"one","v":0}, {"x":"C","y":"two","v":6},
                {"x":"D","y":"one","v":0}, {"x":"D","y":"two","v":8}]
            assert snapshot("Live null and duplicate coordinates")["x"] == ["A","B","C","D"]

            # Read real raster pixels away from label ink and compare with the
            # resolved matrix fill. Inspect a missing slot's hatched raster too.
            sparse_id = chart("Measured zero versus no observation")
            sparse = page.locator(f'#{sparse_id}')
            sparse.scroll_into_view_if_needed()
            painted = page.evaluate("""id => {
              const c=window._chartInstances[id], ratio=c.currentDevicePixelRatio;
              const points=c.getDatasetMeta(0).data;
              function pixel(x,y) {return Array.from(c.ctx.getImageData(Math.round(x*ratio),Math.round(y*ratio),1,1).data);}
              const zero=points[0], center=zero.getCenterPoint();
              const colors=new Set(), x=c.scales.x.getPixelForValue('Two'), y=c.scales.y.getPixelForValue('Sparse');
              for(let dx=-12;dx<12;dx++) for(let dy=-8;dy<8;dy++) colors.add(pixel(x+dx,y+dy).join(','));
              return {zero:pixel(center.x+zero.width*.4,center.y-8),missingColors:[...colors],
                pointCount:points.length,missing:{x,y},zeroColor:zero.options.backgroundColor};
            }""", sparse_id)
            assert painted["pointCount"] == 3
            assert painted["zero"] == [226,242,241,255], painted
            assert len(painted["missingColors"]) > 2
            assert "226,242,241,255" not in painted["missingColors"]
            bbox = sparse.bounding_box()
            page.mouse.move(bbox["x"] + painted["missing"]["x"], bbox["y"] + painted["missing"]["y"])
            expect(page.locator('.fw-heatmap-tooltip:visible')).to_contain_text('No observation')
            page.mouse.move(0,0)
            expect(page.locator('.fw-heatmap-tooltip:visible')).to_have_count(0)

            # First draw and subsequent responsive layout both use axis slots.
            page.set_viewport_size({"width": 900 if width == 390 else 520, "height": 900})
            page.wait_for_function("""id => {const c=window._chartInstances[id];
              return Math.abs(c.width-c.canvas.parentElement.clientWidth)<1 &&
                c.getDatasetMeta(0).data.every(e=>e.width<=c.chartArea.width/c.scales.x.getLabels().length);
            }""", arg=chart("Complete nonnegative observations"))
            page.evaluate("id=>{window.heatmapText=[];window._chartInstances[id].update('none')}",chart("Complete nonnegative observations"))
            resized = geometry("Complete nonnegative observations")
            for text in resized["labels"]:
                point = next(p for p in resized["points"] if abs(p["center"]["x"]-text["x"])<0.1 and abs(p["center"]["y"]-text["y"])<0.1)
                assert text["width"]+8 <= point["width"]
            page.set_viewport_size({"width":width,"height":900})
            page.locator(f'#{chart("Complete nonnegative observations")}').scroll_into_view_if_needed()
            page.wait_for_function("id=>Math.abs(window._chartInstances[id].width-document.getElementById(id).parentElement.clientWidth)<1",arg=chart("Complete nonnegative observations"))
            geometry("Complete nonnegative observations")

            # A real hover on a slot with no fitted label retains the exact
            # aggregate rather than the compact 12.3k label.
            dense_id = chart("Narrow slots")
            dense_canvas = page.locator(f'#{dense_id}')
            dense_canvas.scroll_into_view_if_needed()
            center = page.evaluate("id=>window._chartInstances[id].getDatasetMeta(0).data[0].getCenterPoint()",dense_id)
            box = dense_canvas.bounding_box()
            page.mouse.move(box["x"]+center["x"],box["y"]+center["y"])
            expect(page.locator('.fw-chart-tooltip')).to_contain_text('12345')
            page.mouse.move(0,0)

            live_id = chart("Filtered platform quantities")
            live_section = page.locator('#sec-heatmaps-—-live-platform-quantities')
            live_section.get_by_role("button",name="Android",exact=True).click()
            page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data.find(c=>c.x==='One'&&c.y==='Regular').v===75",arg=live_id)
            assert snapshot("Complete nonnegative observations")["data"] == complete["data"]
            live_values = snapshot("Filtered platform quantities")["data"]
            for _ in range(3):
                page.evaluate("window.renderAll()")
            for title in ids:
                page.locator(f'#{ids[title]}').scroll_into_view_if_needed()
                page.wait_for_function("id=>!!window._chartInstances[id]",arg=ids[title])
            assert snapshot("Filtered platform quantities")["data"] == live_values
            assert page.evaluate("id=>window._fwFilterEngine._subscribers.showcase_heatmaps.filter(s=>s.id===id).length",live_id) == 1
            assert page.locator(f'[data-heatmap-key="{live_id}"]').count() == 1

            explicit = snapshot("Fixed two-stop palette")["points"][0]["color"]
            light = snapshot("Complete nonnegative observations")["points"][0]["color"]
            for theme in ("trellum dark", "trellum light"):
                page.locator('#fwThemeSelect').select_option(theme)
                for title, cid in ids.items():
                    page.locator(f'#{cid}').scroll_into_view_if_needed()
                    page.wait_for_function("id=>!!window._chartInstances[id]",arg=cid)
                assert page.locator('html').get_attribute('data-theme') == theme
                color = snapshot("Complete nonnegative observations")["points"][0]["color"]
                assert (color != light) if theme.endswith('dark') else (color == light)
                assert snapshot("Fixed two-stop palette")["points"][0]["color"] == explicit
                assert snapshot("Filtered platform quantities")["data"] == live_values
                evidence = os.environ.get('HEATMAP_EVIDENCE_DIR')
                if evidence and theme == "trellum dark":
                    section = page.locator(f'#{sparse_id}').locator("xpath=ancestor::div[contains(@class,'fw-section')][1]")
                    section.screenshot(path=str(Path(evidence) / f'missing-dark-{width}.png'))

            signed = snapshot("Signed values · legacy semantics")
            assert any(t['text'] == '-20' and t['color'] == '#000000' for t in signed['labels'])

            # Empty filter results and a restored result replace key content.
            page.evaluate("window._fwFilterEngine.setFilter('showcase_heatmaps','empty-probe','platform','equals','absent')")
            expect(key("Filtered platform quantities")).to_have_text('No matching observations')
            assert snapshot("Filtered platform quantities")["data"] == []
            page.evaluate("window._fwFilterEngine.setFilter('showcase_heatmaps','empty-probe','platform','equals','__all__')")
            assert snapshot("Filtered platform quantities")["data"] == live_values
            assert key("Filtered platform quantities").locator('.fw-heatmap-gradient').count() == 1

            evidence = os.environ.get('HEATMAP_EVIDENCE_DIR')
            if evidence:
                dest = Path(evidence); dest.mkdir(parents=True, exist_ok=True)
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(dest / f'current-module-{width}.png'),full_page=True)
                for title in ("Complete nonnegative observations", "Measured zero versus no observation"):
                    section = page.locator(f'#{chart(title)}').locator("xpath=ancestor::div[contains(@class,'fw-section')][1]")
                    section.screenshot(path=str(dest / f'{"complete" if title.startswith("Complete") else "missing"}-{width}.png'))
                (dest / f'validation-{width}.json').write_text(json.dumps(result.to_dict(),indent=2))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            # Legacy payload and markup: genuine old schema, no key wrapper.
            legacy_id = chart("Uniform global")
            page.evaluate("""id=>{
              document.querySelector('[data-heatmap-key="'+id+'"]').remove();
              window._fwRenderers.heatmap(id,{type:'heatmap',cells:[{x:'A',y:'Same',v:64}],
                x_labels:['A'],y_labels:['Same'],color_scale:'auto'});
            }""",legacy_id)
            assert snapshot("Uniform global")["data"] == [{"x":"A","y":"Same","v":64}]

            # Supplied nonfinite numeric populations retain raw values and
            # geometry while refusing a valid nonnegative key.
            page.evaluate("""id=>{
              window._fwRenderers.heatmap(id,{type:'heatmap',heatmap_style:'sequential-v1',
                cells:[{x:'A',y:'Same',v:Infinity},{x:'B',y:'Same',v:1}],
                x_labels:['A','B'],y_labels:['Same'],color_scale:'auto'});
            }""",chart("Uniform relative"))
            assert 'Signed or nonfinite' in key("Uniform relative").inner_text()
            assert page.evaluate("id=>window._chartInstances[id].data.datasets[0].data[0].v===Infinity",chart("Uniform relative"))

            # Three explicit stops are not replaced by the automatic hue.
            page.evaluate("""id=>{
              window._fwRenderers.heatmap(id,{type:'heatmap',heatmap_style:'sequential-v1',log_scale:false,
                cells:[{x:'A',y:'Same',v:1},{x:'B',y:'Same',v:2},{x:'C',y:'Same',v:3}],
                x_labels:['A','B','C'],y_labels:['Same'],color_scale:['#ff0000','#00ff00','#0000ff']});
            }""",chart("Column relative"))
            assert [p['color'] for p in snapshot("Column relative")["points"]] == [
                'rgb(255,0,0)', 'rgb(0,255,0)', 'rgb(0,0,255)']

            for unit, expected in (("currency", "$2"), ("percent", "200%")):
                page.evaluate("""([id,unit])=>{
                  window._fwRenderers.heatmap(id,{type:'heatmap',heatmap_style:'sequential-v1',value_format:unit,
                    cells:[{x:'A',y:'Same',v:2}],x_labels:['A'],y_labels:['Same'],color_scale:'auto'});
                }""",[chart("Column relative"),unit])
                assert expected in key("Column relative").inner_text()

            # Existing drop_empty keeps only categories with a positive cell,
            # including its known negative-only removal limitation.
            page.evaluate("""id=>{
              window._fwRenderers.heatmap(id,{type:'heatmap',heatmap_style:'sequential-v1',drop_empty:true,
                cells:[{x:'A',y:'Negative',v:-4},{x:'B',y:'Zero',v:0},{x:'C',y:'Positive',v:2}],
                x_labels:['A','B','C'],y_labels:['Negative','Zero','Positive'],color_scale:'auto'});
            }""",chart("Column relative"))
            assert snapshot("Column relative")["data"] == [{"x":"C","y":"Positive","v":2}]

            if width == 390:  # Explicitly heavy phone/large sparse matrix check.
                sparse_cfg = {
                    'type':'heatmap', 'heatmap_style':'sequential-v1', 'color_scale':'auto',
                    'x_labels':[f'X{i}' for i in range(300)],
                    'y_labels':[f'Y{i}' for i in range(300)],
                    'cells':[{'x':f'X{i}','y':f'Y{i}','v':i+1} for i in range(300)],
                }
                page.evaluate("([id,cfg])=>window._fwRenderers.heatmap(id,cfg)",[legacy_id,sparse_cfg])
                sparse_result = snapshot("Uniform global")
                assert len(sparse_result["data"]) == len(sparse_result["points"]) == 300
                assert len(sparse_result["x"]) == len(sparse_result["y"]) == 300
                assert page.evaluate("id=>window._chartInstances[id].canvas.parentElement.childElementCount",legacy_id) == 2
                assert not any(t['text'] == '—' for t in sparse_result['labels'])

            # Workspace mode keeps the shared wrapper's queued draw lease and
            # pagehide disposal. Reinitializing releases the previous lease.
            cfg = json.loads((tmp_path / 'data.json').read_text())["components"][live_id]
            page.evaluate("""([id,cfg])=>{
              window._fwMetricsWorkspace=true;
              window._fwRenderers.heatmap(id,cfg);
              window._fwRenderers.heatmap(id,cfg);
            }""",[live_id,cfg])
            assert page.evaluate("id=>typeof window._fwMetricDrawLeases[id]",live_id) == 'function'
            assert page.evaluate("id=>window._fwFilterEngine._subscribers.showcase_heatmaps.filter(s=>s.id===id).length",live_id) == 1
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            assert page.evaluate("Object.keys(window._fwMetricDrawLeases).length") == 0
            assert page.evaluate("id=>window._fwFilterEngine._subscribers.showcase_heatmaps.filter(s=>s.id===id).length",live_id) == 0
            assert errors == [], errors
        finally:
            browser.close()


def test_heatmap_paint_geometry_and_lifecycle_desktop(tmp_path):
    _check(tmp_path,1280)


@pytest.mark.heavy
def test_heatmap_paint_geometry_and_lifecycle_phone(tmp_path):
    _check(tmp_path,390)
