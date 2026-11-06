"""Optional calendar-month context on synthetic daily reporting categories."""

import pandas as pd

from trellum.components import (
    AreaChart,
    BarChart,
    ComboChart,
    DataSource,
    FilterBar,
    Grid,
    LineChart,
    RawHTML,
    StackedBar,
)


def add_sections(ctx):
    """Four native sections with independent static and filterable observations."""
    days = pd.date_range("2026-09-15", "2026-11-15").strftime("%Y-%m-%d")
    daily = pd.DataFrame({"day": days, "orders": [32 + (i * 7) % 29 for i in range(len(days))]})
    ctx.add_section("Month context — off and on", [
        RawHTML(html="<p>Identical synthetic daily observations, 15 September–15 November 2026. Month context is off by default. Enabling it adds a 24px month row and quiet dividers; category spacing, date ticks, plot heights and data stay unchanged.</p>"),
        Grid(columns=2, min_width=280, children=[
            LineChart(daily, "day", "orders", "Daily line · default", static=True),
            LineChart(daily, "day", "orders", "Daily line · month context", static=True, month_context=True),
            BarChart(daily, "day", "orders", "Daily bars · default", static=True),
            BarChart(daily, "day", "orders", "Daily bars · month context", static=True, month_context=True),
        ]),
    ])
    filtered = pd.DataFrame([
        {"day": day, "platform": platform, "channel": channel,
         "orders": 11 + p * 37 + c * 19 + (i * (3 + p + c)) % 17}
        for i, day in enumerate(days)
        for p, platform in enumerate(("Web", "App"))
        for c, channel in enumerate(("Direct", "Partner"))
    ])
    source = "showcase_month_context_daily"
    ctx.add_section("Month context — independent daily selection", [
        DataSource(source, filtered),
        FilterBar(source, filtered, filters=[
            {"column": "day", "type": "date_range", "label": "Daily reporting window"},
            {"column": "platform", "type": "toggle", "label": "Reporting platform"},
            {"column": "channel", "type": "toggle", "label": "Reporting channel"},
        ]),
        RawHTML(html="<p>Every day has distinct observations for all four platform/channel combinations. These controls update only this source. Narrow the date window to change the visible months; drag to zoom and double-click a bound plot to reset.</p>"),
        LineChart(filtered, "day", "orders", "Selected daily line", dataset_id=source, month_context=True),
        BarChart(filtered, "day", "orders", "Selected daily bars", dataset_id=source, month_context=True),
    ])
    year = pd.DataFrame({"day": ["2026-12-29", "2026-12-31", "2027-01-02", "2027-01-04"], "orders": [23, 39, 31, 46]})
    leap = pd.DataFrame({"day": ["2028-01-31", "2028-02-02", "2028-02-29", "2028-04-01"], "orders": [12, 0, 27, 19]})
    ctx.add_section("Month context — supplied dates and calendar boundaries", [
        RawHTML(html="<p>These are explicitly synthetic daily reporting-date labels with missing observations. Missing days remain compressed category spacing, and omitted months get no imaginary span. Leap February is valid. First and last observations make no claim about coverage or completeness.</p>"),
        Grid(columns=2, min_width=280, children=[
            LineChart(year, "day", "orders", "Daily observations across a year", static=True, month_context=True),
            BarChart(leap, "day", "orders", "Leap February · missing days and March", static=True, month_context=True),
        ]),
    ])
    family = daily.assign(returns=[4 + i % 5 for i in range(len(days))])
    ctx.add_section("Month context — shared chart families", [
        RawHTML(html="<p>The same optional context works with native areas, stacked bars and dual-axis combos. Existing legends and controls remain. CSV retains its supplied categories and values. Report/section captures include the row; canvas-only captures include the plot and dividers, with date ticks, but omit surrounding text.</p>"),
        Grid(columns=3, min_width=240, children=[
            AreaChart(family, "day", "orders", "Daily order area", static=True, month_context=True),
            StackedBar(family, "day", ["orders", "returns"], "Daily supplied quantities", static=True, month_context=True, value_format="number"),
            ComboChart(family, "day", ["orders"], ["returns"], "Daily quantities · separate axes", static=True, month_context=True, bar_format="number"),
        ]),
    ])
