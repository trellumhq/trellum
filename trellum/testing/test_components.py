"""Python component unit tests.

Instantiates every Component subclass with minimal data, calls render_html(),
and asserts structural correctness of the output and registered data.

Run with:  python3 -m pytest trellum/testing/test_components.py -v
"""

from __future__ import annotations

import pandas as pd
import pytest

from trellum.components import (
    ABCompare,
    AreaChart,
    BarChart,
    ComboChart,
    ComparisonTable,
    DataSource,
    DataTable,
    DateSelector,
    DoughnutChart,
    Dropdown,
    FilterBar,
    FunnelChart,
    Grid,
    HeatmapChart,
    KpiCard,
    KpiRow,
    LineChart,
    MiniKpi,
    Panel,
    PivotTable,
    RawHTML,
    RenderContext,
    ReportHeader,
    ReportMetadata,
    ScatterChart,
    Section,
    SplitPane,
    StackedBar,
    TabGroup,
    Toggle,
    TreemapChart,
    Visible,
)
from trellum.themes import DefaultTheme

# ── Helpers ──────────────────────────────────────────────────

def _make_ctx() -> RenderContext:
    return RenderContext(theme=DefaultTheme)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "event_date": ["2026-04-05", "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09"],
        "platform": ["iOS", "Android", "Web", "iOS", "Android"],
        "revenue": [100.0, 200.0, 150.0, 300.0, 250.0],
        "users": [50, 80, 60, 90, 70],
        "transactions": [10, 20, 15, 30, 25],
        "net_revenue": [90.0, 180.0, 135.0, 270.0, 225.0],
        "category": ["A", "B", "A", "B", "A"],
        "step": ["Step 1", "Step 2", "Step 3", "Step 4", "Step 5"],
        "value": [100, 80, 60, 40, 20],
    })


# ── Chart component tests ───────────────────────────────────

_CHART_SPECS = [
    ("LineChart", lambda df: LineChart(df, x="event_date", y="revenue", title="Test")),
    ("LineChart_multi_y", lambda df: LineChart(df, x="event_date", y=["revenue", "net_revenue"], title="Multi")),
    ("LineChart_ratios", lambda df: LineChart(df, x="event_date", y="revenue", title="Ratio",
                                              ratios=[{"numerator": "revenue", "denominator": "users", "label": "ARPU"}])),
    ("AreaChart", lambda df: AreaChart(df, x="event_date", y="revenue", title="Area")),
    ("AreaChart_stacked", lambda df: AreaChart(df, x="event_date", y=["revenue", "net_revenue"], stacked=True, title="Stacked Area")),
    ("BarChart", lambda df: BarChart(df, x="platform", y="revenue", title="Bar")),
    ("BarChart_horizontal", lambda df: BarChart(df, x="platform", y="revenue", title="HBar", horizontal=True)),
    ("BarChart_stacked", lambda df: BarChart(df, x="event_date", y="revenue", title="Stacked", stacked=True, y_cols=["revenue", "net_revenue"])),
    ("StackedBar", lambda df: StackedBar(df, x="event_date", y_cols=["revenue", "net_revenue"], title="SB")),
    ("ComboChart", lambda df: ComboChart(df, x="event_date", bar_cols=["revenue"], line_cols=["users"], title="Combo")),
    ("DoughnutChart", lambda df: DoughnutChart(df, label="platform", value="revenue", title="Doughnut")),
    ("HeatmapChart", lambda df: HeatmapChart(df, x="platform", y="category", value="revenue", title="Heatmap")),
    ("FunnelChart", lambda df: FunnelChart(df, label="step", value="value", title="Funnel")),
    ("TreemapChart", lambda df: TreemapChart(df, group_cols=["platform", "category"], value="revenue", title="Treemap")),
    ("ScatterChart", lambda df: ScatterChart(df, x="revenue", y="users", title="Scatter")),
    ("ScatterChart_bubble", lambda df: ScatterChart(df, x="revenue", y="users", size="transactions", color_by="platform", title="Bubble")),
]


@pytest.mark.parametrize("name,factory", _CHART_SPECS, ids=[s[0] for s in _CHART_SPECS])
def test_chart_render_html(name, factory):
    ctx = _make_ctx()
    df = _sample_df()
    comp = factory(df)
    html = comp.render_html(ctx)

    assert len(html) > 50, f"{name}: render_html returned suspiciously short output"
    assert "canvas" in html.lower() or "fw-chart" in html.lower() or "fw_c" in html, \
        f"{name}: expected canvas or chart container in HTML"

    assert len(ctx.component_data) >= 1, f"{name}: no data registered in ctx"
    registered = list(ctx.component_data.values())[0]
    assert "type" in registered, f"{name}: registered data missing 'type' key"


