"""Deterministic native summaries of declared complete paths and boundaries."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, Panel, RawHTML, SankeyChart, TabGroup

STAGES = [{"key": f"w{i}", "label": label} for i, label in enumerate(
    ("5 October", "12 October", "19 October", "26 October"), 1)]
STATS = ("sum", "max", "count")


def facts():
    """Nine source facts produce eight grouped paths, with supplied total 1,000."""
    return pd.DataFrame([
        ["Low", "Low", "Low", "Low", 100, "iOS"],
        ["Low", "Low", "Low", "Low", 160, "iOS"],
        ["Low", "Mid", "Mid", "High", 120, "Android"],
        ["Low", "Low", "Mid", "Mid", 80, "iOS"],
        ["Mid", "Mid", "High", "High", 220, "Android"],
        ["Mid", "Low", "Low", "Mid", 100, "iOS"],
        ["High", "High", "High", "VIP", 120, "Android"],
        ["High", "Mid", "Mid", "Mid", 60, "iOS"],
        ["VIP", "VIP", "VIP", "VIP", 40, "Android"],
    ], columns=["w1", "w2", "w3", "w4", "players", "platform"])


def edge_facts():
    return pd.DataFrame([
        ["w1", "w2", "Browse", "Consider", 80, "Store"],
        ["w1", "w2", "Browse", "No purchase", 20, "Store"],
        ["w2", "w3", "Consider", "Purchase", 45, "Store"],
        ["w2", "w3", "Consider", None, 5, "Web"],
        ["w3", "w4", "Purchase", "Return", 15, "Store"],
        ["w3", "w4", "Purchase", "No purchase", 0, "Web"],
    ], columns=["from_week", "to_week", "from_category", "to_category", "players", "channel"])


def quantity_facts():
    return pd.DataFrame([
        [None, "Unavailable", 12.25, "Measured"],
        ["Unavailable", None, 0.125, "Measured"],
        ['<safe> "quoted",\nline', "Same", 1e-9, "Measured"],
        ["Same", "Same", 0, "Zero"],
    ], columns=["a", "b", "usd", "selection"])


def declaration(stat, boundary=None, **options):
    return {"stat": stat, "scope": "complete_paths" if boundary is None else "adjacent_boundary",
            **({"from_stage": f"w{boundary}", "to_stage": f"w{boundary + 1}"} if boundary else {}), **options}


def paths(df, title, **options):
    return SankeyChart(df, title, mode="paths", stages=STAGES, weight="players", unit="players", **options)


def edges(df, title, **options):
    return SankeyChart(df, title, mode="edges", stages=STAGES, weight="players", unit="players",
                       source="from_category", target="to_category", source_stage="from_week", target_stage="to_week", **options)


def add_sections(ctx):
    df = facts()
    ctx.add_section("Sankey summaries — static off and on", [
        RawHTML(html="<p>Optional author-declared summaries preserve the plot and exact supplied records. Nine synthetic source facts group into eight complete paths. A path has one weight across all four stages.</p>"),
        Grid(columns=2, min_width=280, children=[Panel(title=f"Header {state}", children=[
            paths(df, f"Sankey summary {state}", static=True,
                  header_summary=declaration("sum") if state == "on" else None)]) for state in ("off", "on")]),
    ])
    source = "showcase_sankey_summary_paths"
    ctx.add_section("Sankey summaries — filtered complete paths", [
        DataSource(source, df), FilterBar(source, df, filters=[{"column": "platform", "type": "toggle"}]),
        RawHTML(html="<p>All: total 1,000, largest path 260, entries 8. iOS: 500 / 260 / 4. Android: 500 / 220 / 4. Counts describe grouped paths, including supplied zeros, rather than source rows or unique people. This section's filters are independent.</p>"),
        Grid(columns=3, min_width=280, children=[Panel(title=stat, children=[
            paths(df, "Filtered paths " + stat, dataset_id=source, header_summary=declaration(stat))]) for stat in STATS]),
    ])
    df = edge_facts()
    source = "showcase_sankey_summary_boundaries"
    ctx.add_section("Sankey summaries — independent adjacent boundaries", [
        DataSource(source, df), FilterBar(source, df, filters=[{"column": "channel", "type": "toggle"}]),
        RawHTML(html="<p>Independent boundary totals: 100, 50, 15; largest edges: 80, 45, 15; grouped entries: 2 each. These are separate populations. Web: first boundary has no observations, second has weight 5, third has a supplied zero. No combined cohort total is assigned.</p>"),
        TabGroup([{"label": f"Boundary {i}", "content": [Grid(columns=3, min_width=280, children=[
            Panel(title=stat, children=[edges(df, f"Boundary {i} {stat}", dataset_id=source,
                                            header_summary=declaration(stat, i))]) for stat in STATS])]} for i in (1, 2, 3)]),
    ])
    q = quantity_facts()
    source = "showcase_sankey_summary_quantities"
    zero = facts().iloc[:1].copy()
    zero["players"] = 0
    ctx.add_section("Sankey summaries — zero empty and unavailable quantities", [
        DataSource(source, q), FilterBar(source, q, filters=[{"column": "selection", "type": "toggle"}]),
        RawHTML(html="<p>Explicit USD quantities include fractional weights, a tiny positive path, a supplied zero, null classification and literal Unavailable. Repeated stage labels keep distinct keys. Exact records and CSV retain raw units and supplied precision. An empty measured population shows a dash for weight reducers; grouped-entry count stays zero.</p>"),
        Grid(columns=2, min_width=280, children=[Panel(title="Declared quantity", children=[
            SankeyChart(q, "USD quantity " + stat, mode="paths", stages=[{"key": key, "label": "Repeated stage"} for key in ("a", "b")],
                        weight="usd", unit="USD", weight_kind="quantity", dataset_id=source,
                        header_context="Independent <quantity> context",
                        header_summary=declaration(stat, format="number" if stat == "count" else "currency",
                                                   label='<safe> "USD"\nsummary' if stat == "sum" else stat))]) for stat in STATS]),
        TabGroup([{"label": "Supplied zero", "content": [Grid(columns=3, min_width=280, children=[
            paths(zero, "Zero paths " + stat, static=True, header_summary=declaration(stat)) for stat in STATS])]},
            {"label": "Empty paths", "content": [Grid(columns=3, min_width=280, children=[
                paths(zero.iloc[:0], "Empty paths " + stat, static=True, header_summary=declaration(stat)) for stat in STATS])]}]),
    ])
