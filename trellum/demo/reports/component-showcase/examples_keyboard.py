"""Small native examples of table sorting and control keyboard focus."""

import pandas as pd

from trellum.components import (
    DataSource,
    DataTable,
    FilterBar,
    LineChart,
    RawHTML,
    Toggle,
    Visible,
)


def add_sections(ctx):
    """Add one independently filtered section without report-local JavaScript."""
    frame = pd.DataFrame({
        "Day": ["2026-09-01", "2026-09-01", "2026-09-02", "2026-09-02",
                "2026-09-03", "2026-09-03"],
        "Platform": ["iOS", "Android"] * 3,
        "Label": ["Measured zero", "First tie", "Second tie", "Small decimal",
                  "Largest amount", "Negative amount"],
        "Amount": [0, 2.5, 2.5, 0.125, 8.75, -1.25],
    })
    dataset = "showcase_keyboard_observations"
    toggle = "showcase_keyboard_explanation"
    ctx.add_section("Keyboard — table sorting and control focus", [
        DataSource(dataset, frame),
        FilterBar(dataset, frame, filters=[
            {"column": "Platform", "label": "Keyboard examples: platform", "type": "toggle"},
        ]),
        RawHTML("<p>Use Tab to reach sortable column headings. Enter or Space sorts "
                "the same way as a click; the first sort is descending and the next "
                "ascending. Focus stays on that heading. Search and CSV include every "
                "matching observation, even beyond the four-row display limit. "
                "Tab also reveals the chart CSV button. Enter or Space activates "
                "the native toggle below to change its explanatory content.</p>"),
        DataTable(frame, "Keyboard observations", dataset_id=dataset,
                  columns=["Label", "Amount"], max_rows=4, sortable=True, searchable=True),
        Toggle(toggle, ["Summary", "Details"]),
        Visible([RawHTML("<p>Summary: daily amounts include a measured zero and decimals.</p>")],
                toggle, "Summary"),
        Visible([RawHTML("<p>Details: equal amounts retain their supplied row order. "
                         "The platform filter changes both the table and chart.</p>")],
                toggle, "Details"),
        LineChart(frame, x="Day", y="Amount", title="Daily keyboard example amounts",
                  dataset_id=dataset),
    ])
