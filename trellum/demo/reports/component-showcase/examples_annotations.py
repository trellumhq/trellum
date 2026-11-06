"""Synthetic dated events using native LineChart annotations and filters."""

import pandas as pd

from trellum.components import DataSource, FilterBar, LineChart, RawHTML

ANNOTATIONS = {
    "events": [
        {"date": "2026-07-28", "end_date": "2026-08-05", "label": "Synthetic carry-in window", "type": "event"},
        {"date": "2026-08-02", "label": "Synthetic release A", "type": "release"},
        {"date": "2026-08-03", "label": "Synthetic campaign start", "type": "campaign"},
        {"date": "2026-08-04", "end_date": "2026-08-12", "label": "Synthetic experiment", "type": "ab_test"},
        {"date": "2026-08-06", "label": "Synthetic same-day release", "type": "release"},
        {"date": "2026-08-06", "label": "Synthetic same-day incident", "type": "incident"},
        {"date": "2026-08-06", "label": "Synthetic same-day campaign", "type": "campaign"},
        {"date": "2026-08-08", "label": "Synthetic maintenance", "type": "incident"},
        {"date": "2026-08-11", "label": "Synthetic midweek release", "type": "release"},
        {"date": "2026-08-18", "end_date": "2026-08-20", "label": "Synthetic deliberately long event name with a complete original date range that wraps on a phone", "type": "event"},
        {"date": "2026-08-22", "label": "Synthetic later release", "type": "release"},
        {"date": "2026-08-26", "label": "Synthetic final weekly bucket exclusion", "type": "event"},
        {"date": "2026-08-30", "end_date": "2026-09-04", "label": "Synthetic carry-out window", "type": "campaign"},
    ],
    "weekday_highlight": {"days": [0], "label": "Sundays", "default_visible": True},
}


def add_sections(ctx):
    """Four independent native examples; no external data or configuration writes."""
    days = pd.date_range("2026-08-01", periods=31).strftime("%Y-%m-%d")
    daily = pd.DataFrame({"day": days, "observations": [36 + (i * 7) % 23 for i in range(31)]})
    ctx.add_section("Annotations — synthetic daily comparison", [
        RawHTML(html="<p>Synthetic August observations, identical in both charts. Full labels keep the legacy presentation; grouped markers keep every eligible event in a collapsed list. Expand before report export to include event names.</p>"),
        LineChart(daily, "day", "observations", "Synthetic daily · full labels", static=True, event_labels="full", colors=["#2563eb"]),
        LineChart(daily, "day", "observations", "Synthetic daily · grouped events", static=True, colors=["#2563eb"], header_context="Synthetic observations · original event dates appear in details"),
    ])
    filtered = pd.DataFrame([
        {"day": day, "channel": channel, "observations": offset + (i * factor) % 19}
        for channel, offset, factor in (("Direct", 20, 3), ("Partner", 41, 5))
        for i, day in enumerate(days)
    ])
    ctx.add_section("Annotations — synthetic independent date selection", [
        DataSource("showcase_annotations_daily", filtered),
        FilterBar("showcase_annotations_daily", filtered, filters=[
            {"column": "day", "type": "date_range", "label": "Event window"},
            {"column": "channel", "type": "toggle", "label": "Observation channel"},
        ]),
        RawHTML(html="<p>Change or clear the date window to change event membership. Channel changes observations, not event names. These controls affect only this source; the comparison above stays fixed. Drag a plot to zoom; double-click a bound plot to reset.</p>"),
        LineChart(filtered, "day", "observations", "Synthetic selected window", dataset_id="showcase_annotations_daily"),
    ])
    weekly = pd.DataFrame({"day": ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24"], "observations": [270, 315, 292, 338]})
    ctx.add_section("Annotations — synthetic weekly mapping", [
        RawHTML(html="<p>Supplied weekly observations use Monday category labels. Existing annotations snap to the preceding label; ranges can snap to one line. Details retain original dates and complete ranges, including the carry-in window. The 26 August event is beyond the last label (24 August) and remains excluded even though it falls in that final weekly bucket.</p>"),
        LineChart(weekly, "day", "observations", "Synthetic weekly · snapped dates", static=True),
    ])
    quiet = pd.DataFrame({"day": ["2026-09-14", "2026-09-15", "2026-09-16"], "observations": [17, 21, 19]})
    categories = pd.DataFrame({"category": ["First", "Second", "Third"], "observations": [11, 27, 18]})
    ctx.add_section("Annotations — synthetic quiet and category axes", [
        RawHTML(html="<p>No eligible dated events means no details row or reserved space. Category axes do not map dates. Recurring Sundays remain plain native lines on the August daily plots; they never become numbered events.</p>"),
        LineChart(quiet, "day", "observations", "Synthetic quiet dates", static=True),
        LineChart(categories, "category", "observations", "Synthetic category axis", static=True),
    ])
