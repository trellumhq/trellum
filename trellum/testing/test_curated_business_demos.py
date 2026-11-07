"""Business-demo arithmetic and the generated reports' native components."""

import csv
import importlib
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, Grid, LineChart, RenderContext, SankeyChart
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.runner.discovery import discover_report
from trellum.testing.test_player_overview_compact import pinned_frames
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "trellum/demo/reports"


def report_module(slug):
    report = discover_report(str(REPORTS / slug))
    return report, importlib.import_module(report.__module__)


def retention_facts():
    # Deliberately unequal cohort sizes make a mean of rates incorrect.
    rows = []
    for day in pd.date_range("2026-06-01", "2026-11-06"):
        for platform, scale in (("iOS", 1), ("Android", 3)):
            for horizon in (1, 3, 7, 14, 30):
                eligible = scale * (10 + day.day * 3 + horizon)
                retained = scale * (2 + day.day % 9)
                rows.append(dict(cohort_date=day.strftime("%Y-%m-%d"), title="Coral Quest",
                                 platform=platform, day_number=horizon,
                                 cohort_size=eligible, retained_users=retained))
    return pd.DataFrame(rows)


def funnel_facts():
    return pd.DataFrame([
        dict(event_date=day, title=title, device=device, step=step, step_order=order,
             users=count * scale)
        for day in ("2026-11-05", "2026-11-06")
        for title in ("Coral Quest", "Iron Vanguard", "Neon Racer")
        for device, scale in (("Phone", 1), ("Desktop", 2))
        for order, (step, count) in enumerate((
            ("store_view", 100), ("item_view", 60), ("add_to_cart", 35),
            ("checkout", 20), ("purchase", 12)))
    ])


def build_report(slug, output, monkeypatch):
    monkeypatch.setenv("FW_NOW", "2026-11-07T12:00:00Z")
    monkeypatch.setenv("FW_PROJECT_ROOT", str(ROOT / "trellum/demo"))
    report, module = report_module(slug)
    frames = pinned_frames()
    if slug == "monetization":
        ua = pd.DataFrame([dict(event_date="2026-11-06", title="Coral Quest", platform="iOS",
                                campaign_type="ua", spend=140., installs=50,
                                impressions=1000, clicks=100)])
        journey = pd.DataFrame([dict(event_date="2026-11-06", title="Coral Quest",
                                     spender_tier=tier, from_node="paid", to_node="easy",
                                     transitions=100)
                                for tier in ("whale", "dolphin", "minnow", "non_spender")])
        mapping = {module.queries.REVENUE_DAILY: frames["main"], module.queries.UA_SPEND: ua,
                   module.queries.JOURNEY: journey}
        monkeypatch.setattr(module.cohort_quality, "query_df", lambda *a, **k: retention_facts())
    elif slug == "conversion":
        mapping = {module.queries.FUNNEL: funnel_facts()}
    else:
        mapping = {module.queries.PLAYER_DAILY: frames["main"],
                   module.queries.PLAYER_SEGMENTS: frames["segments"],
                   module.queries.RETENTION: frames["cohorts"]}
    monkeypatch.setattr(module, "query_df", lambda conn, sql, params=None: mapping[sql].copy())
    config = report.load_config(str(REPORTS / slug))
    ctx = ReportContext(config, slug, str(output))
    monkeypatch.setattr(ctx, "get_connection", lambda name: "fixture")
    monkeypatch.setattr(ctx, "read_source", lambda name: pd.DataFrame([
        {"month": "2026-11", "title": "Coral Quest", "budget_usd": 200.}]))
    report().generate(ctx)
    details = compute_details(ctx, mock_data=True)
    validation = validate_report(ctx, details=details)
    issues = [(c.id, c.message) for c in validation.checks if c.level in {"warn", "fail"}]
    assert not issues, issues
    render_report(ctx, str(output), auto_refresh=False, validation=validation, details=details)
    copy_vendor_tree(output)
    return ctx


