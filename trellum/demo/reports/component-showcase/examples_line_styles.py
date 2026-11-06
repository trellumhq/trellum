"""Deterministic ordinary LineChart marker and interpolation examples."""

import math

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML, TabGroup


def _frame(count):
    return pd.DataFrame({
        "day": pd.date_range("2026-04-01", periods=count).strftime("%Y-%m-%d"),
        "observations": [round(60 + 18 * math.sin(i / 6) + i % 7, 2) for i in range(count)],
    })


def add_sections(ctx):
    """Add genuine framework examples; no external sources or current dates."""
    ctx.add_section("Line styles: density follows the plot", [
        RawHTML("<p>Persistent 2px dots appear when connected observations are at least "
                "12px apart in the current plot. Resize the panel or drag to zoom; "
                "all source observations remain available to the nearest-point tooltip. "
                "Double-click the live chart below to reset its zoom.</p>"),
        TabGroup([
            {"label": label, "content": [LineChart(
                _frame(count), x="day", y="observations", title=label,
                static=True)]}
            for label, count in (("Dense: 181 daily observations", 181),
                                 ("Sparse: 12 daily observations", 12),
                                 ("Width-sensitive: 35 daily observations", 35))
        ], "showcase_line_density"),
    ])
    isolated = _frame(500)
    isolated["observations"] = float("nan")
    isolated.loc[[67, 249, 412], "observations"] = [35, 65, 0]
    gaps = _frame(12)
    gaps["observations"] = [0, 20, None, None, 45, None, 0, 35, 25, None, None, 10]
    ctx.add_section("Line styles: gaps and isolated measurements", [
        RawHTML("<p>Static fixtures retain supplied null gaps. Isolated measurements "
                "always paint a dot, including zero and series with more than 400 labels. "
                "These fixtures intentionally stay independent of report filters.</p>"),
        TabGroup([
            {"label": "500 labels, three measurements", "content": [LineChart(
                isolated, x="day", y="observations", title="Isolated measurements including zero", static=True)]},
            {"label": "Null gaps", "content": [LineChart(
                gaps, x="day", y="observations", title="Static null gaps and measured zero", static=True)]},
        ], "showcase_line_gaps"),
    ])
    plateau = _frame(8)
    plateau["observations"] = [0, 20, 20, 80, 80, 0, 100, 100]
    ctx.add_section("Line styles: optional interpolation", [
        RawHTML("<p>Straight paths are the default. Optional tension=0.35 infers movement "
                "between observations and can overshoot, even between equal values. "
                "Curves are not averages, forecasts or monotone estimates. Both plots "
                "retain the same source values, axes and height.</p>"),
        Grid(children=[LineChart(plateau, x="day", y="observations", title=title,
                                 static=True, tension=tension)
                       for title, tension in (("Default straight path", 0), ("Optional curve: tension 0.35", .35))]),
    ])
    frame = pd.concat([_frame(181).assign(platform=platform, users=users)
                       for platform, users in (("iOS", 10), ("Android", 20))], ignore_index=True)
    dataset = "showcase_line_styles"
    ctx.add_section("Line styles: filtered observations and ratios", [
        DataSource(dataset, frame),
        FilterBar(dataset, frame, filters=[
            {"column": "day", "type": "date_range"},
            {"column": "platform", "type": "toggle", "label": "Line styles: platform"},
        ]),
        RawHTML("<p>Filters rebuild ordinary lines from the original synthetic daily rows. "
                "The ratio sums observations and users before division. Marker density "
                "and optional curves do not change aggregation, plotted values or CSV.</p>"),
        TabGroup([
            {"label": "Filtered totals", "content": [LineChart(frame, x="day", y="observations",
                title="Filtered daily observations", dataset_id=dataset)]},
            {"label": "Ratio by platform", "content": [LineChart(frame, x="day", dataset_id=dataset,
                ratios=[{"numerator": "observations", "denominator": "users", "label": "Per user"}],
                stack_by="platform", title="Filtered ratio by platform", tension=.35)]},
        ], "showcase_line_live"),
    ])
