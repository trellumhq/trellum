"""Deterministic hierarchy, supplied-color and sizing-limit examples."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, Panel, RawHTML, TreemapChart


def add_sections(ctx):
    """Add genuine static and report-filtered treemap mechanics."""
    complete = pd.DataFrame({
        "region": ["North", "North", "South", "South", "West", "West"],
        "channel": ["Store", "Web", "Store", "Web", "Store", "Web"],
        "revenue": [36000, 24000, 22000, 12000, 5990, 10],
    })

    def chart(frame, title, **options):
        return TreemapChart(frame, ["region", "channel"], "revenue", title,
                            value_format="currency", static=True, **options)

    ctx.add_section("Treemaps — hierarchy and optional shares", [
        RawHTML(html=(
            "<p>Six supplied leaves total $100,000, including a $10 tile. Store "
            "under North and Store under South are different full paths. Rectangle "
            "area encodes revenue; color identifies the first hierarchy group. "
            "Optional percentages describe every supplied value, even tiles too "
            "small for labels. Authors must supply additive quantities.</p>")),
        Grid(columns=2, min_width=280, children=[
            Panel(title="Shares off by default", children=[chart(complete, "Default hierarchy")]),
            Panel(title="Optional shares on", children=[chart(
                complete, "Hierarchy with shares", show_percentages=True)]),
        ]),
    ])
    long = complete.copy()
    long["region"] = long["region"].map({
        "North": "Northern regional retail and partner operations",
        "South": "Southern regional retail and partner operations",
        "West": "Western regional retail and partner operations",
    })
    long["channel"] = long["channel"].map({
        "Store": "Store sales and assisted customer orders",
        "Web": "Web orders and extended delivery arrangements",
    })
    ctx.add_section("Treemaps — long paths and tiny tiles", [
        RawHTML(html=("<p>Full labels appear only when they fit at native label size. "
                      "The key and expandable details preserve full paths and exact values.</p>")),
        chart(long, "Long hierarchy names", show_percentages=True),
    ])
    filtered = pd.DataFrame([
        {**row, "platform": platform, "revenue": row["revenue"] * weight + index}
        for index, row in enumerate(complete.to_dict("records"))
        for platform, weight in (("Desktop", 0.65), ("Phone", 0.35))
    ])
    dataset = "showcase_treemaps"
    ctx.add_section("Treemaps — filter the complete population", [
        DataSource(dataset, filtered),
        FilterBar(dataset, filtered, filters=[
            {"column": "platform", "label": "Treemaps: platform", "type": "toggle"},
            {"column": "region", "label": "Treemaps: region", "type": "dropdown"},
        ]),
        RawHTML(html=("<p>Filters recalculate the current supplied population. "
                      "Open details remain open while their rows update. Colors use "
                      "a chart-local first-group mapping, including after filtering.</p>")),
        TreemapChart(filtered, ["region", "channel"], "revenue", "Filtered hierarchy",
                     dataset_id=dataset, value_format="currency", show_percentages=True),
    ])
    supplied = pd.DataFrame({
        "region": ["North", "North", "South", "South"],
        "channel": ["Store", "Store", "Store", "Store"],
        "revenue": [900, 100, 700, 300],
        "source": ["Organic", "Paid", "Paid", "Organic"],
        "margin": [0.1, 0.9, 0.8, 0.2],
    })
    ctx.add_section("Treemaps — supplied color fields", [
        RawHTML(html=(
            "<p>Supplied colors retain the existing first-source-child behavior, "
            "not an aggregate metric. Numeric margin colors change when source rows "
            "are reversed despite unchanged revenue. No quantitative color legend "
            "is claimed. Categorical palette assignment follows encounter order.</p>")),
        Grid(columns=2, min_width=280, children=[
            Panel(title=title, children=[chart(frame, title, color_col=color)])
            for title, frame, color in (
                ("Categorical source color", supplied, "source"),
                ("Numeric supplied color", supplied, "margin"),
                ("Numeric reversed rows", supplied.iloc[::-1], "margin"),
            )
        ]),
    ])
    ctx.add_section("Treemaps — supplied sizing caveats", [
        RawHTML(html=(
            "<p>These diagnostics retain legacy zero, missing and signed sizing. "
            "Zero-total shares are undefined; incomplete or signed populations "
            "have unavailable shares. Live blank parents can suppress leaves. "
            "Static names keep their existing string conversion. The existing "
            "treemap CSV contains headers only; details do not redefine export.</p>")),
        Grid(columns=2, min_width=280, children=[
            Panel(title=title, children=[chart(pd.DataFrame({
                "region": ["North"] * len(values),
                "channel": ["Store", "Web"][:len(values)], "revenue": values,
            }), title, show_percentages=True)])
            for title, values in (
                ("Measured zeros", [0, 0]),
                ("Missing supplied value", [70, None]),
                ("Signed supplied values", [100, -30]),
            )
        ]),
    ])
    dense = pd.DataFrame({
        "region": ["North"] * 12 + ["South"] * 12,
        "channel": [f"Item {index + 1:02d}" for index in range(24)],
        "revenue": [50 - index for index in range(24)],
    })
    ctx.add_section("Treemaps — many fitting labels", [
        RawHTML(html=("<p>Twenty-four distinct supplied leaf values remain in details. "
                      "The optional share line can hide an entire label when the "
                      "complete label no longer fits; type and rectangles keep their size.</p>")),
        Grid(columns=2, min_width=280, children=[
            Panel(title="Dense labels, shares off", children=[chart(dense, "Dense default")]),
            Panel(title="Dense labels, shares on", children=[chart(
                dense, "Dense shares", show_percentages=True)]),
        ]),
    ])