@pytest.mark.parametrize("name,factory", _CHART_SPECS, ids=[s[0] for s in _CHART_SPECS])
def test_chart_with_dataset_id(name, factory):
    ctx = _make_ctx()
    df = _sample_df()
    comp = factory(df)

    # Set dataset_id if the component supports it
    if hasattr(comp, "dataset_id"):
        comp.dataset_id = "test_ds"

    html = comp.render_html(ctx)
    assert html  # should not crash


# ── Chart class method tests ────────────────────────────────

_CHART_CLASSES = [
    LineChart, AreaChart, BarChart, StackedBar, ComboChart,
    DoughnutChart, HeatmapChart, FunnelChart, TreemapChart, ScatterChart,
]


@pytest.mark.parametrize("cls", _CHART_CLASSES, ids=[c.__name__ for c in _CHART_CLASSES])
def test_chart_client_js(cls):
    js = cls.client_js()
    assert len(js) > 100, f"{cls.__name__}: client_js() too short"
    assert "window._fwRenderers" in js, f"{cls.__name__}: client_js missing renderer registration"


@pytest.mark.parametrize("cls", _CHART_CLASSES, ids=[c.__name__ for c in _CHART_CLASSES])
def test_chart_css(cls):
    css = cls.css()
    assert isinstance(css, str)


@pytest.mark.parametrize("cls", _CHART_CLASSES, ids=[c.__name__ for c in _CHART_CLASSES])
def test_chart_cdn_deps(cls):
    deps = cls.cdn_deps()
    assert isinstance(deps, list)
    assert "chartjs" in deps or any("chart" in d for d in deps), \
        f"{cls.__name__}: expected Chart.js CDN dependency"


def test_heatmap_needs_matrix_cdn():
    deps = HeatmapChart.cdn_deps()
    assert "chartjs_matrix" in deps


# ── BarChart top_n tests ────────────────────────────────────

def test_barchart_top_n_static_collapses_tail_into_other():
    """BarChart(top_n=3) with 6 distinct x-values keeps top 3 + 'Other (3 items)'."""
    df = pd.DataFrame({
        "category": ["A", "B", "C", "D", "E", "F"],
        "count":    [100,  50,  80,  10,  20,   5],
    })
    ctx = _make_ctx()
    comp = BarChart(df, x="category", y="count", title="Top 3", top_n=3)
    comp.render_html(ctx)

    registered = list(ctx.component_data.values())[0]
    labels = registered["labels"]
    # Top 3 by count desc: A(100), C(80), B(50); tail D+E+F = 35.
    assert labels[:3] == ["A", "C", "B"]
    assert labels[3] == "Other (3 items)"
    # Sum of original column should equal sum after collapsing.
    data = registered["datasets"][0]["data"]
    assert sum(data) == df["count"].sum()
    # "Other" row should equal D+E+F.
    assert data[3] == 35


def test_barchart_top_n_smaller_than_n_no_other_bucket():
    """top_n larger than distinct count returns all rows, no 'Other' bar."""
    df = pd.DataFrame({"category": ["A", "B"], "count": [10, 5]})
    ctx = _make_ctx()
    comp = BarChart(df, x="category", y="count", title="t", top_n=10)
    comp.render_html(ctx)
    registered = list(ctx.component_data.values())[0]
    assert registered["labels"] == ["A", "B"]
    assert all("Other" not in lbl for lbl in registered["labels"])


def test_barchart_top_n_dataset_id_serializes_to_cfg():
    """In dataset_id mode top_n is forwarded to the JS runtime, with implicit sort=desc."""
    df = pd.DataFrame({"category": ["A", "B"], "count": [10, 5]})
    ctx = _make_ctx()
    comp = BarChart(df, x="category", y="count", title="t",
                    dataset_id="ds1", top_n=20)
    comp.render_html(ctx)
    cfg = list(ctx.component_data.values())[0]
    assert cfg["top_n"] == 20
    # top_n implies desc when no explicit sort was provided.
    assert cfg["sort"] == "desc"
    # Default is to show "Other" — flag only emitted when explicitly disabled.
    assert "top_n_show_other" not in cfg


def test_barchart_top_n_hide_other_static():
    """top_n_show_other=False drops the tail entirely (no 'Other' bar)."""
    df = pd.DataFrame({
        "category": ["A", "B", "C", "D", "E", "F"],
        "count":    [100,  50,  80,  10,  20,   5],
    })
    ctx = _make_ctx()
    comp = BarChart(df, x="category", y="count", title="t",
                    top_n=3, top_n_show_other=False)
    comp.render_html(ctx)
    registered = list(ctx.component_data.values())[0]
    labels = registered["labels"]
    assert len(labels) == 3
    assert all("Other" not in lbl for lbl in labels)
    # Sum should be ONLY top 3 (tail dropped), not the total.
    data = registered["datasets"][0]["data"]
    assert sum(data) == 230  # A(100) + C(80) + B(50)


