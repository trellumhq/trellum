"""Chromium checks for AreaChart fills, legend visibility and filter lifecycle."""
from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest

from trellum.components import AreaChart, DataSource, FilterBar
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def _report(output):
    rows = []
    for day, values in enumerate(((10, 5, 3), (13, 4, 5), (12, 8, 2), (15, 6, 7))):
        for platform, scale in (("web", 1), ("ios", 2)):
            rows.append({"day": f"2026-10-0{day + 1}", "platform": platform,
                         "a": values[0] * scale, "b": values[1] * scale,
                         "c": values[2] * scale})
    df = pd.DataFrame(rows)
    ctx = ReportContext(config={"name": "Area fills", "studio": "test",
                                "category": "Test", "description": "Area fill checks."},
                        slug="area-fills", output_dir=str(output))
    ctx.add_section("Area filters", [DataSource("areas", df),
        FilterBar("areas", df, filters=[{"column": "platform", "type": "toggle"}])])
    ctx.add_section("Areas", [
        AreaChart(df, x="day", y=["a", "b", "c"], title="Ordinary",
                  dataset_id="areas"),
        AreaChart(df, x="day", y=["a", "b", "c"], title="Stacked",
                  stacked=True, dataset_id="areas"),
        AreaChart(df.iloc[::2], x="day", y=["a", "b", "c"], title="Static stacked",
                  stacked=True, static=True),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


def _check_area_chart(width):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")

    from pathlib import Path
    import tempfile

    output = Path(tempfile.mkdtemp(prefix="area-chart-browser-"))
    _report(output)
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html", wait_until="networkidle")
            for cid in page.locator("canvas[data-fw-kind='AreaChart']").evaluate_all("els => els.map(e => e.id)"):
                page.locator(f"#{cid}").scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=cid)

            charts = page.evaluate("""() => Object.entries(window._chartInstances)
                .filter(([id, c]) => c.canvas.dataset.fwKind === 'AreaChart')
                .map(([id,c]) => ({id,title:c.canvas.dataset.fwTitle,height:c.height,
                  ds:c.data.datasets.map(d=>({label:d.label,data:d.data.slice(),fill:d.fill,
                    bg:d.backgroundColor,border:d.borderWidth,point:d.pointRadius,tension:d.tension}))}))""")
            ordinary, stacked, static_stacked = charts
            expected_height = 320 if width == 1280 else 180
            assert [ordinary["height"], stacked["height"], static_stacked["height"]] == [expected_height] * 3
            assert [d["bg"][-2:] for d in ordinary["ds"]] == ["1f"] * 3
            assert [d["border"] for d in ordinary["ds"]] == [2] * 3
            assert all(d["point"] == 0 and d["tension"] == 0 for d in ordinary["ds"])
            assert [d["bg"][-2:] for d in stacked["ds"]] == ["a6"] * 3
            assert [d["border"] for d in stacked["ds"]] == [1.5] * 3
            assert [d["fill"] for d in stacked["ds"]] == ["origin", "-1", "-1"]
            assert all(d["point"] == 0 and d["tension"] == 0 for d in stacked["ds"])
            initial_targets = page.evaluate("""id => window._chartInstances[id].data.datasets.map((_,i)=>
                window._chartInstances[id].getDatasetMeta(i).$filler.fill)""", stacked["id"])
            assert initial_targets == ["origin", 0, 1], initial_targets

            # Scan the actual canvas raster for each series' fill color at
            # translucent alpha; this verifies three adjacent bands paint.
            paint_counts = page.evaluate("""id => {
              const c=window._chartInstances[id], ctx=c.ctx, image=ctx.getImageData(0,0,c.canvas.width,c.canvas.height);
              return c.data.datasets.map(ds=>{
                const color=ds.backgroundColor.slice(1,7), rgb=[0,2,4].map(i=>parseInt(color.slice(i,i+2),16));
                let count=0;
                for(let i=0;i<image.data.length;i+=4) if(image.data[i+3] > 150 && image.data[i+3] < 180 &&
                  Math.abs(image.data[i]-rgb[0])<4 && Math.abs(image.data[i+1]-rgb[1])<4 &&
                  Math.abs(image.data[i+2]-rgb[2])<4) count++;
                return count;
              });
            }""", stacked["id"])
            assert all(count > 10 for count in paint_counts), paint_counts

            # Actual Chart.js legend clicks hide each series independently.
            hidden_targets = page.evaluate("""id => {
              const c=window._chartInstances[id], out=[];
              for (const hidden of [0,1,2]) {
                c.data.datasets.forEach((_,i)=>c.setDatasetVisibility(i,true)); c.update();
                c._fwLegendEl.querySelectorAll('.cl')[hidden].click();
                out.push(c.data.datasets.map((_,i)=>c.getDatasetMeta(i).$filler.fill));
              }
              return out;
            }""", stacked["id"])
            assert hidden_targets == [["origin", "origin", 1], ["origin", 0, 0], ["origin", 0, 1]]

            before_static = page.evaluate("id=>window._chartInstances[id].data.datasets[0].data.slice()", static_stacked["id"])
            page.get_by_role("button", name="ios", exact=True).click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 20", arg=stacked["id"])
            after_filter = page.evaluate("id=>window._chartInstances[id].data.datasets[0].data.slice()", stacked["id"])
            assert after_filter[0] == 20 and before_static[0] == 10
            assert page.evaluate("id=>window._chartInstances[id].data.datasets[0].data.slice()", static_stacked["id"]) == before_static
            filtered_style = page.evaluate("""id => {const c=window._chartInstances[id];return {
              opacity:c.data.datasets.map(d=>d.backgroundColor.slice(-2)),
              targets:c.data.datasets.map((_,i)=>c.getDatasetMeta(i).$filler.fill)}}""", stacked["id"])
            assert filtered_style == {"opacity": ["a6"] * 3, "targets": ["origin", 0, 1]}

            select = page.locator("#fwThemeSelect")
            for theme in ("trellum dark", "trellum light"):
                select.select_option(theme)
                for chart in charts:
                    page.locator(f"#{chart['id']}").scroll_into_view_if_needed()
                    page.wait_for_function("""id => {
                      const c = window._chartInstances[id], palette = window.getThemeColors().chart_colors;
                      return c.data.datasets.every((d, i) =>
                        d.borderColor === palette[i % palette.length] &&
                        d.backgroundColor === d.borderColor + d._fillOpacity);
                    }""", arg=chart["id"])
                assert page.locator("html").get_attribute("data-theme") == theme
            page.evaluate("window.renderAll()")
            page.wait_for_function("id => !!window._chartInstances[id]", arg=stacked["id"])
            refreshed = page.evaluate("""id => {const c=window._chartInstances[id];return {
              opacity:c.data.datasets.map(d=>d.backgroundColor.slice(-2)),
              fill:c.data.datasets.map(d=>d.fill), targets:c.data.datasets.map((_,i)=>c.getDatasetMeta(i).$filler.fill),
              values:c.data.datasets[0].data.slice(),height:c.height}}""", stacked["id"])
            assert refreshed["opacity"] == ["a6"] * 3
            assert refreshed["fill"] == ["origin", "-1", "-1"]
            assert refreshed["targets"] == ["origin", 0, 1]
            assert refreshed["values"] == after_filter and refreshed["height"] == expected_height
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == [], errors
            browser.close()
    finally:
        server.shutdown()


def test_area_chart_paint_and_lifecycle_desktop():
    _check_area_chart(1280)


@pytest.mark.heavy
def test_area_chart_paint_and_lifecycle_phone():
    _check_area_chart(390)
