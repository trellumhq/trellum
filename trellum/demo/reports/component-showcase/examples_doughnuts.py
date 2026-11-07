"""Doughnut examples using ordinary components and deterministic supplied data."""

import pandas as pd

from trellum.components import DataSource, DoughnutChart, FilterBar, Grid, Panel, RawHTML


def add_sections(ctx):
    """Add compact rings, optional totals, filtering and eligibility examples."""
    complete = pd.DataFrame({
        "category": ["Organic", "Paid social", "Paid search", "Partnerships", "Other"],
        "value": [48000, 30000, 14000, 6000, 2000],
    })

    def chart(frame, title, **options):
        return DoughnutChart(frame, "category", "value", title, static=True,
                             value_format="currency", **options)

    ctx.add_section("Doughnuts — compact rings and optional total", [
        RawHTML(html=(
            "<p>Five supplied acquisition categories total $100,000. The default "
            "ring has no center total. The optional labeled total uses the same "
            "ring size. Values and shares include every category in this result, "
            "even when a legend button hides its arc. Totals require additive "
            "quantities; units are supplied explicitly by the report author.</p>"
        )),
        Grid(columns=2, min_width=280, children=[
            Panel(title="Default ring · center off", children=[chart(complete, "Default ring")]),
            Panel(title="Optional total · center on", children=[
                chart(complete, "Optional total", center_total=True, center_label="Revenue")]),
        ]),
    ])
    long = pd.DataFrame({
        "category": ["Organic discovery and referrals", "Paid social · prospecting campaigns",
                     "Paid search · branded queries", "Creator partnerships", "Referral partners",
                     "Email and returning visitors", "Other acquisition sources"],
        "value": [58000, 30000, 7000, 3800, 900, 200, 100],
    })
    ctx.add_section("Doughnuts — long names and tiny values", [
        RawHTML(html=(
            "<p>All seven categories remain in the wrapping legend, including the "
            "$100 / 0.1% category. Optional show_labels=True labels only slices "
            "with at least 3% of all categories in the current result.</p>"
        )),
        chart(long, "Long names and tiny values", show_labels=True),
    ])
    duplicate = pd.DataFrame({"category": ["Organic", "Organic", "Paid"],
                              "value": [25000, 15000, 10000]})
    ctx.add_section("Doughnuts — static duplicate grain", [
        RawHTML(html=(
            "<p>These static rows deliberately repeat Organic. Each row keeps "
            "its own arc, value, legend button and CSV row. Static inputs are "
            "not grouped; dataset-bound charts use the existing category grouping.</p>"
        )),
        chart(duplicate, "Static duplicate grain"),
    ])
    filtered = pd.DataFrame([
        {"category": row.category, "platform": platform, "value": row.value * weight}
        for row in complete.itertuples(index=False)
        for platform, weight in (("iOS", 0.6), ("Android", 0.4))
    ])
    dataset = "showcase_doughnuts"
    ctx.add_section("Doughnuts — report filters and ring visibility", [
        DataSource(dataset, filtered),
        FilterBar(dataset, filtered, filters=[
            {"column": "platform", "label": "Doughnuts: platform", "type": "toggle"},
            {"column": "category", "label": "Doughnuts: category", "type": "dropdown"},
        ]),
        RawHTML(html=(
            "<p>Platform and category filters update the current result and its "
            "total. Click an arc to select a report category; use the existing "
            "FilterBar to clear it. Legend buttons only hide or restore arcs. "
            "Hiding leaves the full current-result total, shares and CSV intact. "
            "All-hidden rings offer Restore all without changing report filters. "
            "A redraw resets arc visibility. Repeated arc-click selection memory "
            "after redraw remains a separate runtime limitation.</p>"
        )),
        DoughnutChart(filtered, "category", "value", "Filterable acquisition revenue",
                      dataset_id=dataset, cross_filter=True, show_labels=True,
                      center_total=True, center_label="Revenue", value_format="currency"),
    ])
    cases = [
        ("Measured zeros", ["Organic", "Paid"], [0, 0]),
        ("Empty result", [], []),
        ("Incomplete supplied values", ["Organic", "Paid", "Search"], [48000, None, 14000]),
        ("Signed supplied values", ["Organic", "Adjustment", "Search"], [80, -20, 40]),
    ]
    ctx.add_section("Doughnuts — total and share eligibility", [
        RawHTML(html=(
            "<p>Zero is a measured total, but zero-total shares are undefined. "
            "Empty, incomplete, signed and nonfinite results cannot claim a new "
            "total or shares. Their supplied values remain available in the legend. "
            "Chart.js signed/incomplete arc behavior and live aggregation's null "
            "coercion are pre-existing unsupported semantics; this presentation "
            "does not replace them with a different calculation.</p>"
        )),
        Grid(columns=2, min_width=280, children=[
            Panel(title=title, children=[chart(
                pd.DataFrame({"category": labels, "value": values}), title,
                center_total=True, center_label="Supplied total")])
            for title, labels, values in cases
        ]),
    ])