def test_barchart_top_n_hide_other_dataset_id_emits_flag():
    """In dataset_id mode the flag is forwarded to JS as `top_n_show_other`."""
    df = pd.DataFrame({"category": ["A", "B"], "count": [10, 5]})
    ctx = _make_ctx()
    comp = BarChart(df, x="category", y="count", title="t",
                    dataset_id="ds1", top_n=20, top_n_show_other=False)
    comp.render_html(ctx)
    cfg = list(ctx.component_data.values())[0]
    assert cfg["top_n_show_other"] is False


# ── HeatmapChart per-cell aggregation tests ────────────────

def test_heatmap_static_aggregates_duplicate_cells():
    """Multiple rows sharing (x, y) should sum into a single matrix point."""
    df = pd.DataFrame({
        "hour":  ["00", "00", "00", "01"],
        "dow":   ["Mon", "Mon", "Mon", "Tue"],
        "count": [1,    1,    1,    5],
    })
    ctx = _make_ctx()
    comp = HeatmapChart(df, x="hour", y="dow", value="count", title="t")
    comp.render_html(ctx)
    cfg = list(ctx.component_data.values())[0]
    # 4 input rows but only 2 distinct (x, y) coordinates.
    assert len(cfg["cells"]) == 2
    cell_map = {(c["x"], c["y"]): c["v"] for c in cfg["cells"]}
    assert cell_map[("00", "Mon")] == 3.0
    assert cell_map[("01", "Tue")] == 5.0


def test_heatmap_dataset_id_js_aggregates_per_cell():
    """The reactive renderer JS must aggregate by (x, y) — not push one matrix
    point per row, which was the bug that forced static workarounds."""
    js = HeatmapChart.client_js()
    # The aggregator MUST collect into a cellMap (one entry per coord)
    # rather than pushing a fresh cell per row.
    assert "cellMap" in js, "live heatmap should aggregate into a cellMap"
    # Negative assertion against the old shape (cells.push per row).
    assert "cells.push({ x: xv, y: yv, v: r[cfg.value_col]" not in js, \
        "live heatmap still uses the old per-row push (no aggregation)"


def test_funnel_cdn():
    deps = FunnelChart.cdn_deps()
    assert isinstance(deps, list)


def test_treemap_cdn():
    deps = TreemapChart.cdn_deps()
    assert isinstance(deps, list)


# ── KPI tests ────────────────────────────────────────────────

def test_kpi_card_render():
    ctx = _make_ctx()
    card = KpiCard(label="Revenue", value="$1,234")
    html = card.render_html(ctx)
    assert "fw-kpi-card" in html
    registered = list(ctx.component_data.values())[0]
    assert registered["label"] == "Revenue"


def test_kpi_card_formats():
    for fmt in ["number", "currency", "chips", "percent", "ratio", "plain"]:
        ctx = _make_ctx()
        card = KpiCard(label="Test", value=1234.56, format=fmt)
        html = card.render_html(ctx)
        assert len(html) > 20


def test_kpi_row_render():
    ctx = _make_ctx()
    row = KpiRow([
        {"label": "Revenue", "value": 1000, "format": "currency"},
        {"label": "Users", "value": 500, "format": "number"},
    ])
    html = row.render_html(ctx)
    assert "fw-kpi-grid" in html
    assert len(ctx.component_data) >= 2
    labels = [d["label"] for d in ctx.component_data.values()]
    assert "Revenue" in labels
    assert "Users" in labels


def test_kpi_row_with_dataset_id():
    ctx = _make_ctx()
    row = KpiRow(
        [{"label": "Rev", "value": 100, "format": "currency"}],
        dataset_id="test_ds",
    )
    html = row.render_html(ctx)
    assert html
    assert len(ctx.component_data) >= 1


def test_kpi_row_live_delta_col():
    ctx = _make_ctx()
    row = KpiRow(
        [
            {
                "label": "Revenue",
                "format": "currency",
                "agg": "sum",
                "column": "revenue",
                "delta_col": "wow_pct",
                "delta_format": "percent",
            },
            {
                "label": "DAU",
                "format": "number",
                "agg": "sum",
                "column": "dau",
                "delta_col": "wow_dau",
                "delta_format": "percent",
                "delta_direction": "auto",
            },
        ],
        dataset_id="test_ds",
    )
    html = row.render_html(ctx)
    assert html
    registered = list(ctx.component_data.values())[0]
    assert registered["type"] == "kpi_row_live"
    kpis = registered["kpis"]
    assert kpis[0]["delta_col"] == "wow_pct"
    assert kpis[0]["delta_format"] == "percent"
    assert kpis[1]["delta_col"] == "wow_dau"
    assert kpis[1]["delta_direction"] == "auto"


def test_kpi_row_live_delta_in_client_js():
    js = KpiRow.client_js()
    assert "delta_col" in js
    assert "delta_format" in js
    assert "fw-kpi-delta" in js


def test_kpi_row_client_js():
    js = KpiRow.client_js()
    assert "window._fwRenderers" in js


