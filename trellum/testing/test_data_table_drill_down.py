"""Row actions reuse dropdown ownership without changing table populations."""

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
from urllib.parse import unquote, urlencode, urlsplit

import pandas as pd
import pytest

from trellum.components import ComparisonTable, DataSource, DataTable, FilterBar, LiveDataSource, ScopedDataSource
from trellum.components.base import RenderContext
from trellum.components.filters.dropdown import DropdownFilter
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report
from trellum.validation.checks.table_drill_down import check_table_drill_down
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "6b531ff681d7e7d182b76fc46a821d58bf952c43"


def examples():
    spec = importlib.util.spec_from_file_location("examples_table_drill_down", ROOT / "trellum/demo/reports/component-showcase/examples_table_drill_down.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context(path):
    return ReportContext({"name": "Table drill-down", "studio": "test", "category": "Components",
                          "description": "Daily synthetic campaign/platform observations demonstrate optional source and campaign row actions, filter ownership, exact population totals and selected daily installs."},
                         "table-drill-down", str(path))


def register(table):
    ctx = RenderContext(DEFAULT_THEME)
    return table.render_html(ctx), ctx.component_data


def frame():
    return pd.DataFrame({"Label": ["Same", "Same", "No key"], "key": ["a", "b", None],
                         "Value": [Decimal("1.0000000000000001"), Decimal("2.5"), Decimal("0")]})


def action(**changes):
    return {"dataset_id": "target", "column": "key", "value_column": "key", "label_column": "Label", **changes}


def test_strict_copied_keyword_only_api_subclass_and_dataset_precedence():
    @dataclass
    class Extended(DataTable):
        caption: str = "caption"

    original = action(context=[{"column": "key", "label": "Selected key"}])
    table = Extended(frame(), "Actions", 2, True, None, "population", True,
                     ["Label", "Value"], None, True, None, "positional", drill_down=original)
    original["context"][0]["label"] = "changed"
    assert table.caption == "positional"
    assert table.drill_down["context"][0]["label"] == "Selected key"
    _, data = register(table)
    cfg = next(iter(data.values()))
    assert cfg["dataset_id"] == "population" and cfg["drillDown"]["dataset_id"] == "target"
    assert "rows" not in cfg
    _, data = register(DataTable(frame(), "Static", static=True, columns=["Label", "Value"], drill_down=action()))
    cfg = next(iter(data.values()))
    assert cfg["drillDownKeys"] == ["a", "b", None]
    assert cfg["columns"] == ["Label", "Value"]
    assert cfg["rawRows"][0][1] == "1.0000000000000001"
    assert cfg["drillDown"]["context"] == [{"column": "key", "label": "key"}]
    assert "drill" not in register(DataTable(frame(), "Default", static=True))[0].lower()


@pytest.mark.parametrize("option", [False, [], {}, {**action(), "unknown": 1},
    action(dataset_id=""), action(column=1), action(value_column="absent"), action(label_column="key"),
    action(context={}), action(context=[{}]), action(context=[{"column": "key", "label": ""}]),
    action(context=[{"column": "key", "label": "Key", "x": 1}]),
    action(context=[{"column": "key", "label": "Key"}] * 2)])
def test_strict_option_validation_at_construction_and_after_mutation(option):
    with pytest.raises(ValueError, match="drill_down"):
        DataTable(frame(), "Invalid", columns=["Label", "Value"], drill_down=option)
    table = DataTable(frame(), "Mutated", columns=["Label", "Value"])
    table.drill_down = option
    with pytest.raises(ValueError, match="drill_down"):
        register(table)


@pytest.mark.parametrize("option", [None, "true", 1, [], {}])
def test_parent_clear_strict_boolean(option):
    with pytest.raises(ValueError, match="clear_on_parent_change"):
        FilterBar("target", frame(), [{"column": "key", "depends_on": "Label", "clear_on_parent_change": option}])


@pytest.mark.parametrize("changes", [{}, {"depends_on": []}, {"depends_on": ""},
    {"depends_on": [""]}, {"depends_on": [2]}, {"depends_on": "Label", "type": "toggle"}])
def test_parent_clear_requires_dropdown_and_nonempty_parents(changes):
    with pytest.raises(ValueError, match="clear_on_parent_change"):
        FilterBar("target", frame(), [{"column": "key", "clear_on_parent_change": True, **changes}])


def test_parent_clear_default_and_false_payload_unchanged():
    plugin = DropdownFilter()
    base = {"column": "key", "depends_on": "Label"}
    assert plugin.build_config("f", "key", base, frame()) == plugin.build_config("f", "key", {**base, "clear_on_parent_change": False}, frame())
    assert plugin.build_config("f", "key", {**base, "clear_on_parent_change": True}, frame())["clear_on_parent_change"] is True


@pytest.mark.parametrize("kind", ["missing-target", "missing-owner", "ambiguous", "toggle", "missing-context", "population-column", "mutated", "live-target", "live-population", "live-scope", "live-propagation"])
def test_report_reference_and_live_validation(kind):
    df = frame()
    table = DataTable(df, "Actions", dataset_id="population", drill_down=action())
    sources = {"population": DataSource("population", df), "target": DataSource("target", df)}
    bars = [FilterBar("target", df, [{"column": "key"}])]
    if kind == "missing-target":
        sources.pop("target")
    elif kind == "missing-owner":
        bars = []
    elif kind == "ambiguous":
        bars.append(FilterBar("target", df, [{"column": "key"}]))
    elif kind == "toggle":
        bars[0].filters[0]["type"] = "toggle"
    elif kind == "missing-context":
        table.drill_down["context"] = [{"column": "missing", "label": "Missing"}]
    elif kind == "population-column":
        sources["population"].df = df.drop(columns="key")
    elif kind == "mutated":
        table.drill_down["label_column"] = "missing"
    else:
        live = LiveDataSource("live", query="lookup", df=df)
        sources["live"] = live
        if kind == "live-target":
            table.drill_down["dataset_id"] = "live"
        elif kind == "live-population":
            table.dataset_id = "live"
        elif kind == "live-scope":
            sources["population"] = ScopedDataSource("population", parent="live")
        else:
            bars[0].propagate_to = {"live": {"key": "key"}}
    result = ValidationResult("test")
    check_table_drill_down([(table, "Rows"), *((bar, "Owners") for bar in bars)], sources, result)
    assert any(c.id == "datatable-drill-down-invalid" and c.level == "fail" for c in result.checks)


def test_native_module_complete_diagnostics(tmp_path):
    ctx = context(tmp_path)
    examples().add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()
    render_report(ctx, auto_refresh=False)


@contextmanager
def browser_report(path, width=1280, query="", extra=None):
    from playwright.sync_api import sync_playwright

    ctx = context(path)
    module = examples()
    module.add_sections(ctx)
    if extra:
        extra(ctx)
    render_report(ctx, auto_refresh=False)
    data = json.loads((path / "data.json").read_text())
    tables = {cfg.get("title", "default"): cid for cid, cfg in data["components"].items() if cfg["type"] == "table"}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def fulfill(route):
            url = urlsplit(route.request.url)
            assert url.hostname == "drill.test"
            relative = unquote(url.path).lstrip("/")
            file = ROOT / "trellum/static/vendor" / relative[len("_vendor/"):] if relative.startswith("_vendor/") else path / relative
            route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404, body="missing")
        page.route("**/*", fulfill)
        page.goto("http://drill.test/index.html" + query, wait_until="networkidle")
        for cid in tables.values():
            if page.locator(f"#{cid}").evaluate("node=>{for(let p=node;p;p=p.parentElement)if(getComputedStyle(p).display==='none'||p.hidden)return true;return false}"):
                continue
            page.locator(f"#{cid}").scroll_into_view_if_needed()
            page.locator(f"#{cid} table").wait_for()
        yield page, tables, data, module
        assert errors == []
        browser.close()


