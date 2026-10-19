"""A chunked DataSource is split by period without copying its frame.

The reference here is the row-by-row grouping the split used to do: a period
key per row, then one boolean selection per period. Output must match it.
"""

from __future__ import annotations

import tracemalloc

import numpy as np
import orjson
import pandas as pd
import pytest

from trellum.components.base import RenderContext
from trellum.components.filterable import (
    DataSource,
    _period_key,
    _period_row_positions,
    _serialize_columnar,
)
from trellum.reporting.diagnostics.datasets import _chunked_size_bytes
from trellum.themes import DefaultTheme

OPTS = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS


def _frame(n: int = 3_000, *, as_datetime: bool = False, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.Timestamp("2025-11-03") + pd.to_timedelta(rng.integers(0, 150, n), unit="D")
    df = pd.DataFrame({
        "event_date": days if as_datetime else days.strftime("%Y-%m-%d"),
        "title": rng.choice(["Coral Quest", "Iron Vanguard", "Neon Racer"], n),
        "amount": rng.random(n).round(3),
        "count": rng.integers(0, 50, n),
    })
    df.index = rng.permutation(n) + 1000  # labels the split must not depend on
    return df


def _reference_periods(df: pd.DataFrame, chunk_by: str) -> pd.Series:
    col = df["event_date"]
    strs = col.dt.strftime("%Y-%m-%d") if pd.api.types.is_datetime64_any_dtype(col) else col.astype(str)
    return strs.apply(lambda v: _period_key(v, chunk_by))


@pytest.mark.parametrize("chunk_by", ["month", "week"])
@pytest.mark.parametrize("as_datetime", [False, True])
def test_positions_match_a_row_by_row_grouping(chunk_by, as_datetime):
    df = _frame(as_datetime=as_datetime)
    periods = _reference_periods(df, chunk_by).to_numpy()
    got = _period_row_positions(df, "event_date", chunk_by)
    assert sorted(got) == sorted(set(periods))
    for period, positions in got.items():
        np.testing.assert_array_equal(positions, np.flatnonzero(periods == period))


@pytest.mark.parametrize("chunk_by", ["month", "week"])
def test_inline_block_and_chunk_files_match_per_period_selection(chunk_by):
    df = _frame()
    ctx = RenderContext(theme=DefaultTheme)
    DataSource("econ", df, chunk_by=chunk_by, default_chunks=2).render_html(ctx)

    periods = _reference_periods(df, chunk_by)
    newest_first = sorted(periods.unique(), reverse=True)
    inline = df[periods.isin(newest_first[:2])]
    assert ctx._raw_data_blocks["_ds_econ"] == _serialize_columnar(inline)

    expected = {
        f"data_chunk_econ_{p}.json": orjson.dumps({"_ds_econ": _serialize_columnar(df[periods == p])}, option=OPTS)
        for p in newest_first[2:]
    }
    assert ctx._pending_chunks == expected
    assert list(ctx._chunk_manifests["econ"]["chunks"]) == newest_first[2:]


def test_diagnostics_chunk_sizes_match_the_reference():
    df = _frame()
    periods = _reference_periods(df, "month")
    expected = sum(
        len(orjson.dumps(_serialize_columnar(df[periods == p]), option=orjson.OPT_SERIALIZE_NUMPY))
        for p in periods.unique()
    )
    assert _chunked_size_bytes(df, "month") == (expected, periods.nunique())


def test_the_frame_is_left_untouched():
    df = _frame()
    before = df.copy()
    DataSource("econ", df, chunk_by="month").render_html(RenderContext(theme=DefaultTheme))
    _chunked_size_bytes(df, "month")
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.slow
def test_splitting_needs_a_fraction_of_the_frame(monkeypatch):
    """The bound: splitting no longer allocates a copy of the whole frame.

    Serialization is stubbed out so the measurement is the split alone.
    """
    import trellum.components.filterable as filterable

    monkeypatch.setattr(filterable, "_serialize_columnar", lambda d: {"rows": len(d)})
    rng = np.random.default_rng(0)
    n = 240_000
    df = pd.DataFrame({f"m{i}": rng.random(n) for i in range(20)})
    df.insert(0, "event_date", pd.date_range("2024-01-01", periods=n, freq="5min").strftime("%Y-%m-%d"))
    frame_bytes = int(df.memory_usage(deep=True).sum())

    def peak(fn):
        tracemalloc.start()
        try:
            fn()
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    render = peak(lambda: DataSource("x", df, chunk_by="month").render_html(RenderContext(theme=DefaultTheme)))
    sizes = peak(lambda: _chunked_size_bytes(df, "month"))
    assert render < 0.5 * frame_bytes, (render, frame_bytes)
    assert sizes < 0.5 * frame_bytes, (sizes, frame_bytes)
