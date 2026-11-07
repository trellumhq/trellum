"""Synthetic category-entry summaries with independent native section filters."""

import pandas as pd

from trellum.components import DataSource, DoughnutChart, FilterBar, Grid, Panel, RawHTML


def facts():
    """Twelve date × platform × acquisition facts, measured in synthetic units."""
    return pd.DataFrame([
        {"day": f"2026-10-0{day}", "platform": platform, "acquisition": acquisition,
         "amount": amount * factor}
        for day, amounts in ((1, (12, 5, 3)), (2, (18, 0, 2)))
        for platform, factor in (("iOS", 1), ("Android", 2))
        for acquisition, amount in zip(("UA", "RT", "Organic"), amounts)
    ])


def add_sections(ctx):
    snapshot = pd.DataFrame({"acquisition": ["UA", "RT", "Organic"], "amount": [90, 15, 15]})
    ctx.add_section("Doughnut summaries — off and on", [
        RawHTML(html="<p>Synthetic complete, disjoint additive units. Identical source; center totals are off. Titles belong to Panels.</p>"),
        Grid(columns=2, min_width=270, children=[
            Panel(title="Header summary off", children=[
                DoughnutChart(snapshot, "acquisition", "amount", "Summary off", static=True)]),
            Panel(title="Header summary on", children=[
                DoughnutChart(snapshot, "acquisition", "amount", "Summary on", static=True,
                              header_summary={"stat": "sum", "scope": "all_categories"})]),
        ]),
    ])
    for name, source, combined in (("Filtered category amounts", "showcase_doughnut_summary_amounts", False),
                                    ("Independent options", "showcase_doughnut_summary_options", True)):
        frame = facts()
        ctx.add_section(f"Doughnut summaries — {name}", [
            DataSource(source, frame),
            FilterBar(source, frame, filters=[
                {"column": "day", "type": "date_range", "label": f"{name}: dates"},
                {"column": "platform", "type": "toggle", "label": f"{name}: platform"},
                {"column": "acquisition", "type": "toggle", "label": f"{name}: acquisition"},
            ]),
            RawHTML(html="<p>Twelve synthetic date × platform × acquisition facts; bound entries group by acquisition. Overall total 120, largest entry 90, three entries. Filters affect only this section.</p>"),
            Grid(columns=3, min_width=270, children=[
                Panel(title=f"{name}: {stat}", children=[
                    DoughnutChart(frame, "acquisition", "amount", f"{source}-{stat}", dataset_id=source,
                                  center_total=combined, value_format="number", precision=0,
                                  header_context="Synthetic additive units; current delivered dates/platform/acquisition" if combined else None,
                                  header_summary={"stat": stat, "scope": "all_categories"})])
                for stat in ("sum", "max", "count")
            ]),
        ])
    duplicate = pd.DataFrame({"acquisition": ["UA", "UA", "Organic"], "amount": [10, 20, 30]})
    ctx.add_section("Doughnut summaries — static entry grain", [
        RawHTML(html="<p>Static snapshot: UA 10, UA 20, Organic 30. Duplicate labels stay separate entries: total 60, largest entry 30, count 3. A bound chart instead groups matching category keys.</p>"),
        Grid(columns=3, min_width=270, children=[
            Panel(title=f"Static entries: {stat}", children=[
                DoughnutChart(duplicate, "acquisition", "amount", f"Duplicate {stat}", static=True,
                              header_summary={"stat": stat, "scope": "all_categories"})])
            for stat in ("sum", "max", "count")
        ]),
    ])
