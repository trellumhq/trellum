"""Deterministic native stage-entry summaries with independent section filters."""

import pandas as pd

from trellum.components import DataSource, FilterBar, FunnelChart, Grid, Panel, RawHTML


def facts():
    """Twelve date × platform × stage facts; amounts can overlap people."""
    return pd.DataFrame([
        {"day": f"2026-10-0{day}", "platform": platform, "stage": stage, "amount": amount * factor}
        for day, amounts in ((1, (100, 60, 20)), (2, (80, 40, 0)))
        for platform, factor in (("iOS", 1), ("Android", 2))
        for stage, amount in zip(("Opened", "Started", "Completed"), amounts)
    ])


def summary(stat):
    return {"stat": stat, "scope": "current_stages", **({"stage": "Completed"} if stat == "stage" else {})}


def add_sections(ctx):
    snapshot = pd.DataFrame({"stage": ["Opened", "Started", "Completed"], "amount": [540, 300, 60]})
    ctx.add_section("Funnel summaries — off and on", [
        RawHTML(html="<p>Synthetic supplied stage counts may overlap people. No cross-stage sum or conversion is inferred. Identical data, order, percentages and plot height with the optional header off/on.</p>"),
        Grid(columns=2, min_width=270, children=[
            Panel(title=f"Header summary {state}", children=[
                FunnelChart(snapshot, "stage", "amount", f"Funnel summary {state}", static=True,
                            header_summary=summary("max") if state == "on" else None)])
            for state in ("off", "on")]),
    ])
    for title, source, context in (
        ("Filtered stages", "showcase_funnel_summary_stages", False),
        ("Independent header context", "showcase_funnel_summary_context", True),
    ):
        df = facts()
        ctx.add_section(f"Funnel summaries — {title}", [
            DataSource(source, df),
            FilterBar(source, df, filters=[
                {"column": "day", "type": "date_range", "label": f"{title}: dates"},
                {"column": "platform", "type": "toggle", "label": f"{title}: platform"},
                {"column": "stage", "type": "toggle", "label": f"{title}: stage"},
            ]),
            RawHTML(html="<p>Twelve synthetic facts: two dates × two platforms × three stage labels. Bound entries group by displayed string key and sort by descending count. Overall Opened 540, Started 300, Completed 60; largest 540; entry count 3. iOS October 1: Completed 20, largest 100. Android October 2: Completed 0, largest 160. Filters affect only this section.</p>"),
            Grid(columns=3, min_width=270, children=[
                Panel(title=f"{title}: {dict(stage='Selected stage', max='Largest stage', count='Stage entries')[stat]}", children=[
                    FunnelChart(df, "stage", "amount", dict(stage="Completed", max="Largest supplied count", count="Current entry count")[stat], dataset_id=source,
                                header_context="Supplied stage counts; dates and platform selected in this section" if context else None,
                                header_summary=summary(stat))])
                for stat in ("stage", "max", "count")]),
        ])
    duplicate = pd.DataFrame({"stage": ["Completed", "Completed", "Opened"], "amount": [10, 20, 50]})
    missing = pd.DataFrame({"stage": ["Completed", "Opened"], "amount": [None, 50]})
    zero = pd.DataFrame({"stage": ["Completed", "Opened"], "amount": [0, 0]})
    ctx.add_section("Funnel summaries — static eligibility", [
        RawHTML(html="<p>Point-in-time snapshots: duplicate Completed 10/20 and Opened 50 remain three separate entries; max 50, count 3, selected Completed unavailable (ambiguous). Missing is unavailable; real zero remains zero. Entry count does not require complete amounts. No warehouse completeness or original-person count is claimed.</p>"),
        Grid(columns=3, min_width=270, children=[
            Panel(title=f"{name}: {stat}", children=[
                FunnelChart(df, "stage", "amount", f"Funnel {name} {stat}", static=True,
                            header_summary=summary(stat))])
            for name, df, stat in (("Duplicates", duplicate, "stage"), ("Duplicates", duplicate, "max"),
                                   ("Duplicates", duplicate, "count"), ("Missing", missing, "stage"),
                                   ("Missing", missing, "count"), ("Zero", zero, "stage"))]),
    ])
