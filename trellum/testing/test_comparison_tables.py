"""Comparison presentation uses the existing table population and export path."""

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

from trellum.components import ComparisonTable, DataSource
from trellum.components.base import RenderContext
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME, THEME_REGISTRY
from trellum.validation import validate_report


def _examples():
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_comparisons.py"
    spec = importlib.util.spec_from_file_location("examples_comparisons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    table.render_html(ctx)
    return next(iter(ctx.component_data.values()))


def test_legacy_positions_subclasses_and_absolute_values_remain_supported():
    @dataclass
    class NamedComparison(ComparisonTable):
        caption: str = "caption"

    frame = pd.DataFrame({"Metric": ["Revenue"], "Budget": [1234.56789]})
    table = NamedComparison(frame, "Budget", "historically unused", ["Budget"],
                            None, True, {}, ["Budget"], "positional subclass")
    cfg = _register(table)
    assert table.caption == "positional subclass"
    assert cfg["columns"] == ["Budget"]
    assert cfg["rawRows"] == [[1234.56789]]
    assert "conditionalFormats" not in cfg
    assert cfg["comparison"]["context"] is None
    legacy = _register(ComparisonTable(frame, "Budget", "absent", ["Budget"]))
    assert legacy["conditionalFormats"]["Budget"][0]["op"] == ">"
    assert legacy["maxRows"] == 200 and legacy["sortable"] is False


def test_default_projection_hides_only_reason_metadata_and_retains_population():
    frame, context = _examples().comparison_fixture()
    frame = pd.concat([frame] * 30, ignore_index=True)
    cfg = _register(ComparisonTable(frame, "All", "Current", ["Change"], comparison_context=context))
    assert cfg["columns"] == ["Metric", "Previous", "Current", "Change", "Focus"]
    assert cfg["totalRows"] == len(cfg["rawRows"]) == len(cfg["comparisonRows"]) == 240
    assert cfg["comparisonRows"][4]["Change reason"] == "Zero baseline"
    assert cfg["rawRows"][4][2] == 480
    assert cfg["rawRows"][4][3] is None
    for dataset in (None, "comparisons"):
        cfg = _register(ComparisonTable(frame, "Selected", "Current", ["Change"],
                         dataset_id=dataset, columns=["Change reason", "Change"],
                         comparison_context=context, show_divider=False))
        assert cfg["columns"] == ["Change reason", "Change"]
        assert cfg["comparison"]["showDivider"] is False
    assert "conditionalFormats" not in cfg


def test_demo_sections_validate_without_warnings(tmp_path):
    ctx = ReportContext({"name": "Comparisons", "studio": "test", "category": "Components",
                         "description": "Synthetic weekly revenue, cost, refund and retention comparisons "
                                        "demonstrate supplied periods, units, metric directions and missing reasons."},
                        "comparisons", str(tmp_path))
    _examples().add_sections(ctx)
    result = validate_report(ctx)
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()


@pytest.mark.parametrize("bad", [
    [], {}, {"metric_column": "missing"}, {"metric_column": "Metric", "metrics": []},
    {"metric_column": "Metric", "period_labels": {"Current": 3}},
    {"metric_column": "Metric", "change_labels": {"missing": "Change"}},
    {"metric_column": "Metric", "reason_columns": {"Current": "absent"}},
    {"metric_column": "Metric", "metrics": {"Revenue": None}},
    *[{"metric_column": "Metric", "metrics": {"Revenue": {"precision": n}}}
      for n in (-1, 13, True, 1.5)],
    {"metric_column": "Metric", "metrics": {"Revenue": {"format": "magic"}}},
    {"metric_column": "Metric", "metrics": {"Revenue": {"direction": True}}},
])
def test_malformed_new_context_fails_clearly(bad):
    frame, _ = _examples().comparison_fixture()
    with pytest.raises(ValueError, match="ComparisonTable comparison_context"):
        _register(ComparisonTable(frame, "Bad", "Current", ["Change"], comparison_context=bad))


def _edge_fixture():
    keys = ["Duplicate", "Duplicate", "Unknown", "__proto__", "Neutral", "Value", "Absolute",
            "Text", "Unknown measure", "Missing", "NaN", "Infinity", "Minus zero"]
    frame = pd.DataFrame({
        "Metric": keys, "Current": pd.Series([1.23456789] * len(keys), dtype=object),
        "Change": pd.Series([None, None, -.125, .25, -.25, 17.23456, -1.625,
                             "+50%", .123456789, None, float("nan"), float("inf"), -0.0], dtype=object),
        "Reason": ["first <img src=x>", "second & final"] + [""] * (len(keys) - 2),
    })
    context = {"metric_column": "Metric", "period_labels": {"Current": "Now <img src=x>"},
               "reason_columns": {"Change": "Reason"}, "metrics": {
        key: {"direction": "higher", "format": "number", "precision": 3,
              "change_measure": "relative_percent", "change_precision": 2}
        for key in keys if key not in {"Unknown", "__proto__"}
    }}
    context["metrics"]["Neutral"]["direction"] = "neutral"
    context["metrics"]["Value"]["change_measure"] = "value"
    context["metrics"]["Absolute"].update(change_measure="absolute", format="currency")
    context["metrics"]["Unknown measure"]["change_measure"] = "custom"
    return frame, context


@pytest.fixture
def comparison_page(tmp_path):
    from playwright.sync_api import sync_playwright

    ctx = ReportContext({"name": "Comparison examples", "studio": "test"}, "comparisons", str(tmp_path))
    _examples().add_sections(ctx)
    edge, context = _edge_fixture()
    ctx.add_section("Comparison edge cases", [
        ComparisonTable(edge, "Edge static", "Current", ["Change"], static=True,
                        columns=["Current", "Change"], comparison_context=context),
        DataSource("edge_comparisons", edge),
        ComparisonTable(edge, "Edge dataset", "Current", ["Change"], dataset_id="edge_comparisons",
                        columns=["Current", "Change"], comparison_context=context),
        ComparisonTable(edge, "No automatic colors", "Current", ["Change"], static=True,
                        comparison_context=context, conditional_formats={}, show_divider=False),
    ])
    render_report(ctx, auto_refresh=False)
    data = json.loads((tmp_path / "data.json").read_text())
    ids = [cid for cid, cfg in data["components"].items() if cfg["type"] == "table"]
    vendor = Path(__file__).parents[1] / "static/vendor"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def fulfill(route):
            url = urlsplit(route.request.url)
            assert url.hostname == "comparisons.test", route.request.url
            path = unquote(url.path).lstrip("/")
            file = vendor / path[len("_vendor/"):] if path.startswith("_vendor/") else tmp_path / path
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404, body="missing")

        page.route("**/*", fulfill)
        page.goto("http://comparisons.test/index.html", wait_until="networkidle")
        for cid in ids:
            page.locator(f"#{cid}").scroll_into_view_if_needed()
            page.locator(f"#{cid} tbody tr").first.wait_for()
        page.evaluate("window.csvCalls = []; window._fwDownloadCsv = (columns, rows) => csvCalls.push({columns, rows})")
        yield page, ids, data
        assert errors == []
        browser.close()


