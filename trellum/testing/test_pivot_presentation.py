"""PivotTable presentation preserves its values while adding optional freezing."""
from dataclasses import dataclass
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import PivotTable
from trellum.components.base import RenderContext
from trellum.report import ReportContext
from trellum.rendering.html_builder import render_report
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    html = table.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def test_keyword_only_freeze_preserves_positional_api_and_data():
    @dataclass
    class ExtendedPivot(PivotTable):
        note: str = ""

    frame = pd.DataFrame({"Group": ["A", "A", "B"], "Kind": ["x", "y", "x"],
                          "Amount": [100, 900, 300]})
    table = ExtendedPivot(frame, ["Group"], "Kind", "Amount", "sum", "Title", "number", None, False,
                          note="ok", freeze=True, freeze_height=240)
    html, cfg = _register(table)
    assert table.note == "ok" and cfg["data"][0]["Amount"] == 100
    assert 'data-freeze="true"' in html and 'data-freeze-height="240"' in html
    legacy_html, _ = _register(PivotTable(frame, ["Group"], "Kind", "Amount"))
    assert 'data-freeze="false"' in legacy_html
    with pytest.raises(ValueError, match="freeze_height"):
        _register(PivotTable(frame, ["Group"], freeze_height=0))


def test_showcase_validates_without_warnings(tmp_path):
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_pivots.py"
    spec = importlib.util.spec_from_file_location("examples_pivots", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext({"name": "Pivots", "studio": "test", "category": "Components",
                         "description": "Explore synthetic revenue and sales by title, platform, region and month."},
                        "pivots", str(tmp_path))
    module.add_sections(ctx)
    render_report(ctx, auto_refresh=False)
    result = validate_report(ctx)
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()


@pytest.mark.heavy
def test_frozen_pivot_real_chromium(tmp_path):
    from trellum.components import PivotTable
    from playwright.sync_api import sync_playwright
    frame = pd.DataFrame([{"Region": f"R{i % 4}", "Product": f"P{i % 3}",
                           "Month": f"M{i % 8}", "Sales": i * 10} for i in range(64)])
    ctx = ReportContext({"name": "Pivot browser", "studio": "test"}, "pivot-browser", str(tmp_path))
    ctx.add_section("Frozen", [PivotTable(frame, ["Region", "Product"], "Month", "Sales", freeze=True)])
    render_report(ctx, auto_refresh=False)
    vendor = Path(__file__).parents[1] / "static/vendor"
    from urllib.parse import unquote, urlsplit
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 720})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        def serve(route):
            parsed = urlsplit(route.request.url)
            path = unquote(parsed.path).lstrip("/")
            target = vendor / path[len("_vendor/"):] if path.startswith("_vendor/") else tmp_path / path
            route.fulfill(path=str(target)) if target.is_file() else route.fulfill(status=404)
        page.route("**/*", serve)
        page.goto("http://pivot.test/index.html", wait_until="networkidle")
        scroll = page.locator(".fw-pivot-scroll")
        pivot_id = scroll.get_attribute("id")
        page.locator(f"#{pivot_id}_aggSel + .ss-main").click()
        page.locator(".ss-option").filter(has_text="count").click()
        page.wait_for_timeout(150)
        assert scroll.locator("tfoot").inner_text().endswith("64")
        page.locator(f"#{pivot_id}_aggSel + .ss-main").click()
        page.locator(".ss-option").filter(has_text="sum").click()
        page.wait_for_timeout(150)
        page.screenshot(path="/tmp/trellum-pivot-phone.png", full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path="/tmp/trellum-pivot-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 720})
        initial = scroll.evaluate("el => ({second:el.querySelector('tbody tr td:nth-child(2)').getBoundingClientRect().left})")
        scroll.evaluate("el => {el.scrollTop=250; el.scrollLeft=180}")
        page.wait_for_timeout(100)
        metrics = scroll.evaluate("el => {const r=el.getBoundingClientRect(), rows=[...el.querySelectorAll('thead tr')], first=el.querySelector('tbody tr td'), second=el.querySelector('tbody tr td:nth-child(2)'); return {client:el.clientWidth, sw:el.scrollWidth, sh:el.scrollHeight, headers:rows.map(x=>getComputedStyle(x.querySelector('th')).position), headerTops:rows.map(x=>x.getBoundingClientRect().top), first:getComputedStyle(first).position, firstLeft:first.getBoundingClientRect().left, second:getComputedStyle(second).position, secondLeft:second.getBoundingClientRect().left, scrollerTop:r.top, pageOverflow:document.documentElement.scrollWidth>innerWidth}}")
        assert metrics["headers"] == ["sticky", "sticky"] and metrics["first"] == "sticky"
        assert metrics["second"] != "sticky" and metrics["sw"] > metrics["client"] and metrics["sh"] > 320
        assert abs(metrics["headerTops"][0] - metrics["scrollerTop"]) < 3
        assert metrics["headerTops"][1] > metrics["headerTops"][0]
        assert abs(metrics["firstLeft"] - scroll.bounding_box()["x"]) < 3
        assert metrics["secondLeft"] < initial["second"]
        assert not metrics["pageOverflow"] and errors == []
        browser.close()
