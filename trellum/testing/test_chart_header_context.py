"""Optional authored chart header context keeps default chart output stable."""
from __future__ import annotations

import importlib.util
import inspect
from dataclasses import replace
from html import escape
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import (
    AreaChart,
    BarChart,
    ComboChart,
    DoughnutChart,
    FunnelChart,
    HeatmapChart,
    LineChart,
    RenderContext,
    ScatterChart,
    StackedBar,
    TreemapChart,
)
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DefaultTheme
from trellum.validation import validate_report


@pytest.fixture
def frame():
    return pd.DataFrame({
        "day": ["2026-10-01", "2026-10-02"], "label": ["A", "B"],
        "value": [10, 20], "other": [3, 5],
    })


def _charts(df):
    return [
        LineChart(df, "day", "value", "Line"),
        AreaChart(df, "day", "value", "Area"),
        BarChart(df, "label", "value", "Bar"),
        StackedBar(df, "day", ["value"], "Stacked"),
        ComboChart(df, "day", ["value"], ["other"], "Combo"),
        DoughnutChart(df, "label", "value", "Doughnut"),
        HeatmapChart(df, "day", "label", "value", "Heatmap"),
        FunnelChart(df, "label", "value", "Funnel"),
        TreemapChart(df, ["label"], "value", "Treemap"),
        ScatterChart(df, "value", "other", title="Scatter"),
    ]


@pytest.mark.parametrize("index", range(10))
def test_header_context_is_keyword_only_escaped_and_describes_canvas(frame, index):
    chart = _charts(frame)[index]
    assert inspect.signature(type(chart)).parameters["header_context"].kind is inspect.Parameter.KEYWORD_ONLY
    chart.header_context = '<img src=x onerror="alert(1)"> & "literal"'
    ctx = RenderContext(theme=DefaultTheme)
    html = chart.render_html(ctx)
    cid = next(iter(ctx._component_data))
    assert f'id="{cid}-header-context"' in html
    assert escape(chart.header_context, quote=True) in html
    assert "<img src=x" not in html
    assert f'aria-describedby="{cid}-header-context"' in html


@pytest.mark.parametrize("context", [None, "", " \n\t "])
def test_missing_or_blank_context_adds_no_markup_or_spacing(frame, context):
    for chart in _charts(frame):
        baseline_ctx = RenderContext(theme=DefaultTheme)
        default_html = chart.render_html(baseline_ctx)
        chart.header_context = context
        ctx = RenderContext(theme=DefaultTheme)
        with_context_html = chart.render_html(ctx)
        assert with_context_html == default_html
        assert all("header_context" not in config for config in ctx._component_data.values())


@pytest.mark.parametrize("factory", [
    lambda df: LineChart(df, "day", "value", "Line"),
    lambda df: AreaChart(df, "day", "value", "Area"),
    lambda df: BarChart(df, "label", "value", "Bar"),
    lambda df: StackedBar(df, "day", ["value"], "Stacked"),
    lambda df: ComboChart(df, "day", ["value"], ["other"], "Combo"),
    lambda df: DoughnutChart(df, "label", "value", "Doughnut"),
    lambda df: HeatmapChart(df, "day", "label", "value", "Heatmap"),
    lambda df: FunnelChart(df, "label", "value", "Funnel"),
    lambda df: TreemapChart(df, ["label"], "value", "Treemap"),
    lambda df: ScatterChart(df, "value", "other", title="Scatter"),
])
def test_non_string_context_is_rejected_at_construction(frame, factory):
    with pytest.raises(ValueError, match="header_context must be text or None"):
        replace(factory(frame), header_context=object())


def test_invalid_context_and_legacy_positions(frame):
    for chart in _charts(frame):
        with pytest.raises(ValueError, match="header_context must be text or None"):
            chart.header_context = object()
            chart.render_html(RenderContext(theme=DefaultTheme))
    line = LineChart(frame, "day", "value", "Line", None, None, True, None,
                     None, False, None, None, None, None, None, None, None,
                     None, None, False, "compact", None)
    assert line.height == "compact"
    assert line.header_context is None


def test_titleless_legacy_branches_and_custom_combo_height_keep_context(frame):
    static_stack = StackedBar(frame, "day", ["value"], "Suppressed", header_context="Units")
    stack_html = static_stack.render_html(RenderContext(theme=DefaultTheme))
    assert "fw-chart-title" not in stack_html and "Units" in stack_html
    donut = DoughnutChart(frame, "label", "value", "Not emitted", header_context="Units")
    donut_html = donut.render_html(RenderContext(theme=DefaultTheme))
    assert "fw-chart-title" not in donut_html and donut_html.index("Units") < donut_html.index("fw-doughnut-layout")
    combo = ComboChart(frame, "day", ["value"], ["other"], "Custom", height=247,
                       header_context="Units")
    combo_html = combo.render_html(RenderContext(theme=DefaultTheme))
    assert 'height: 247px' in combo_html and "fw-chart-header-context" in combo_html
    assert "fw-chart-container fw-line" not in combo_html


def test_explicit_dataset_id_wins_over_static_true(frame):
    chart = BarChart(frame, "label", "value", "Bound", dataset_id="bound", static=True,
                     header_context="Supplied description")
    ctx = RenderContext(theme=DefaultTheme)
    chart.render_html(ctx)
    assert ctx._component_data[next(iter(ctx._component_data))]["dataset_id"] == "bound"


def test_context_showcase_passes_full_report_diagnostics(tmp_path):
    path = (Path(__file__).resolve().parents[1] / "demo" / "reports"
            / "component-showcase" / "examples_chart_context.py")
    spec = importlib.util.spec_from_file_location("examples_chart_context", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={"name": "Header context", "studio": "test",
                                "category": "Test", "description": (
                                    "Synthetic chart examples for daily orders, revenue units, "
                                    "and authored panel context across report filters.")},
                        slug="chart-header-context", output_dir=str(tmp_path))
    module.add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, [
        (check.id, check.level, check.message)
        for check in result.checks if check.level in {"warn", "fail"}
    ]
