"""Deterministic, independently filtered examples of plotted-series summaries."""

import pandas as pd

from trellum.components import AreaChart, BarChart, DataSource, FilterBar, Grid, RawHTML


def add_sections(ctx):
    snapshot = pd.DataFrame({"category": ["North", "South", "West", "East"],
                             "orders": [20, 10, 0, 5]})
    ctx.add_section("Chart summaries — static off and on", [
        RawHTML(html="<p>Optional summaries use one final plotted vector. Both plots keep their ordinary height.</p>"),
        Grid(columns=2, min_width=270, children=[
            BarChart(snapshot, "category", "orders", "Static summary off", static=True),
            BarChart(snapshot, "category", "orders", "Static summary on", static=True,
                     header_summary={"stat": "sum", "scope": "plotted_series"}),
        ]),
    ])

    categories = pd.DataFrame([
        {"category": category, "channel": channel, "orders": value * factor}
        for category, value in zip(snapshot.category, snapshot.orders)
        for channel, factor in (("Direct", 1), ("Partner", 2))
    ])
    source = "showcase_summary_categories"
    ctx.add_section("Chart summaries — filtered horizontal categories", [
        DataSource(source, categories),
        FilterBar(source, categories, filters=[
            {"column": "channel", "type": "toggle", "label": "Summary categories: channel"},
        ]),
        BarChart(categories, "category", "orders", "Selected category orders",
                 dataset_id=source, horizontal=True, sort="desc",
                 header_context="Orders; matching channel, including measured zero",
                 header_summary={"stat": "sum", "scope": "plotted_series"}),
    ])

    daily = pd.DataFrame([
        {"day": f"2026-10-{day:02d}", "channel": channel,
         "orders": day * factor, "returns": (day % 3) * factor}
        for day in range(1, 5) for channel, factor in (("Direct", 1), ("Partner", 2))
    ])
    source = "showcase_summary_bands"
    ctx.add_section("Chart summaries — selected area band", [
        DataSource(source, daily),
        FilterBar(source, daily, filters=[
            {"column": "channel", "type": "toggle", "label": "Summary areas: channel"},
        ]),
        AreaChart(daily, "day", ["orders", "returns"], "Selected returns band",
                  dataset_id=source, y_labels=["Orders", "Returns"], stacked=True,
                  header_context="Returns; selected raw band, not cumulative stack height",
                  header_summary={"stat": "sum", "scope": "plotted_series", "series": "Returns"}),
    ])

    source = "showcase_summary_top_n"
    ctx.add_section("Chart summaries — Top-N and Other scope", [
        DataSource(source, categories),
        FilterBar(source, categories, filters=[
            {"column": "channel", "type": "toggle", "label": "Summary Top-N: channel"},
        ]),
        RawHTML(html="<p>Last means the last displayed finite bar: here Other, not a latest date. Dropping Other also drops it from the summary.</p>"),
        BarChart(categories, "category", "orders", "Top two and Other",
                 dataset_id=source, top_n=2,
                 header_summary={"stat": "last", "scope": "plotted_series"},
                 header_context="Orders; final displayed entry after descending Top-N and Other"),
    ])
