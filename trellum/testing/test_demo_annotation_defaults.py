"""Business demo defaults retain event data and usable native/custom toggles."""

import importlib.util
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest
import yaml

from trellum.components import DataSource, LineChart, RawHTML
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.runner import events as event_loader

_DEMOS = Path(__file__).parents[1] / "demo/reports"


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_business_events_remain_available_and_custom_canvas_follows_controls(tmp_path, monkeypatch, width):
    from playwright.sync_api import sync_playwright

    config = yaml.safe_load((_DEMOS / "insert-coin/report.yaml").read_text())
    ctx = ReportContext(config=config, slug="annotation-defaults", output_dir=str(tmp_path))
    dates = pd.date_range(end=ctx._now_utc.date(), periods=20).strftime("%Y-%m-%d").tolist()
    events = [
        {"date": dates[i * 3 + 2], "label": "Audit " + kind, "type": kind}
        for i, kind in enumerate(["release", "incident", "campaign", "ab_test"])
    ]
    (tmp_path / "events.yaml").write_text(yaml.safe_dump({"events": events}))
    monkeypatch.setattr(event_loader, "get_project_root", lambda: str(tmp_path))
    loaded = event_loader._load_events(config, ctx)
    assert len(loaded) == 4
    assert {ev["type"] for ev in loaded if ev["default_visible"]} == {"release", "incident"}
    frame = pd.DataFrame({"event_date": dates, "dau": range(100, 120),
                          "iap_revenue": [100] * 20, "spender_tier": ["default"] * 20})
    spec = importlib.util.spec_from_file_location("annotation_game", _DEMOS / "insert-coin/custom_sections.py")
    custom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(custom)
    ctx.add_section("Native and custom", [
        DataSource("arcade", frame),
        LineChart(frame, "event_date", "dau", "Native chart", dataset_id="arcade"),
        LineChart(frame, "event_date", "dau", "Quiet bound", dataset_id="arcade", show_events=False),
        LineChart(frame, "event_date", "dau", "Quiet static", static=True, show_events=False),
        RawHTML(html=custom.CABINET_HTML, js=custom.CABINET_JS, data_key="_arcade", data={
            "tiers": {"default": {"speed": 1, "pull": 22, "size": 14}}, "pit_fraction": 0.55,
        }),
    ])
    render_report(ctx, str(tmp_path), data={"_events": loaded,
                  "_weekday_highlight": {"days": [0], "default_visible": True}}, auto_refresh=False)
    copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 844})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""(() => {
            const clear = CanvasRenderingContext2D.prototype.clearRect;
            const text = CanvasRenderingContext2D.prototype.fillText;
            CanvasRenderingContext2D.prototype.clearRect = function(...args) {
                if(this.canvas.id === 'rmCanvas') window.paintedEvents = [];
                return clear.apply(this,args);
            };
            CanvasRenderingContext2D.prototype.fillText = function(...args) {
                if(this.canvas.id === 'rmCanvas' && String(args[0]).startsWith('Audit '))
                    (window.paintedEvents ||= []).push(args[0]);
                return text.apply(this,args);
            };
        })();""")

        def serve(route):
            target = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
            route.fulfill(path=str(target)) if target.is_file() else route.fulfill(status=404)

        page.route("**/*", serve)
        page.goto("http://annotation-defaults.test/index.html", wait_until="networkidle")
        page.wait_for_function("document.querySelector('#fwAnnoBar')._rideMetricAnnotations && window.paintedEvents?.length === 2")
        values = page.evaluate("Object.values(_chartInstances)[0].data.datasets[0].data")
        assert sorted(page.evaluate("paintedEvents")) == ["Audit incident", "Audit release"]
        assert page.evaluate("Object.values(_chartInstances)[0]._fwEventMarkers.length") == 2
        def check_quiet():
            for title in ("Quiet bound", "Quiet static"):
                canvas = page.locator(f'canvas[data-fw-title="{title}"]')
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id=>!!_chartInstances[id]", arg=canvas.get_attribute("id"))
                state = canvas.evaluate("""el=>{
                    const chart=Chart.getChart(el), annos=chart.options.plugins.annotation.annotations;
                    return {keys:Object.keys(annos), markers:chart._fwEventMarkers || [],
                        controls:!!document.getElementById(el.id+'-adaptive-events'),
                        data:chart.data.datasets[0].data};
                }""")
                assert state["keys"] and all(key.startswith("wd_") for key in state["keys"])
                assert not state["markers"] and not state["controls"]
            return state["data"]
        assert check_quiet() == values
        for kind, count in [("campaign", 3), ("ab_test", 4)]:
            page.locator(f'[data-anno-type="{kind}"]').click()
            page.wait_for_function("count => paintedEvents.length === count", arg=count)
            assert page.evaluate("Object.values(_chartInstances)[0]._fwEventMarkers.length") == count
            check_quiet()
        page.locator('[data-anno-type="campaign"]').click()
        page.wait_for_function("!paintedEvents.includes('Audit campaign')")
        assert page.evaluate("Object.values(_chartInstances)[0].data.datasets[0].data") == values
        # The reader's explicit choice survives a reload; it is not reset to the demo default.
        page.reload(wait_until="networkidle")
        page.wait_for_function("window.paintedEvents?.length === 3")
        assert page.locator('[data-anno-type="ab_test"]').evaluate("el=>el.classList.contains('active')")
        check_quiet()
        page.evaluate("fw.filterEngine.setFilter('arcade','short','event_date','range', {min:_reportData._events[0].date,max:_reportData._events[2].date})")
        page.locator("#fwThemeSelect").select_option("trellum light")
        check_quiet()
        page.evaluate("window.renderAll()")
        check_quiet()
        assert page.evaluate("_reportData._events.length") == 4
        assert errors == []
        browser.close()