def selections(page, dataset):
    return page.evaluate("id=>Object.fromEntries(Object.values(_fwFilterEngine.getFilterState(id)).map(f=>[f.column,f.value]))", dataset)


def select(page, dataset, column, values):
    assert page.evaluate("([d,c,v])=>{const o=_fwTableDropdownOwner(d,c);if(o.reason)throw Error(o.reason);o.select(v);return true}", [dataset, column, values])
    page.wait_for_function("([d,c,v])=>JSON.stringify(_fwTableDropdownOwner(d,c).values.slice().sort())===JSON.stringify(v.slice().sort())", arg=[dataset, column, values])


def chip_context(page, cid):
    return page.locator(f"#{cid}").locator("xpath=preceding-sibling::*[contains(@class,'fw-table-selection')][1]")


def matching_rows(page, dataset):
    return page.evaluate("id=>_fwFilterEngine.getFiltered(id)", dataset)


def totals_text(page, cid):
    return page.locator(f"#{cid} tfoot td").all_text_contents()



def assert_population_totals(page, cid, facts):
    cells = totals_text(page, cid)
    installs = sum(r["Installs"] for r in facts)
    spend = sum((Decimal(str(r["Spend"])) for r in facts), Decimal(0))
    revenue = sum((Decimal(str(r["Revenue"])) for r in facts), Decimal(0))
    assert cells[3] == f"{installs:,}"
    assert cells[4] == f"${spend.quantize(Decimal('.01'), rounding=ROUND_HALF_UP):,}"
    assert cells[5] == f"${revenue.quantize(Decimal('.01'), rounding=ROUND_HALF_UP):,}"
    assert cells[6] == f"${(spend / installs).quantize(Decimal('.01'), rounding=ROUND_HALF_UP):,}"

