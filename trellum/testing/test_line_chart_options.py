"""LineChart ``height`` and ``header_summary``: markup, config and validation."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re

import pandas as pd
import pytest

from trellum.components import Grid, LineChart, RenderContext
from trellum.components.charts.base import _chart_container_html
from trellum.components.charts.line_area import line_option_problems
from trellum.themes import DefaultTheme
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult

_RATIOS = [{"numerator": "revenue", "denominator": "users", "label": "ARPU"}]


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "day": ["2026-04-05", "2026-04-06", "2026-04-07"],
        "revenue": [100.0, 200.0, 150.0],
        "users": [50, 80, 60],
    })


def _render(chart: LineChart) -> tuple[str, dict]:
    ctx = RenderContext(theme=DefaultTheme)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def _classes(html: str, prefix: str) -> list[str]:
    return re.findall(rf'<div class="({prefix}[^"]*)"', html)


# ── Appended fields keep existing calls working ──────────────

def test_new_fields_come_last_so_positional_calls_are_unchanged():
    names = [f.name for f in dataclasses.fields(LineChart) if f.init and not f.kw_only]
    assert names[-2:] == ["height", "header_summary"]
    assert names[:5] == ["df", "x", "y", "title", "y_labels"]
    chart = LineChart(_df(), "day", "revenue", "Revenue")
    assert (chart.height, chart.header_summary) == ("balanced", None)


def test_default_markup_has_no_summary_and_keeps_the_title_markup():
    html, cfg = _render(LineChart(_df(), x="day", y="revenue", title="Revenue"))
    assert "data-chart-summary" not in html and "fw-chart-head" not in html
    assert "header_summary" not in cfg
    assert 'class="fw-chart-title fw-line-chart-title"' in html
    assert _classes(html, "fw-chart-container") == ["fw-chart-container fw-line-chart fw-line-balanced"]


def test_shared_container_helper_output_is_unchanged_for_other_charts():
    assert _chart_container_html("fw_c1", "Bar") == (
        '<div class="fw-chart-title" style="font-size:13px;color:var(--text-secondary);'
        'margin-bottom:4px;">Bar</div><div class="fw-chart-container">'
        '<canvas id="fw_c1"></canvas><button class="fw-chart-dl" data-csv-chart="fw_c1" '
        'title="Download CSV"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14">'
        '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"></path>'
        '<polyline points="7 10 12 15 17 10"></polyline>'
        '<line x1="12" y1="15" x2="12" y2="3"></line></svg></button></div>'
    )


@pytest.mark.parametrize("height", ["balanced", "compact", "roomy"])
def test_height_selects_the_size_class_on_every_render_path(height):
    variants = [
        LineChart(_df(), x="day", y="revenue", title="T", height=height),
        LineChart(_df(), x="day", y="revenue", title="T", height=height, dataset_id="d"),
        LineChart(_df(), x="day", y="revenue", title="T", height=height, dataset_id="d",
                  ratios=_RATIOS),
        LineChart(_df(), x="day", y="revenue", title="T", height=height, dataset_id="d",
                  stack_by_options={"Total": "", "Day": "day"}),
        LineChart(_df(), x="day", y="revenue", title="T", height=height, dataset_id="d",
                  ratios=_RATIOS, stack_by_options={"Total": "", "Day": "day"}),
    ]
    for chart in variants:
        html, _ = _render(chart)
        assert f"fw-line-chart fw-line-{height}" in html, chart
        assert "fw-line-chart-title" in html


def test_other_chart_types_are_not_resized():
    from trellum.components import AreaChart, BarChart
    ctx = RenderContext(theme=DefaultTheme)
    for chart in (AreaChart(_df(), x="day", y="revenue", title="A"),
                  BarChart(_df(), x="day", y="revenue", title="B")):
        html = chart.render_html(ctx)
        assert "fw-line-" not in html


# ── Summary configuration ────────────────────────────────────

_SUMMARY = {"stat": "mean", "series": 0, "label": "Daily avg", "format": "number"}


def test_summary_is_registered_in_static_live_and_ratio_configs():
    static_html, static_cfg = _render(
        LineChart(_df(), x="day", y="revenue", title="T", header_summary=_SUMMARY))
    live_html, live_cfg = _render(
        LineChart(_df(), x="day", y="revenue", title="T", dataset_id="d", header_summary=_SUMMARY))
    ratio_html, ratio_cfg = _render(
        LineChart(_df(), x="day", y="revenue", title="T", dataset_id="d", ratios=_RATIOS,
                  header_summary={"stat": "last"}))
    for html in (static_html, live_html, ratio_html):
        assert re.search(r'<div class="fw-chart-head">.*data-chart-summary="fw_c1"', html)
    assert static_cfg["header_summary"] == _SUMMARY
    assert live_cfg["header_summary"] == _SUMMARY
    assert ratio_cfg["header_summary"] == {"stat": "last", "series": 0, "label": "Latest"}
    json.dumps([static_cfg, live_cfg, ratio_cfg])


def test_summary_markup_carries_no_label_text_and_defaults_come_from_the_chart():
    nasty = '<img src=x onerror="alert(1)">'
    html, cfg = _render(LineChart(
        _df(), x="day", y="revenue", title="T", dataset_id="d", y_format="currency",
        header_summary={"stat": "sum", "label": nasty}))
    assert nasty not in html and "onerror" not in html
    assert cfg["header_summary"]["label"] == nasty  # data; the runtime sets textContent
    assert "format" not in cfg["header_summary"]  # the runtime uses the chart's own value format
    assert cfg["header_summary"]["series"] == 0


def test_summary_without_title_still_renders_the_header():
    html, _ = _render(LineChart(_df(), x="day", y="revenue", header_summary=_SUMMARY))
    assert 'class="fw-chart-head"' in html and "fw-chart-title" not in html


def test_title_markup_inside_the_header_matches_the_plain_title():
    plain, _ = _render(LineChart(_df(), x="day", y="revenue", title="<b>Revenue</b>"))
    head, _ = _render(LineChart(_df(), x="day", y="revenue", title="<b>Revenue</b>",
                                header_summary=_SUMMARY))
    assert "<b>Revenue</b></div>" in plain and "<b>Revenue</b></div>" in head


def test_forecast_series_shape_resolution_and_empty_payload_compatibility():
    frame = _df()
    frame["cost"] = [20, 30, 25]
    baseline = _render(LineChart(frame, "day", ["revenue", "cost"], "T"))[1]
    for declaration in (None, [], ()):
        cfg = _render(LineChart(frame, "day", ["revenue", "cost"], "T",
                                forecast_series=declaration))[1]
        assert cfg == baseline
    assert inspect.signature(LineChart).parameters["forecast_series"].kind == inspect.Parameter.KEYWORD_ONLY
    assert [f.name for f in dataclasses.fields(LineChart) if f.init and not f.kw_only][-2:] == ["height", "header_summary"]

    labels = ["Revenue", "Cost"]
    for declaration in ([1], ("Cost",), [0, "Cost"]):
        _, cfg = _render(LineChart(frame, "day", ["revenue", "cost"], "T",
                                   y_labels=labels, forecast_series=declaration, static=True))
        assert [i for i, ds in enumerate(cfg["datasets"]) if ds.get("_fwForecast")] == (
            [0, 1] if len(declaration) == 2 else [1])
    _, static_cfg = _render(LineChart(frame, "day", ["revenue", "cost"], "T",
                                      y_labels=labels, forecast_series=[1], static=True))
    assert "forecast_series" not in static_cfg
    assert static_cfg["datasets"][1]["borderDash"] == [7, 4]
    assert "_fwForecast" not in static_cfg["datasets"][0]
    _, bound_cfg = _render(LineChart(frame, "day", ["revenue", "cost"], "T",
                                     y_labels=labels, forecast_series=["Cost"],
                                     dataset_id="d", static=True))
    assert bound_cfg["dataset_id"] == "d" and bound_cfg["forecast_series"] == [1]
    _, ratio_cfg = _render(LineChart(frame, "day", "revenue", "T", dataset_id="d",
        ratios=[{"numerator": "revenue", "denominator": "users", "label": "ARPU"}],
        forecast_series=["ARPU"]))
    assert ratio_cfg["forecast_series"] == [0]


@pytest.mark.parametrize("value", ["Forecast", 1, True, [1.0], [None], {1}, [True]])
def test_invalid_forecast_series_declaration_shape(value):
    with pytest.raises(ValueError, match="forecast_series"):
        LineChart(_df(), "day", "revenue", forecast_series=value)


@pytest.mark.parametrize("declaration,labels,error", [
    ([3], ["Revenue", "Cost"], "outside"),
    (["Nope"], ["Revenue", "Cost"], "no series"),
    ([0], ["Revenue", "Revenue"], "caption is shared"),
    ([0, "Revenue"], ["Revenue", "Cost"], "more than once"),
    (["Revenue"], ["Revenue", "Revenue"], "more than one"),
])
def test_forecast_resolution_rejects_invalid_and_mutated_declarations(declaration, labels, error):
    chart = LineChart(_df(), "day", ["revenue", "users"], y_labels=labels)
    chart.forecast_series = declaration
    with pytest.raises(ValueError, match=error):
        chart.render_html(RenderContext(theme=DefaultTheme))


def test_forecast_mutable_inputs_are_copied_and_stack_splits_are_rejected_only_when_enabled():
    declaration = [1]
    chart = LineChart(_df(), "day", ["revenue", "users"], forecast_series=declaration)
    declaration.clear()
    assert chart.forecast_series == [1]
    assert LineChart(_df(), "day", "revenue", dataset_id="d", stack_by="platform",
                     forecast_series=[])
    with pytest.raises(ValueError, match="dynamic"):
        LineChart(_df(), "day", "revenue", dataset_id="d", stack_by="platform",
                  forecast_series=[0]).render_html(RenderContext(theme=DefaultTheme))


def test_forecast_selection_uses_actual_static_and_bound_caption_fallbacks():
    _, bound = _render(LineChart(_df(), "day", ["revenue", "users"],
        dataset_id="d", y_labels=["Revenue"], forecast_series=["users"]))
    assert bound["forecast_series"] == [1]
    _, blank = _render(LineChart(_df(), "day", ["revenue", "users"],
        static=True, y_labels=["Revenue", ""], forecast_series=["Series 2"]))
    assert blank["datasets"][1]["_fwForecast"]
    with pytest.raises(ValueError, match="caption is shared"):
        _render(LineChart(_df(), "day", ["revenue", "users"], static=True,
            y_labels=["Series 2", ""], forecast_series=[1]))
    with pytest.raises(ValueError, match="outside"):
        _render(LineChart(_df(), "day", ["revenue", "users"], static=True,
            y_labels=["Revenue"], forecast_series=[1]))


def test_forecast_option_preserves_subclass_positional_fields():
    @dataclasses.dataclass
    class Child(LineChart):
        extra: str = "child"

    assert inspect.signature(Child).parameters["extra"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert inspect.signature(Child).parameters["forecast_series"].kind == inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("kwargs", [
    {"height": "tall"},
    {"height": 220},
    {"height": []},
    {"header_summary": {"stat": "mean", 1: True, "extra": True}},
    {"header_summary": "mean"},
    {"header_summary": {}},
    {"header_summary": {"stat": "median"}},
    {"header_summary": {"stat": "mean", "extra": 1}},
    {"header_summary": {"stat": "mean", "format": "hex"}},
    {"header_summary": {"stat": "mean", "label": 5}},
    {"header_summary": {"stat": "mean", "label": "x" * 61}},
    {"header_summary": {"stat": "mean", "series": 1}},
    {"header_summary": {"stat": "mean", "series": -1}},
    {"header_summary": {"stat": "mean", "series": True}},
    {"header_summary": {"stat": "mean", "series": 1.0}},
    {"header_summary": {"stat": "mean", "series": "Nope"}},
    {"header_summary": {"stat": "mean"}, "stack_by": "platform"},
    {"header_summary": {"stat": "mean"}, "stack_by_options": {"Total": "", "Day": "day"}},
    {"header_summary": {"stat": "sum"}, "dataset_id": "d", "ratios": _RATIOS},
])
def test_unusable_options_are_rejected(kwargs):
    with pytest.raises(ValueError):
        LineChart(_df(), x="day", y="revenue", **kwargs)


def test_several_series_require_an_explicit_choice_by_position_or_label():
    y = ["revenue", "users"]
    with pytest.raises(ValueError, match="series is required"):
        LineChart(_df(), x="day", y=y, header_summary={"stat": "mean"})
    LineChart(_df(), x="day", y=y, header_summary={"stat": "mean", "series": 1})
    LineChart(_df(), x="day", y=y, y_labels=["Rev", "Users"],
              header_summary={"stat": "mean", "series": "Users"})
    with pytest.raises(ValueError, match="matches no series"):
        LineChart(_df(), x="day", y=y, y_labels=["Rev", "Users"],
                  header_summary={"stat": "mean", "series": "Revenue"})
    with pytest.raises(ValueError, match="more than one"):
        LineChart(_df(), x="day", y=y, y_labels=["Same", "Same"],
                  header_summary={"stat": "mean", "series": "Same"})


def test_ratio_series_are_selected_among_the_ratios_not_the_columns():
    two = _RATIOS + [{"numerator": "users", "denominator": "revenue", "label": "Inverse"}]
    LineChart(_df(), x="day", y="", dataset_id="d", ratios=two,
              header_summary={"stat": "mean", "series": "Inverse"})
    with pytest.raises(ValueError, match="series is required"):
        LineChart(_df(), x="day", y="", dataset_id="d", ratios=two,
                  header_summary={"stat": "mean"})
    with pytest.raises(ValueError):
        LineChart(_df(), x="day", y="", dataset_id="d", ratios=two,
                  header_summary={"stat": "mean", "series": 2})


@pytest.mark.parametrize("stat", ["mean", "sum", "last", "min", "max"])
def test_every_statistic_is_accepted(stat):
    assert not line_option_problems(
        LineChart(_df(), x="day", y="revenue", header_summary={"stat": stat}))


# ── Validator ────────────────────────────────────────────────

def test_validator_reports_options_changed_after_construction():
    chart = LineChart(_df(), x="day", y="revenue", header_summary=_SUMMARY)
    chart.height = "huge"
    chart.header_summary = {"stat": "mean", "series": 3}
    good = LineChart(_df(), x="day", y="revenue", header_summary=_SUMMARY)
    other = Grid(children=[good])  # not a LineChart: ignored
    result = ValidationResult(slug="t")
    _check_chart_options([(chart, "Sec"), (good, "Sec"), (other, "Sec")], result)
    assert [(c.id, c.level, c.section) for c in result.checks] == [
        ("line-chart-height-invalid", "fail", "Sec"),
        ("line-chart-summary-invalid", "fail", "Sec"),
    ]


def test_validate_report_flags_a_chart_changed_after_construction():
    from trellum.report import ReportContext
    from trellum.validation import validate_report

    chart = LineChart(_df(), x="day", y="revenue", title="Revenue", header_summary=_SUMMARY)
    ctx = ReportContext(
        config={"slug": "t", "name": "T", "studio": "s", "category": "c",
                "description": "d", "schedule": {"cron": "*/5 * * * *"}},
        slug="t", output_dir="/tmp/test-line-chart-options")
    ctx.add_section("Charts", [chart])
    clean = validate_report(ctx)
    assert "chart options" in clean.groups_run
    assert not [c for c in clean.checks if c.id.startswith("line-chart-")]

    chart.header_summary = {"stat": "mean", "series": 4}
    flagged = [c for c in validate_report(ctx).checks if c.id == "line-chart-summary-invalid"]
    assert len(flagged) == 1 and flagged[0].level == "fail"
    assert flagged[0].section == "Charts"
