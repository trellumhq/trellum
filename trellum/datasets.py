"""Datasets: the one fetch behind ``ctx.metrics()``.

A metric binds to a dataset (``metrics.yaml`` ``datasets:``); a report asks
for metrics, not tables, and gets a long-format DataFrame at the dataset's
grain: the time column, the requested dimensions, one column per measure.
Everything here is the plumbing between those two: resolving ids to one
dataset, turning the request into SQL (or a provider call), and remembering
the answer for the rest of the build.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from trellum.metrics import Dataset, Metric, load_metrics_result

#: Window when neither the call nor the dataset says otherwise.
DEFAULT_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class DataRequest:
    """What one ``ctx.metrics()`` call needs from a dataset.

    ``columns`` are measures (the union of ``column``/``numerator``/
    ``denominator`` of the requested metrics), ``dimensions`` the ``by``
    breakdown, ``start``/``end`` the window (both None for ``time: none``),
    ``grain`` the dataset's declared grain.
    """

    columns: frozenset[str]
    dimensions: tuple[str, ...]
    start: str | None
    end: str | None
    grain: str | None


def dim_additive(metrics: list[Metric]) -> bool:
    """True when every metric survives a GROUP BY over time + dims.

    ``count`` means rows, so a count metric in the request keeps native grain.
    """
    return all(m.agg != "count" for m in metrics)


def build_sql(ds: Dataset, req: DataRequest, rollup: bool = True) -> str:
    """The shortcut SQL for ``req`` against ``ds``.

    Columns are emitted in a stable order (keys as declared, measures sorted)
    so an identical request always binds to identical SQL and hits
    ``query_df``'s disk cache. Derived columns from the dataset's ``columns:``
    map are expanded inline; with ``rollup`` they become ``SUM(expr) AS name``.
    """
    keys = ([ds.time_column] if ds.time_column else []) + list(req.dimensions)
    measures = sorted(req.columns)

    def _expr(c: str) -> str:
        e = ds.columns.get(c)
        if rollup:
            return f"SUM({e or c}) AS {c}"
        return f"{e} AS {c}" if e else c

    sql = f"SELECT {', '.join(keys + [_expr(c) for c in measures])} FROM {ds.table}"
    where = []
    if ds.time_column and req.start:
        where.append(f"{ds.time_column} BETWEEN :start AND :end")
    if ds.where:
        where.append(f"({ds.where})")
    if where:
        sql += " WHERE " + " AND ".join(where)
    if rollup and keys:
        sql += f" GROUP BY {', '.join(keys)}"
    return sql


def _provider(spec: str):
    """``module:function`` -> the callable, importable from the project root."""
    from trellum.project import get_project_root

    root = get_project_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    mod_name, _, fn_name = spec.partition(":")
    return getattr(importlib.import_module(mod_name), fn_name)


def load_dataset(ctx: Any, name: str, req: DataRequest, rollup: bool = True) -> pd.DataFrame:
    """Fetch ``req`` from dataset ``name``, memoized on ``ctx`` for this build.

    The SQL shortcut runs through ``query_df`` (so its disk cache and the
    mock harness both apply); a provider is called ``(ctx, req)`` and may
    ignore ``req`` -- its frame is then projected to the needed columns and,
    when ``rollup`` holds, summed over time + dims so both paths return the
    same shape.
    """
    ds = load_metrics_result().datasets[name]
    memo = ctx.__dict__.setdefault("_dataset_memo", {})
    key = (name, req.columns, req.dimensions, req.start, req.end, rollup)
    if key in memo:
        return memo[key]

    keys = ([ds.time_column] if ds.time_column else []) + list(req.dimensions)
    if ds.provider:
        df = _provider(ds.provider)(ctx, req)
        df = df[keys + sorted(req.columns)]
        if rollup and keys:
            df = df.groupby(keys, as_index=False).sum()
    else:
        # Module attribute, not a bare import: the test harness patches
        # trellum.data.query_df, exactly like declare_live_query.
        import trellum.data as _data

        params = {"start": req.start, "end": req.end} if req.start else None
        df = _data.query_df(ctx.get_connection(ds.source), build_sql(ds, req, rollup),
                            params=params, label=f"DATASET {name}"[:30])
    memo[key] = df
    return df


def metrics_frame(ctx: Any, ids: list[str], by=None, window=None) -> pd.DataFrame:
    """The body of ``ReportContext.metrics``: resolve, validate, request."""
    reg = load_metrics_result()
    metrics = []
    for mid in ids:
        m = reg.metrics.get(mid)
        if m is None:
            raise ValueError(f"unknown metric '{mid}' -- not in metrics.yaml.")
        if not m.executable:
            raise ValueError(f"metric '{mid}' is descriptive (no agg); nothing to fetch.")
        if not m.dataset:
            raise ValueError(f"metric '{mid}' has no `dataset:` binding in metrics.yaml.")
        metrics.append(m)
    by_ds = {m.dataset for m in metrics}
    if len(by_ds) > 1:
        split = ", ".join(f"{m.name} is on '{m.dataset}'" for m in metrics)
        raise ValueError(f"{split} -- call ctx.metrics once per dataset.")
    ds = reg.datasets[metrics[0].dataset]

    dims = tuple(by or ())
    for d in dims:
        if d not in ds.dimensions:
            raise ValueError(f"dataset '{ds.name}' has no dimension '{d}'; "
                             f"declared: {', '.join(ds.dimensions) or '(none)'}")

    start = end = None
    if ds.time_column:
        if isinstance(window, (tuple, list)):
            start, end = str(window[0]), str(window[1])
        else:
            days = int(window or ds.lookback or DEFAULT_LOOKBACK_DAYS)
            start = (ctx._now_utc - timedelta(days=days)).strftime("%Y-%m-%d")
            end = ctx.today

    cols = frozenset(c for m in metrics for c in (m.column, m.numerator, m.denominator) if c)
    req = DataRequest(cols, dims, start, end, ds.grain)
    return load_dataset(ctx, ds.name, req, rollup=dim_additive(metrics))
