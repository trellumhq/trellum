"""Deterministic standalone choices and report-local view navigation."""

import pandas as pd

from trellum.components import DataSource, Dropdown, FilterBar, LineChart, RawHTML, TabGroup


def add_sections(ctx):
    """Add native controls and dataset-backed charts to a report context."""
    frame = pd.DataFrame([
        {"day": day, "platform": platform, "revenue": 120 + i * 9 + extra,
         "orders": 12 + i + extra // 10}
        for i, day in enumerate(pd.date_range("2026-09-01", periods=10).strftime("%Y-%m-%d"))
        for platform, extra in (("iOS", 30), ("Android", 0))
    ])
    dataset = "showcase_controls_daily"
    ctx.add_section("Control examples: data and platform", [
        DataSource(dataset, frame),
        FilterBar(dataset, frame, filters=[
            {"column": "platform", "label": "Control examples: platform", "type": "toggle"},
        ]),
    ])

    def chart(title, metric="revenue"):
        return LineChart(frame, x="day", y=metric, title=title, dataset_id=dataset,
                         stack_by="platform", y_format="currency" if metric == "revenue" else "number")

    ctx.add_section("Standalone choices", [
        RawHTML("<p>Choose a caption or search the full choice list. This standalone "
                "Dropdown retains its own selected value; the platform filter above "
                "controls the synthetic charts below.</p>"),
        Dropdown("showcase_controls_caption", [
            "Daily revenue across all supported platforms and acquisition channels — supplied totals",
            "Orders across platforms — a longer selected caption wraps inside its panel",
            "UnbrokenIdentifier_" + "platform" * 18,
        ], label="Displayed caption"),
        Dropdown("showcase_controls_empty", [], label="Unavailable comparison"),
    ])
    ctx.add_section("Underlined report tabs", [
        TabGroup([
            {"label": "Revenue", "content": [chart("Revenue by platform")]},
            {"label": "Orders across all supported platforms — daily supplied totals",
             "content": [chart("Orders by platform", "orders")]},
        ], "showcase_controls_tabs", label="Metric view"),
    ])
    labels = ["Overview", "Daily revenue", "Daily orders", "Platform trends",
              "Acquisition context", "Returning customers", "New customers",
              "Regional coverage", "Longer report view — revenue across supported platforms",
              "Order comparison", "Revenue comparison", "Final supplied view"]

    def views(prefix):
        return [{"label": label, "content": [chart(f"{prefix}: {label}", "orders" if i % 2 else "revenue")]}
                for i, label in enumerate(labels)]

    ctx.add_section("Searchable report views", [
        TabGroup(views("Selector"), "showcase_controls_selector", "dropdown", label="Choose a supplied view"),
    ])
    ctx.add_section("Side list follows panel width", [
        RawHTML("<p>The optional sidebar lists every view on wide panels. At 520px "
                "or less it becomes a searchable selector and keeps the selected "
                "view. Chart plotting heights remain unchanged.</p>"),
        TabGroup(views("Side list"), "showcase_controls_sidebar", "sidebar", label="Browse supplied views"),
    ])
    ctx.add_section("Nested and sibling report views", [
        TabGroup([
            {"label": "Outer revenue", "content": [TabGroup([
                {"label": "Nested revenue", "content": [chart("Nested revenue chart")]},
                {"label": "Nested orders", "content": [chart("Nested orders chart", "orders")]},
            ], "showcase_controls_inner")]},
            {"label": "Outer orders", "content": [chart("Outer orders chart", "orders")]},
        ], "showcase_controls_outer"),
        TabGroup([
            {"label": "Sibling revenue", "content": [chart("Sibling revenue chart")]},
            {"label": "Sibling orders", "content": [chart("Sibling orders chart", "orders")]},
        ], "showcase_controls_sibling"),
    ])
    ctx.add_section("No report views", [TabGroup([], "showcase_controls_no_views")])
