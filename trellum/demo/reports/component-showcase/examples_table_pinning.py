"""Opt-in DataTable sticky edges inside its bounded native scroller."""

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, Grid, KpiRow, RawHTML


def _snapshot():
    return pd.DataFrame([
        {"Observation": f"Session {i + 1:02d}", "Platform": "iOS" if i % 2 == 0 else "Android",
         "Source": ("Search", "Video", "Referral")[i % 3], "Sessions": i + 1,
         "Purchases": (i + 1) % 5, "Revenue": (i + 1) * 3.25}
        for i in range(30)
    ])


def _totals():
    return {"Sessions": {"agg": "sum", "precision": 0},
            "Purchases": {"agg": "sum", "precision": 0},
            "Revenue": {"agg": "sum", "format": "currency"}}


def _observations():
    return pd.DataFrame([
        {"Observation": f"Visit {i + 1:02d}", "Platform": "iOS" if i % 2 == 0 else "Android",
         "Source": ("Search", "Social", "Referral")[i % 3], "Sessions": i + 1,
         "Purchases": (i * 3) % 7, "Revenue": (i + 1) * (2 if i % 4 == 0 else 1),
         "Conversion": ((i * 3) % 7) / (i + 1)}
        for i in range(42)
    ])


def add_sections(ctx):
    snapshot = _snapshot()
    common = {"df": snapshot, "static": True, "columns": [
        "Observation", "Platform", "Source", "Sessions", "Purchases", "Revenue"],
        "totals": _totals(), "max_rows": 30}
    ctx.add_section("DataTable pinning — static snapshots", [
        RawHTML(html=("<p>All four tables use the same 30 supplied observations. Pinning keeps the "
                      "selected table edge visible inside its own scroll area; horizontal scrolling "
                      "still moves every column, including Observation.</p>")),
        Grid([
            DataTable(title="Default · no pinned edges", **common),
            DataTable(title="Pinned header", pin_header=True, **common),
            DataTable(title="Pinned total footer", pin_footer=True, **common),
            DataTable(title="Pinned header and total", pin_header=True, pin_footer=True, **common),
        ], columns=2, min_width=360),
    ])

    observations = _observations()
    ctx.add_section("DataTable pinning — filterable observations", [
        DataSource("table_pinning_visits", observations),
        FilterBar("table_pinning_visits", observations, filters=[
            {"column": "Platform", "label": "Platform", "type": "toggle"},
            {"column": "Source", "label": "Source", "type": "toggle"},
        ]),
        RawHTML(html=("<p>42 distinct visits with additive sessions, purchases and revenue. Search and "
                      "the Rows selector change only this table's display and CSV; report filters "
                      "change both its delivered totals and the full-scope KPI.</p>")),
        KpiRow([{"column": "Revenue", "label": "All filtered visit revenue", "agg": "sum", "format": "currency"}],
               dataset_id="table_pinning_visits"),
        DataTable(observations, "Pinned visit contributions", dataset_id="table_pinning_visits",
                  columns=["Observation", "Platform", "Source", "Sessions", "Purchases", "Revenue", "Conversion"],
                  row_limit=10, searchable=True, sortable=True, totals={
                      **_totals(),
                      "Conversion": {"agg": "ratio", "numerator": "Purchases",
                                     "denominator": "Sessions", "format": "percent", "precision": 1},
                  }, pin_header=True, pin_footer=True),
    ])