def test_native_actions_population_math_context_search_and_csv(tmp_path):
    with browser_report(tmp_path) as (page, tables, data, m):
        source, campaign = tables["Source observations"], tables["Campaign observations"]
        facts = m.observation_frame().to_dict("records")
        assert len(facts) == 56 and any(r["Installs"] == r["Spend"] == 0 for r in facts)
        assert len(matching_rows(page, m.BASE)) == 56
        baseline_total = totals_text(page, source)
        assert_population_totals(page, source, facts)
        button = page.locator(f"#{source} [data-fw-drill-action]").first
        source_key = button.evaluate("e=>e._fwDrillKey")
        button.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("([d,k])=>_fwTableDropdownOwner(d,'source_key').values[0]===k", arg=[m.DETAIL, source_key])
        assert page.evaluate("document.activeElement.hasAttribute('data-fw-drill-action')")
        assert totals_text(page, source) == baseline_total
        expected_source = [r for r in facts if r["source_key"] == source_key]
        assert len(matching_rows(page, m.CAMPAIGNS)) == len(expected_source)
        assert_population_totals(page, campaign, expected_source)
        cb = page.locator(f"#{campaign} [data-fw-drill-action]").first
        campaign_key = cb.evaluate("e=>e._fwDrillKey")
        cb.click()
        page.wait_for_function("([d,k])=>_fwTableDropdownOwner(d,'campaign_key').values[0]===k", arg=[m.DETAIL, campaign_key])
        detail = [r for r in expected_source if r["campaign_key"] == campaign_key]
        assert len(matching_rows(page, m.DETAIL)) == len(detail)
        assert len(matching_rows(page, m.CAMPAIGNS)) == len(expected_source)
        chart_id = next(cid for cid, cfg in data["components"].items() if cfg.get("dataset_id") == m.DETAIL and cfg["type"] == "chart")
        page.locator(f"#{chart_id}").scroll_into_view_if_needed()
        actual = page.evaluate("id=>_chartInstances[id].data.datasets[0].data", chart_id)
        expected = [sum(r["Installs"] for r in detail if r["Day"].day == day) for day in range(1, 5)]
        assert actual == expected
        assert str(sum(r["Installs"] for r in detail)) in page.locator(".fw-kpi-value").last.inner_text().replace(",", "")
        # Local search never clears the report's selection or hidden IDs into search/CSV.
        search = page.locator(f'[data-table-search="{source}"]')
        search.fill("no such observation")
        page.locator(f"#{source}").locator("xpath=following-sibling::*[contains(@class,'fw-table-drill-empty')][1]").get_by_role("button", name="Clear search").wait_for()
        assert source_key in chip_context(page, source).inner_text()
        assert "local search" in page.locator(f"#{source}").locator("xpath=following-sibling::*[contains(@class,'fw-table-drill-empty')][1]").inner_text()
        page.get_by_role("button", name="Clear search", exact=True).click()
        assert search.input_value() == ""
        assert selections(page, m.DETAIL)["source_key"] == [source_key]
        # Repeating the sole selected campaign clears just Campaign.
        page.locator(f"#{campaign} [data-fw-drill-action]").first.click()
        page.wait_for_function("d=>_fwTableDropdownOwner(d,'campaign_key').values.length===0", arg=m.DETAIL)
        assert selections(page, m.DETAIL)["source_key"] == [source_key]
        select(page, m.DETAIL, "source_key", ["Affiliate"])
        assert "Affiliate" in chip_context(page, source).inner_text()
        assert page.locator(f"#{source} [aria-pressed=true]").count() == 0
        # CSV contains all matching rows in the table's sort order, no hidden key.
        page.evaluate("window.drillCsv=null;_fwDownloadCsv=(columns,rows)=>window.drillCsv={columns,rows}")
        page.locator(f"#{source} th").nth(4).click()
        page.locator(f'[data-csv-target="{source}"]').click()
        csv = page.evaluate("window.drillCsv")
        assert "source_key" not in csv["columns"] and "campaign_key" not in csv["columns"]
        assert len(csv["rows"]) == 56
        assert [r[4] for r in csv["rows"]] == sorted([r["Installs"] for r in facts], reverse=True)
        assert totals_text(page, source) == baseline_total


@pytest.mark.parametrize("path", ["dropdown", "owner-clear", "row", "chip"])
def test_parent_change_clears_children_through_each_owner_path(tmp_path, path):
    with browser_report(tmp_path) as (page, tables, _, m):
        source = tables["Source observations"]
        select(page, m.DETAIL, "source_key", ["Paid search"])
        select(page, m.DETAIL, "campaign_key", ["Paid search / New"])
        if path == "dropdown":
            # Actual SlimSelect UI, rather than directly setting an engine predicate.
            bar = page.locator(f'[data-fw-ds-id="{m.DETAIL}"]')
            bar.locator('.ss-main').first.click()
            page.locator('.ss-content.ss-open-below .ss-option, .ss-content.ss-open-above .ss-option').filter(has_text="Organic").first.click()
            page.keyboard.press("Escape")
        elif path == "owner-clear":
            fid = page.evaluate("d=>_fwTableDropdownOwner(d,'source_key').node.dataset.filterId", m.DETAIL)
            page.locator(f'[data-filter-id="{fid}"][data-action="clear-all"]').click()
        elif path == "row":
            page.locator(f"#{source} [data-fw-drill-action]").filter(has_text="Paid search").first.click()
        else:
            chip_context(page, source).get_by_role("button", name="Clear Source", exact=True).click()
        page.wait_for_function("d=>_fwTableDropdownOwner(d,'campaign_key').values.length===0", arg=m.DETAIL)
        assert selections(page, m.DETAIL)["campaign_key"] == []
        # Parent clearing leaves all child keys available, yet must clear the child.
        if path != "dropdown":
            assert selections(page, m.DETAIL)["source_key"] == []
            assert "Paid search / New" in page.evaluate("d=>Array.from(_fwTableDropdownOwner(d,'campaign_key').node.options).map(o=>o.value)", m.DETAIL)


