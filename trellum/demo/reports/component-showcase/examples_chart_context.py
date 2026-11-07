"""Optional authored header context on report charts."""

import pandas as pd

from trellum.components import (
    BarChart,
    DataSource,
    DoughnutChart,
    FilterBar,
    Grid,
    LineChart,
    RawHTML,
    SplitPane,
)


def add_sections(ctx):
    daily = pd.DataFrame([
        {"day": f"2026-10-{day:02d}", "channel": channel,
         "revenue": (1200 + day * 73) * factor,
         "orders": (24 + day * 2) * factor}
        for day in range(1, 7)
        for channel, factor in (("Direct", 1), ("Partner", 2))
    ])
    source = "showcase_header_context_daily"
    ctx.add_section("Chart header context — filtered revenue and summary", [
        DataSource(source, daily),
        FilterBar(source, daily, filters=[
            {"column": "channel", "label": "Chart context: channel", "type": "toggle"},
        ]),
        RawHTML(html=(
            "<p>The summary follows the matching completed orders in the plotted "
            "series. The authored unit and aggregation description stays fixed "
            "when channel filters change.</p>"
        )),
        LineChart(daily, "day", "orders", "Completed orders · filtered",
                  dataset_id=source, header_summary={"stat": "sum", "format": "number"},
                  header_context="Orders; sum of matching completed orders"),
    ])

    segments = pd.DataFrame({
        "channel": ["Direct", "Partner", "Marketplace"],
        "revenue": [18200, 9400, 6300],
    })
    ctx.add_section("Chart header context — units and definitions", [
        RawHTML(html=(
            "<p>Plain text supplied by the report author. Context is not inferred "
            "from data and does not change with report selections.</p>"
        )),
        BarChart(segments, "channel", "revenue", "Completed order value",
                 static=True, header_context="USD; supplied completed-order revenue"),
        Grid(columns=2, min_width=270, children=[
            BarChart(segments, "channel", "revenue", "Independent static bar",
                     static=True, header_context="USD; supplied snapshot"),
            DoughnutChart(segments, "channel", "revenue", "", static=True,
                          header_context="Supplied snapshot: three channels\nLong labels wrap within narrow panels"),
        ]),
        Grid(columns=1, show_titles=False, children=[
            LineChart(daily, "day", "orders", "Hidden title, visible summary and context",
                      dataset_id=source,
                      header_summary={"stat": "sum", "format": "number"},
                      header_context="Orders; summary and authored context"),
        ]),
    ])

    context_free = pd.DataFrame({"day": ["A", "B", "C"], "orders": [8, 13, 9]})
    ctx.add_section("Chart header context — omitted and blank values", [
        RawHTML(html=(
            "<p>Omitting context or supplying whitespace adds no context element "
            "or spacing. This blank value is deliberate.</p>"
        )),
        SplitPane(
            LineChart(context_free, "day", "orders", "No added context", static=True),
            LineChart(context_free, "day", "orders", "Whitespace context", static=True,
                      header_context="  \n  "),
            "1:1",
        ),
    ])