def test_monthly_retention_uses_complete_mature_weighted_populations():
    _, module = report_module("monetization")
    quality = module.cohort_quality
    raw = retention_facts()
    cells = quality.monthly_cells(raw, "2026-11-07")
    # Six months x two independent platforms x five horizons.
    assert len(cells) == 60
    assert not cells.duplicated(["cohort", "day"]).any()
    july = cells[(cells.cohort_month == "2026-07") & (cells.platform == "iOS") & (cells.day == 7)].iloc[0]
    expected = [row for row in raw.to_dict("records") if row["cohort_date"].startswith("2026-07")
                and row["platform"] == "iOS" and row["day_number"] == 7]
    ratio = sum(row["retained_users"] for row in expected) / sum(row["cohort_size"] for row in expected)
    assert july.retention == pytest.approx(ratio)
    assert july.retention != pytest.approx(sum(row["retained_users"] / row["cohort_size"] for row in expected) / 31)
    november = cells[cells.cohort_month == "2026-11"]
    assert november.state.eq("not_mature").all() and november.retention.isna().all()
    october = cells[(cells.cohort_month == "2026-10") & (cells.platform == "iOS")]
    assert october.set_index("day").state.to_dict() == {
        1: "observed", 3: "observed", 7: "observed", 14: "not_mature", 30: "not_mature"}
    summary = quality.mature_summary(cells)
    assert len(summary) == 8  # June–September, two platforms.
    for day in (1, 7, 30):
        selected = cells[(cells.cohort_month < "2026-10") & (cells.day == day)]
        assert summary[f"retained_d{day}"].sum() == selected.retained.sum()
        assert summary[f"eligible_d{day}"].sum() == selected.eligible.sum()


def test_missing_zero_and_partial_cohorts_never_become_invented_rates():
    _, module = report_module("monetization")
    raw = retention_facts()
    july = raw.cohort_date.str.startswith("2026-07") & raw.platform.eq("iOS")
    raw.loc[july & raw.day_number.eq(1), "retained_users"] = 0
    raw = raw[~(july & raw.day_number.eq(7) & raw.cohort_date.eq("2026-07-02"))]
    cells = module.cohort_quality.monthly_cells(raw, "2026-11-07")
    group = cells[(cells.cohort_month == "2026-07") & cells.platform.eq("iOS")].set_index("day")
    assert group.loc[1, "state"] == "observed" and group.loc[1, "retention"] == 0
    assert group.loc[7, "state"] == "unavailable" and pd.isna(group.loc[7, "retention"])
    summary = module.cohort_quality.mature_summary(cells)
    assert "2026-07 · Coral Quest · iOS" not in summary.Cohort.tolist()


def test_native_sankey_edges_preserve_grain_and_reconcile_stage_growth():
    report, _ = report_module("conversion")
    raw = funnel_facts()
    edges, stages = report._build_flow(raw)
    assert [stage["key"] for stage in stages] == [
        "store_view", "item_view", "add_to_cart", "checkout", "purchase"]
    phone = edges[(edges.title == "Coral Quest") & (edges.device == "Phone") &
                  (edges.event_date == "2026-11-06")]
    assert phone.groupby("from_stage").users.sum().to_dict() == {
        "store_view": 100, "item_view": 60, "add_to_cart": 35, "checkout": 20}
    assert phone[(phone.to_stage == "purchase") & phone.to_node.eq("Purchase")].users.sum() == 12
    increased = raw[(raw.title == "Coral Quest") & (raw.device == "Phone") &
                    (raw.event_date == "2026-11-06")].copy()
    increased.loc[increased.step.eq("purchase"), "users"] = 25
    growth, _ = report._build_flow(increased)
    last = growth[growth.to_stage.eq("purchase")]
    assert last.users.tolist() == [20, 5]
    assert last.from_node.tolist() == ["Checkout", "Additional arrivals"]
    absent = increased[~increased.step.eq("checkout")]
    incomplete, declared = report._build_flow(absent)
    assert declared == stages
    assert not incomplete.to_stage.eq("purchase").any()
    empty, declared = report._build_flow(raw.iloc[:0])
    assert declared == stages and empty.empty
    chart = SankeyChart(growth, mode="edges", stages=stages, weight="users", unit="stage users",
                        source="from_node", target="to_node", source_stage="from_stage", target_stage="to_stage")
    assert chart.snapshot(growth)["total"] is None  # No whole-path population is inferred.


