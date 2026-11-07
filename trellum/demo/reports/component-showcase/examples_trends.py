"""Deterministic ordinary native fits; reusable without adding tour navigation."""
from __future__ import annotations

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, Grid, LineChart, ScatterChart

TREND = {"method": "linear", "series": "each"}


def facts():
    """Daily facts, including measured zero and incomplete supplied inputs."""
    return pd.DataFrame([
        {"day": f"2026-10-{day:02d}", "platform": platform,
         "observed": None if day == 6 and platform == "Web" else 0 if day == 1 else day * factor,
         "visits": 0 if day == 5 else 10 * factor,
         "spend": day * factor, "return_value": 3 * day * factor + 2,
         "bubble_size": day * 7}
        for day in range(1, 15) for platform, factor in (("App", 2), ("Web", 3))
    ])


def filtered_components(source="showcase_native_trends"):
    """One complete snapshot, native filters and compact ordinary chart views."""
    df = facts()
    return [
        DataSource(source, df),
        FilterBar(source, df, filters=[{"column": "day", "type": "date_range"},
                                      {"column": "platform", "type": "toggle"}]),
        Grid(columns=2, min_width=270, children=[
            LineChart(df, "day", "observed", "Filtered daily trend", dataset_id=source, trend_line=TREND,
                      header_summary={"stat": "mean", "series": 0, "label": "Mean plotted value"},
                      header_context="Synthetic daily totals. Incomplete October 6 is excluded from the fit; legacy plot aggregation is retained."),
            LineChart(df, "day", title="Filtered daily ratio", dataset_id=source, trend_line=TREND, y_format="percent",
                      ratios=[{"numerator": "observed", "denominator": "visits", "label": "Daily ratio"}],
                      header_context="Unweighted fit across eligible daily ratios, after summing selected numerators and denominators."),
            LineChart(df, "day", "observed", "Separate platform trends", dataset_id=source, stack_by="platform", trend_line=TREND),
            ScatterChart(df, "spend", "return_value", color_by="platform", size="bubble_size",
                         title="Numeric scatter trends", dataset_id=source, trend_line=TREND,
                         header_context="Unweighted coordinate pairs; bubble sizes are not regression weights."),
        ]),
        DataTable(df, "Source observations", dataset_id=source),
    ]


def static_chart():
    df = pd.DataFrame({"day": [f"2026-10-{d:02d}" for d in range(1, 9)],
                       "observed": [0, 4, None, 8, 10, 12, 14, 16],
                       "forecast": [2, 4, 6, 8, 10, 12, 14, 16]})
    return LineChart(df, "day", ["observed", "forecast"], "Static observed trend", static=True,
                     y_labels=["Observed", "Supplied forecast"], forecast_series=["Supplied forecast"],
                     trend_line={"method": "linear", "series": [{"column": "observed"}]},
                     header_context="Independent supplied snapshot. A measured zero counts; the null slot stays a gap. Forecast values are not fitted.")


def add_sections(ctx):
    ctx.add_section("Native linear trends", filtered_components())
    ctx.add_section("Independent observed snapshot", [static_chart()])
