"""Complete synthetic event-linked traffic, shared controls and optional heights."""

import pandas as pd

from trellum.components import (
    DataSource, DataTable, DoughnutChart, FilterBar, Grid, KpiRow, Panel,
    RawHTML, Section, StackedBar, Toggle,
)

BREAKDOWNS = {"Channel": "channel", "New/Returning": "user_type", "Inactivity": "inactivity"}
VARIANTS = (
    ("local", "Traffic composition — local controls, responsive plots", False, None),
    ("shared", "Traffic composition — shared controls, responsive plots", True, None),
    ("shared_220", "Traffic composition — shared controls, 220px plots", True, 220),
)


def facts():
    """48 complete date × platform × channel × exclusive-persona facts.

    Counts represent disjoint synthetic events, never distinct people. Every
    event receives one channel and its channel's fixed D7 revenue exactly once.
    """
    return pd.DataFrame([
        {"day": f"2026-08-0{day}", "platform": platform, "channel": channel,
         "attribution_source": source, "user_type": "New" if persona == 0 else "Returning",
         "inactivity": ("0 days · new", "1–6 days", "7–29 days", "30+ days")[persona],
         "events": count * multiplier, "d7_revenue": count * multiplier * revenue}
        for day in (1, 2)
        for platform, multiplier in (("iOS", 1), ("Android", 2))
        for channel, source, counts, revenue in (
            ("UA", "Synthetic acquisition network", (10, 4, 2, 1), 1),
            ("RT", "Synthetic reactivation network", (2, 6, 4, 3), 3),
            ("Organic", "Synthetic store discovery", (20, 8, 5, 3), .5))
        for persona, count in enumerate(counts)
    ])


def add_sections(ctx):
    for key, title, shared, height in VARIANTS:
        frame = facts()
        dataset = "showcase_traffic_" + key
        paid, organic = dataset + "_paid", dataset + "_organic"
        stack_id, normalize_id = dataset + "_breakdown", dataset + "_mode"
        # Explicit source predicates, not an effect of expanding a disclosure.
        partitions = {paid: frame[frame.channel.isin(["UA", "RT"])].copy(),
                      organic: frame[frame.channel.eq("Organic")].copy()}
        controls = [Grid(columns=2, children=[
            Toggle(stack_id, list(BREAKDOWNS)),
            Toggle(normalize_id, ["Absolute", "Percentage"]),
        ])] if shared else []
        ctx.add_section(title, [
            DataSource(dataset, frame),
            *[DataSource(name, subset) for name, subset in partitions.items()],
            FilterBar(dataset, frame, filters=[
                {"column": "day", "type": "date_range", "label": "Event dates"},
                {"column": "platform", "type": "toggle", "label": "Platform"},
            ], propagate_to={name: {"day": "day", "platform": "platform"} for name in partitions}),
            RawHTML(html="<p>Synthetic teaching data: 48 complete date × platform × channel × persona facts "
                    "represent 408 disjoint events and $480 event-linked D7 revenue. Every event has exactly one "
                    "channel and one persona: new (0 inactivity), or returning after 1–6, 7–29, or 30+ days. "
                    "Revenue per event is UA $1, RT $3, Organic $0.50, assigned once; all cohorts are mature "
                    "as of 4 November 2026. These are synthetic source assignments, not distinct-person or "
                    "production attribution claims. Each metric/date has its own denominator.</p>"),
            KpiRow(dataset_id=dataset, kpis=[
                {"label": "Synthetic events", "agg": "sum", "column": "events", "format": "number"},
                {"label": "D7 revenue", "agg": "sum", "column": "d7_revenue", "format": "currency"},
                {"label": "D7 revenue / event", "agg": "ratio", "numerator": "d7_revenue", "denominator": "events", "format": "currency"},
                {"label": "Source facts", "agg": "count", "format": "number"},
            ]),
            *controls,
            Grid(columns=2, children=[
                StackedBar(frame, "day", [metric], caption, dataset_id=dataset,
                           stack_by="channel", stack_by_options=dict(BREAKDOWNS), normalize_toggle=True,
                           value_format=units, stack_sort="label_asc", show_controls=not shared, height=height,
                           stack_toggle_id=stack_id if shared else None,
                           normalize_toggle_id=normalize_id if shared else None,
                           header_context=context)
                for metric, caption, units, context in (
                    ("events", "Event composition", "number", "Disjoint synthetic events; denominator is matching events on each date."),
                    ("d7_revenue", "D7 revenue composition", "currency", "Event-linked USD; denominator is matching D7 revenue on each date."))
            ]),
            Grid(columns=2, children=[
                Panel(title=caption, children=[DoughnutChart(frame, "channel", metric, caption,
                    dataset_id=dataset, value_format=units)])
                for metric, caption, units in (("events", "Event share by channel", "number"),
                                               ("d7_revenue", "Revenue share by channel", "currency"))
            ]),
            *[Section(caption, collapsible=True, default_collapsed=True, children=[
                RawHTML(html=f"<p>{predicate} Disjoint and exhaustive together with the other partition. "
                        "The parent date/platform filters also apply here. Expanding this disclosure changes visibility only. "
                        "Rows retain date × platform × channel × persona grain; counts and D7 USD are additive facts. "
                        "These are source rows, not an aggregated group detail view.</p>"),
                DataTable(partitions[name], caption, dataset_id=name, columns=[
                    "day", "platform", "channel", "attribution_source", "user_type", "inactivity", "events", "d7_revenue"]),
            ]) for name, caption, predicate in (
                (paid, "Paid source facts", "Explicit source predicate: channel in UA, RT; full period 192 events / $372."),
                (organic, "Organic source facts", "Explicit source predicate: channel = Organic; full period 216 events / $108."))],
        ])
