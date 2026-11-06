"""DataTable population/search/sort/export in actual temporary report builds."""

from __future__ import annotations

import importlib.util
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

from trellum.components import DataSource, DataTable
from trellum.components.base import RenderContext
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    table.render_html(ctx)
    return next(iter(ctx.component_data.values()))


def test_static_population_projection_and_raw_numeric_precision():
    frame = pd.DataFrame({
        "id": ["0002", "0010", "0001"], "day": ["2026-09-01"] * 3,
        "amount": pd.Series([1234.004, 0, None], dtype="Float64"),
        "hidden": ["secret"] * 3,
    })
    cfg = _register(DataTable(frame, "Rows", max_rows=1, columns=["id", "amount", "day"]))
    assert cfg["columns"] == ["id", "amount", "day"]
    assert cfg["numericColumns"] == ["amount"]
    assert cfg["totalRows"] == len(cfg["rows"]) == len(cfg["rawRows"]) == 3
    assert cfg["rawRows"] == [["0002", 1234.004, "2026-09-01"],
                              ["0010", 0, "2026-09-01"], ["0001", None, "2026-09-01"]]
    assert cfg["rows"][0][1] == "1,234.00"


def test_numeric_metadata_uses_original_types_and_text_catalogs_need_no_duplicate_rows():
    frame = pd.DataFrame({"id": ["001", "002"], "day": pd.to_datetime(["2026-09-01"] * 2),
                          "flag": [True, False], "object_number": pd.Series([0, 1.25], dtype=object)})
    assert _register(DataTable(frame, "Types"))["numericColumns"] == ["object_number"]
    text = _register(DataTable(frame[["id", "day", "flag"]], "Catalog"))
    assert text["numericColumns"] == []
    assert "rawRows" not in text
    empty = _register(DataTable(pd.DataFrame(), "Empty snapshot", dataset_id="events",
                                columns=["ID", "Amount"]))
    assert empty["columns"] == ["ID", "Amount"]
    nullable = _register(DataTable(pd.DataFrame({
        "amount": pd.Series([0, pd.NA, 1.25], dtype=object),
    }), "Nullable object numbers"))
    assert nullable["rawRows"] == [[0], [None], [1.25]]


def _precision_frame():
    return pd.DataFrame({
        "Label": ['precision <img src=x onerror="window.bad=1">', 'precision, "quoted"',
                  "precision third", "zero", "missing", "negative"],
        "ID": ["0002", "0010", "0001", "0000", "0004", "0005"],
        "Day": ["2026-09-01"] * 6,
        "Amount": [1234.003, 1234.004, 1234.004, 0, None, -2.5],
        "Hidden": ["must not search or export"] * 6,
    })


@pytest.fixture
def report_page(tmp_path):
    from playwright.sync_api import sync_playwright

    ctx = ReportContext({"name": "DataTable examples", "studio": "test"},
                        "tables", str(tmp_path))
    module_path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_tables.py"
    spec = importlib.util.spec_from_file_location("examples_tables", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.add_sections(ctx)
    frame = _precision_frame()
    options = {"df": frame, "max_rows": 2, "sortable": True, "searchable": True,
               "columns": ["Label", "ID", "Day", "Amount"],
               "conditional_formats": {"Amount": [{"op": ">", "value": 0, "color": "var(--accent-green)"}]}}
    ctx.add_section("Precision", [
        DataTable(title="Precision static", static=True, **options),
        DataSource("showcase_precision", frame),
        DataTable(title="Precision dataset", dataset_id="showcase_precision", **options),
        DataTable(pd.DataFrame({
            "Label": ["larger integer", "larger decimal", "integer tie"],
            "Integer": [9007199254740993, 9007199254740992, 9007199254740993],
            "Decimal": [Decimal("1.00000000000000000002"),
                        Decimal("1.00000000000000000003"), Decimal("-2.5E-10")],
        }), "Exact numeric values", max_rows=2, sortable=True, static=True),
    ])
    render_report(ctx, auto_refresh=False)
    data = json.loads((tmp_path / "data.json").read_text())
    table_ids = [cid for cid, cfg in data["components"].items() if cfg["type"] == "table"]
    vendor = Path(__file__).parents[1] / "static/vendor"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def fulfill(route):
            url = urlsplit(route.request.url)
            assert url.hostname == "tables.test", route.request.url
            path = unquote(url.path).lstrip("/")
            file = vendor / path[len("_vendor/"):] if path.startswith("_vendor/") else tmp_path / path
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404, body="not found")

        page.route("**/*", fulfill)
        page.goto("http://tables.test/index.html", wait_until="networkidle")
        for cid in table_ids:
            page.locator(f"#{cid}").scroll_into_view_if_needed()
            page.locator(f"#{cid} tbody tr").first.wait_for()
        page.evaluate("""() => {
            window.csvCalls = [];
            window._fwDownloadCsv = (columns, rows, filename) => window.csvCalls.push({columns, rows, filename});
        }""")
        yield page, table_ids, data
        assert errors == []
        browser.close()