def test_unchanged_and_equivalent_parent_sets_preserve_child_and_multiselect_row_replaces(tmp_path):
    with browser_report(tmp_path) as (page, tables, _, m):
        select(page, m.DETAIL, "source_key", ["Paid search", "Organic"])
        select(page, m.DETAIL, "campaign_key", ["Paid search / New"])
        select(page, m.DETAIL, "source_key", ["Organic", "Paid search"])
        assert selections(page, m.DETAIL)["campaign_key"] == ["Paid search / New"]
        select(page, m.DETAIL, "source_key", ["Organic", "Paid search"])
        assert selections(page, m.DETAIL)["campaign_key"] == ["Paid search / New"]
        page.evaluate("d=>{const o=_fwTableDropdownOwner(d,'source_key');_fwFilterEngine.setFilter(d,o.node.dataset.filterId,'source_key','in',['Paid search','Organic'])}", m.DETAIL)
        assert selections(page, m.DETAIL)["campaign_key"] == ["Paid search / New"]
        assert "Paid search, Organic" in chip_context(page, tables["Source observations"]).inner_text()
        page.locator(f'#{tables["Source observations"]} [data-fw-drill-action]').first.click()
        page.wait_for_function("d=>_fwTableDropdownOwner(d,'source_key').values.length===1", arg=m.DETAIL)
        assert selections(page, m.DETAIL)["campaign_key"] == []


def test_initial_url_restoration_and_later_clearing_preserve_date_platform(tmp_path):
    m = examples()
    query = "?" + urlencode({m.BASE + ".Day": "2026-10-02..2026-10-03", m.BASE + ".Platform": "iOS",
                              m.DETAIL + ".source_key": "Paid search", m.DETAIL + ".campaign_key": "Paid search / New"})
    with browser_report(tmp_path, query=query) as (page, tables, _, m):
        assert selections(page, m.DETAIL)["source_key"] == ["Paid search"]
        assert selections(page, m.DETAIL)["campaign_key"] == ["Paid search / New"]
        before = selections(page, m.BASE)
        assert before["Platform"] == "iOS"
        assert before["Day"] == {"min": "2026-10-02", "max": "2026-10-03"}
        assert len(matching_rows(page, m.DETAIL)) == 2
        chip_context(page, tables["Source observations"]).get_by_role("button", name="Clear Source", exact=True).click()
        page.wait_for_function("d=>_fwTableDropdownOwner(d,'campaign_key').values.length===0", arg=m.DETAIL)
        assert selections(page, m.DETAIL)["source_key"] == []
        assert selections(page, m.BASE) == before
        params = page.evaluate("Object.fromEntries(new URL(location.href).searchParams)")
        assert params[m.BASE + ".Platform"] == "iOS" and m.BASE + ".Day" in params
        assert m.DETAIL + ".source_key" not in params and m.DETAIL + ".campaign_key" not in params
        select(page, m.DETAIL, "source_key", ["Organic"])
        assert selections(page, m.BASE) == before


@pytest.mark.parametrize("opted", [False, True])
def test_existing_unopted_cascade_preserves_available_child_and_opted_clears_it(tmp_path, opted):
    def extra(ctx):
        df = pd.DataFrame({"parent": ["A", "B"], "child": ["Shared", "Shared"]})
        child = {"column": "child", "depends_on": "parent"}
        if opted:
            child["clear_on_parent_change"] = True
        ctx.add_section("Shared child", [DataSource("shared", df), FilterBar("shared", df, [{"column": "parent"}, child])])
    with browser_report(tmp_path, extra=extra) as (page, _, _, _):
        select(page, "shared", "parent", ["A"])
        select(page, "shared", "child", ["Shared"])
        select(page, "shared", "parent", ["B"])
        assert selections(page, "shared")["child"] == ([] if opted else ["Shared"])
        select(page, "shared", "child", ["Shared"])
        select(page, "shared", "parent", [])
        assert selections(page, "shared")["child"] == ([] if opted else ["Shared"])


