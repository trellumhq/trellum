"""Compact authoring example for opt-in Grid theme color cycling."""

import pandas as pd

from trellum.components.charts.bar import BarChart
from trellum.components.charts.line_area import AreaChart, LineChart
from trellum.components.layout import Grid


def grid_color_cycle_example():
    df = pd.DataFrame({"day": ["Mon", "Tue", "Wed"], "value": [3, 5, 4]})
    charts = [LineChart(df, "day", "value", "Line"),
              AreaChart(df, "day", "value", "Area"),
              BarChart(df, "day", "value", "Bar"),
              LineChart(df, "day", "value", "Line 2"),
              AreaChart(df, "day", "value", "Area 2"),
              BarChart(df, "day", "value", "Bar 2")]
    return Grid(charts, columns=3, color_cycle="theme")
