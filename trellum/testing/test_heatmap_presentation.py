"""Heatmap constructor compatibility and unchanged static aggregation."""

import inspect
from dataclasses import dataclass, fields

import pandas as pd
import pytest

from trellum.components import HeatmapChart, RenderContext
from trellum.themes import DefaultTheme


def _frame():
    return pd.DataFrame({"x": ["B", "A", "B", "C", "A", "D"],
                         "y": ["two", "one", "two", "one", "one", "one"],
                         "v": [None, 4, 7, 0, None, "bad"]})


def test_constructor_preserves_all_positional_and_subclass_arguments():
    params = inspect.signature(HeatmapChart).parameters
    assert params["show_values"].kind is inspect.Parameter.KEYWORD_ONLY
    assert [f.name for f in fields(HeatmapChart) if f.init and not f.kw_only] == [
        "df", "x", "y", "value", "title", "color_scale", "log_scale", "normalize",
        "drop_empty", "value_format", "dataset_id", "static"]

    @dataclass
    class Specialized(HeatmapChart):
        extra: str = "extra"

    chart = Specialized(_frame(), "x", "y", "v", "T", ("#fff", "#000"), False,
                        "row", True, "currency", "bound", True, "subclass", show_values=False)
    assert chart.extra == "subclass" and chart.show_values is False
    assert chart.normalize == "row" and chart.dataset_id == "bound" and chart.static


@pytest.mark.parametrize("invalid", [0, 1, None, "False", [], {}])
def test_values_option_requires_bool(invalid):
    with pytest.raises(ValueError, match="show_values must be a bool"):
        HeatmapChart(_frame(), "x", "y", "v", show_values=invalid)


@pytest.mark.parametrize("normalize", [None, "row", "col"])
@pytest.mark.parametrize("log_scale", [None, False, True])
def test_static_cells_and_order_are_unchanged(normalize, log_scale):
    ctx = RenderContext(theme=DefaultTheme)
    chart = HeatmapChart(_frame(), "x", "y", "v", normalize=normalize, log_scale=log_scale)
    html = chart.render_html(ctx)
    cfg = next(iter(ctx.component_data.values()))
    assert cfg["cells"] == [
        {"x": "B", "y": "two", "v": 7.0}, {"x": "A", "y": "one", "v": 4.0},
        {"x": "C", "y": "one", "v": 0.0}, {"x": "D", "y": "one", "v": 0.0}]
    assert cfg["x_labels"] == ["B", "A", "C", "D"]
    assert cfg["y_labels"] == ["two", "one"]
    assert cfg["heatmap_style"] == "sequential-v1" and cfg["show_values"] is True
    assert cfg["scale_population"] == "nonnegative"
    assert 'data-heatmap-key="fw_c1"' in html
    assert cfg.get("normalize") == normalize
    assert cfg.get("log_scale") == log_scale


def test_explicit_dataset_binding_wins_over_static_and_keeps_palette():
    ctx = RenderContext(theme=DefaultTheme)
    chart = HeatmapChart(_frame(), "x", "y", "v", color_scale=("#123456", "#abcdef"),
                         static=True, dataset_id="bound", drop_empty=True, show_values=False)
    chart.render_html(ctx)
    cfg = next(iter(ctx.component_data.values()))
    assert cfg["dataset_id"] == "bound" and "cells" not in cfg
    assert cfg["drop_empty"] and cfg["show_values"] is False
    assert cfg["color_scale"] == ("#123456", "#abcdef")


@pytest.mark.parametrize("value", [-1, float("inf"), -float("inf")])
def test_unsupported_population_is_tagged_without_rewriting_cells(value):
    frame = pd.DataFrame({"x": ["A"], "y": ["B"], "v": [value]})
    ctx = RenderContext(theme=DefaultTheme)
    chart = HeatmapChart(frame, "x", "y", "v")
    chart.render_html(ctx)
    cfg = next(iter(ctx.component_data.values()))
    assert cfg["cells"][0]["v"] == value
    assert cfg["scale_population"] == "unsupported"