def test_duplicate_labels_raw_special_keys_disabled_rows_and_independent_instances(tmp_path):
    special = 'special["色"]<&'
    column = 'key["色"]'
    keys = ['a["é"]<&', 'b] "💡"', None, "", 23, True]
    def extra(ctx):
        df = pd.DataFrame({column: keys, "Label": ["Repeated long descriptive observation label " * 8] * len(keys), "Amount": [Decimal("1.0000000000000001")] * len(keys)})
        owner_df = pd.DataFrame({column: keys[:2]})
        ctx.add_section("Special keys", [
            DataSource(special, owner_df), FilterBar(special, owner_df, [{"column": column, "label": "Special key"}]),
            DataTable(df, "Special actions", static=True, columns=["Label", "Amount"],
                      drill_down=action(dataset_id=special, column=column, value_column=column)),
            DataTable(df, "Independent actions", static=True, columns=["Label", "Amount"],
                      drill_down=action(dataset_id=special, column=column, value_column=column)),
        ])
    with browser_report(tmp_path, extra=extra) as (page, tables, data, m):
        cid = tables["Special actions"]
        buttons = page.locator(f"#{cid} [data-fw-drill-action]")
        assert buttons.count() == 6
        assert buttons.first.bounding_box()["height"] > 30
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        assert buttons.nth(2).is_disabled() and "raw string" in buttons.nth(2).get_attribute("aria-label")
        assert all(buttons.nth(i).is_disabled() for i in (2, 3, 4, 5))
        buttons.nth(0).click()
        page.wait_for_function("([d,c,v])=>_fwTableDropdownOwner(d,c).values[0]===v", arg=[special, column, keys[0]])
        assert selections(page, special)[column] == [keys[0]]
        assert keys[0] in chip_context(page, cid).inner_text()
        assert buttons.nth(0).get_attribute("aria-pressed") == "true"
        assert buttons.nth(1).get_attribute("aria-pressed") == "false"
        assert page.locator(f'#{tables["Independent actions"]} [aria-pressed=true]').count() == 1
        buttons.nth(1).click()
        page.wait_for_function("([d,c,v])=>_fwTableDropdownOwner(d,c).values[0]===v", arg=[special, column, keys[1]])
        assert selections(page, special)[column] == [keys[1]]
        assert not selections(page, m.DETAIL).get("source_key")
        page.evaluate("window.drillCsv=null;_fwDownloadCsv=(columns,rows)=>window.drillCsv={columns,rows}")
        page.locator(f'[data-csv-target="{cid}"]').click()
        csv = page.evaluate("window.drillCsv")
        assert csv["columns"] == ["Label", "Amount"]
        assert csv["rows"][0][1] == "1.0000000000000001"
        # An unavailable but valid raw string is disabled, never routed elsewhere.
        cfg = data["components"][cid]
        cfg["drillDownKeys"][0] = "absent"
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, cfg])
        assert buttons.nth(0).is_disabled() and "unavailable" in buttons.nth(0).get_attribute("aria-label")
        # Ambiguous runtime owners disable actions instead of selecting the first.
        page.evaluate("d=>{const bar=Array.from(document.querySelectorAll('[data-fw-filter-bar]')).find(b=>b.dataset.fwDsId===d);bar.after(bar.cloneNode(true))}", special)
        page.wait_for_function("id=>document.getElementById(id).querySelector('[data-fw-drill-action]').disabled", arg=cid)
        assert buttons.nth(1).is_disabled()


