"""Deterministic layout examples with ordinary report filtering."""

import pandas as pd

from trellum.components import (
    BarChart, DataSource, FilterBar, Grid, LineChart, Panel, RawHTML, SplitPane,
)


def add_sections(ctx):
    """Add panel-width layout examples to a real ReportContext."""
    days = pd.date_range("2026-09-01", periods=21).strftime("%Y-%m-%d")
    frame = pd.DataFrame([
        {"day": day, "platform": platform, "channel": channel,
         "revenue": (180 + i * 6 + (i % 7) * 17) * platform_weight * channel_weight,
         "orders": (20 + i + i % 5) * platform_weight * channel_weight}
        for i, day in enumerate(days)
        for platform, platform_weight in (("iOS", 2), ("Android", 1))
        for channel, channel_weight in (("Organic", 2), ("Paid", 1))
    ])
    dataset = "showcase_layouts_daily"
    ctx.add_section("", [
        DataSource(dataset, frame),
        FilterBar(dataset, frame, filters=[
            {"column": "platform", "label": "Layout examples: platform", "type": "toggle"},
        ]),
    ])

    def trend(title, metric="revenue", **options):
        return LineChart(
            frame, x="day", y=metric, title=title, dataset_id=dataset,
            y_format="currency" if metric == "revenue" else "number", **options,
        )

    breakdowns = {"Platform": "platform", "Channel": "channel"}
    ctx.add_section("Grid — up to three columns, 280px minimum", [
        RawHTML(html=(
            "<p>Daily store revenue and orders from a supplied synthetic fixture. "
            "This Grid requests at most three columns with min_width=280 and "
            "card=True. Each cell keeps its chart's own plotting height, title "
            "and controls. The platform filter above updates every example.</p>"
        )),
        Grid(columns=3, min_width=280, card=True, children=[
            trend("Daily revenue · balanced 220px", stack_by_options=breakdowns),
            trend("Daily orders · compact 160px", metric="orders", height="compact"),
            BarChart(frame, x="channel", y="revenue", title="Revenue by acquisition channel",
                     dataset_id=dataset, y_format="currency"),
        ]),
    ])
    ctx.add_section("SplitPane — 2:1 with complete chart context", [
        RawHTML(html=(
            "<p>ratio='2:1', left_min_width=480, right_min_width=240. Both bare "
            "charts keep their title, breakdown controls and runtime legend "
            "on their own side. When the ratio cannot accommodate both minimums, "
            "the left chart appears above the right.</p>"
        )),
        SplitPane(
            trend("Revenue trend by platform or channel", stack_by_options=breakdowns),
            trend("Orders by platform or channel", metric="orders", height="compact",
                  stack_by_options=breakdowns),
            "2:1", left_min_width=480, right_min_width=240,
        ),
    ])
    ctx.add_section("Nested panel — columns fit the available panel", [
        RawHTML(html=(
            "<p>The right pane holds a Panel containing a two-column Grid with "
            "min_width=240. It uses one column when its panel is narrow, even "
            "on a wide screen. Expanding the available width restores columns.</p>"
        )),
        SplitPane(
            trend("Daily revenue · roomy 320px", height="roomy"),
            Panel(title="Daily order context", children=[
                Grid(columns=2, min_width=240, children=[
                    trend("Order volume", metric="orders", height="compact"),
                    BarChart(frame, x="channel", y="orders", title="Orders by channel",
                             dataset_id=dataset),
                ]),
            ]),
            "2:1", left_min_width=480, right_min_width=240,
        ),
    ])
    ctx.add_section("Optional omission — redundant chart title", [
        Panel(title="Daily revenue by platform", children=[
            RawHTML(html=(
                "<p>This Grid explicitly sets show_titles=False because the panel "
                "already names the metric. Breakdown controls remain visible. "
                "A nested Grid would keep its own visibility options.</p>"
            )),
            Grid(columns=1, show_titles=False, children=[
                trend("Daily revenue by platform", stack_by_options=breakdowns),
            ]),
        ]),
    ])
