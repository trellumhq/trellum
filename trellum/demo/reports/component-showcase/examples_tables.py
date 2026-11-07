"""Deterministic acquisition rows for DataTable search, sort and CSV examples."""

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, RawHTML


def add_sections(ctx):
    frame = pd.DataFrame([
        {
            "Event ID": f"{index + 1:05d}",
            "Day": f"2026-09-{index % 28 + 1:02d}",
            "Platform": "iOS" if index % 2 == 0 else "Android",
            "Source": ("Search", "Social", "Partner", "Direct")[index % 4],
            "Campaign": "Reactivation" if index >= 48 else "Launch",
            "Installs": 40 + index * 17 % 110,
            "Spend": round((index % 9) * 12.345 + 0.004, 3),
            "Revenue": round(95 + (index * 31 % 140) + index / 1000, 3),
        }
        for index in range(80)
    ])
    options = {"df": frame, "max_rows": 10, "searchable": True, "sortable": True}
    ctx.add_section("DataTable — static population", [
        RawHTML(html=(
            "<p>These 80 synthetic acquisition events show 10 rows at a time. "
            "Optional local search and sorting are enabled. Search for "
            "<strong>Reactivation</strong> to find 32 events beyond the initial "
            "display. Click Revenue to sort every match. CSV exports every "
            "matching row in that order, including rows beyond the display "
            "limit, with original numeric precision.</p>"
        )),
        DataTable(title="Acquisition events · static", static=True, **options),
    ])
    ctx.add_section("DataTable — filtered population", [
        DataSource("showcase_tables_acquisition", frame),
        FilterBar("showcase_tables_acquisition", frame, filters=[
            {"column": "Platform", "label": "Table examples: platform", "type": "toggle"},
        ]),
        RawHTML(html=(
            "<p>The platform control narrows this second table's population. "
            "Its own optional search and sort continue to apply after filtering. "
            "The matching count and CSV cover the current platform selection "
            "and local search together; the 10-row display limit never limits "
            "the download. Event IDs and dates remain text.</p>"
        )),
        DataTable(title="Acquisition events · filterable",
                  dataset_id="showcase_tables_acquisition", **options),
    ])
