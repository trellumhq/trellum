"""Native row actions over deterministic daily campaign/platform observations."""

import pandas as pd

from trellum.components import (
    DataSource,
    DataTable,
    FilterBar,
    KpiRow,
    LineChart,
    RawHTML,
    ScopedDataSource,
)

BASE = "table_drill_facts"
CAMPAIGNS = "table_drill_campaigns"
DETAIL = "table_drill_detail"


def observation_frame():
    rows = []
    for si, source in enumerate(["Paid search", "Organic", "Partner", "Affiliate"]):
        for ci, campaign in enumerate(["New", "Returning"]):
            for day in range(1, 5):
                for pi, platform in enumerate(["iOS", "Android"]):
                    if source == "Affiliate" and platform == "Android":
                        continue
                    installs = 0 if (si, ci, day, pi) == (0, 0, 1, 0) else (4 - si) * 5 + day + ci + pi
                    spend = 0 if installs == 0 else ((4 - si) * 500 + day * 31 + ci * 17 + pi * 13) / 100
                    revenue = round(spend * (0.8 + ci * 0.2), 2)
                    rows.append({"Day": pd.Timestamp(2026, 10, day), "Platform": platform,
                                 "Source": source, "Campaign": campaign, "source_key": source,
                                 "campaign_key": f"{source} / {campaign}", "Installs": installs,
                                 "Spend": spend, "Revenue": revenue,
                                 "CPI": spend / installs if installs else None})
    return pd.DataFrame(rows).sort_values(["Installs", "source_key", "campaign_key", "Day", "Platform"],
                                          ascending=[False, True, True, True, True], kind="stable").reset_index(drop=True)


def action(column, label):
    return {"dataset_id": DETAIL, "column": column, "value_column": column, "label_column": label,
            "context": [{"column": "source_key", "label": "Source"},
                        {"column": "campaign_key", "label": "Campaign"}]}


def table_totals():
    return {"Installs": {"agg": "sum", "precision": 0},
            "Spend": {"agg": "sum", "format": "currency"},
            "Revenue": {"agg": "sum", "format": "currency"},
            "CPI": {"agg": "ratio", "numerator": "Spend", "denominator": "Installs", "format": "currency"}}


def add_sections(ctx):
    facts = observation_frame()
    # The campaign table intentionally ignores its own entity selection.
    accepted = ctx.config.setdefault("validation", {}).setdefault("accept_inactive_filters", {})
    accepted[CAMPAIGNS] = list(dict.fromkeys([*accepted.get(CAMPAIGNS, []), "campaign_key"]))
    ctx.add_section("Table drill-down — report selection", [
        DataSource(BASE, facts),
        FilterBar(BASE, facts, filters=[
            {"column": "Day", "type": "date_range"},
            {"column": "Platform", "type": "toggle"},
        ]),
    ])
    ctx.add_section("Table drill-down — selection owners", [
        ScopedDataSource(CAMPAIGNS, parent=BASE),
        ScopedDataSource(DETAIL, parent=BASE),
        FilterBar(DETAIL, facts, filters=[
            {"column": "source_key", "label": "Source"},
            {"column": "campaign_key", "label": "Campaign", "depends_on": "source_key",
             "clear_on_parent_change": True},
        ], propagate_to={CAMPAIGNS: {"source_key": "source_key"}}),
        RawHTML(html=(
            "<p>56 deterministic synthetic daily campaign/platform observations cover four sources, "
            "two campaigns per source and four dates. Each row is one observation; labels repeat. "
            "These are observation tables, not ranked aggregate source summaries. "
            "Row buttons replace the owning dropdown selection; press the sole selected key again to clear it. "
            "Dropdowns retain their native multiple selection. Campaign keys include their source.</p>"
        )),
    ])
    columns = ["Source", "Campaign", "Day", "Platform", "Installs", "Spend", "Revenue", "CPI"]
    ctx.add_section("Table drill-down — static reference off and on", [
        RawHTML(html="<p>The same three supplied observations, without and with an optional source action. Raw identity columns stay hidden from cells and CSV.</p>"),
        DataTable(facts.head(3), "Static observations · default", columns=columns, static=True),
        DataTable(facts.head(3), "Static observations · source actions", columns=columns, static=True,
                  drill_down=action("source_key", "Source")),
    ])
    ctx.add_section("Table drill-down — source observations", [
        RawHTML(html="<p>All report-filtered observations and totals, independent of source/campaign selection. Rows starts at 10; Affiliate lies beyond the initial slice. Local search, sorting, row limits and CSV affect only this table.</p>"),
        DataTable(facts, "Source observations", dataset_id=BASE, columns=columns,
                  drill_down=action("source_key", "Source"), row_limit=10, searchable=True,
                  sortable=True, totals=table_totals()),
    ])
    ctx.add_section("Table drill-down — campaign observations", [
        RawHTML(html="<p>These observations and totals follow Source and ignore Campaign. Clearing Source also clears Campaign. Date/platform remain. Affiliate has only iOS observations: Affiliate plus Android retains its context and explains the empty result.</p>"),
        DataTable(facts, "Campaign observations", dataset_id=CAMPAIGNS, columns=columns,
                  drill_down=action("campaign_key", "Campaign"), row_limit=10, searchable=True,
                  sortable=True, totals=table_totals(), pin_header=True, pin_footer=True),
    ])
    ctx.add_section("Table drill-down — selected detail", [
        RawHTML(html="<p>Native detail KPI and daily trend follow both dropdown selections. Real measured zero remains zero. CPI totals divide underlying summed spend by installs; row ratios are never added or averaged.</p>"),
        KpiRow([{"column": "Installs", "label": "Selected installs", "agg": "sum", "format": "number"}],
               dataset_id=DETAIL),
        LineChart(facts, x="Day", y="Installs", title="Selected daily installs", dataset_id=DETAIL),
    ])