def _export(page, cid):
    page.locator(f'[data-csv-target="{cid}"]').click()
    return page.evaluate("window.csvCalls.at(-1)")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_showcase_whole_population_filter_search_sort_csv_and_rendering(report_page, width):
    page, ids, data = report_page
    page.set_viewport_size({"width": width, "height": 1000})
    static, reactive = ids[:2]
    for cid in (static, reactive):
        assert page.locator(f"#{cid} tbody tr").count() == 10
        assert len(_export(page, cid)["rows"]) == 80
        page.locator(f'[data-table-search="{cid}"]').fill("  REACTIVATION  ")
        page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'reactivation'", arg=cid)
        page.locator(f"#{cid} th").filter(has_text="Revenue").click()
        # Independently calculate matches/order from the synthetic fixture's formula.
        expected_indices = sorted(range(48, 80), key=lambda i: -(95 + i * 31 % 140 + i / 1000))
        exported = _export(page, cid)
        assert [row[0] for row in exported["rows"]] == [f"{i + 1:05d}" for i in expected_indices]
        assert [row[-1] for row in exported["rows"]] == [round(95 + i * 31 % 140 + i / 1000, 3) for i in expected_indices]
        assert page.locator(f"#{cid} .fw-table-note").inner_text() == (
            "Showing 10 of 32 matching rows (80 supplied) — CSV exports 32 rows")
        assert page.locator(f"#{cid} tbody tr").count() == 10

    page.get_by_role("button", name="Android", exact=True).click()
    page.wait_for_function("id => document.querySelector('#' + id + ' .fw-table-note').textContent.includes('16 matching')", arg=reactive)
    expected = sorted(range(49, 80, 2), key=lambda i: -(95 + i * 31 % 140 + i / 1000))
    assert [row[0] for row in _export(page, reactive)["rows"]] == [f"{i + 1:05d}" for i in expected]
    # A full renderer re-entry (e.g. refresh) also retains local state/one counter.
    page.evaluate("([id, cfg]) => window._fwRenderers.table(id, cfg)", [reactive, data["components"][reactive]])
    assert [row[0] for row in _export(page, reactive)["rows"]] == [f"{i + 1:05d}" for i in expected]
    assert page.locator(f'[data-table-search="{reactive}"] + .fw-table-count').count() == 1

    for theme in ("trellum light", "trellum dark"):
        page.evaluate("name => switchTheme(name)", theme)
        page.evaluate("document.fonts.ready")
        for cid in (static, reactive):
            styles = page.locator(f"#{cid}").evaluate("""el => {
                const ths = el.querySelectorAll('th'), cells = el.querySelectorAll('tbody tr:first-child td');
                return {text: getComputedStyle(cells[0]).textAlign,
                    date: getComputedStyle(cells[1]).textAlign,
                    number: getComputedStyle(cells[7]).textAlign,
                    heading: getComputedStyle(ths[7]).textAlign,
                    figures: getComputedStyle(cells[7]).fontVariantNumeric,
                    padding: getComputedStyle(cells[7]).paddingTop,
                    rows: [1, 2].map(n => getComputedStyle(el.querySelector('tbody tr:nth-child('+n+') td')).backgroundColor)};
            }""")
            assert styles["text"] == styles["date"]
            assert styles["text"] in {"left", "start"}
            assert styles["number"] == styles["heading"] == "right"
            assert styles["figures"] == "tabular-nums"
            assert styles["padding"] == "6px"
            assert styles["rows"][0] != styles["rows"][1]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shots = os.environ.get("TRELLUM_SCREENSHOT_DIR")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.evaluate("window.scrollTo(0, 0)")
            if width == 390:
                page.screenshot(path=str(Path(shots) / f"data-table-{width}-{theme.replace(' ', '-')}-numbers.png"), full_page=True)
            page.evaluate("document.querySelectorAll('.fw-table-scroll').forEach(el => el.scrollLeft = 0)")
            page.screenshot(path=str(Path(shots) / f"data-table-{width}-{theme.replace(' ', '-')}.png"), full_page=True)