def _export(page, cid):
    page.locator(f'[data-csv-target="{cid}"]').click()
    return page.evaluate("csvCalls.at(-1)")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_real_comparisons_filters_csv_themes_and_local_scroll(comparison_page, width):
    page, ids, data = comparison_page
    page.set_viewport_size({"width": width, "height": 1000})
    static, reactive, explicit, legacy = ids[:4]
    frame, _ = _examples().comparison_fixture()
    for cid in (static, reactive):
        rows = page.locator(f"#{cid} tbody tr")
        assert rows.count() == 8
        assert rows.nth(0).locator("td").nth(3).inner_text() == "+10.8%\nImproved"
        assert rows.nth(1).locator("td").nth(3).inner_text() == "-6.4%\nImproved"
        assert rows.nth(2).locator("td").nth(3).inner_text() == "+3.0 pp\nWorsened"
        assert rows.nth(3).locator("td").nth(3).inner_text() == "0.0 pp\nUnchanged"
        assert rows.nth(4).locator("td").nth(2).inner_text() == "$480"
        assert rows.nth(4).locator("td").nth(3).inner_text() == "—\nZero baseline"
        assert rows.nth(5).locator("td").nth(2).inner_text() == "—\nNot mature"
        assert rows.nth(5).locator("td").nth(3).inner_text() == "—\nAwaiting day 7"
        assert rows.nth(6).locator("td").nth(3).inner_text() == "—\nNo current data"
        assert rows.nth(7).locator("td").nth(3).inner_text() == "+11.6%"
        assert rows.nth(1).locator("td").nth(3).locator("span").first.evaluate("e => e.style.color") == "var(--text-positive, var(--accent-green))"
        assert rows.nth(2).locator("td").nth(3).locator("span").first.evaluate("e => e.style.color") == "var(--text-negative, var(--accent-red))"
        for n in (3, 4, 5, 6, 7):
            assert rows.nth(n).locator("td").nth(3).locator("span").first.evaluate("e => e.style.color") == ""
        exported = _export(page, cid)
        assert exported["columns"] == ["Metric", "Previous", "Current", "Change"]
        assert exported["rows"][0][-1] == .108
        assert exported["rows"][1][2:] == [frame.iloc[1]["Current"], frame.iloc[1]["Change"]]
        assert exported["rows"][2][-1] == pytest.approx(3)
        assert exported["rows"][4][2:] == [480, ""]
    assert _export(page, legacy)["rows"][0][-1] == 20000
    assert page.locator(f"#{explicit} tbody tr").nth(0).locator("td").nth(3).locator("span").first.evaluate("e => e.style.color") == ""
    assert page.locator(f"#{explicit} tbody tr").nth(1).locator("td").nth(3).locator("span").first.evaluate("e => e.style.color") == "var(--accent-red)"
    assert page.locator(f"#{explicit} tbody tr").nth(1).locator("td").nth(3).inner_text().endswith("Improved")

    for theme in THEME_REGISTRY:
        page.evaluate("name => switchTheme(name)", theme)
        for cid in (static, reactive):
            styles = page.locator(f"#{cid}").evaluate("""el => {
                const th=el.querySelector('th:last-child'), td=el.querySelector('td:last-child');
                return {border:getComputedStyle(td).borderLeftColor,
                    token:getComputedStyle(el).getPropertyValue('--border-color').trim(),
                    align:getComputedStyle(td).textAlign, header:getComputedStyle(th).textAlign,
                    stripe:getComputedStyle(el.querySelector('tr:nth-child(2) td')).backgroundColor,
                    first:getComputedStyle(el.querySelector('td')).backgroundColor};
            }""")
            assert styles["align"] == styles["header"] == "right"
            assert styles["stripe"] != styles["first"]
            # Resolve the token in-browser rather than assume a theme color syntax.
            assert page.locator(f"#{cid} td:last-child").first.evaluate("""e => {
                const probe=document.createElement('span'); probe.style.color='var(--border-color)';
                e.append(probe); const color=getComputedStyle(probe).color; probe.remove();
                return getComputedStyle(e).borderLeftColor === color;
            }""")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if theme in ("trellum light", "trellum dark"):
            directory = os.environ.get("TRELLUM_SCREENSHOT_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                page.evaluate("document.querySelectorAll('.fw-table-scroll').forEach(e => e.scrollLeft=0); scrollTo(0,0)")
                page.screenshot(path=str(Path(directory) / f"comparison-{width}-{theme.replace(' ', '-')}.png"), full_page=True)
                page.locator(f"#{static}").screenshot(path=str(Path(directory) / f"comparison-table-{width}-{theme.replace(' ', '-')}.png"))
        if width == 390:
            assert page.locator(f"#{static}").evaluate("e => e.scrollWidth > e.clientWidth")
            for cid in (static, reactive):
                assert page.locator(f"#{cid} colgroup").count() == 0
                page.locator(f"#{cid}").evaluate("e => e.scrollLeft=e.scrollWidth")
                assert page.locator(f"#{cid}").evaluate("e => e.scrollLeft > 0")
            if theme in ("trellum light", "trellum dark") and directory:
                page.locator(f"#{static}").screenshot(path=str(Path(directory) / f"comparison-table-{width}-{theme.replace(' ', '-')}-outcomes.png"))

    page.get_by_role("button", name="Availability", exact=True).click()
    page.wait_for_function("id => document.querySelectorAll('#'+id+' tbody tr').length === 3", arg=reactive)
    assert [row[0] for row in _export(page, reactive)["rows"]] == frame.iloc[4:7]["Metric"].tolist()
    for index, reason in enumerate(("Zero baseline", "Awaiting day 7", "No current data")):
        assert page.locator(f"#{reactive} tbody tr").nth(index).locator("td").last.inner_text() == "—\n" + reason
    page.evaluate("([id, cfg]) => window._fwRenderers.table(id, cfg)", [reactive, data["components"][reactive]])
    assert "Zero baseline" in page.locator(f"#{reactive} tbody tr").first.inner_text()
    # Empty and restored results use the same native filter engine.
    page.evaluate("window._fwFilterEngine.setFilter('showcase_comparisons','probe','Focus','equals','absent')")
    page.wait_for_function("id => !document.querySelector('#'+id+' tbody tr')", arg=reactive)
    assert _export(page, reactive)["rows"] == []
    page.evaluate("window._fwFilterEngine.setFilter('showcase_comparisons','probe','Focus','equals','Availability')")
    page.wait_for_function("id => document.querySelectorAll('#'+id+' tbody tr').length === 3", arg=reactive)


def test_raw_edge_cases_metadata_projection_and_redraw(comparison_page):
    page, ids, data = comparison_page
    for cid in ids[4:6]:
        rows = page.locator(f"#{cid} tbody tr")
        assert rows.nth(0).locator("td").last.inner_text() == "—\nfirst <img src=x>"
        assert rows.nth(1).locator("td").last.inner_text() == "—\nsecond & final"
        assert rows.nth(2).locator("td").last.inner_text() == "-0.125"
        assert rows.nth(3).locator("td").last.inner_text() == "0.25"
        assert rows.nth(4).locator("td").last.inner_text() == "-25.00%"
        assert rows.nth(5).locator("td").last.inner_text() == "17.23"
        assert rows.nth(6).locator("td").last.inner_text() == "-$1.63\nWorsened"
        assert rows.nth(7).locator("td").last.inner_text() == "+50%"
        assert rows.nth(8).locator("td").last.inner_text() == "0.123456789"
        for index in (9, 10, 11):
            assert rows.nth(index).locator("td").last.inner_text() == "—\nUnavailable"
        assert rows.nth(12).locator("td").last.inner_text() == "0.00%\nUnchanged"
        assert page.locator(f"#{cid} th").first.inner_text() == "Now <img src=x>"
        assert page.locator(f"#{cid} img").count() == 0
        exported = _export(page, cid)
        assert exported["columns"] == ["Current", "Change"]
        assert exported["rows"][0] == [1.23456789, ""]
        assert exported["rows"][5][1] == 17.23456
        assert exported["rows"][7][1] == "+50%"
    reactive = ids[5]
    page.evaluate("window._fwFilterEngine.setFilter('edge_comparisons','probe','Reason','equals','second & final')")
    page.wait_for_function("id => document.querySelectorAll('#'+id+' tbody tr').length === 1", arg=reactive)
    assert page.locator(f"#{reactive} tbody").inner_text().endswith("second & final")
    page.evaluate("([id, cfg]) => window._fwRenderers.table(id, cfg)", [reactive, data["components"][reactive]])
    assert page.locator(f"#{reactive} tbody").inner_text().endswith("second & final")
    # Simulate nonfinite values delivered by a future live result, independently
    # of JSON's conversion to null in the initial static/dataset snapshot.
    page.evaluate("""([id, cfg]) => {
        window._fwLiveWrap = (id, cfg, draw) => draw([
            {Metric:'NaN',Current:0,Change:NaN,Reason:'raw NaN'},
            {Metric:'Infinity',Current:8,Change:Infinity,Reason:'raw infinity'}]);
        window._fwRenderers.table(id, cfg);
    }""", [reactive, data["components"][reactive]])
    assert page.locator(f"#{reactive} tbody tr").first.inner_text() == "0.000\t—\nraw NaN"
    assert page.locator(f"#{reactive} tbody tr").last.inner_text() == "8.000\t—\nraw infinity"
    neutral = ids[6]
    assert page.locator(f"#{neutral} .fw-comparison-divider").count() == 0
    assert page.locator(f"#{neutral} [style*='color:']").count() == 0
