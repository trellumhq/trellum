import pandas as pd
import pytest

from trellum.components.base import RenderContext
from trellum.components.charts.bar import BarChart
from trellum.components.charts.line_area import AreaChart, LineChart
from trellum.components.layout import Grid
from trellum.themes import DEFAULT_THEME


def test_grid_theme_color_cycle_assigns_authored_slots_and_keeps_defaults():
    frame = pd.DataFrame({"x": ["a", "b"], "y": [1, 2]})
    charts = [LineChart(frame, "x", "y"), AreaChart(frame, "x", "y"),
              BarChart(frame, "x", "y", "Bars")]
    grid = Grid(charts, color_cycle="theme")
    ctx = RenderContext(DEFAULT_THEME)
    ctx.render_child(grid)
    colors = [next(iter(config["datasets"]))["borderColor"]
              for config in ctx.component_data.values()]
    assert colors == list(DEFAULT_THEME.chart_colors[:3])
    assert len(set(colors)) == 3


def test_grid_color_cycle_excludes_category_and_multimetric_and_counts_slots():
    frame = pd.DataFrame({"x": ["a"], "y": [1], "z": [2]})
    override = LineChart(frame, "x", "y", colors=["#abcdef"])
    multi = LineChart(frame, "x", ["y", "z"])
    eligible = BarChart(frame, "x", "y", "Bars")
    ctx = RenderContext(DEFAULT_THEME)
    ctx.render_child(Grid([override, multi, eligible], color_cycle="theme"))
    assert id(eligible) in ctx.grid_color_slots
    assert ctx.grid_color(eligible) == DEFAULT_THEME.chart_colors[2]
    assert id(override) in ctx.grid_color_slots
    assert id(multi) not in ctx.grid_color_slots


def test_grid_color_cycle_is_keyword_only_and_validated():
    with pytest.raises(TypeError):
        Grid([], 2, False, 0, "theme")
    with pytest.raises(ValueError, match="color_cycle"):
        Grid([], color_cycle="other")
