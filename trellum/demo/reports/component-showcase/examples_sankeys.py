"""Deterministic supplied paths, unavailable observations and adjacent flows."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, Panel
from trellum.components.charts.sankey import SankeyChart

STAGES = [{"key": f"w{i}", "label": label} for i, label in enumerate(
    ("5 October", "12 October", "19 October", "26 October"), 1)]


def paths_frame():
    return pd.DataFrame([
        ["Low", "Low", "Low", "Low", 260, "iOS"],
        ["Low", "Mid", "Mid", "High", 120, "Android"],
        ["Low", "Low", "Mid", "Mid", 80, "iOS"],
        ["Mid", "Mid", "High", "High", 220, "Android"],
        ["Mid", "Low", "Low", "Mid", 100, "iOS"],
        ["High", "High", "High", "VIP", 120, "Android"],
        ["High", "Mid", "Mid", "Mid", 60, "iOS"],
        ["VIP", "VIP", "VIP", "VIP", 40, "Android"],
    ], columns=["w1", "w2", "w3", "w4", "players", "platform"])


def edges_frame():
    # Independent populations at each boundary: no full paths are asserted.
    return pd.DataFrame([
        ["w1", "w2", "Browse", "Consider", 80, "Store"],
        ["w1", "w2", "Browse", "No purchase", 20, "Store"],
        ["w2", "w3", "Consider", "Purchase", 45, "Store"],
        ["w2", "w3", "Consider", None, 5, "Web"],
        ["w3", "w4", "Purchase", "Return", 15, "Store"],
        ["w3", "w4", "Purchase", "No purchase", 0, "Web"],
    ], columns=["from_week", "to_week", "from_category", "to_category", "players", "channel"])


def add_sections(ctx):
    """Four named sections with independent native datasets and filters."""
    frame = paths_frame()
    ctx.add_section("Sankey — complete weekly paths", [
        DataSource("showcase_sankey_paths", frame),
        FilterBar("showcase_sankey_paths", frame, filters=[
            {"column": "platform", "type": "toggle", "label": "Paths platform"}]),
        SankeyChart(frame, "Weekly supplied player paths", mode="paths", stages=STAGES,
                    weight="players", unit="players", dataset_id="showcase_sankey_paths",
                    header_context="Synthetic fixed cohort: 1,000 players. iOS selects 500; All restores 1,000. Exact complete paths retain origin and final associations after ribbons merge."),
    ])
    special = frame.copy()
    special.loc[0, "players"] = 212
    special = pd.concat([special, pd.DataFrame([
        ["Low", None, "Low", "Low", 12, "iOS"],
        ["No purchase", "No purchase", "No purchase", "No purchase", 35, "iOS"],
        ["Low", "High", "VIP", "VIP", 1, "iOS"],
        ["Unavailable", "Unavailable", "Unavailable", "Unavailable", 0, "Android"],
    ], columns=frame.columns)], ignore_index=True)
    ctx.add_section("Sankey — observations and tiny flows", [
        DataSource("showcase_sankey_observations", special),
        FilterBar("showcase_sankey_observations", special, filters=[
            {"column": "platform", "type": "toggle", "label": "Observation platform"}]),
        SankeyChart(special, "Unavailable, no purchase and one player", mode="paths", stages=STAGES,
                    weight="players", unit="players", dataset_id="showcase_sankey_observations",
                    header_context="Unavailable observation, measured No purchase, and literal Unavailable are distinct. A one-player path keeps proportional area; a supplied zero remains in exact details."),
    ])
    edges = edges_frame()
    ctx.add_section("Sankey — adjacent transitions only", [
        DataSource("showcase_sankey_edges", edges),
        FilterBar("showcase_sankey_edges", edges, filters=[
            {"column": "channel", "type": "toggle", "label": "Transition channel"}]),
        SankeyChart(edges, "Independent adjacent populations", mode="edges", stages=STAGES,
                    source="from_category", target="to_category", source_stage="from_week", target_stage="to_week",
                    weight="players", unit="players", dataset_id="showcase_sankey_edges",
                    header_context="Boundary totals are 100, 50 and 15 players. The supplied populations are unbalanced; no entry, exit or balancing link is invented."),
    ])
    zero = frame.iloc[:1].copy()
    zero["players"] = 0
    ctx.add_section("Sankey — zero flow and empty selection", [
        Grid(columns=2, min_width=280, children=[
            Panel(title=title, children=[SankeyChart(
                data, title, mode="paths", stages=STAGES, weight="players", unit="players", static=True)])
            for title, data in (("Supplied zero-weight path", zero), ("No supplied paths", frame.iloc[:0]))
        ]),
    ])
