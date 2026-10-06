"""How big each dataset is, on the wire and in chunks."""

from __future__ import annotations

from typing import Any, Literal

import orjson
import pandas as pd

FilterStatus = Literal["active", "propagated", "inactive", "untracked"]

from trellum.reporting.diagnostics.types import (
    DatasetMetric,
)
from trellum.reporting.diagnostics.walk import _walk_view


def _serialize_size_bytes(df: pd.DataFrame) -> int:
    """Return the size in bytes of the columnar JSON serialization.

    Uses the same serializer as ``DataSource._render`` so the number
    matches what actually lands in ``data.json``. Falls back to
    ``df.memory_usage`` if the serializer ever errors (defensive — we
    don't want a diagnostics-pass failure to break the run).
    """
    from trellum.components.filterable import _serialize_columnar

    try:
        payload = _serialize_columnar(df)
        return len(orjson.dumps(payload, option=orjson.OPT_SERIALIZE_NUMPY))
    except Exception:
        try:
            return int(df.memory_usage(deep=True).sum())
        except Exception:
            return 0

def _chunked_size_bytes(df: pd.DataFrame, chunk_by: str) -> tuple[int, int]:
    """Return (total_size_bytes, chunk_count) for a chunked DataSource.

    Mirrors the period grouping done by ``_render_chunked``; the chunk
    files aren't on disk yet at compute_details time so we re-serialize
    each period in memory. For ``chunk_by`` values the framework
    doesn't recognize, return ``(0, 0)``.
    """
    from trellum.components.filterable import _find_date_col, _period_key

    if chunk_by not in {"month", "week"}:
        return 0, 0
    if df is None or len(df) == 0:
        return 0, 0
    date_col = _find_date_col(df)
    if date_col is None:
        return 0, 0

    work = df.copy()
    if pd.api.types.is_datetime64_any_dtype(work[date_col].dtype):
        date_strs = work[date_col].dt.strftime("%Y-%m-%d")
    else:
        date_strs = work[date_col].astype(str)
    work["__fw_period__"] = date_strs.apply(lambda v: _period_key(v, chunk_by))
    periods = sorted(work["__fw_period__"].unique())

    total = 0
    for period in periods:
        sub = work[work["__fw_period__"] == period].drop(columns=["__fw_period__"])
        total += _serialize_size_bytes(sub)
    return total, len(periods)

def _collect_datasource_entries(ctx: Any) -> list[tuple[Any, str | None]]:
    """Return (DataSource, scope_or_None) pairs across the report.

    Flat sections produce ``scope=None``; each scoped section's DataSources
    carry the scope name so multi-scope reports can be analysed correctly.
    """
    from trellum.components.filterable import DataSource

    entries: list[tuple[Any, str | None]] = []
    for comp, _sec in _walk_view(list(getattr(ctx, "sections", []) or [])):
        if isinstance(comp, DataSource):
            entries.append((comp, None))
    for scope_name, scope_sections in (getattr(ctx, "scopes", {}) or {}).items():
        for comp, _sec in _walk_view(scope_sections):
            if isinstance(comp, DataSource):
                entries.append((comp, scope_name))
    return entries

def compute_dataset_metrics(ctx: Any) -> list[DatasetMetric]:
    """Walk every DataSource and measure rows / columns / serialized size."""
    metrics: list[DatasetMetric] = []
    for ds, scope in _collect_datasource_entries(ctx):
        df = getattr(ds, "df", None)
        if not isinstance(df, pd.DataFrame):
            continue

        rows = len(df)
        col_names = [str(c) for c in df.columns]
        chunk_by = getattr(ds, "chunk_by", None)

        if chunk_by:
            chunk_total, chunk_count = _chunked_size_bytes(df, chunk_by)
            # Everything ends up serialized either way; a chunked dataset
            # reports its bytes under chunks, so the inline figure is zero.
            size_inline = 0
        else:
            chunk_total, chunk_count = 0, 0
            size_inline = _serialize_size_bytes(df)

        metrics.append(
            {
                "id": ds.id,
                "rows": rows,
                "columns": len(col_names),
                "column_names": col_names,
                "size_bytes_inline": size_inline,
                "size_bytes_chunks_total": chunk_total,
                "chunk_by": chunk_by,
                "chunk_count": chunk_count,
                "scope": scope,
            }
        )
    return metrics