def test_mini_kpi_render():
    ctx = _make_ctx()
    kpi = MiniKpi(label="DAU", value=10000, format="number")
    html = kpi.render_html(ctx)
    assert "fw-mini-kpi" in html
    registered = list(ctx.component_data.values())[0]
    assert registered["label"] == "DAU"


# ── Table tests ──────────────────────────────────────────────

def test_data_table_render():
    ctx = _make_ctx()
    df = _sample_df()
    table = DataTable(df, title="Test Table")
    html = table.render_html(ctx)
    assert "fw-table" in html or "fw_c" in html
    assert len(ctx.component_data) >= 1
    registered = list(ctx.component_data.values())[0]
    assert registered["type"] == "table"


def test_data_table_with_dataset_id():
    ctx = _make_ctx()
    df = _sample_df()
    table = DataTable(df, title="Live Table", dataset_id="ds1")
    html = table.render_html(ctx)
    assert html
    assert len(ctx.component_data) >= 1


def test_data_table_client_js():
    js = DataTable.client_js()
    assert len(js) > 50
    assert "window._fwRenderers" in js


def test_pivot_table_render():
    ctx = _make_ctx()
    df = _sample_df()
    pt = PivotTable(df, rows=["platform"], values="revenue", title="Pivot")
    html = pt.render_html(ctx)
    assert html
    assert len(ctx.component_data) >= 1


def test_comparison_table_render():
    ctx = _make_ctx()
    df = pd.DataFrame({
        "metric": ["Revenue", "Users", "ARPU"],
        "current": [1000, 500, 2.0],
        "previous": [900, 480, 1.875],
    })
    ct = ComparisonTable(df, title="Compare", value_col="current", compare_cols=["previous"])
    html = ct.render_html(ctx)
    assert html


def test_comparison_table_auto_conditional_formats():
    ctx = _make_ctx()
    df = pd.DataFrame({
        "metric": ["Revenue", "DAU"],
        "value": [1000, 500],
        "wow_pct": [5.2, -1.3],
        "mom_pct": [-2.1, 3.4],
    })
    ct = ComparisonTable(
        df, title="KPIs", value_col="value",
        compare_cols=["wow_pct", "mom_pct"],
    )
    html = ct.render_html(ctx)
    assert html
    registered = list(ctx.component_data.values())[0]
    cf = registered.get("conditionalFormats", {})
    assert "wow_pct" in cf
    assert "mom_pct" in cf
    assert any(r["op"] == ">" and r["value"] == 0 for r in cf["wow_pct"])
    assert any(r["op"] == "<" and r["value"] == 0 for r in cf["wow_pct"])


def test_comparison_table_explicit_conditional_formats():
    ctx = _make_ctx()
    df = pd.DataFrame({
        "metric": ["A"], "value": [100], "delta": [5.0],
    })
    custom_cf = {"delta": [{"op": ">=", "value": 10, "color": "blue"}]}
    ct = ComparisonTable(
        df, title="Custom", value_col="value",
        compare_cols=["delta"], conditional_formats=custom_cf,
    )
    html = ct.render_html(ctx)
    registered = list(ctx.component_data.values())[0]
    cf = registered.get("conditionalFormats", {})
    assert cf["delta"][0]["color"] == "blue"


def test_comparison_table_with_dataset_id():
    ctx = _make_ctx()
    df = pd.DataFrame({
        "metric": ["Rev"], "value": [100], "wow": [5.0],
    })
    ct = ComparisonTable(
        df, title="Live", value_col="value",
        compare_cols=["wow"], dataset_id="my_ds",
    )
    html = ct.render_html(ctx)
    assert html
    registered = list(ctx.component_data.values())[0]
    assert registered.get("dataset_id") == "my_ds"


# ── DataSource + FilterBar tests ─────────────────────────────

def test_data_source_render():
    ctx = _make_ctx()
    df = _sample_df()
    ds = DataSource("test_ds", df)
    html = ds.render_html(ctx)
    assert html
    assert len(ctx.component_data) >= 1
    data = list(ctx.component_data.values())[0]
    assert data["type"] == "data_source"


def test_data_source_no_section_wrap():
    ds = DataSource("x", _sample_df())
    assert ds._no_section_wrap is True


def test_filter_bar_render():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("test_ds", df, filters=[
        {"column": "platform", "label": "Platform"},
    ])
    html = fb.render_html(ctx)
    assert "Platform" in html or "platform" in html.lower()


def test_filter_bar_no_section_wrap():
    fb = FilterBar("x", _sample_df(), filters=[])
    assert fb._no_section_wrap is True


def test_filter_bar_date_range():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("ds", df, filters=[
        {"column": "event_date", "label": "Date Range", "type": "date_range"},
    ])
    html = fb.render_html(ctx)
    assert html


def test_filter_bar_client_js():
    js = FilterBar.client_js()
    assert len(js) > 50


