"""Deterministic native adaptive event examples; reusable without navigation wiring.

Sparse events keep their short names beside the markers, even on a phone. The
dense October fixture collapses names to small markers where they would overlap,
and its date filter returns to names when the window narrows."""
import pandas as pd

from trellum.components import DataSource, FilterBar, LineChart

ANNOTATIONS = {
    "events": [
        {"date": "2026-08-27", "label": "Launch", "type": "campaign"},
        {"date": "2026-09-07", "label": "Store update", "type": "release"},
        {"date": "2026-09-28", "end_date": "2026-10-04", "label": "Carry-in campaign", "type": "campaign"},
        *[{"date": f"2026-10-{i + 1:02}", "label": f"October release {i + 1}", "type": "release"} for i in range(12)],
        {"date": "2026-10-06", "label": "Same-day incident", "type": "incident"},
        {"date": "2026-10-18", "end_date": "2026-11-04", "label": "Long event name with complete original dates and a range that continues beyond the selected month", "type": "event"},
    ],
    "weekday_highlight": {"days": [0], "label": "Sundays", "default_visible": True},
}


def observations(start="2026-08-20"):
    """One supplied observation per UTC day and channel, without preaggregation."""
    days = pd.date_range(start, periods=31).strftime("%Y-%m-%d")
    return pd.DataFrame([
        {"day": day, "channel": channel, "value": offset + (i * 7) % 23}
        for channel, offset in (("Direct", 20), ("Partner", 41))
        for i, day in enumerate(days)
    ])


def add_sections(ctx):
    """Register compact native cases; caller supplies ANNOTATIONS config.

    Resize the page, or narrow the Event dates filter, to move the dense chart
    between markers only and readable names. Exact names and original dates are
    always available from the marker or the chart's All events control.
    """
    sparse = observations().groupby("day", as_index=False).value.sum()
    ctx.add_section("Readable events", [
        LineChart(sparse, "day", "value", "Sparse events", static=True),
    ])
    dense = observations("2026-10-01")
    ctx.add_section("", [
        DataSource("adaptive_events", dense),
        FilterBar("adaptive_events", dense, filters=[
            {"column": "day", "type": "date_range", "label": "Event dates"},
            {"column": "channel", "type": "toggle", "label": "Channel"},
        ]),
    ])
    ctx.add_section("Crowded events", [
        LineChart(dense, "day", "value", "Dense events", dataset_id="adaptive_events"),
    ])
    weekly = dense[dense.day.isin(["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"])].groupby("day", as_index=False).value.sum()
    ctx.add_section("Original dates and weekly positions", [
        LineChart(weekly, "day", "value", "Weekly events", static=True),
    ])
    quiet = observations("2026-12-01").groupby("day", as_index=False).value.sum()
    ctx.add_section("No dated events", [
        LineChart(quiet, "day", "value", "Quiet events", static=True),
    ])
