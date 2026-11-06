"""Deterministic native ordinary LineChart result presentations."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML, ScopedDataSource


def add_sections(ctx):
    frame = pd.DataFrame({
        "day": ["2026-10-01", "2026-10-02", "2026-10-03"] * 2,
        "sample": ["Measured zero"] * 3 + ["Completed"] * 3,
        "value": [0, 0, 0, 12, 25, 18],
    })
    source = "line_results_observations"
    ctx.add_section("Line results — completed data and measured zeros", [
        DataSource(source, frame),
        FilterBar(source, frame, filters=[{"column": "sample", "type": "toggle", "label": "Supplied sample"}]),
        RawHTML(html="<p>Synthetic supplied observations. Measured zeros remain real points; completed lines keep their ordinary plot. The local sample filter below can select a result with no matches.</p>"),
        LineChart(frame, "day", "value", "Selected supplied observations", dataset_id=source,
                  header_summary={"stat": "sum", "label": "Selected total"}),
    ])
    child = "line_results_subset"
    ctx.add_section("Line results — local subset and no matches", [
        ScopedDataSource(child, parent=source),
        FilterBar(child, frame, filters=[{"column": "sample", "type": "toggle", "label": "Local supplied sample",
            "options": ["Measured zero", "Completed", "No supplied match"], "default": "No supplied match"}]),
        LineChart(frame, "day", "value", "Local selected observations", dataset_id=child, height="compact"),
    ])
    unavailable = pd.DataFrame({"day": ["2026-10-01", "2026-10-02"], "value": [None, None]})
    empty = unavailable.iloc[:0].copy()
    ctx.add_section("Line results — supplied empty and unavailable observations", [
        Grid(columns=2, children=[
            LineChart(empty, "day", "value", "Empty supplied snapshot", static=True),
            LineChart(unavailable, "day", "value", "Declared unavailable observations", static=True,
                      series_availability=[{"series": 0, "required_columns": ["value"],
                          "reason": "The source has not supplied these observations."}]),
        ]),
        RawHTML(html="<p>Loading and failure require a hosted live source. The <a href='../live-result-states/'>hosted live result examples</a> explain requested and displayed selections while retaining actual prior observations. Standalone snapshots do not fabricate live requests.</p>"),
    ])