def test_reactive_line_colors_are_opt_in_and_copy_existing_author_list():
    frame = pd.DataFrame({"day": ["2026-11-06"], "revenue": [10], "users": [2]})

    def config(**options):
        ctx = RenderContext(DEFAULT_THEME)
        LineChart(frame, "day", "revenue", dataset_id="facts", **options).render_html(ctx)
        return next(iter(ctx.component_data.values()))

    assert config() == config(colors=None) == config(colors=[])
    colors = ["#123456"]
    supplied = config(colors=colors)
    assert supplied["colors"] == colors
    supplied["colors"].append("#abcdef")
    assert colors == ["#123456"]
    ratio = config(colors=colors, ratios=[{"numerator": "revenue", "denominator": "users", "label": "ARPU"}])
    assert ratio["colors"] == colors


def test_reactive_line_colors_survive_filters_themes_and_keep_category_precedence(tmp_path):
    from playwright.sync_api import sync_playwright

    frame = pd.DataFrame([
        {"day": day, "platform": platform, "revenue": revenue, "users": users}
        for day in ("2026-11-05", "2026-11-06")
        for platform, revenue, users in (("iOS", 20, 5), ("Android", 30, 10))
    ])
    ctx = ReportContext({"name": "Explicit reactive series colours", "annotations": False},
                        "series-colours", str(tmp_path))
    ctx.add_section("Series colours", [
        DataSource("facts", frame),
        FilterBar("facts", frame, filters=[{"column": "platform", "type": "toggle"}]),
        LineChart(frame, "day", ["revenue", "users"], "Value series", dataset_id="facts",
                  colors=["#123456"], forecast_series=[1]),
        LineChart(frame, "day", title="Ratio series", dataset_id="facts", colors=["#abcdef"],
                  ratios=[{"numerator": "revenue", "denominator": "users", "label": "ARPU"}]),
        LineChart(frame, "day", "revenue", "Category series", dataset_id="facts",
                  colors=["#123456"], stack_by="platform", category_colors={
                      "domain": "platform", "column": "platform", "slots": {"iOS": 0, "Android": 1},
                      "overrides": {"iOS": "#e15759", "Android": "#59a14f"}}),
    ])
    render_report(ctx, str(tmp_path), auto_refresh=False)
    copy_vendor_tree(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            for title in ("Value series", "Ratio series", "Category series"):
                page.get_by_text(title, exact=True).scroll_into_view_if_needed()
            page.wait_for_function("Object.keys(_chartInstances).length===3")
            inspect = """() => Object.fromEntries(Object.entries(_reportData.components)
                .filter(([id,c])=>c.chartType==='line').map(([id,c])=>[c.title,
                    _chartInstances[id].data.datasets.map(d=>({label:d.label,color:d.borderColor,
                        data:d.data,dash:d.borderDash}))]))"""
            first = page.evaluate(inspect)
            assert [r["color"] for r in first["Value series"]] == ["#123456", page.evaluate("getThemeColors().chart_colors[1]")]
            assert first["Value series"][1]["dash"] == [7, 4]
            assert first["Ratio series"][0]["color"] == "#abcdef"
            assert {r["label"]: r["color"] for r in first["Category series"]} == {"iOS": "#e15759", "Android": "#59a14f"}
            page.get_by_role("button", name="iOS", exact=True).click()
            page.wait_for_function("Object.values(_chartInstances).some(c=>c.data.datasets[0].data[0]===20)")
            page.evaluate("switchTheme(Object.keys(_themes).find(n=>n!==document.documentElement.dataset.theme))")
            page.wait_for_function("Object.values(_chartInstances).some(c=>c.data.datasets[0].borderColor==='#123456')")
            filtered = page.evaluate(inspect)
            assert filtered["Value series"][0]["color"] == "#123456"
            assert filtered["Value series"][0]["data"] == [20, 20]
            assert filtered["Value series"][1]["color"] == page.evaluate("getThemeColors().chart_colors[1]")
            assert filtered["Value series"][1]["dash"] == [7, 4]
            assert filtered["Ratio series"][0]["color"] == "#abcdef"
            assert filtered["Ratio series"][0]["data"] == [4, 4]
            assert filtered["Category series"][0]["color"] == "#e15759"
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("slug", ["monetization", "conversion", "player-overview"])
def test_native_business_report_desktop_filters_and_presentation(tmp_path, monkeypatch, slug):
    from playwright.sync_api import expect, sync_playwright

    ctx = build_report(slug, tmp_path, monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            if slug == "monetization":
                cohort = page.locator(".fw-cohort")
                cohort.scroll_into_view_if_needed()
                expect(cohort.locator("tbody tr")).to_have_count(5)
                expect(cohort).to_contain_text("Not mature")
                page.get_by_text("Cohort platform", exact=True).locator("..").get_by_role("button", name="Android", exact=True).click()
                page.wait_for_function("_fwFilterEngine.getFiltered('ua_quality_mature').every(r=>r.platform==='Android')")
                expect(cohort).to_contain_text("Android")
                with page.expect_download() as pending:
                    cohort.get_by_role("button", name="Download CSV", exact=True).click()
                exported = list(csv.DictReader(StringIO(Path(pending.value.path()).read_text(encoding="utf-8-sig"))))
                assert len(exported) == 30 and all("Android" in row["cohort"] for row in exported)
                assert all(row["forecast"] == "" for row in exported)
                assert {row["observed_state"] for row in exported} == {"observed", "not_mature"}
                # Native observed-only heatmap never receives nulls as zero.
                page.get_by_role("tab", name="Retention heat", exact=True).click()
                heat_id = page.evaluate("Object.entries(_reportData.components).find(([id,c])=>c.type==='heatmap'&&c.dataset_id==='ua_quality_observed')[0]")
                page.wait_for_function("id=>!!_chartInstances[id]", arg=heat_id)
                assert page.evaluate("_fwFilterEngine.getFiltered('ua_quality_observed').every(r=>r.state==='observed'&&r.retention!==null)")
                footer = page.locator("table tfoot").last
                expect(footer).to_contain_text("Total")
                independent = retention_facts()
                for horizon in (1, 7, 30):
                    selected = independent[(independent.cohort_date < "2026-10-01") &
                                           independent.platform.eq("Android") & independent.day_number.eq(horizon)]
                    expected = selected.retained_users.sum() / selected.cohort_size.sum() * 100
                    expect(footer).to_contain_text(f"{expected:.1f}%")
            elif slug == "conversion":
                flow = page.locator(".fw-sankey").first
                flow.scroll_into_view_if_needed()
                canvas = flow.locator("canvas").get_attribute("id")
                page.wait_for_function("id=>!!_chartInstances[id]?._fwSankey", arg=canvas)
                page.get_by_text("Device", exact=True).first.locator('xpath=ancestor::div[contains(@class,"fw-filter-item")][1]').locator(".ss-main").click()
                page.locator(".ss-option").get_by_text("Phone", exact=True).click()
                page.wait_for_function("_fwFilterEngine.getFiltered('flow_coral_quest').every(r=>r.device==='Phone')")
                expect(flow).to_contain_text("Illustrative links derived from aggregate stage counts")
                with page.expect_download() as pending:
                    flow.get_by_role("button", name="Download CSV", exact=True).click()
                exported = list(csv.DictReader(StringIO(Path(pending.value.path()).read_text(encoding="utf-8-sig"))))
                assert len(exported) == 8 and all(row["mode"] == "edges" for row in exported)
                assert sum(int(row["weight"]) for row in exported if row["source_stage_key"] == "store_view") == 200
                assert "_flow_coral_quest" not in json.loads((tmp_path / "data.json").read_text())
            else:
                grid = next(c for s in ctx.sections if s["title"] == "Daily Trends" for c in s["components"] if isinstance(c, Grid))
                assert grid.color_cycle == "theme"
                colors = page.evaluate("getThemeColors().chart_colors.slice(0,6)")
                assert len(set(colors)) == 6
                assert all(c.height == "compact" and c.header_summary["stat"] == "mean" for c in grid.children)
                for title, color in zip([c.title for c in grid.children], colors):
                    heading = page.get_by_text(title, exact=True).first
                    heading.scroll_into_view_if_needed()
                    expect(heading).to_be_visible()
                    cid = page.evaluate("title=>Object.entries(_reportData.components).find(([id,c])=>c.title===title&&c.chartType==='line')[0]", title)
                    page.wait_for_function("id=>!!_chartInstances[id]", arg=cid)
                    assert page.evaluate("id=>_chartInstances[id].data.datasets[0].borderColor", cid) == color
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