@pytest.mark.parametrize("mode", ["static", "dataset"])
def test_raw_precision_stable_csv_subset_null_zero_and_escaping(report_page, mode):
    page, ids, _ = report_page
    cid = ids[2 if mode == "static" else 3]
    exported = _export(page, cid)
    assert exported["columns"] == ["Label", "ID", "Day", "Amount"]
    assert [row[-1] for row in exported["rows"]] == [1234.003, 1234.004, 1234.004, 0, "", -2.5]
    assert exported["filename"] == "tables_table.csv"
    assert page.locator(f"#{cid} img").count() == 0
    assert page.evaluate("window.bad || 0") == 0
    assert '<img src=x onerror="window.bad=1">' in page.locator(f"#{cid} tbody tr").first.inner_text()
    assert page.locator(f"#{cid} tbody tr td").nth(3).evaluate("el => el.style.color") == "var(--accent-green)"
    search = page.locator(f'[data-table-search="{cid}"]')
    search.fill("precision")
    page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'precision'", arg=cid)
    page.locator(f"#{cid} th").filter(has_text="Amount").click()
    assert [row[1] for row in _export(page, cid)["rows"]] == ["0010", "0001", "0002"]
    assert page.locator(f"#{cid} tbody tr").count() == 2
    # Text IDs keep lexicographic ordering and left alignment.
    page.locator(f"#{cid} th").filter(has_text="ID").click()
    assert [row[1] for row in _export(page, cid)["rows"]] == ["0010", "0002", "0001"]
    search.fill("must not search")
    page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'must not search'", arg=cid)
    assert _export(page, cid)["rows"] == []


def test_export_retains_last_delivered_live_population(report_page):
    page, ids, _ = report_page
    cid = ids[3]
    # Emulate the live wrapper's delivered result and a later engine result
    # that has not yet been drawn. Export must not ask the engine for it.
    page.evaluate("""id => {
        const cfg = window._reportData.components[id];
        window._fwLiveWrap = (id, cfg, draw) => {
            window.deliverTableResult = draw;
            draw([{Label:'retained', ID:'0001', Day:'2026-09-01', Amount:7.1234}]);
        };
        window._fwRenderers.table(id, cfg);
        window._fwFilterEngine.getFiltered = () => { throw new Error('Export must use the delivered population'); };
    }""", cid)
    assert _export(page, cid)["rows"] == [["retained", "0001", "2026-09-01", 7.1234]]
    page.evaluate("window.deliverTableResult([{Label:'new result', ID:'0002', Day:'2026-09-02', Amount:0}])")
    assert _export(page, cid)["rows"] == [["new result", "0002", "2026-09-02", 0]]


