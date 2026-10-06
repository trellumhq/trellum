"""The shapes written into _details.json.

A host and the report's own filter-health badge both read this file,
so the keys are a contract; keeping them as TypedDicts says so.
"""

from __future__ import annotations

from typing import Literal, TypedDict

FilterStatus = Literal["active", "propagated", "inactive", "untracked"]

class FilterCell(TypedDict, total=False):
    column: str
    target_column: str
    status: FilterStatus
    reason: str
    # Set by validate_report when chart-filter-coverage is suppressed for
    # the row's dataset_id. Surface as "✗ (suppressed)" in viewers — the
    # problem is real but explicitly accepted.
    suppressed: bool

class ChartFilterRow(TypedDict):
    chart_id: str
    chart_title: str
    chart_kind: str
    section: str
    scope: str | None
    dataset_id: str | None
    filters: list[FilterCell]
    # Value columns that are constant within each x-axis value. The
    # FilterBar may structurally reach the DS (filters: active /
    # propagated), but if every filter dimension was already collapsed
    # before the column was attached (e.g. a daily total merged on
    # event_date only into a date×audience×spender grain), no filter
    # actually changes the chart's plotted number — ratios stay
    # constant, absolutes inflate by the count of replicated rows.
    # Empty list = not detected; null = chart wasn't introspectable.
    static_value_columns: list[str]

class DatasetMetric(TypedDict):
    id: str
    rows: int
    columns: int
    column_names: list[str]
    size_bytes_inline: int
    size_bytes_chunks_total: int
    chunk_by: str | None
    chunk_count: int
    scope: str | None

class ReportTotals(TypedDict):
    datasource_count: int
    chart_count: int
    total_rows: int
    total_size_bytes: int

class ReportDetails(TypedDict):
    datasets: list[DatasetMetric]
    filter_matrix: list[ChartFilterRow]
    coverage_by_dataset: dict[str, list[FilterCell]]
    totals: ReportTotals
    data_source: Literal["real", "mock"]