def test_filter_bar_cdn_deps_includes_slider():
    deps = FilterBar.cdn_deps()
    assert "nouislider_js" in deps
    assert "nouislider_css" in deps


# ── SliderFilter tests ────────────────────────────────────────

def test_slider_render_range_mode():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("ds", df, filters=[
        {"column": "revenue", "label": "Revenue", "type": "slider"},
    ])
    html = fb.render_html(ctx)
    assert "fw-slider" in html
    assert 'data-mode="range"' in html
    assert "Revenue" in html


def test_slider_render_single_mode():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("ds", df, filters=[
        {"column": "value", "label": "Value", "type": "slider", "mode": "single"},
    ])
    html = fb.render_html(ctx)
    assert 'data-mode="single"' in html


def test_slider_build_config_range_bounds_and_step():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "revenue", {}, df)
    assert cfg["type"] == "slider"
    assert cfg["mode"] == "range"
    assert cfg["min"] == df["revenue"].min()
    assert cfg["max"] == df["revenue"].max()
    assert cfg["default_min"] == cfg["min"]
    assert cfg["default_max"] == cfg["max"]
    # Float column: step derived as (max - min) / 100.
    assert cfg["step"] == (cfg["max"] - cfg["min"]) / 100


def test_slider_build_config_integer_column_step_defaults_to_one():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "users", {}, df)
    assert cfg["step"] == 1


def test_slider_build_config_explicit_step_overrides_derivation():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "revenue", {"step": 5}, df)
    assert cfg["step"] == 5


def test_slider_build_config_default_min_max_overrides():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "revenue", {"default_min": 150, "default_max": 250}, df)
    assert cfg["default_min"] == 150
    assert cfg["default_max"] == 250
    # Full extent bounds are untouched by the default overrides.
    assert cfg["min"] == df["revenue"].min()
    assert cfg["max"] == df["revenue"].max()


def test_slider_build_config_empty_column_guard():
    """Empty/all-NaN column must not raise -- mirrors DateRangeFilter._compute_bounds."""
    from trellum.components.filters import get_filter
    df = pd.DataFrame({"revenue": pd.Series([], dtype="float64")})
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "revenue", {}, df)
    assert cfg["min"] == 0
    assert cfg["max"] == 0

    df_all_nan = pd.DataFrame({"revenue": [float("nan"), float("nan")]})
    cfg_nan = plugin.build_config("f0", "revenue", {}, df_all_nan)
    assert cfg_nan["min"] == 0
    assert cfg_nan["max"] == 0


def test_slider_build_config_single_mode_values():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "value", {"mode": "single"}, df)
    assert cfg["mode"] == "single"
    assert cfg["values"] == sorted(df["value"].unique().tolist())


# ── Ordinal slider tests (sliders over ordered categories) ────

def test_slider_ordinal_render_has_modifier_class_and_data_mode():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("ds", df, filters=[
        {"column": "platform", "label": "Tier", "type": "slider",
         "values": ["Web", "Android", "iOS"]},
    ])
    html = fb.render_html(ctx)
    assert "fw-filter-slider-item--ordinal" in html
    assert 'data-mode="range"' in html


def test_slider_ordinal_build_config_positions_and_full_span_defaults():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "platform", {
        "values": ["Web", "Android", "iOS"],
    }, df)
    assert cfg["type"] == "slider"
    assert cfg["ordinal"] is True
    assert cfg["values"] == ["Web", "Android", "iOS"]
    assert cfg["min"] == 0
    assert cfg["max"] == 2
    assert cfg["step"] == 1
    # No default_min/default_max given -> full span (first..last position).
    assert cfg["default_min"] == 0
    assert cfg["default_max"] == 2


def test_slider_ordinal_build_config_explicit_defaults_map_to_positions():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "platform", {
        "values": ["Web", "Android", "iOS"],
        "default_min": "Android", "default_max": "iOS",
    }, df)
    assert cfg["default_min"] == 1
    assert cfg["default_max"] == 2


def test_slider_ordinal_build_config_labels_passed_through():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "platform", {
        "values": ["Web", "Android", "iOS"],
        "labels": {"Web": "The Web", "Android": "Droid", "iOS": "Apple"},
    }, df)
    assert cfg["labels"] == {"Web": "The Web", "Android": "Droid", "iOS": "Apple"}


def test_slider_ordinal_build_config_single_mode_default_position():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "platform", {
        "values": ["Web", "Android", "iOS"],
        "mode": "single", "default": "iOS",
    }, df)
    assert cfg["mode"] == "single"
    assert cfg["default"] == 2


def test_slider_ordinal_build_config_single_mode_defaults_to_first_position():
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "platform", {
        "values": ["Web", "Android", "iOS"], "mode": "single",
    }, df)
    assert cfg["default"] == 0