def test_live_result_delivery_preserves_search_sort_and_matching_export(report_page):
    page, ids, _ = report_page
    cid = ids[3]
    page.evaluate("""id => {
        const cfg = {...window._reportData.components[id], numericColumns:[]};
        window._fwRenderers.table(id, cfg); // A snapshot with unknown dtypes.
    }""", cid)
    page.locator(f'[data-table-search="{cid}"]').fill("precision")
    page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'precision'", arg=cid)
    page.locator(f"#{cid} th").filter(has_text="Amount").click()
    page.evaluate("""() => {
        const engine = window._fwFilterEngine;
        engine.registerLiveDataset('showcase_precision', {onChange: () => {}});
        engine.setLiveRows('showcase_precision', ['Label', 'ID', 'Day', 'Amount'], [
            ['precision A', '0001', '2026-09-02', 0],
            ['other', '0002', '2026-09-02', 100],
            ['precision B', '0003', '2026-09-02', 99.125],
            ['precision C', '0004', '2026-09-02', 99.126],
            ['precision D', '0005', '2026-09-02', 99.126]
        ]);
    }""")
    assert [row[1] for row in _export(page, cid)["rows"]] == ["0004", "0005", "0003", "0001"]
    assert page.locator(f"#{cid} tbody tr").count() == 2
    assert page.locator(f"#{cid} .fw-table-note").inner_text() == (
        "Showing 2 of 4 matching rows (5 supplied) — CSV exports 4 rows")


def test_static_large_integer_and_decimal_sort_and_csv_are_exact(report_page):
    page, ids, _ = report_page
    cid = ids[4]
    page.locator(f"#{cid} th").filter(has_text="Integer").click()
    exported = _export(page, cid)["rows"]
    assert [row[0] for row in exported] == ["larger integer", "integer tie", "larger decimal"]
    assert [row[1] for row in exported] == ["9007199254740993", "9007199254740993", "9007199254740992"]
    page.locator(f"#{cid} th").filter(has_text="Decimal").click()
    exported = _export(page, cid)["rows"]
    assert [row[0] for row in exported] == ["larger decimal", "larger integer", "integer tie"]
    assert [row[2] for row in exported] == ["1.00000000000000000003", "1.00000000000000000002", "-2.5E-10"]


def test_workspace_page_does_not_limit_matching_csv(report_page):
    page, ids, _ = report_page
    cid = ids[2]
    page.evaluate("""id => {
        window._fwMetricsWorkspace = {definitionId:id};
        window._fwMetricsCatalog = Array.from({length:80}, (_, i) => ({
            monitored:false, name:'metric_' + String(i).padStart(4,'0'), anchor:'definition-' + i
        }));
        history.replaceState(null, '', '#definition-60');
        window._fwRenderers.table(id, {columns:['Metric','Owner'],
            rows:window._fwMetricsCatalog.map((r, i) => [r.name, i >= 40 ? 'late' : 'early']),
            maxRows:25, workspacePageSize:25, totalRows:80, sortable:true, searchable:true});
    }""", cid)
    assert page.locator(f"#{cid} tbody tr").first.inner_text() == "metric_0050\tlate"
    assert page.locator("#definition-60").count() == 1
    page.locator(f'[data-table-search="{cid}"]').fill("late")
    page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'late'", arg=cid)
    page.locator(f"#{cid} th").first.click()
    page.locator(f"#{cid} [data-workspace-table-next]").click()
    assert page.locator(f"#{cid} tbody tr").count() == 15
    assert [row[0] for row in _export(page, cid)["rows"]] == [f"metric_{i:04d}" for i in range(79, 39, -1)]
    assert "Page 2 of 2 · CSV exports 40 rows" in page.locator(f"#{cid} .fw-table-note").inner_text()


def test_legacy_payload_still_exports_all_available_sorted_matches(report_page):
    page, ids, _ = report_page
    cid = ids[2]
    page.evaluate("""id => window._fwRenderers.table(id, {
        columns:['Item','Value'], rows:[['match one','1.2K'], ['other','0'], ['match two','1.3K']],
        totalRows:4, maxRows:1, searchable:true, sortable:true
    })""", cid)
    page.locator(f'[data-table-search="{cid}"]').fill("match")
    page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'match'", arg=cid)
    page.locator(f"#{cid} th").filter(has_text="Value").click()
    assert _export(page, cid)["rows"] == [["match two", "1.3K"], ["match one", "1.2K"]]
    assert "3 available of 4 supplied" in page.locator(f"#{cid} .fw-table-note").inner_text()
