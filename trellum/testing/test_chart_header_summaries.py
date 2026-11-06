"""Explicit plotted-series summary declarations and legacy output parity."""
from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import AreaChart, BarChart, RenderContext
from trellum.components.charts._header_summary import header_summary_config
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DefaultTheme
from trellum.validation import validate_report
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult

BASELINE = "ef3a50c40d30d02cb678a1cf8535661db5393bb7"
ROOT = Path(__file__).resolve().parents[2]
SUMMARY = {"stat": "sum", "scope": "plotted_series"}


def frame():
    return pd.DataFrame({"category": ["C", "B", "A"], "value": [0, 20, 10],
                         "other": [1, 2, 3]})


def render(chart):
    ctx = RenderContext(theme=DefaultTheme)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class(name):
    """Load historical source without creating or modifying any checkout."""
    if subprocess.run(["git", "cat-file", "-e", BASELINE], cwd=ROOT,
                      capture_output=True).returncode:
        pytest.skip("Historical baseline is unavailable in this checkout")
    file = "bar.py" if name == "BarChart" else "line_area.py"
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE}:trellum/components/charts/{file}"], cwd=ROOT, text=True)
    module = types.ModuleType(f"_summary_baseline_{name}")
    sys.modules[module.__name__] = module
    exec(compile(source, f"baseline/{file}", "exec"), module.__dict__)
    return getattr(module, name)


@pytest.mark.parametrize("cls", [BarChart, AreaChart])
@pytest.mark.parametrize("bound", [False, True])
def test_keyword_only_opt_out_matches_historical_html_and_payload(cls, bound):
    assert inspect.signature(cls).parameters["header_summary"].kind == inspect.Parameter.KEYWORD_ONLY
    kwargs = {"dataset_id": "source"} if bound else {}
    old = baseline_class(cls.__name__)(frame(), "category", "value", "Unchanged", **kwargs)
    current = cls(frame(), "category", "value", "Unchanged", **kwargs)
    assert render(current) == render(old)
    assert "header_summary" not in render(current)[1]
    assert "data-chart-summary" not in render(current)[0]


@pytest.mark.parametrize("cls", [BarChart, AreaChart])
@pytest.mark.parametrize("stat,label", [("mean", "Shown average"), ("sum", "Shown total"),
    ("last", "Last shown"), ("min", "Shown min"), ("max", "Shown max")])
def test_defaults_formats_and_no_plot_changes(cls, stat, label):
    plain = cls(frame(), "category", "value", "Chart", y_format="currency")
    chart = replace(plain, header_summary={"stat": stat, "scope": "plotted_series"})
    html, cfg = render(chart)
    summary = cfg.pop("header_summary")
    assert cfg == render(plain)[1]
    assert summary == {"stat": stat, "scope": "plotted_series", "series": 0,
                       "label": label, "format": "currency"}
    assert 'data-chart-summary=' in html and "fw-line-chart" not in html
    chart.header_summary["format"] = "plain"
    assert render(chart)[1]["header_summary"]["format"] == "plain"
    chart.y_format = "decimal1"
    del chart.header_summary["format"]
    assert render(chart)[1]["header_summary"]["format"] == "number"


@pytest.mark.parametrize("cls", [BarChart, AreaChart])
@pytest.mark.parametrize("bad", [False, [], {}, {"stat": "sum"},
    {**SUMMARY, "scope": "visible"}, {**SUMMARY, "extra": 1},
    {**SUMMARY, "stat": []}, {**SUMMARY, "stat": "median"},
    {**SUMMARY, "series": True}, {**SUMMARY, "series": -1},
    {**SUMMARY, "series": float("inf")}, {**SUMMARY, "series": None},
    {**SUMMARY, "series": "missing"}, {**SUMMARY, "series": 1},
    {**SUMMARY, "label": " "}, {**SUMMARY, "label": "x" * 61},
    {**SUMMARY, "label": None}, {**SUMMARY, "format": "decimal1"},
    {**SUMMARY, "format": None}, {**SUMMARY, "format": "percent"},
    {**SUMMARY, "format": "ratio"}])