def test_slider_ordinal_build_config_default_outside_values_falls_back_safely():
    """A default outside `values` is a validator FAIL (see test_validation.py),
    but build_config must not raise -- render defensively, same principle as
    the empty/NaN numeric column guard."""
    from trellum.components.filters import get_filter
    df = _sample_df()
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "platform", {
        "values": ["Web", "Android", "iOS"],
        "default_min": "does-not-exist", "default_max": "also-missing",
    }, df)
    assert cfg["default_min"] == 0
    assert cfg["default_max"] == 2


def test_slider_ordinal_build_config_works_on_non_numeric_column():
    """The whole point: no numeric dtype requirement when `values` is given."""
    from trellum.components.filters import get_filter
    df = _sample_df()
    assert not pd.api.types.is_numeric_dtype(df["step"].dtype)
    plugin = get_filter("slider")
    cfg = plugin.build_config("f0", "step", {
        "values": ["Step 1", "Step 2", "Step 3", "Step 4", "Step 5"],
    }, df)
    assert cfg["min"] == 0
    assert cfg["max"] == 4


def test_slider_ordinal_readout_uses_labels_with_fallback():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("ds", df, filters=[
        {"column": "platform", "label": "Tier", "type": "slider",
         "values": ["Web", "Android", "iOS"],
         "labels": {"Web": "The Web"},  # Android/iOS have no label -> fall back
         "default_min": "Web", "default_max": "Android"},
    ])
    html = fb.render_html(ctx)
    assert "The Web" in html   # labeled endpoint
    assert "Android" in html  # unlabeled endpoint falls back to raw value


def test_slider_ordinal_single_mode_readout():
    ctx = _make_ctx()
    df = _sample_df()
    fb = FilterBar("ds", df, filters=[
        {"column": "platform", "label": "Tier", "type": "slider",
         "values": ["Web", "Android", "iOS"], "mode": "single",
         "labels": {"iOS": "Apple"}, "default": "iOS"},
    ])
    html = fb.render_html(ctx)
    assert 'data-mode="single"' in html
    assert "Apple" in html


def test_slider_client_js_non_empty():
    from trellum.components.filters import get_filter
    plugin = get_filter("slider")
    assert len(plugin.client_js()) > 50


def test_slider_cdn_deps():
    from trellum.components.filters import get_filter
    plugin = get_filter("slider")
    assert plugin.cdn_deps() == ["nouislider_js", "nouislider_css"]


# ── Controls tests ───────────────────────────────────────────

def test_toggle_render():
    ctx = _make_ctx()
    t = Toggle(id="test_toggle", options=["A", "B", "C"])
    html = t.render_html(ctx)
    assert "A" in html and "B" in html and "C" in html


def test_toggle_default():
    t = Toggle(id="t", options=["X", "Y"])
    assert t.default == "X"


def test_dropdown_render():
    ctx = _make_ctx()
    d = Dropdown(id="dd", options=["Opt1", "Opt2"])
    html = d.render_html(ctx)
    assert "Opt1" in html


def test_tab_group_render():
    ctx = _make_ctx()
    tg = TabGroup(tabs=[
        {"label": "Tab A", "content": [RawHTML(html="<p>Content A</p>")]},
        {"label": "Tab B", "content": [RawHTML(html="<p>Content B</p>")]},
    ])
    html = tg.render_html(ctx)
    assert "Tab A" in html
    assert "Tab B" in html


def test_date_selector_render():
    ctx = _make_ctx()
    ds = DateSelector(id="date_sel")
    html = ds.render_html(ctx)
    assert "Today" in html or "Yesterday" in html


# ── Layout tests ─────────────────────────────────────────────

def test_section_render():
    ctx = _make_ctx()
    s = Section(title="My Section", children=[RawHTML(html="<p>hi</p>")])
    html = s.render_html(ctx)
    assert "My Section" in html


def test_section_collapsible():
    ctx = _make_ctx()
    s = Section(title="Collapse Me", children=[RawHTML(html="<p>body</p>")], collapsible=True)
    html = s.render_html(ctx)
    assert "Collapse Me" in html


def test_section_anchor_is_the_deep_link_target():
    """``anchor`` is the id ``?only=<id>`` and ``#<id>`` address. Without one
    the div carries no id at all, so nothing changes for existing reports."""
    ctx = _make_ctx()
    kids = [RawHTML(html="<p>body</p>")]
    assert 'id="metric-dau"' in Section("DAU", kids, anchor="metric-dau").render_html(ctx)
    assert 'id="metric-dau"' in Section(
        "DAU", kids, collapsible=True, anchor="metric-dau").render_html(ctx)
    assert '<div class="fw-section">' in Section("DAU", kids).render_html(ctx)


def test_panel_render():
    ctx = _make_ctx()
    p = Panel(children=[RawHTML(html="<p>inside panel</p>")])
    html = p.render_html(ctx)
    assert "inside panel" in html


def test_panel_with_title():
    ctx = _make_ctx()
    p = Panel(children=[RawHTML(html="<p>x</p>")], title="My Panel")
    html = p.render_html(ctx)
    assert "My Panel" in html


