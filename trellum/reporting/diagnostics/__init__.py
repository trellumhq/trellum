"""Per-report diagnostics.

Computes a structured ``ReportDetails`` post-generate covering:

  * ``filter_matrix`` — per chart, which FilterBar filters actually
    affect it (active / propagated / inactive / untracked) and why.
  * ``datasets`` — per DataSource size and row count metrics.
  * ``totals`` — aggregate dataset count, chart count, total row count
    and total serialized size.

The matrix is **static reach analysis** — it walks the component tree
and applies the propagation rules the JS filter engine actually uses
(see ``trellum/components/filterable.py``). A filter ``col`` reaches
a DataSource ``X`` iff:

  1. ``X.id == FilterBar.dataset_id`` and ``col`` is in ``X.df.columns``
     (status = ``"active"``); or
  2. ``X.id`` appears in ``FilterBar.propagate_to`` AND ``col`` is mapped
     in that entry AND the mapped target column is in ``X.df.columns``
     (status = ``"propagated"``).

Anything else is ``"inactive"``.

RawHTML / ABCompare components emit a single ``"untracked"`` row because
the framework can't statically know whether their JS subscribes to the
filter engine. Their coverage has to be eyeballed.

The output goes to ``output/<slug>/_details.json`` and is also embedded
in ``_meta.json`` under the ``details`` key.
"""

from __future__ import annotations

import os
from typing import Any

import orjson

from trellum.reporting.diagnostics.datasets import compute_dataset_metrics
from trellum.reporting.diagnostics.filters import compute_filter_effectiveness
from trellum.reporting.diagnostics.types import (
    ChartFilterRow,
    DatasetMetric,
    FilterCell,
    ReportDetails,
    ReportTotals,
)
from trellum.reporting.diagnostics.walk import _detect_static_columns

__all__ = [
    "compute_details",
    "enrich_details_with_disk",
    "write_details",
    "compute_dataset_metrics",
    "compute_filter_effectiveness",
    "ReportDetails",
    # Re-exported for anything reading _details.json: these name the shape of
    # the file, which a host and the report's own filter-health badge parse.
    "DatasetMetric",
    "ReportTotals",
    "ChartFilterRow",
    "FilterCell",
    # test_validation.py reaches for this one directly.
    "_detect_static_columns",
]


def compute_details(ctx: Any, *, mock_data: bool = False) -> ReportDetails:
    """Build the full ReportDetails for a generated ``ReportContext``.

    ``mock_data=True`` flags the run as having used the test runner's
    synthetic DataFrames instead of real database results. The rows /
    sizes that come out are then a structural smoke test only — every
    DataFrame is capped at ~30 rows. Surface this prominently wherever the
    figures are shown, so they aren't read as production reality.
    """
    datasets = compute_dataset_metrics(ctx)
    matrix = compute_filter_effectiveness(ctx)

    total_rows = sum(d["rows"] for d in datasets)
    total_size = sum(
        d["size_bytes_inline"] + d["size_bytes_chunks_total"] for d in datasets
    )
    chart_count = sum(1 for r in matrix if r["chart_kind"] not in {"RawHTML", "ABCompare"})

    # All charts on the same DataSource share the same filter coverage —
    # the classification depends on (FilterBar, ds_id) only, not on the
    # specific chart. Collapsing to per-DS makes the in-report badge
    # lookup O(1) per chart and keeps the JS payload tiny.
    coverage_by_dataset: dict[str, list[FilterCell]] = {}
    for row in matrix:
        ds = row.get("dataset_id")
        if ds and ds not in coverage_by_dataset and row.get("filters"):
            coverage_by_dataset[ds] = row["filters"]

    return {
        "datasets": datasets,
        "filter_matrix": matrix,
        "coverage_by_dataset": coverage_by_dataset,
        "totals": {
            "datasource_count": len(datasets),
            "chart_count": chart_count,
            "total_rows": total_rows,
            "total_size_bytes": total_size,
        },
        "data_source": "mock" if mock_data else "real",
    }

def enrich_details_with_disk(details: ReportDetails, output_dir: str) -> ReportDetails:
    """Replace size estimates with actual on-disk artifact sizes.

    Pre-render, ``compute_details`` only knows the in-memory DataFrame.
    Post-render, the framework has written ``data.json`` plus
    ``data_chunk_{id}_{period}.json`` files. The framework also keeps
    chunks from previous runs on disk (history is never garbage
    collected), so the disk total can be much larger than the in-memory
    serialization. Reporting disk reality is what the user sees in the
    host / S3 deployment.

    Per-DataSource size_bytes_chunks_total + chunk_count are replaced
    with the actual sum of matching ``data_chunk_*`` files. The top-level
    ``total_size_bytes`` becomes the size of ``data.json`` plus every
    ``data_chunk_*.json`` in the output dir.
    """
    if not os.path.isdir(output_dir):
        return details

    files = os.listdir(output_dir)

    # Per-DataSource: sum data_chunk_{id}_*.json (and the multi-scope
    # variant data_chunk_{scope}_{id}_*.json).
    for d in details.get("datasets", []):
        ds_id = d["id"]
        scope = d.get("scope")
        prefixes: list[str] = [f"data_chunk_{ds_id}_"]
        if scope:
            prefixes.append(f"data_chunk_{scope}_{ds_id}_")
        chunk_total = 0
        chunk_count = 0
        for f in files:
            if not f.endswith(".json"):
                continue
            if not any(f.startswith(p) for p in prefixes):
                continue
            chunk_total += os.path.getsize(os.path.join(output_dir, f))
            chunk_count += 1
        if chunk_count > 0:
            d["size_bytes_chunks_total"] = chunk_total
            d["chunk_count"] = chunk_count

    # Totals: actual size of data.json + every data_chunk_*.json.
    total = 0
    data_json = os.path.join(output_dir, "data.json")
    if os.path.isfile(data_json):
        total += os.path.getsize(data_json)
    for f in files:
        if f.startswith("data_chunk_") and f.endswith(".json"):
            total += os.path.getsize(os.path.join(output_dir, f))
    details.setdefault("totals", {})["total_size_bytes"] = total

    return details

def write_details(output_dir: str, details: ReportDetails) -> None:
    """Persist the ReportDetails to ``_details.json`` in ``output_dir``."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "_details.json")
    with open(path, "wb") as f:
        f.write(orjson.dumps(details, option=orjson.OPT_INDENT_2))