def test_invalid_construction_render_and_mutation_validation(cls, bad):
    with pytest.raises(ValueError, match="header_summary"):
        cls(frame(), "category", "value", "Invalid", header_summary=bad)
    chart = cls(frame(), "category", "value", "Invalid")
    chart.header_summary = bad
    result = ValidationResult(slug="summary")
    _check_chart_options([(chart, "Section")], result)
    assert result.summary["fail"] == 1
    with pytest.raises(ValueError, match="header_summary"):
        render(chart)


@pytest.mark.parametrize("cls", [BarChart, AreaChart])
def test_actual_static_zip_bound_fallback_and_duplicates(cls):
    values = {"y_cols": ["value", "other"]} if cls is BarChart else {"y": ["value", "other"]}
    common = {"df": frame(), "x": "category", "title": "Selected", "y_labels": ["First"], **values}
    if cls is BarChart:
        common["y"] = "value"
    # Static zip produces only one series, so implicit selection is valid.
    chart = cls(**common, header_summary=SUMMARY)
    assert len(render(chart)[1]["datasets"]) == 1
    with pytest.raises(ValueError, match="series is required"):
        replace(chart, dataset_id="source")
    chart = replace(chart, dataset_id="source", header_summary={**SUMMARY, "series": "other"})
    assert header_summary_config(chart)["header_summary"]["series"] == "other"
    chart.y_labels = ["Duplicate", "Duplicate"]
    chart.header_summary = {**SUMMARY, "series": "Duplicate"}
    with pytest.raises(ValueError, match="unique"):
        render(chart)
    chart.header_summary["series"] = 1
    assert header_summary_config(chart)["header_summary"]["series"] == 1
    chart.header_summary["series"] = "other"
    result = ValidationResult(slug="mutated-identity")
    _check_chart_options([(chart, "Selected")], result)
    assert result.summary["fail"] == 1


def test_ratio_and_net_color_renderer_precedence():
    ratio = [{"numerator": "value", "denominator": "other", "label": "Rate"}]
    bar = BarChart(frame(), "category", "value", "Net", dataset_id="source",
                   ratios=ratio, header_summary={"stat": "mean", "scope": "plotted_series", "series": "Rate"})
    assert render(bar)[1]["header_summary"]["series"] == "Rate"
    with pytest.raises(ValueError, match="sum is undefined"):
        replace(bar, header_summary={**SUMMARY, "format": "number"})
    with pytest.raises(ValueError, match="net_color with ratios"):
        replace(bar, net_color=True)
    net = replace(bar, ratios=None, net_color=True, y_cols=["value", "other"], header_summary=SUMMARY)
    assert render(net)[1]["header_summary"]["series"] == 0
    assert render(replace(net, header_summary={**SUMMARY, "series": "Net"}))[1]["net_color"]
    with pytest.raises(ValueError, match="unique"):
        replace(net, header_summary={**SUMMARY, "series": "value"})
    # Static flags do not alter the existing static value-column path.
    static = replace(bar, dataset_id=None, header_summary=SUMMARY)
    assert render(static)[1]["datasets"][0]["label"] == "value"


def example_context(output):
    path = ROOT / "trellum/demo/reports/component-showcase/examples_chart_summaries.py"
    spec = importlib.util.spec_from_file_location("examples_chart_summaries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={"name": "Chart summaries", "studio": "test", "category": "Test",
        "description": "Synthetic orders by category and channel, selected area bands and final plotted scope."},
        slug="chart-summaries", output_dir=str(output))
    module.add_sections(ctx)
    return ctx


def test_native_examples_have_zero_diagnostic_warnings_and_failures(tmp_path):
    ctx = example_context(tmp_path)
    result = validate_report(ctx, details=compute_details(ctx))
    issues = [(check.id, check.level, check.message) for check in result.checks
              if check.level in {"warn", "fail"}]
    assert issues == []