def test_grid_render():
    ctx = _make_ctx()
    g = Grid(children=[
        RawHTML(html="<p>Cell 1</p>"),
        RawHTML(html="<p>Cell 2</p>"),
    ], columns=2)
    html = g.render_html(ctx)
    assert "Cell 1" in html and "Cell 2" in html


def test_grid_min_width_lays_out_auto_fill_tiles():
    ctx = _make_ctx()
    html = Grid(children=[RawHTML(html="<p>a</p>")], columns=2,
                min_width=340).render_html(ctx)
    # auto-fill, not the fixed column count -- and the class grid.css keys
    # the tile treatment (short chart, no title, no breakdown toggle) off.
    assert "repeat(auto-fill, minmax(340px, 1fr))" in html
    assert "fw-grid-auto" in html
    plain = Grid(children=[RawHTML(html="<p>a</p>")], columns=2).render_html(ctx)
    assert "repeat(2, 1fr)" in plain and "fw-grid-auto" not in plain


def test_grid_card_mode():
    ctx = _make_ctx()
    g = Grid(children=[RawHTML(html="<p>card</p>")], card=True)
    html = g.render_html(ctx)
    assert html


def test_split_pane_render():
    ctx = _make_ctx()
    sp = SplitPane(
        left=RawHTML(html="<p>left side</p>"),
        right=RawHTML(html="<p>right side</p>"),
    )
    html = sp.render_html(ctx)
    assert "fw-split" in html
    assert "left side" in html and "right side" in html


def test_raw_html_render():
    ctx = _make_ctx()
    r = RawHTML(html="<div class='custom'>Hello</div>", js="console.log('test');")
    html = r.render_html(ctx)
    assert "custom" in html
    assert "Hello" in html


def test_raw_html_with_data():
    ctx = _make_ctx()
    r = RawHTML(html="<p>data</p>", data_key="my_data", data={"x": [1, 2, 3]})
    html = r.render_html(ctx)
    assert html
    assert "my_data" in ctx.raw_data_blocks


def test_visible_render():
    ctx = _make_ctx()
    v = Visible(
        children=[RawHTML(html="<p>visible content</p>")],
        toggle_target="my_toggle",
        toggle_value="A",
    )
    html = v.render_html(ctx)
    assert "visible content" in html
    assert "my_toggle" in html or "data-toggle" in html


# ── ABCompare tests ──────────────────────────────────────────

def test_ab_compare_render():
    ctx = _make_ctx()
    ab = ABCompare(
        rows=[
            {"key": "revenue", "label": "Revenue", "control": 1000, "test": 1100, "format": "currency"},
            {"key": "users", "label": "Users", "control": 500, "test": 520, "format": "number"},
        ],
        title="A/B Test",
        control_label="Control",
        test_label="Variant A",
    )
    html = ab.render_html(ctx)
    assert "A/B Test" in html or "ab" in html.lower()
    assert len(ctx.component_data) >= 1


def test_ab_compare_client_js():
    js = ABCompare.client_js()
    assert len(js) > 100


# ── Cross-component: collect_assets ──────────────────────────

def test_collect_assets():
    ctx = _make_ctx()
    df = _sample_df()

    components = [
        LineChart(df, x="event_date", y="revenue", title="Line"),
        BarChart(df, x="platform", y="users", title="Bar"),
        KpiRow([{"label": "Rev", "value": 100, "format": "currency"}]),
        DataTable(df, title="Table"),
    ]
    for c in components:
        ctx.render_child(c)

    css, js = ctx.collect_assets()
    assert len(css) > 0, "No CSS collected from components"
    assert len(js) > 0, "No JS collected from components"
    assert "window._fwRenderers" in js


def test_collect_cdn_deps():
    ctx = _make_ctx()
    df = _sample_df()

    ctx.render_child(LineChart(df, x="event_date", y="revenue", title="L"))
    ctx.render_child(HeatmapChart(df, x="platform", y="category", value="revenue", title="H"))

    deps = ctx.collect_cdn_deps()
    assert "chartjs" in deps
    assert "chartjs_matrix" in deps


# ── Render context tests ────────────────────────────────────

def test_ctx_next_id_increments():
    ctx = _make_ctx()
    id1 = ctx.next_id()
    id2 = ctx.next_id()
    assert id1 != id2
    assert id1.startswith("fw_c")


def test_ctx_register_data():
    ctx = _make_ctx()
    ctx.register("test_1", {"type": "chart", "x": "date"})
    assert "test_1" in ctx.component_data
    assert ctx.component_data["test_1"]["type"] == "chart"


def test_ctx_reset():
    ctx = _make_ctx()
    ctx.register("x", {"type": "t"})
    ctx.add_raw_js("alert(1)")
    ctx.add_raw_data("k", {"v": 1})
    ctx.reset()
    assert len(ctx.component_data) == 0
    assert len(ctx.raw_js_blocks) == 0
    assert len(ctx.raw_data_blocks) == 0


