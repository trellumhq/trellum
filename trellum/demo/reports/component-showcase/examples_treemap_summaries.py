"""Native optional leaf-entry summaries over deterministic synthetic USD facts."""

import pandas as pd

from trellum.components import (
    DataSource,
    DataTable,
    FilterBar,
    Grid,
    Panel,
    RawHTML,
    TabGroup,
    TreemapChart,
)


def facts():
    """Twenty source facts aggregate to four leaves, total 900 synthetic USD."""
    return pd.DataFrame([
        {"day": f"2026-08-0{day}", "platform": platform, "parent": parent,
         "leaf": leaf, "amount": weight * day * factor, "source_color": parent,
         "numeric_color": weight / 40}
        for day in (1, 2) for platform, factor in (("iOS", 1), ("Android", 2))
        for parent, leaf, weight in (("Paid", "UA", 4), ("Paid", "UA", 6),
                                    ("Paid", "RT", 20), ("Organic", "Store", 30), ("Organic", "Web", 40))
    ])


def summary(stat, **options):
    return {"stat": stat, "scope": "leaf_entries", **options}


def _filters(source, df):
    return [DataSource(source, df), FilterBar(source, df, filters=[
        {"column": "day", "type": "date_range"}, {"column": "platform", "type": "toggle"},
    ])]


def _chart(df, title, **options):
    return TreemapChart(df, ["parent", "leaf"], "amount", title,
                        value_format="currency", **options)


def add_sections(ctx):
    df = facts()
    ctx.add_section("Treemap summaries — off and on", [
        RawHTML(html="<p>Synthetic additive USD amounts, not real-report parity. The same twenty source facts produce four final leaves: 90, 180, 270, 360. Parents total 900 and leaves total 900; a leaf summary never adds both to make 1800. Geometry, fitting labels, details and colors retain their existing contracts.</p>"),
        Grid(columns=2, min_width=280, children=[Panel(title=f"Treemap header {state}", children=[
            _chart(df, f"Treemap summary {state}", static=True,
                   header_summary=summary("sum") if state == "on" else None)]) for state in ("off", "on")]),
    ])
    source = "showcase_treemap_summary_leaves"
    ctx.add_section("Treemap summaries — filtered leaf reducers and count", [
        *_filters(source, df),
        RawHTML(html="<p>All current leaves: total $900, count 4, mean $225, minimum $90, maximum $360. iOS August 1: values 10, 20, 30, 40; total $100 and unweighted mean $25. Paid/UA has two source facts per date/platform, but one final leaf entry. Count is independent of numeric eligibility. Source rows below are a separate grain; treemap generic CSV remains header-only.</p>"),
        Grid(columns=3, min_width=270, children=[Panel(title=f"Leaf {stat}", children=[
            _chart(df, "Filtered treemap " + stat, dataset_id=source, header_summary=summary(stat))])
            for stat in ("sum", "count", "mean", "min", "max")]),
        Panel(title="Treemap source rows — current source-row grain", children=[
            DataTable(df, "Treemap source rows — current source-row grain", dataset_id=source)]),
    ])
    source = "showcase_treemap_summary_context"
    ctx.add_section("Treemap summaries — independent context shares and colors", [
        *_filters(source, df),
        RawHTML(html="<p>Independent section filters. Header context and optional tile percentages are separate options. Supplied categorical and numeric colors retain first-source-child semantics. The header never changes full-path details or which labels fit.</p>"),
        Grid(columns=2, min_width=280, children=[Panel(title=title, children=[
            _chart(df, title, dataset_id=source, color_col=color, show_percentages=shares,
                   header_context="Synthetic USD <context>" if context else None,
                   header_summary=summary("mean") if enabled else None)])
            for title, color, shares, context, enabled in (
                ("Treemap context only", None, False, True, False),
                ("Treemap summary only", None, False, False, True),
                ("Treemap categorical context", "source_color", True, True, True),
                ("Treemap numeric context", "numeric_color", True, True, True))]),
    ])
    edges = pd.DataFrame([
        {"case": case, "parent": parent, "leaf": leaf, "amount": amount}
        for case, rows in (
            ("Zero", [("A", "One", 20), ("A", "Zero", 0)]),
            ("Null", [("A", "One", 20), ("A", "Two", None)]),
            ("String", [("A", "One", 20), ("A", "Two", "10")]),
            ("Blank", [("", "One", 20), ("A", "Two", 10)]),
            ("Signed", [("A", "One", 20), ("A", "Two", -5)]),
            ("All zero", [("A", "One", 0), ("A", "Two", 0)]),
            ("Duplicate", [("A.B", "C", 4), ("A.B", "C", 6), ("A", "B.C", 20),
                           ("P", "Same", 30), ("Q", "Same", 40), ('Quotes "\n<safe>', "Leaf", 5)]),
        ) for parent, leaf, amount in rows
    ], dtype=object)
    source = "showcase_treemap_summary_eligibility"
    empty = edges.iloc[:0]
    ctx.add_section("Treemap summaries — zero invalid blank and duplicate leaves", [
        DataSource(source, edges),
        FilterBar(source, edges, filters=[{"column": "case", "type": "toggle"}]),
        RawHTML(html="<p>Native legacy sizing remains: signed/all-zero geometry can be unsupported; blank parents may suppress leaves. Numeric headers conservatively withhold invalid or suppressed input. Count observes final plugin entries, not geometry validity or source survival. A real zero among final leaves is valid. Dotted names, quotes and repeated names use existing stringified ordered paths; duplicate facts at one path aggregate. Empty numeric results are unavailable.</p>"),
        Grid(columns=3, min_width=270, children=[Panel(title=f"Eligibility {stat}", children=[
            _chart(edges, "Treemap eligibility " + stat, dataset_id=source, header_summary=summary(stat))])
            for stat in ("min", "mean", "count")]),
        TabGroup([{"label": "Start", "content": []}, {"label": "Reveal treemap", "content": [
            _chart(df, "Revealed treemap with long wrapping title", static=True,
                   header_context="Independent <context>",
                   header_summary=summary("sum", label="Long <safe> leaf summary " + "x" * 30)),
            _chart(empty, "Empty treemap numeric", static=True, header_summary=summary("min")),
            _chart(empty, "Empty treemap count", static=True, header_summary=summary("count")),
        ]}]),
    ])