def test_owned_lifecycle_renderall_theme_hidden_replacement_and_config_removal(tmp_path):
    with browser_report(tmp_path) as (page, tables, data, m):
        cid = tables["Source observations"]
        def counts():
            return page.evaluate("Object.fromEntries(Object.entries(_fwFilterEngine._subscribers).map(([id,s])=>[id,s.filter(x=>x.id.includes('_drill_')).length]))")
        original = counts()
        for _ in range(3):
            page.evaluate("window.renderAll()")
            page.locator(f"#{cid}").scroll_into_view_if_needed()
            page.wait_for_timeout(60)
        assert counts() == original
        select(page, m.DETAIL, "source_key", ["Paid search"])
        chip_context(page, cid).get_by_role("button", name="Clear Source", exact=True).focus()
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, data["components"][cid]])
        assert page.evaluate("document.activeElement.textContent==='Clear Source'")
        page.evaluate("id=>document.getElementById(id).closest('.fw-section').hidden=true", cid)
        select(page, m.DETAIL, "source_key", ["Organic"])
        page.evaluate("id=>document.getElementById(id).closest('.fw-section').hidden=false", cid)
        assert "Organic" in chip_context(page, cid).inner_text()
        page.locator("#fwThemeSelect").select_option("trellum dark")
        assert counts() == original
        # Synchronous population redraw restores row-key focus, then context if gone.
        page.locator(f"#{cid} [data-fw-drill-action]").first.focus()
        page.evaluate("d=>_fwFilterEngine.notify(d)", m.BASE)
        assert page.evaluate("document.activeElement.hasAttribute('data-fw-drill-action')")
        campaign = tables["Campaign observations"]
        page.locator(f"#{campaign} [data-fw-drill-action]").first.focus()
        page.evaluate("d=>_fwTableDropdownOwner(d,'source_key').select(['Affiliate'])", m.DETAIL)
        page.wait_for_function("d=>_fwTableDropdownOwner(d,'source_key').values[0]==='Affiliate'", arg=m.DETAIL)
        assert page.evaluate("document.activeElement !== document.body && document.activeElement.getAttribute('aria-label').includes('context')")
        # Config removal releases selection and population subscriptions immediately.
        cfg = data["components"][cid]
        page.evaluate("([id,cfg])=>{const next={...cfg};delete next.drillDown;_fwRenderers.table(id,next)}", [cid, cfg])
        assert not page.locator(f"#{cid} [data-fw-drill-action]").count()
        assert counts()[m.DETAIL] == original[m.DETAIL] - 1
        assert counts()[m.BASE] == original[m.BASE] - 1
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, cfg])
        assert counts() == original
        # Replacing a node disposes the detached node before wiring the replacement.
        page.evaluate("id=>{const old=document.getElementById(id);window.detachedTable=old;old.replaceWith(old.cloneNode(true))}", cid)
        page.wait_for_function("!window.detachedTable._fwDrillDispose")
        assert page.locator(f'[data-table-search="{cid}"]').evaluate("e=>e.oninput===null")
        assert page.locator(f'[data-table-row-limit="{cid}"]').evaluate("e=>e.onchange===null")
        assert page.locator(f'[data-csv-target="{cid}"]').evaluate("e=>e.onclick===null")
        assert counts()[m.DETAIL] == original[m.DETAIL] - 1
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, cfg])
        assert counts() == original
        page.evaluate("id=>document.getElementById(id).remove()", cid)
        page.wait_for_function("([ds,n])=>_fwFilterEngine._subscribers[ds].filter(x=>x.id.includes('_drill_')).length===n", arg=[m.DETAIL, original[m.DETAIL] - 1])
        page.evaluate("delete window.detachedTable")


def test_clearing_unchanged_empty_parent_preserves_child(tmp_path):
    with browser_report(tmp_path) as (page, _, _, m):
        select(page, m.DETAIL, "campaign_key", ["Paid search / New"])
        select(page, m.DETAIL, "source_key", [])
        assert selections(page, m.DETAIL)["campaign_key"] == ["Paid search / New"]


