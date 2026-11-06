"""Narrow keyboard fixes checked in generated reports with vendored assets."""

from __future__ import annotations

import base64
import csv
import importlib.util
import json
import mimetypes
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

from trellum.components import DataTable
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "d6a8382d316987f6becb27e71426634281e42928"
ASSETS = ("js/components/data_table.js", "css/components/data_table.css",
          "css/components/chart_base.css", "css/components/toggle.css")


def _build(out):
    ctx = ReportContext({
        "name": "Report keyboard examples", "studio": "test", "category": "Components",
        "description": "Deterministic daily observations for table sorting, native toggle content and chart CSV keyboard focus.",
    }, "report-keyboard", str(out))
    source = ROOT / "trellum/demo/reports/component-showcase/examples_keyboard.py"
    spec = importlib.util.spec_from_file_location("examples_keyboard", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.add_sections(ctx)
    ctx.add_section("Unsortable regression", [DataTable(
        pd.DataFrame({"Label": ["Unsortable"], "Amount": [0]}), "Unsortable snapshot",
        static=True, sortable=False,
    )])
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    (out / "diagnostics.json").parent.mkdir(parents=True, exist_ok=True)
    (out / "diagnostics.json").write_text(json.dumps(result.to_dict(), indent=2))
    (out / "details.json").write_text(json.dumps(details, indent=2, default=str))
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()
    render_report(ctx, auto_refresh=False)
    copy_vendor_tree(out)
    # Compare against the actual baseline assets inside the same native report.
    # No replacement renderer or report-local JavaScript is involved.
    html = (out / "index.html").read_text()
    before = out / "before"
    before.mkdir()
    for asset in ASSETS:
        current = (ROOT / "trellum/static" / asset).read_text()
        original = subprocess.run(
            ["git", "show", f"{BASELINE}:trellum/static/{asset}"],
            cwd=ROOT, text=True, capture_output=True, check=True,
        ).stdout
        assert current in html, asset
        html = html.replace(current, original)
    (before / "index.html").write_text(html)
    data = json.loads((out / "data.json").read_text())
    return data


def _download_rows(page, button):
    with page.expect_download() as downloaded:
        button.click()
    with open(downloaded.value.path(), encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_keyboard_sort_focus_and_pointer_parity(tmp_path, width, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    monkeypatch.setenv("FW_NOW", "2026-09-10T12:00:00+00:00")
    data = _build(tmp_path)
    table_id = next(cid for cid, cfg in data["components"].items()
                    if cfg["type"] == "table" and cfg.get("sortable"))
    chart_id = next(cid for cid, cfg in data["components"].items() if cfg["type"] == "chart")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.add_init_script("localStorage.removeItem('fw-theme')")
        errors, missing = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            url = urlsplit(route.request.url)
            assert url.hostname == "keyboard.test", route.request.url
            path = unquote(url.path).lstrip("/")
            file = tmp_path / path
            if not file.is_file() and path.startswith("before/"):
                file = tmp_path / path.removeprefix("before/")
            if file.is_file():
                route.fulfill(path=str(file), content_type=mimetypes.guess_type(file)[0]
                              or "application/octet-stream")
            else:
                missing.append(path)
                route.fulfill(status=404, body="Missing local asset")

        page.route("**/*", serve)

        def open_report(before=False, theme="trellum light"):
            page.goto("http://keyboard.test/" + ("before/" if before else "") + "index.html",
                      wait_until="networkidle")
            page.locator("#fwThemeSelect").select_option(theme)
            section = page.locator(".fw-section").first
            section.locator("canvas").scroll_into_view_if_needed()
            page.wait_for_function("id => !!window._chartInstances[id]", arg=chart_id)
            page.evaluate("id=>{const chart=_chartInstances[id];chart.stop();chart.update('none')}", chart_id)
            page.wait_for_function("document.getAnimations().filter(a=>Number.isFinite(a.effect.getComputedTiming().iterations)).every(a=>a.playState!=='running')")
            page.mouse.move(0, 0)
            return section

        # Actual baseline/default and pointer-sort pixels, both themes and viewports.
        for theme in ("trellum light", "trellum dark"):
            shots, shapes = {}, {}
            for before in (True, False):
                section = open_report(before, theme)
                for state in ("default", "pointer"):
                    if state == "pointer":
                        page.locator(f"#{table_id} th").nth(1).click()
                        page.mouse.move(0, 0)
                    key = (before, state)
                    shots[key] = section.screenshot(
                        path=str(evidence / f"{width}-{theme.replace(' ', '-')}-{'before' if before else 'after'}-{state}.png"),
                        animations="disabled")
                    shapes[key] = section.locator("table, th, td, canvas, .fw-toggle-group, .fw-toggle-btn, .fw-chart-dl").evaluate_all(
                        "els=>els.map(e=>{const r=e.getBoundingClientRect();return [r.width,r.height]})")
            for state in ("default", "pointer"):
                assert shots[True, state] == shots[False, state], (width, theme, state)
                assert shapes[True, state] == shapes[False, state]

        section = open_report()
        table = page.locator(f"#{table_id}")
        headers = table.get_by_role("columnheader")
        expect(headers).to_have_count(2)
        expect(headers.nth(0)).to_have_attribute("aria-sort", "none")
        expect(headers.nth(1)).to_have_attribute("tabindex", "0")
        csv_button = page.locator(f'[data-csv-target="{table_id}"]')
        csv_button.focus()
        page.keyboard.press("Tab")
        expect(headers.nth(0)).to_be_focused()
        page.keyboard.press("Tab")
        expect(headers.nth(1)).to_be_focused()
        scroll = page.evaluate("scrollY")
        page.keyboard.press("Space")
        expect(headers.nth(1)).to_be_focused()
        expect(headers.nth(1)).to_have_attribute("aria-sort", "descending")
        assert page.evaluate("scrollY") == scroll
        descending = ["Largest amount", "First tie", "Second tie", "Small decimal", "Measured zero", "Negative amount"]
        assert [row["Label"] for row in _download_rows(page, csv_button)] == descending
        assert table.locator("tbody tr").count() == 4
        headers.nth(1).focus()
        page.keyboard.press("Enter")
        expect(headers.nth(1)).to_be_focused()
        expect(headers.nth(1)).to_have_attribute("aria-sort", "ascending")
        ascending = ["Negative amount", "Measured zero", "Small decimal", "First tie", "Second tie", "Largest amount"]
        assert [row["Label"] for row in _download_rows(page, csv_button)] == ascending
        # Independent pointer path has the same full-population orders and stable ties.
        open_report()
        headers.nth(1).click()
        assert [row["Label"] for row in _download_rows(page, csv_button)] == descending
        headers.nth(1).click()
        assert [row["Label"] for row in _download_rows(page, csv_button)] == ascending

        search = page.locator(f'[data-table-search="{table_id}"]')
        toggle = section.locator('[data-toggle-id="showcase_keyboard_explanation"]')
        search.fill("tie")
        toggle.get_by_role("button", name="Details", exact=True).focus()
        expect(table.locator("tbody tr")).to_have_count(2)
        expect(toggle.get_by_role("button", name="Details", exact=True)).to_be_focused()
        assert [row["Label"] for row in _download_rows(page, csv_button)] == ["First tie", "Second tie"]
        search.fill("")
        expect(table.locator("tbody tr")).to_have_count(4)
        section.get_by_role("button", name="iOS", exact=True).click()
        expect(table.locator("tbody tr")).to_have_count(3)
        expect(headers.nth(1)).to_have_attribute("aria-sort", "ascending")
        headers.nth(1).focus()
        page.keyboard.press("Enter")
        expect(headers.nth(1)).to_have_attribute("aria-sort", "descending")
        assert [row["Label"] for row in _download_rows(page, csv_button)] == ["Largest amount", "Second tie", "Measured zero"]

        unsorted = page.locator(".fw-section").nth(1).get_by_role("columnheader")
        assert unsorted.evaluate_all("els=>els.every(e=>!e.hasAttribute('tabindex')&&!e.hasAttribute('aria-sort')&&!e.classList.contains('fw-sortable'))")
        # Re-entry and theme redraw must not accumulate sort handlers.
        for _ in range(2):
            page.evaluate("id=>_fwRenderers.table(id,_reportData.components[id])", table_id)
            headers.nth(1).focus()
            page.keyboard.press("Space")
        expect(headers.nth(1)).to_have_attribute("aria-sort", "descending")

        for theme in ("trellum light", "trellum dark"):
            page.locator("#fwThemeSelect").select_option(theme)
            headers.nth(1).focus()
            page.keyboard.press("Enter")
            expected_direction = "ascending" if theme.endswith("light") else "descending"
            expect(headers.nth(1)).to_have_attribute("aria-sort", expected_direction)
            expect(headers.nth(1)).to_be_focused()
            section.screenshot(path=str(evidence / f"{width}-{theme.replace(' ', '-')}-header-focus.png"), animations="disabled")
            assert headers.nth(1).evaluate("e=>getComputedStyle(e).outlineOffset") == "-2px"

            details = toggle.get_by_role("button", name="Details", exact=True)
            details.focus()
            page.keyboard.press("Space")
            expect(section.get_by_text("Details: equal amounts", exact=False)).to_be_visible()
            page.wait_for_function("e=>getComputedStyle(e).outlineOffset==='-2px'", arg=details.element_handle())
            assert details.evaluate("e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect(),p=e.parentElement.getBoundingClientRect();return s.outlineStyle==='solid'&&parseFloat(s.outlineWidth)===2&&r.left>=p.left&&r.right<=p.right&&r.top>=p.top&&r.bottom<=p.bottom}")
            section.screenshot(path=str(evidence / f"{width}-{theme.replace(' ', '-')}-toggle-focus.png"), animations="disabled")
            toggle.get_by_role("button", name="Summary", exact=True).focus()
            page.keyboard.press("Enter")
            expect(section.get_by_text("Summary: daily amounts", exact=False)).to_be_visible()

            chart_csv = section.locator(".fw-chart-dl")
            page.mouse.move(0, 0)
            # Use actual Tab navigation to focus the otherwise transparent control.
            toggle.get_by_role("button", name="Details", exact=True).focus()
            page.keyboard.press("Tab")
            expect(chart_csv).to_be_focused()
            expect(chart_csv).to_have_css("opacity", "1")
            section.screenshot(path=str(evidence / f"{width}-{theme.replace(' ', '-')}-csv-focus.png"), animations="disabled")
            assert len(_download_rows(page, chart_csv)) == 3

        # Real native capture, using the existing capture policy and vendored html2canvas.
        # Focus styling and transparent CSV chrome must not become export content.
        page.mouse.move(0, 0)
        page.locator("#fwThemeSelect").focus()
        plain = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
        headers.nth(1).focus()
        focused = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
        assert plain["format"] == focused["format"] == "trellum-analysis-capture"
        assert plain["image"]["data_base64"] == focused["image"]["data_base64"]
        (evidence / f"{width}-native-capture.png").write_bytes(base64.b64decode(focused["image"]["data_base64"]))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert section.evaluate("e=>e.scrollWidth<=e.clientWidth")
        # Supplemental workspace regression uses the existing renderer, rather
        # than representing this temporary DOM as a generated workspace report.
        workspace = page.evaluate("""() => {
            const previous = window._fwMetricsWorkspace;
            const node = document.createElement('div'); node.id = 'keyboard-workspace';
            document.body.appendChild(node);
            window._fwMetricsWorkspace = {};
            try {
                _fwRenderers.table(node.id, {columns:['Metric','Value'],
                    rows:Array.from({length:60}, (_,i)=>['metric_'+String(i).padStart(3,'0'),i]),
                    totalRows:60,maxRows:2,sortable:true,workspacePageSize:25});
                function key(value) {
                    const th = node.querySelector('th'); th.focus();
                    th.dispatchEvent(new KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true}));
                    return {sort:node.querySelector('th').getAttribute('aria-sort'),
                        focus:document.activeElement===node.querySelector('th'),
                            outline:getComputedStyle(node.querySelector('th')).outlineOffset,
                        first:node.querySelector('tbody td').textContent};
                }
                const enter=key('Enter'), space=key(' ');
                node.querySelector('[data-workspace-table-next]').click();
                const next={rows:node.querySelectorAll('tbody tr').length,
                    first:node.querySelector('tbody td').textContent,
                    focus:document.activeElement.hasAttribute('data-workspace-table-next')};
                return {enter,space,next};
            } finally { node.remove(); window._fwMetricsWorkspace=previous; }
        }""")
        assert workspace == {
            "enter": {"sort": "descending", "focus": True, "outline": "2px", "first": "metric_059"},
            "space": {"sort": "ascending", "focus": True, "outline": "2px", "first": "metric_000"},
            "next": {"rows": 25, "first": "metric_025", "focus": True},
        }
        assert missing == []
        assert errors == []
        browser.close()
