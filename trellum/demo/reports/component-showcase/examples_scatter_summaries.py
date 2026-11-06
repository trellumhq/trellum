"""Deterministic native scatter summaries over synthetic point observations."""

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, Grid, Panel, RawHTML, ScatterChart, TabGroup


def facts():
    """Eight synthetic date × platform × channel observations, no person grain."""
    return pd.DataFrame([
        {"day": f"2026-10-0{day}", "platform": platform, "channel": channel,
         "spend": spend * day * factor, "revenue": revenue * day * factor,
         "supplied_size": size * day * factor}
        for day in (1, 2)
        for platform, factor in (("iOS", 1), ("Android", 2))
        for channel, spend, revenue, size in (("UA", 10, 15, 100), ("Organic", 4, 5, 40))
    ])


def summary(stat, axis=None, **kwargs):
    return {"stat": stat, "scope": "point_entries", **({"axis": axis} if axis else {}), **kwargs}


def filters(source, df):
    return [DataSource(source, df), FilterBar(source, df, filters=[
        {"column": "day", "type": "date_range"},
        {"column": "platform", "type": "toggle"},
    ])]


def add_sections(ctx):
    df = facts()
    ctx.add_section("Scatter summaries — off and on", [
        RawHTML(html="<p>Synthetic point observations. The optional header preserves ordinary dots, axes, legends, tooltips, zoom and plot height. No point names are added.</p>"),
        Grid(columns=2, min_width=270, children=[Panel(title=f"Scatter header {state}", children=[
            ScatterChart(df, "spend", "revenue", color_by="channel", title=f"Scatter summary {state}",
                         x_format="currency", y_format="currency", static=True,
                         header_summary=summary("mean", "x") if state == "on" else None)])
            for state in ("off", "on")]),
    ])
    source = "showcase_scatter_summary_coordinates"
    ctx.add_section("Scatter summaries — filtered coordinates and entries", [
        *filters(source, df),
        RawHTML(html="<p>Eight synthetic date × platform × channel facts. All entries: mean spend $15.75, mean revenue $22.50; minimum spend $4, revenue $5; maximum spend $40, revenue $60; count 8. iOS October 1: count 2, mean spend $7, revenue $10, maximum revenue $15. Android October 2: count 2, mean spend $28, revenue $40, minimum revenue $20. Means are unweighted coordinates, never summed means or revenue/spend ratios. Source observations CSV below describes source rows, not painted dots or unique people. The existing generic scatter chart CSV remains header-only.</p>"),
        Grid(columns=3, min_width=270, children=[Panel(title=f"Current {stat} {axis or 'entries'}", children=[
            ScatterChart(df, "spend", "revenue", color_by="channel", title=f"Filtered {stat} {axis or 'entries'}",
                         x_format="currency", y_format="currency", dataset_id=source,
                         header_summary=summary(stat, axis))])
            for stat, axis in (("mean", "x"), ("mean", "y"), ("min", "x"), ("min", "y"),
                               ("max", "x"), ("max", "y"), ("count", None))]),
        Panel(title="Source observations — current source rows", children=[
            DataTable(df, "Source observations — current source rows", dataset_id=source)]),
    ])
    source = "showcase_scatter_summary_context"
    ctx.add_section("Scatter summaries — independent context and bubble plots", [
        *filters(source, df),
        RawHTML(html="<p>This section has independent native filters. Header context and summary are separate options. Bubble radius keeps its existing scaling; coordinate summaries do not certify supplied size eligibility or bubble drawing. Ordinary dots and bubble tooltips remain unchanged.</p>"),
        Grid(columns=2, min_width=270, children=[Panel(title=title, children=[
            ScatterChart(df, "spend", "revenue", size="supplied_size" if bubble else None,
                         color_by="channel", title=title, x_format="currency", y_format="currency",
                         dataset_id=source, header_context="Synthetic supplied pairs <context>" if context else None,
                         header_summary=summary("mean", "y") if enabled else None)])
            for title, bubble, context, enabled in (
                ("Ordinary context only", False, True, False), ("Ordinary summary only", False, False, True),
                ("Bubble context and summary", True, True, True), ("Bubble default", True, False, False))]),
    ])
    duplicate = pd.DataFrame({"spend": [4, 4], "revenue": [5, 5]})
    missing = pd.DataFrame({"spend": [4, None], "revenue": [5, 5]})
    zero = pd.DataFrame({"spend": [-4, 4], "revenue": [0, 0]})
    ctx.add_section("Scatter summaries — duplicate missing and zero eligibility", [
        RawHTML(html="<p>Static snapshots: two duplicate coordinate pairs remain two entries. A missing coordinate withholds coordinate reducers while entry count persists. Negative coordinates are valid; a real zero coordinate or mean stays zero. All current entries must have usable coordinate pairs; no upstream completeness or precision is recovered.</p>"),
        Grid(columns=3, min_width=270, children=[Panel(title=f"{name}: {stat}", children=[
            ScatterChart(data, "spend", "revenue", title=f"Scatter {name} {stat}", static=True,
                         header_summary=summary(stat, None if stat == "count" else "x"))])
            for name, data in (("Duplicates", duplicate), ("Missing", missing), ("Zero", zero))
            for stat in ("mean", "count")]),
        TabGroup([{"label": "Start", "content": []}, {"label": "Reveal scatter", "content": [
            ScatterChart(zero, "spend", "revenue", title="Revealed scatter with a long wrapping title",
                         static=True, header_context="Independent <context>",
                         header_summary=summary("mean", "y", label="Long <safe> coordinate summary " + "x" * 25))]}]),
    ])
