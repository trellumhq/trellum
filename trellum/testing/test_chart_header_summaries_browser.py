"""Native generated report checks using vendored Chromium assets."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import AreaChart, BarChart, Grid, TabGroup
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report

from trellum.testing.test_chart_header_summaries import (
    BASELINE, ROOT, SUMMARY, baseline_class, example_context, frame,
)


def _build(output):
    ctx = example_context(output)
    # Diagnostics cover the authored native examples, before browser-only edge fixtures.
    result = validate_report(ctx, details=compute_details(ctx))
    issues = [(c.id, c.level, c.message) for c in result.checks if c.level in {"warn", "fail"}]
    assert issues == []
    ctx.add_section("Edge fixtures", [
        Grid(columns=1, show_titles=False, children=[
            AreaChart(frame(), "category", "value", "Hidden title", static=True,
                      header_summary=SUMMARY, header_context="Independent authored context"),
        ]),
        TabGroup([
            {"label": "Start", "content": [BarChart(frame(), "category", "value", "Visible start", static=True)]},
            {"label": "Reveal summary", "content": [AreaChart(frame(), "category", "value", "Revealed area", static=True,
                                                               header_summary=SUMMARY)]},
        ]),
        BarChart(frame(), "category", "value", "Ratio mean", dataset_id="showcase_summary_categories",
                 ratios=[{"numerator": "orders", "denominator": "orders", "label": "Rate"}],
                 y_format="percent", header_summary={"stat": "mean", "scope": "plotted_series"}),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    return issues


def _route(page, root):
    def serve(route):
        path = root / unquote(urlparse(route.request.url).path).lstrip("/")
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            route.fulfill(status=404, body="Missing native test asset")
    page.route("**/*", serve)


def _ready(page):
    page.evaluate("document.fonts.ready")
    for canvas in page.locator("canvas:visible").all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))


def _id(page, title):
    return page.locator(f'canvas[data-fw-title="{title}"]').get_attribute("id")


def _value(page, cid):
    return page.locator(f'[data-chart-summary="{cid}"] .fw-chart-summary-value').text_content()


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_summaries_filter_empty_restore_theme_and_capture(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    evidence = Path(os.environ.get("TRELLUM_SUMMARY_EVIDENCE", "/tmp/trellum-chart-summaries"))
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            _route(page, tmp_path)
            page.goto("http://chart-summaries.test/index.html", wait_until="networkidle")
            _ready(page)
            bar = _id(page, "Selected category orders")
            area = _id(page, "Selected returns band")
            top = _id(page, "Top two and Other")
            ratio = _id(page, "Ratio mean")
            static = _id(page, "Static summary on")
            assert [_value(page, cid) for cid in (static, bar, area, top, ratio)] == ["35", "105", "12", "15", "100.00%"]
            assert page.locator('[data-show-titles="false"] .fw-chart-title').is_hidden()
            assert page.locator('[data-show-titles="false"] .fw-chart-summary').is_visible()
            assert page.locator('[data-show-titles="false"] .fw-chart-header-context').is_visible()
            assert page.locator(f'#{bar}').get_attribute("aria-describedby").split() == [bar + "-header-context", bar + "-summary-scope"]
            assert "Top-N and Other" in page.locator(f'[data-chart-summary="{bar}"]').get_attribute("title")
            page.get_by_role("tab", name="Reveal summary", exact=True).click()
            revealed = _id(page, "Revealed area")
            page.locator(f'#{revealed}').scroll_into_view_if_needed()
            page.wait_for_function("id => !!window._chartInstances[id]", arg=revealed)
            assert _value(page, revealed) == "30"

            section = page.locator(f'#{bar}').locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
            section.get_by_role("button", name="Partner", exact=True).click()
            page.wait_for_function("id => document.querySelector('[data-chart-summary=\"' + id + '\"] .fw-chart-summary-value').textContent === '70'", arg=bar)
            assert _value(page, area) == "12" and _value(page, top) == "15"
            assert _value(page, ratio) == "100.00%"
            # Native engine empty state retains chart zero-fill semantics but no stale total.
            page.evaluate("fw.filterEngine.setFilter('showcase_summary_categories', 'empty-probe', 'channel', 'equals', 'missing')")
            page.wait_for_function("id => document.querySelector('[data-chart-summary=\"' + id + '\"] .fw-chart-summary-value').textContent === '—'", arg=bar)
            assert _value(page, ratio) == "—"
            page.evaluate("fw.filterEngine.clearFilter('showcase_summary_categories', 'empty-probe')")
            page.wait_for_function("id => document.querySelector('[data-chart-summary=\"' + id + '\"] .fw-chart-summary-value').textContent === '70'", arg=bar)
            section.get_by_role("button", name="All", exact=True).click()
            page.wait_for_function("id => document.querySelector('[data-chart-summary=\"' + id + '\"] .fw-chart-summary-value').textContent === '105'", arg=bar)
            # Partner raw stacked band is [2,4,0,2], so its sum is 8, not cumulative orders.
            area_section = page.locator(f'#{area}').locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
            area_section.get_by_role("button", name="Partner", exact=True).click()
            page.wait_for_function("id => document.querySelector('[data-chart-summary=\"' + id + '\"] .fw-chart-summary-value').textContent === '8'", arg=area)
            assert page.evaluate("id => window._chartInstances[id].data.datasets[1].data", area) == [2, 4, 0, 2]
            # Visibility and zoom do not redefine summary scope.
            page.evaluate("id => {const c=window._chartInstances[id];c.setDatasetVisibility(1,false);c.update();c.zoom(1.2);}", area)
            assert _value(page, area) == "8"

            for theme in ("trellum light", "trellum dark"):
                page.locator("#fwThemeSelect").select_option(theme)
                _ready(page)
                assert _value(page, bar) == "105" and _value(page, area) == "8"
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                for title, name in (("Selected category orders", "bar"), ("Selected returns band", "area"),
                                    ("Static summary on", "static")):
                    chart = page.locator(f'canvas[data-fw-title="{title}"]').locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
                    chart.screenshot(path=str(evidence / f"{width}-{theme.split()[-1]}-{name}.png"))

            page.evaluate("window.renderAll();window.renderAll()")
            _ready(page)
            assert _value(page, bar) == "105" and _value(page, area) == "8"
            assert page.locator(f'#{bar}').get_attribute("aria-describedby").split().count(bar + "-summary-scope") == 1
            # Exercise the new scope with the native static renderer. The
            # legacy unscoped Line summary math intentionally stays unchanged.
            values = page.evaluate("""id => {
                const cfg=window._reportData.components[id], saved=JSON.parse(JSON.stringify(cfg));
                const read=()=>document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent;
                const draw=(data,stat,scoped=true)=>{
                    cfg.datasets[0].data=data;
                    cfg.header_summary={stat,series:0,label:'Probe',format:'plain'};
                    if(scoped) cfg.header_summary.scope='plotted_series';
                    window._fwRenderers.chart(id,cfg);return read();
                };
                const out={};
                out.stats=['mean','sum','last','min','max'].map(stat=>draw([null,0,8,Infinity,-2,NaN,null],stat));
                out.empty=draw([null,NaN,Infinity], 'sum');
                out.zero=draw([0,null], 'last');
                out.sumOverflow=draw([1e308,1e308], 'sum');
                out.meanOverflow=draw([1e308,1e308], 'mean');
                out.legacyOverflow=draw([1e308,1e308], 'sum',false);
                cfg.header_summary=saved.header_summary;cfg.datasets=saved.datasets;
                window._fwRenderers.chart(id,cfg);
                delete cfg.header_summary;window._fwRenderers.chart(id,cfg);
                const box=document.querySelector('[data-chart-summary="'+id+'"]');
                out.removed={text:box.textContent,display:getComputedStyle(box).display,
                    described:document.getElementById(id).getAttribute('aria-describedby')};
                cfg.header_summary=saved.header_summary;window._fwRenderers.chart(id,cfg);
                out.restored=read();return out;
            }""", static)
            assert values == {"stats": ["2", "6", "-2", "-2", "8"], "empty": "—", "zero": "0",
                              "sumOverflow": "—", "meanOverflow": "—", "legacyOverflow": "Infinity",
                              "removed": {"text": "", "display": "none", "described": None}, "restored": "35"}
            if width == 1280:
                # Native report section capture, plus ordinary full-report PNG/PDF exports.
                snapshot = section.evaluate("el => fw.captureElementSnapshotForAnalysis(el)")
                (evidence / "native-section.json").write_text(json.dumps(snapshot, indent=2))
                (evidence / "native-section.png").write_bytes(base64.b64decode(snapshot["image"]["data_base64"]))
                for extension, fn in (("png", "exportPNG"), ("pdf", "exportPDF")):
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f"fw.{fn}()")
                    download.value.save_as(str(evidence / f"native-report.{extension}"))
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_opt_out_visual_parity_against_actual_baseline(tmp_path, width):
    from playwright.sync_api import sync_playwright

    evidence = Path(os.environ.get("TRELLUM_SUMMARY_EVIDENCE", "/tmp/trellum-chart-summaries"))
    evidence.mkdir(parents=True, exist_ok=True)
    roots = []
    baseline_class("BarChart")  # Skip only when the checkout lacks historical objects.
    current_js = (ROOT / "trellum/static/js/components/chart_base.js").read_text()
    baseline_js = subprocess.check_output(["git", "show", f"{BASELINE}:trellum/static/js/components/chart_base.js"], cwd=ROOT, text=True)
    for name in ("baseline", "current"):
        output = tmp_path / name
        output.mkdir()
        classes = [baseline_class(c.__name__) for c in (BarChart, AreaChart)] if name == "baseline" else [BarChart, AreaChart]
        ctx = ReportContext(config={"name": "Default output", "studio": "test", "category": "Test",
            "description": "Baseline parity for ordinary static bars and areas."}, slug="baseline", output_dir=str(output))
        ctx.add_section("Defaults", [c(frame(), "category", "value", title, static=True)
                                     for c, title in zip(classes, ("Default bar", "Default area"))])
        render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
        if name == "baseline":
            replaced = False
            for file in [output / "index.html", *output.rglob("*.js")]:
                source = file.read_text()
                if current_js in source:
                    file.write_text(source.replace(current_js, baseline_js))
                    replaced = True
            assert replaced, "Baseline must also execute its historical chart runtime"
        roots.append(output)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            images = {}
            for name, root in zip(("baseline", "current"), roots):
                page = browser.new_page(viewport={"width": width, "height": 900})
                _route(page, root)
                page.goto("http://baseline.test/index.html", wait_until="networkidle")
                _ready(page)
                for theme in ("trellum light", "trellum dark"):
                    page.locator("#fwThemeSelect").select_option(theme)
                    _ready(page)
                    images[name, theme] = page.locator(".fw-section").screenshot(
                        path=str(evidence / f"opt-out-{name}-{width}-{theme.split()[-1]}.png"))
                page.close()
            assert images["baseline", "trellum light"] == images["current", "trellum light"]
            assert images["baseline", "trellum dark"] == images["current", "trellum dark"]
        finally:
            browser.close()
