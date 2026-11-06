"""Optional totals over deterministic campaign and event observations."""

from decimal import Decimal

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, KpiRow, RawHTML


def campaign_frame():
    return pd.DataFrame({
        "Campaign": ["Measured zero", "Launch", "Ongoing"],
        "Spend": [Decimal("0.00"), Decimal("10.01"), Decimal("90.09")],
        "Installs": [0, 3, 27],
        "Revenue": [Decimal("0.00"), Decimal("30.03"), Decimal("90.09")],
        "ROAS": [None, 3, 1],
        "Recovery": [None, 3, 1],
    })


def declarations():
    return {
        "Spend": {"agg": "sum", "format": "currency"},
        "Installs": {"agg": "sum", "precision": 0},
        "Revenue": {"agg": "sum", "format": "currency"},
        "ROAS": {"agg": "ratio", "numerator": "Revenue", "denominator": "Spend", "format": "ratio"},
        "Recovery": {"agg": "ratio", "numerator": "Revenue", "denominator": "Spend", "format": "percent", "precision": 1},
    }


def event_frame():
    return pd.DataFrame([
        {"Event": f"Contributor {i + 1:02d}", "Platform": "iOS" if i % 2 == 0 else "Android",
         "Spend": i + 1, "Installs": i % 7,
         "Revenue": (i + 1) * (2 if i % 3 == 0 else 1), "ROAS": 2 if i % 3 == 0 else 1}
        for i in range(36)
    ])


def add_sections(ctx):
    events = event_frame()
    # Each event is a distinct contributor; platform filters never duplicate
    # a coarse observation across a fabricated dimension.
    ctx.add_section("Table totals — delivered event filters", [
        DataSource("table_totals_events", events),
        FilterBar("table_totals_events", events, filters=[
            {"column": "Platform", "label": "Totals platform", "type": "toggle"},
        ]),
    ])
    campaigns = campaign_frame()
    ctx.add_section("Table totals — exact campaign snapshot off and on", [
        RawHTML(html=(
            "<p>Three independent synthetic campaign observations include measured zero. "
            "Exact totals: spend $100.10, installs 30, revenue $120.12; weighted ROAS "
            "120.12 / 100.10 = 1.20x (120.0%). The two defined row rates average 2.00x, "
            "which is not the weighted total. Decimal inputs retain their supplied precision.</p>"
        )),
        DataTable(campaigns, "Campaigns · default", static=True),
        DataTable(campaigns, "Campaigns · totals", static=True, totals=declarations()),
    ])
    totals = declarations()
    del totals["Recovery"]
    ctx.add_section("Table totals — all matching contributors", [
        RawHTML(html=(
            "<p>36 distinct synthetic event contributors: spend 666, installs 105, revenue 876. "
            "Rows starts at 10. Sorting precedes the display limit; local search narrows table "
            "totals and CSV, including contributors beyond the first ten. Rows and local search "
            "leave the full filtered revenue KPI unchanged; the platform filter changes both.</p>"
        )),
        KpiRow([{"column": "Revenue", "label": "All filtered event revenue", "agg": "sum", "format": "currency"}],
               dataset_id="table_totals_events"),
        DataTable(events, "Event contributions · totals", dataset_id="table_totals_events",
                  row_limit=10, searchable=True, sortable=True, totals=totals),
    ])
    incomplete = pd.DataFrame({
        "Observation": ["Measured zero", "Unreported revenue"],
        "Spend": [0, 0], "Installs": [0, 4], "Revenue": [Decimal("0"), None],
        "ROAS": [None, None], "Recovery": [None, None],
    })
    ctx.add_section("Table totals — independent incomplete metrics", [
        RawHTML(html=(
            "<p>Spend is a measured $0.00 and installs total 4. Revenue is incomplete, so its "
            "total and revenue-based ROAS are withheld. The independent install/spend ratio "
            "is also withheld because its summed denominator is zero. Reasons name each metric.</p>"
        )),
        DataTable(incomplete, "Incomplete observations · totals", static=True, totals={
            **declarations(),
            "Recovery": {"agg": "ratio", "numerator": "Installs", "denominator": "Spend", "format": "percent"},
        }),
    ])
