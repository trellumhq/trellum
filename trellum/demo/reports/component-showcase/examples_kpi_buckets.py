"""Native eight-chart composition over one deterministic acquisition snapshot."""

from datetime import date, timedelta

import pandas as pd

from trellum.components import (
    BarChart,
    CalendarCoverage,
    DataSource,
    FilterBar,
    Grid,
    RawHTML,
    TabGroup,
    Toggle,
    Visible,
)
from trellum.components.calendar_coverage import calendar_bounds

SOURCE = "showcase_kpi_bucket_facts"
GRAINS = ("daily", "weekly", "monthly", "quarterly")
HORIZONS = (1, 7, 30)
START, END = "2026-06-12", "2026-09-23"


def facts():
    """208 platform/date facts; supplied additive counts, never distinct people."""
    records = []
    day = date.fromisoformat(START)
    while day <= date.fromisoformat(END):
        for platform, installs, cost, revenue, retained, depositors in (
            ("iOS", 10, 20, (5, 12, 30), (4, 3, 2), (1, 2, 3)),
            ("Android", 30, 30, (15, 18, 30), (12, 9, 6), (2, 3, 4)),
        ):
            row = {"day": day.isoformat(), "platform": platform, "installs": installs, "cost": cost}
            for grain in GRAINS:
                row[grain + "_bucket"] = calendar_bounds(day, grain)[0].isoformat()
            for i, horizon in enumerate(HORIZONS):
                row.update(
                    {
                        f"revenue_d{horizon}": revenue[i],
                        f"retained_d{horizon}": retained[i],
                        f"depositors_d{horizon}": depositors[i],
                    }
                )
            records.append(row)
        day += timedelta(days=1)
    return pd.DataFrame(records)


def declaration(frame, grain, horizon):
    column = grain + "_bucket"
    buckets = {
        key: tuple(x.isoformat() for x in calendar_bounds(date.fromisoformat(key), grain))
        for key in sorted(frame[column].unique())
    }
    return CalendarCoverage(
        dataset_id=SOURCE,
        date_column="day",
        start=START,
        end=END,
        grain=grain,
        bucket_column=column,
        buckets=buckets,
        input_columns=(
            "cost",
            "installs",
            f"revenue_d{horizon}",
            f"retained_d{horizon}",
            f"depositors_d{horizon}",
        ),
        horizon={"name": f"D{horizon}", "days": horizon, "observed_through": "2026-11-04"},
    )


def charts(frame, grain, horizon, context=None):
    revenue, retained, depositors = (
        f"{name}_d{horizon}" for name in ("revenue", "retained", "depositors")
    )
    return [
        BarChart(
            frame,
            grain + "_bucket",
            numerator,
            title,
            dataset_id=SOURCE,
            y_format=unit,
            bucket_context=context,
            ratios=[{"numerator": numerator, "denominator": denominator, "label": title}]
            if denominator
            else None,
        )
        for title, numerator, denominator, unit in (
            ("Cost", "cost", None, "currency"),
            ("Installs", "installs", None, "number"),
            ("CPI", "cost", "installs", "currency"),
            (f"D{horizon} revenue", revenue, None, "currency"),
            (f"D{horizon} recovery", revenue, "cost", "percent"),
            (f"D{horizon} retention", retained, "installs", "percent"),
            (f"D{horizon} revenue / install", revenue, "installs", "currency"),
            (f"D{horizon} first-depositor proxies", depositors, None, "number"),
        )
    ]


def add_sections(ctx):
    frame = facts()
    ctx.add_section(
        "KPI buckets — original snapshot and filters",
        [
            DataSource(SOURCE, frame),
            FilterBar(
                SOURCE,
                frame,
                filters=[
                    {"column": "day", "type": "date_range", "label": "Acquisition dates"},
                    {"column": "platform", "type": "toggle", "label": "Platform"},
                ],
            ),
            RawHTML(
                html="<p>208 deterministic iOS/Android acquisition facts, 12 June–23 September 2026. "
                "D1/D7/D30 supplied counts and first-depositor proxies are fully mature as of 4 November 2026; "
                "no forecasts or period distinct-person claims. Native bars sum original facts and divide sums. "
                "Optional calendar metadata does not change those values. CSV retains plotted values only; extra hover dates are not exported.</p>"
            ),
        ],
    )
    tabs = []
    for variant, enabled in (("Baseline", False), ("Calendar context", True)):
        prefix = "kpi_buckets_" + ("on" if enabled else "off")
        views = []
        for grain in GRAINS:
            horizon_views = []
            for horizon in HORIZONS:
                coverage = declaration(frame, grain, horizon) if enabled else None
                horizon_views.append(
                    Visible(
                        toggle_target=prefix + "_horizon",
                        toggle_value=f"D{horizon}",
                        children=[
                            *([coverage] if coverage else []),
                            Grid(
                                columns=2,
                                min_width=280,
                                children=charts(frame, grain, horizon, coverage),
                            ),
                        ],
                    )
                )
            views.append(
                Visible(
                    toggle_target=prefix + "_grain",
                    toggle_value=grain.title(),
                    children=horizon_views,
                )
            )
        tabs.append(
            {
                "label": variant,
                "content": [
                    Grid(
                        columns=2,
                        children=[
                            Toggle(prefix + "_grain", [g.title() for g in GRAINS]),
                            Toggle(prefix + "_horizon", [f"D{h}" for h in HORIZONS], default="D7"),
                        ],
                    ),
                    *views,
                ],
            }
        )
    ctx.add_section(
        "KPI buckets — eight native trends", [TabGroup(tabs=tabs, id="kpi_bucket_context_views")]
    )