def baseline_module():
    result = subprocess.run(["git", "show", f"{BASELINE}:trellum/components/tables.py"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        pytest.skip(f"Required history object {BASELINE} unavailable; functional drill-down coverage still runs")
    module = types.ModuleType("_drill_baseline")
    sys.modules[module.__name__] = module
    exec(compile(result.stdout, f"{BASELINE}/tables.py", "exec"), module.__dict__)
    for loader, suffix in (("load_css", "css"), ("load_js", "js")):
        asset = subprocess.run(["git", "show", f"{BASELINE}:trellum/static/{suffix}/components/data_table.{suffix}"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
        real = getattr(module, loader)
        setattr(module, loader, lambda name, real=real, asset=asset, suffix=suffix:
                asset if name == f"components/data_table.{suffix}" else real(name))
    return module


def test_default_off_payload_matches_required_actual_baseline():
    module = baseline_module()
    for bound in (None, "bound"):
        options = {"static": True, "dataset_id": bound, "columns": ["Label", "Value"],
                   "row_limit": 10, "sortable": True, "searchable": True, "pin_header": True,
                   "totals": {"Value": {"agg": "sum"}}}
        assert register(module.DataTable(frame(), "Baseline", **options)) == register(DataTable(frame(), "Baseline", **options))


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_default_off_pixels_match_required_actual_baseline(tmp_path, width):
    from playwright.sync_api import sync_playwright
    module = baseline_module()
    for folder, cls in (("before", module.DataTable), ("after", DataTable)):
        ctx = context(tmp_path / folder)
        ctx.add_section("Default table", [cls(frame(), "Default table", static=True, columns=["Label", "Value"],
                                               sortable=True, row_limit=10, totals={"Value": {"agg": "sum"}})])
        render_report(ctx, auto_refresh=False)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        def fulfill(route):
            path = unquote(urlsplit(route.request.url).path).lstrip("/")
            if path.startswith("_vendor/"):
                file = ROOT / "trellum/static/vendor" / path[len("_vendor/"):]
            else:
                file = tmp_path / path
            route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404, body="missing")
        page.route("**/*", fulfill)
        for theme in ("trellum light", "trellum dark"):
            images = []
            for folder in ("before", "after"):
                page.goto(f"http://baseline.test/{folder}/index.html", wait_until="networkidle")
                page.locator(".fw-table-scroll").scroll_into_view_if_needed()
                page.locator(".fw-table td").first.wait_for()
                page.locator("#fwThemeSelect").select_option(theme)
                images.append(page.locator(".fw-table-scroll").screenshot(animations="disabled"))
            assert images[0] == images[1]
        browser.close()


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_real_native_selected_and_empty_capture_light_dark_and_exports(tmp_path, width):
    evidence = Path("/tmp/trellum-row-drill-evidence")
    evidence.mkdir(exist_ok=True)
    with browser_report(tmp_path, width=width) as (page, tables, _, m):
        cid = tables["Campaign observations"]
        select(page, m.DETAIL, "source_key", ["Affiliate"])
        select(page, m.DETAIL, "campaign_key", ["Affiliate / New"])
        section = page.locator(f"#{cid}").locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
        source_total = totals_text(page, tables["Source observations"])
        base_state = selections(page, m.BASE)
        for state in ("selected", "empty"):
            if state == "empty":
                page.locator(f'[data-fw-ds-id="{m.BASE}"]').get_by_role("button", name="Android", exact=True).click()
                assert selections(page, m.DETAIL)["source_key"] == ["Affiliate"]
                assert selections(page, m.DETAIL)["campaign_key"] == ["Affiliate / New"]
                assert selections(page, m.BASE)["Day"] == base_state["Day"]
                assert not page.locator(f"#{cid} tbody tr").count()
                assert not page.locator(f"#{cid} tfoot").count()
                assert "current report filters" in section.inner_text()
                assert page.locator(f"#{cid}").bounding_box()["height"] < 140
            else:
                assert "8 matching rows" in page.locator(f"#{cid}-totals-note").inner_text()
                assert totals_text(page, tables["Source observations"]) == source_total
            assert "Affiliate / New" in chip_context(page, cid).inner_text()
            assert not chip_context(page, cid).evaluate("(node)=>node.closest('.fw-table-scroll')")
            for theme in ("trellum light", "trellum dark"):
                page.locator("#fwThemeSelect").select_option(theme)
                section.scroll_into_view_if_needed()
                path = evidence / f"drill-{width}-{state}-{theme.replace(' ', '-')}.png"
                section.screenshot(path=str(path), animations="disabled")
                capture = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
                assert capture["format"] == "trellum-analysis-capture"
                (evidence / f"capture-{width}-{state}-{theme.replace(' ', '-')}.png").write_bytes(base64.b64decode(capture["image"]["data_base64"]))
                (evidence / f"capture-{width}-{state}-{theme.replace(' ', '-')}.json").write_text(json.dumps(capture))
                assert path.stat().st_size > 1000
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                if width == 390 and state == "selected":
                    assert page.locator(f"#{cid}").evaluate("e=>e.scrollWidth>e.clientWidth")
            if state == "empty":
                chip_context(page, cid).get_by_role("button", name="Clear Source", exact=True).click()
                page.wait_for_function("d=>_fwTableDropdownOwner(d,'campaign_key').values.length===0", arg=m.DETAIL)
                assert selections(page, m.BASE)["Platform"] == "Android"
                assert len(matching_rows(page, m.CAMPAIGNS)) == 24
                assert page.evaluate("document.activeElement!==document.body")
        # Ordinary vendored export presentation still builds valid PNG/PDF.
        for fmt in ("PNG", "PDF"):
            with page.expect_download() as download:
                page.evaluate("fmt=>fw['export'+fmt]()", fmt)
            path = evidence / f"report-{width}.{fmt.lower()}"
            download.value.save_as(path)
            assert path.stat().st_size > 1000
            if fmt == "PDF":
                assert path.read_bytes().startswith(b"%PDF-")


def test_missing_owner_then_normal_readiness_and_live_injection_are_safe(tmp_path):
    df = frame()
    rendering = RenderContext(DEFAULT_THEME)
    rendering._counter = 9999
    bar_html = FilterBar("late", df, [{"column": "key"}]).render_html(rendering)
    bar_id, bar_cfg = next(iter(rendering.component_data.items()))
    with browser_report(tmp_path) as (page, tables, data, _):
        cid = tables["Static observations · source actions"]
        cfg = dict(data["components"][cid])
        cfg["drillDown"] = action(dataset_id="late")
        cfg["columns"] = ["Label"]
        cfg["rows"] = [["Duplicate"], ["Duplicate"]]
        cfg["drillDownKeys"] = ["a", "b"]
        cfg.pop("rawRows", None)
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, cfg])
        assert page.locator(f"#{cid} button").first.is_disabled()
        page.evaluate("([html,id,cfg])=>{_fwFilterEngine.init('late',[{key:'a'},{key:'b'}]);document.body.insertAdjacentHTML('beforeend',html);_fwRenderers.filter_bar(id,cfg)}", [bar_html, bar_id, bar_cfg])
        page.wait_for_function("id=>!document.getElementById(id).querySelector('button').disabled", arg=cid)
        page.locator(f"#{cid} button").first.focus()
        page.keyboard.press("Space")
        page.wait_for_function("_fwTableDropdownOwner('late','key').values[0]==='a'")
        assert page.locator(f"#{cid} button").first.get_attribute("aria-pressed") == "true"
        page.evaluate("_fwFilterEngine.registerLiveDataset('late',{onChange:()=>{throw Error('must not query')}})")
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, cfg])
        assert page.locator(f"#{cid} button").first.is_disabled()
        assert "Live" in page.locator(f"#{cid} button").first.get_attribute("aria-label")


