"""Native Overview composition over fully observed synthetic acquisition cohorts."""

from datetime import date, timedelta

import pandas as pd

from trellum.components import (
    BarChart,
    DataSource,
    DoughnutChart,
    FilterBar,
    Grid,
    KpiRow,
    LineChart,
    Panel,
    RawHTML,
    Section,
    StackedBar,
)

DATASET_ID = "showcase_overview_daily"


def cohort_frame():
    """Return 168 deterministic facts; monetary amounts are USD, dates are UTC.

    Every October 1–28 cohort has its complete observed D7 outcome at the
    November 5, 2026 UTC snapshot. No value is a forecast or presentation total.
    """
    rows = []
    for day in range(28):
        for platform_index, platform in enumerate(("iOS", "Android")):
            for source_index, source in enumerate(("Organic", "Paid search", "Partner")):
                installs = 90 + 11 * day + 29 * platform_index + 37 * source_index + 9 * (day % 4)
                cost_cents = installs * (38 + 21 * source_index + 13 * platform_index) + 97 * (day % 5)
                payers = 4 + day // 3 + 2 * platform_index + 3 * source_index
                revenue_cents = payers * (610 + 17 * day + 53 * source_index) + 31 * platform_index
                rows.append({
                    "cohort_date": (date(2026, 10, 1) + timedelta(days=day)).isoformat(),
                    "platform": platform, "acquisition_type": source,
                    "installs": installs, "cost": cost_cents / 100,
                    "d7_revenue": revenue_cents / 100, "d7_payers": payers,
                })
    return pd.DataFrame(rows)


def kpi_specs():
    """Ordinary cards: daily additive means, followed by period ratios."""
    return [
        {"label": label, "agg": "avg_by_date", "column": column,
         "date_col": "cohort_date", "format": fmt}
        for label, column, fmt in (
            ("Daily avg cost · USD", "cost", "currency"),
            ("Daily avg installs", "installs", "number"),
            ("Daily avg D7 revenue · USD", "d7_revenue", "currency"),
        )
    ] + [
        {"label": label, "agg": "ratio", "numerator": numerator,
         "denominator": denominator, "format": fmt}
        for label, numerator, denominator, fmt in (
            ("Period recovery D7", "d7_revenue", "cost", "percent"),
            ("Cost / install · USD", "cost", "installs", "currency"),
            ("Revenue / install · USD", "d7_revenue", "installs", "currency"),
            ("D7 payer share", "d7_payers", "installs", "percent"),
        )
    ]


def add_sections(ctx):
    """Append an independently filtered Overview to a complete ReportContext."""
    frame = cohort_frame()
    common = {"df": frame, "x": "cohort_date", "dataset_id": DATASET_ID}
    trends = []
    for title, column, fmt in (
        ("Daily cost · USD", "cost", "currency"),
        ("Daily installs", "installs", "number"),
        ("Daily D7 revenue · USD", "d7_revenue", "currency"),
    ):
        trends.append(LineChart(title=title, y=column, y_labels=[title], y_format=fmt,
                                height="compact", header_summary={"stat": "mean", "label": "Daily avg"},
                                **common))
    for title, numerator, denominator, fmt in (
        ("Daily recovery D7", "d7_revenue", "cost", "percent"),
        ("Daily cost / install · USD", "cost", "installs", "currency"),
        ("Daily revenue / install · USD", "d7_revenue", "installs", "currency"),
    ):
        trends.append(LineChart(title=title, y_format=fmt,
                                ratios=[{"numerator": numerator, "denominator": denominator, "label": title}],
                                height="compact", header_summary={"stat": "mean", "label": "Daily avg"},
                                **common))

    ctx.add_section("Overview — synthetic acquisition cohorts", [
        DataSource(DATASET_ID, frame),
        FilterBar(DATASET_ID, frame, filters=[
            {"column": "cohort_date", "type": "date_range", "label": "Cohort dates"},
            {"column": "platform", "type": "toggle", "label": "Overview: platform"},
            {"column": "acquisition_type", "type": "toggle", "label": "Overview: acquisition type"},
        ]),
        RawHTML(html=(
            "<p>Deterministic synthetic acquisition cohorts, October 1–28, 2026. "
            "Installs are conversions; amounts are USD. All D7 outcomes are fully "
            "observed as of November 5, 2026 UTC; no forecasts are supplied.</p>"
            "<p>Cards show daily additive averages and ratios of selected period "
            "totals. Trend summaries average the plotted daily values: daily "
            "recovery and per-install means can differ from period ratios because "
            "each day has equal weight. Recovery is D7 revenue / cost.</p>"
        )),
        KpiRow(kpi_specs(), dataset_id=DATASET_ID),
        Grid(columns=3, min_width=280, children=[
            Panel(title=title, children=[DoughnutChart(
                frame, "acquisition_type", column, title, dataset_id=DATASET_ID,
                value_format=fmt,
            )])
            for title, column, fmt in (
                ("Cost by acquisition type · USD", "cost", "currency"),
                ("Installs by acquisition type", "installs", "number"),
                ("D7 revenue by acquisition type · USD", "d7_revenue", "currency"),
            )
        ]),
        Grid(columns=3, min_width=280, children=trends),
        Section("Detailed daily acquisition trends", [
            BarChart(frame, "cohort_date", "cost", "Daily cost and D7 revenue · USD",
                     y_cols=["cost", "d7_revenue"], y_labels=["Cost", "D7 revenue"],
                     y_format="currency", dataset_id=DATASET_ID),
            StackedBar(frame, "cohort_date", ["installs"], "Daily installs by acquisition type",
                       dataset_id=DATASET_ID, stack_by="acquisition_type",
                       normalize_toggle=True, value_format="number"),
        ], collapsible=True, default_collapsed=True, anchor="overview-daily-detail"),
    ])
