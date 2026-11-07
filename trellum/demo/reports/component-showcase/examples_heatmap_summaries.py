"""Native, deterministic cell-entry summaries with section-local filters."""
import pandas as pd

from trellum.components import (
    DataSource,
    DataTable,
    FilterBar,
    Grid,
    HeatmapChart,
    Panel,
    RawHTML,
    TabGroup,
)


def facts():
    """Twenty delivered facts; duplicate 30-cell facts deliberately split 12+18."""
    return pd.DataFrame([
        {"day": f"2026-08-0{day}", "platform": platform, "x": x, "y": y,
         "units": value * day * multiplier, "source": source}
        for day in (1, 2) for platform, multiplier in (("iOS", 1), ("Android", 2))
        for x, y, values in (("09", "Mon", (0,)), ("12", "Mon", (10,)),
                              ("09", "Tue", (20,)), ("12", "Tue", (12, 18)))
        for source, value in enumerate(values, 1)
    ])


def summary(stat, **options):
    return {"stat": stat, "scope": "current_cells", **options}


def edge_facts():
    return pd.DataFrame([
        {"case": case, "day": "2026-08-01", "platform": "iOS", "x": x, "y": y, "units": value}
        for case, entries in (
            ("Absent and zero", [("09", "Mon", 0), ("12", "Mon", 10), ("09", "Tue", 20)]),
            ("Null input", [("09", "Mon", None), ("12", "Mon", 10)]),
            ("String input", [("09", "Mon", "2"), ("12", "Mon", 10)]),
            ("Boolean input", [("09", "Mon", True), ("12", "Mon", 10)]),
            ("All zero", [("09", "Mon", 0), ("12", "Tue", 0)]),
            ("Signed cells", [("09", "Mon", -20), ("12", "Mon", 10), ("12", "Tue", -5)]),
            ("Fractional rates", [("09", "Mon", .1), ("12", "Mon", .3)]),
            ("Invalid dropped group", [("09", "Mon", None), ("12", "Tue", 10)]),
        ) for x, y, value in entries
    ], dtype=object)


def _filters(source, df, *, cases=False):
    return [DataSource(source, df), FilterBar(source, df, filters=[
        {"column": "day", "type": "date_range", "label": "Observation dates"},
        {"column": "platform", "type": "toggle"},
        *([{"column": "case", "type": "toggle"}] if cases else []),
    ])]


def add_sections(ctx):
    df = facts()
    snapshot = df.groupby(["x", "y"], sort=False, as_index=False)["units"].sum()
    source = "showcase_heatmap_summary_pair"
    ctx.add_section("Heatmap summaries — static off and on", [
        *_filters(source, df),
        RawHTML(html="<p>Deterministic example quantities in units. The two static snapshots retain identical cells, key, labels and plot height; section filters affect only the raw source table here. Observation dates are August 1/2, 2026. Mon/Tue are supplied example categories, not weekdays computed from those dates.</p>"),
        Grid(columns=2, min_width=270, children=[Panel(title=f"Summary {state}", children=[
            HeatmapChart(snapshot, "x", "y", "units", f"Heatmap summary {state}", static=True,
                         log_scale=False, header_summary=summary("sum") if state == "on" else None)
        ]) for state in ("off", "on")]),
        DataTable(df, "Raw source rows · units (not matrix CSV)", dataset_id=source),
    ])
    source = "showcase_heatmap_summary_reducers"
    ctx.add_section("Heatmap summaries — filtered cell reducers and count", [
        *_filters(source, df),
        RawHTML(html="<p>Twenty source rows aggregate into four coordinate entries: 0, 90, 180, 270. Full cell total 540; mean 135; minimum 0; maximum 270; count 4. iOS + August 1 gives 0, 10, 20, 30: total 60; mean 15; count 4. Each 30-cell contribution is split into 12+18, so the mean denominator is aggregate cells, not source rows. Count is independent of numeric completeness; absent coordinates are excluded. No unique-user or warehouse-completeness claim.</p>"),
        Grid(columns=2, min_width=270, children=[Panel(title=stat.capitalize(), children=[
            HeatmapChart(df, "x", "y", "units", f"Filtered cells {stat}", dataset_id=source,
                         header_summary=summary(stat))]) for stat in ("sum", "mean", "min", "max", "count")]),
        DataTable(df, "Filtered raw source rows · units", dataset_id=source),
    ])
    source = "showcase_heatmap_summary_normalization"
    ctx.add_section("Heatmap summaries — raw units and relative colors", [
        *_filters(source, df),
        RawHTML(html="<p>Identical raw cell mean (135 initially) under global and row-relative colors. Independent authored context describes units; filters belong only to this section. Normalization, log scaling and zoom do not redefine summary arithmetic.</p>"),
        Grid(columns=2, min_width=270, children=[Panel(title=name, children=[
            HeatmapChart(df, "x", "y", "units", f"{name} cell mean", dataset_id=source,
                         normalize=normalize, log_scale=False, header_summary=summary("mean"),
                         header_context="Supplied units · independently authored context" if normalize else None)
        ]) for name, normalize in (("Global", None), ("Row-relative", "row"))]),
    ])
    edge = edge_facts()
    source = "showcase_heatmap_summary_eligibility"
    ctx.add_section("Heatmap summaries — missing, zero and input eligibility", [
        *_filters(source, edge, cases=True),
        RawHTML(html="<p>Select one case. Absent and zero has three observed entries and one truly absent coordinate. Null, string and boolean values retain legacy plot coercions but withhold numeric summaries; count remains an entry count. drop_empty keeps a cell only when both coordinate groups have positive cells, so zero-only and negative-only groups may disappear. The original-input guard includes invalid values in groups later dropped. Signed finite values are eligible. Fractional rates demonstrate an unweighted percent mean; additive rate sums are rejected. Summary arithmetic does not certify missingness, coordinate identity or upstream precision.</p>"),
        Grid(columns=2, min_width=270, children=[Panel(title=f"{stat} · drop_empty={drop}", children=[
            HeatmapChart(edge, "x", "y", "units", f"Eligibility {stat} {drop}", dataset_id=source,
                         drop_empty=drop, header_summary=summary(stat))])
            for stat, drop in (("mean", False), ("count", False), ("mean", True), ("count", True))]),
        TabGroup(tabs=[{"label": "Raw source rows", "content": [
            DataTable(edge, "Eligibility raw source rows · supplied units", dataset_id=source)]},
            {"label": "Rate snapshot", "content": [HeatmapChart(
                pd.DataFrame({"x": ["09", "12"], "y": ["Mon", "Mon"], "units": [.1, .3]}),
                "x", "y", "units", "Fractional rate mean", static=True, value_format="percent",
                header_context="Supplied fractional rates · unweighted cell mean",
                header_summary=summary("mean", label="Rate <mean> & supplied"))]}]),
    ])