def test_ctx_render_child_string():
    ctx = _make_ctx()
    result = ctx.render_child("<p>raw string</p>")
    assert result == "<p>raw string</p>"


def test_ctx_render_child_unknown():
    ctx = _make_ctx()
    result = ctx.render_child(42)
    assert "Unknown" in result


# ── ReportHeader tests ──────────────────────────────────────
# Covers the compact title and subtitle layout and the dedicated metadata
# strip that now owns freshness.

def test_report_header_titleblock_wraps_title_and_meta():
    ctx = _make_ctx()
    header = ReportHeader(name="Live Ops Monitor", subtitle="A test subtitle")
    html = header.render_html(ctx)
    assert '<div class="fw-titleblock">' in html
    assert "<h1>Live Ops Monitor</h1>" in html
    assert '<div class="fw-meta">' in html
    # h1 and fw-meta both live inside fw-titleblock, ahead of fw-meta's close.
    titleblock_start = html.index('<div class="fw-titleblock">')
    meta_start = html.index('<div class="fw-meta">')
    assert titleblock_start < meta_start


def test_report_header_keeps_subtitle_separate_from_freshness():
    ctx = _make_ctx()
    header = ReportHeader(name="Report", subtitle="One FilterBar drives a KPI row")
    html = header.render_html(ctx)
    meta_start = html.index('<div class="fw-meta">')
    meta_end = html.index("</div>", meta_start)
    meta_html = html[meta_start:meta_end]
    assert "One FilterBar drives a KPI row" in meta_html
    assert 'id="fwFreshness"' not in html


def test_report_metadata_always_renders_freshness_hook():
    # Freshness is populated client-side from data.json, even when no optional
    # values were supplied through ctx.set_header(meta=...).
    ctx = _make_ctx()
    html = ReportMetadata().render_html(ctx)
    assert '<span class="fw-freshness" id="fwFreshness"></span>' in html


def test_report_metadata_escapes_labels_and_values_and_preserves_zero():
    html = ReportMetadata(values={
        '<Last event>': 'A & B <latest>', 'Rows': 0,
        'Omit None': None, 'Omit blank': '   ',
    }).render_html(_make_ctx())
    assert '&lt;Last event&gt;' in html
    assert 'A &amp; B &lt;latest&gt;' in html
    assert '>0</span>' in html
    assert 'Omit None' not in html
    assert 'Omit blank' not in html


def test_report_header_empty_subtitle_renders_empty_span():
    ctx = _make_ctx()
    header = ReportHeader(name="Report", subtitle="")
    html = header.render_html(ctx)
    assert '<span class="fw-subtitle"></span>' in html


def test_report_header_nav_html_wrapped_in_nav_group():
    ctx = _make_ctx()
    mark = '<svg viewBox="0 0 40 40" width="15" height="15"></svg>'
    header = ReportHeader(name="Report", nav_html=mark)
    html = header.render_html(ctx)
    assert f'<div class="fw-nav-group">{mark}</div>' in html


def test_report_header_no_nav_group_when_nav_html_empty():
    ctx = _make_ctx()
    header = ReportHeader(name="Report")
    html = header.render_html(ctx)
    assert "fw-nav-group" not in html


def test_report_header_slow_help_affordance_untouched():
    # The framework's own "Slow?" help control is out of scope for this
    # redesign (its demotion is a portal-side concern) -- it must still
    # render as-is in the right-hand controls.
    ctx = _make_ctx()
    header = ReportHeader(name="Report")
    html = header.render_html(ctx)
    assert 'id="fwHelpBtn"' in html
    assert ">Slow?<" in html


def test_report_header_css_enlarges_brand_mark():
    css = ReportHeader.css()
    assert ".fw-nav-group > svg" in css
    assert "width: 22px" in css


def test_report_header_css_keeps_compact_title_without_wrapping():
    css = ReportHeader.css()
    # Keep the report title compact even when a theme uses a larger heading
    # scale; the header row remains a single line on desktop.
    assert "flex-wrap: nowrap" in css
    assert "font-size: 12px" in css


def test_report_header_css_mobile_two_row_stack():
    css = ReportHeader.css()
    mobile_start = css.index("@media (max-width: 640px)")
    mobile_css = css[mobile_start:]
    assert ".fw-titleblock { flex-direction: column" in mobile_css


def test_report_metadata_client_js_shows_age_label_and_preserves_thresholds():
    js = ReportMetadata.client_js()
    assert "el.title = fullTxt" in js
    assert "'Updated ' + h + 'h ago'" in js
    assert "hh + mh + ah" in js
    assert "el.innerHTML = '<span class=\"fw-fresh-dot\"></span>' + fullTxt" in js
    assert "rs * 4" in js and "rs * 2" in js