def test_comparison_and_workspace_ignore_injected_drill_down(tmp_path):
    with browser_report(tmp_path) as (page, tables, data, m):
        cid = tables["Source observations"]
        original = selections(page, m.DETAIL)
        cfg = data["components"][cid]
        for extra in ({"comparison": {"context": None, "compareColumns": [], "valueColumn": "Installs"}},
                      {"workspacePageSize": 25}):
            page.evaluate("([id,cfg,extra])=>_fwRenderers.table(id,{...cfg,...extra})", [cid, cfg, extra])
            assert not page.locator(f"#{cid} [data-fw-drill-action]").count()
            assert not page.evaluate("id=>!!document.getElementById(id)._fwDrillDispose", cid)
            assert selections(page, m.DETAIL) == original
    comparison = ComparisonTable(frame(), "Existing comparison", "Value", [], static=True)
    comparison.drill_down = action()
    assert all("drillDown" not in cfg for cfg in register(comparison)[1].values())


def test_measured_zero_retained_in_native_detail_and_no_filter_side_effects(tmp_path):
    m = examples()
    query = "?" + urlencode({m.BASE + ".Day": "2026-10-01..2026-10-01", m.BASE + ".Platform": "iOS",
                              m.DETAIL + ".source_key": "Paid search", m.DETAIL + ".campaign_key": "Paid search / New"})
    with browser_report(tmp_path, query=query) as (page, tables, data, m):
        rows = matching_rows(page, m.DETAIL)
        assert len(rows) == 1 and rows[0]["Installs"] == rows[0]["Spend"] == rows[0]["Revenue"] == 0
        assert float(page.locator(".fw-kpi-value").last.inner_text()) == 0
        chart_id = next(cid for cid, cfg in data["components"].items() if cfg.get("dataset_id") == m.DETAIL and cfg["type"] == "chart")
        page.locator(f"#{chart_id}").scroll_into_view_if_needed()
        assert page.evaluate("id=>_chartInstances[id].data.datasets[0].data", chart_id) == [0]
        state = selections(page, m.DETAIL)
        cid = tables["Source observations"]
        page.locator(f'[data-table-row-limit="{cid}"]').select_option("all")
        page.locator(f'[data-table-search="{cid}"]').fill("Partner")
        page.wait_for_timeout(250)
        assert selections(page, m.DETAIL) == state
        assert len(matching_rows(page, m.DETAIL)) == 1
        assert "Paid search / New" in chip_context(page, cid).inner_text()


def test_native_hidden_tab_mounts_with_current_owner_selection(tmp_path):
    from trellum.components import RawHTML, TabGroup
    m = examples()
    def extra(ctx):
        ctx.add_section("Deferred drill actions", [TabGroup([
            {"label": "Overview", "content": [RawHTML(html="<p>Observations in the next tab.</p>")]},
            {"label": "Drill observations", "content": [DataTable(m.observation_frame(), "Deferred observations", static=True,
                columns=["Source", "Installs"], drill_down=m.action("source_key", "Source"))]},
        ], id="drill-tabs")])
    with browser_report(tmp_path, extra=extra) as (page, tables, _, m):
        cid = tables["Deferred observations"]
        select(page, m.DETAIL, "source_key", ["Affiliate"])
        assert not page.locator(f"#{cid}").is_visible()
        page.get_by_role("tab", name="Drill observations", exact=True).click()
        page.locator(f"#{cid} table").wait_for()
        assert "Affiliate" in chip_context(page, cid).inner_text()
        assert page.locator(f"#{cid} [aria-pressed=true]").count() == 8
        select(page, m.DETAIL, "source_key", ["Organic"])
        page.get_by_role("tab", name="Overview", exact=True).click()
        select(page, m.DETAIL, "source_key", ["Partner"])
        page.get_by_role("tab", name="Drill observations", exact=True).click()
        assert "Partner" in chip_context(page, cid).inner_text()


def test_unchanged_all_toggle_parent_does_not_clear_opted_child(tmp_path):
    def extra(ctx):
        df = pd.DataFrame({"parent": ["A", "B"], "child": ["Shared", "Shared"]})
        ctx.add_section("Toggle parent", [DataSource("toggle_parent", df), FilterBar("toggle_parent", df, [
            {"column": "parent", "type": "toggle"},
            {"column": "child", "depends_on": "parent", "clear_on_parent_change": True},
        ])])
    with browser_report(tmp_path, extra=extra) as (page, _, _, _):
        select(page, "toggle_parent", "child", ["Shared"])
        bar = page.locator('[data-fw-ds-id="toggle_parent"]')
        bar.get_by_role("button", name="All", exact=True).click()
        assert selections(page, "toggle_parent")["child"] == ["Shared"]
        bar.get_by_role("button", name="A", exact=True).click()
        assert selections(page, "toggle_parent")["child"] == []
