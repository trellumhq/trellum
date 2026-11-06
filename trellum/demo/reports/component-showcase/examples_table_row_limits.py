"""Examples for the optional ranked DataTable row-count selector."""

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, KpiRow, RawHTML


def _frame():
    return pd.DataFrame([
        {"Event": f"EV-{i + 1:03d}", "Platform": "iOS" if i < 60 else "Android",
         "Campaign": f"Campaign {i + 1:03d}",
         "Value": 0 if i in (0, 1, 60, 61) else (i % 11) * 10}
        for i in range(120)
    ])


def add_sections(ctx):
    frame = _frame()
    base = {"df": frame, "max_rows": 20, "searchable": True, "sortable": True}
    ctx.add_section("DataTable row limits — legacy", [
        RawHTML(html="<p>The legacy table uses max_rows=20. Its CSV still contains every matching row.</p>"),
        DataTable(title="Legacy display limit", static=True, **base),
    ])
    ctx.add_section("DataTable row limits — opt in", [
        RawHTML(html="<p>The Rows selector starts at 20 and changes display only; search, sorting and CSV use the full population.</p>"),
        DataTable(title="Optional row selector", static=True, row_limit=20, **base),
    ])
    ctx.add_section("DataTable row limits — filtered", [
        DataSource("row_limit_events", frame),
        FilterBar("row_limit_events", frame, filters=[
            {"column": "Platform", "label": "Platform", "type": "toggle"},
        ]),
        RawHTML(html="<p>Choose All to display every delivered matching event (120, or 60 per platform). Search also matches rows beyond the initial 10. The metric remains the sum and count of all filtered events.</p>"),
        KpiRow([{"column": "Value", "label": "Total value", "agg": "sum"},
                {"column": "Event", "label": "Events", "agg": "count"}],
               dataset_id="row_limit_events"),
        DataTable(frame, title="Filterable events", dataset_id="row_limit_events",
                  max_rows=5, row_limit=10, searchable=True, sortable=True),
    ])
