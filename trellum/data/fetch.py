"""Reading a query result into a DataFrame, repeated text as categories.

A driver creates a new ``str`` for every text cell, so a two-million-row result
with a handful of distinct titles holds millions of copies of the same words:
first in the row tuples, then in the DataFrame. Text that repeats is stored as
a ``category`` column instead -- one small integer code per row plus each
distinct value once -- and for DB-API cursors it is encoded batch by batch while
the result is read, so neither the row tuples nor a ``str`` per cell ever exist
for those columns.

Which columns: text (only ``str`` and missing values) where at most half the
non-missing values in the first batch are distinct. Ids, emails and free text
stay ordinary text columns. Every other column is converted exactly the way
``pd.DataFrame(rows)`` converts it, so its dtype does not change.
"""

from __future__ import annotations

import decimal
from typing import Any

import numpy as np
import pandas as pd

from trellum.data.ssh_tunnel import unwrap

#: Rows read per ``fetchmany()`` call.
_FETCH_BATCH_ROWS = 10_000

#: A text column becomes a category when at most this fraction of its
#: non-missing values is distinct.
_CATEGORY_MAX_DISTINCT = 0.5

_TEXT_TYPES = frozenset({str, type(None)})


def execute_query(conn: Any, sql: str, compact_text: bool = False) -> pd.DataFrame:
    """Execute SQL and return DataFrame. Dispatches based on connection type.

    ``compact_text`` returns repeated text as category columns.
    """
    conn = unwrap(conn)
    type_name = type(conn).__module__ + "." + type(conn).__qualname__
    compact = compact_text_columns if compact_text else (lambda df: df)

    if "google.cloud.bigquery" in type_name:
        return compact(conn.query(sql).to_dataframe())

    if "clickhouse_connect" in type_name:
        return compact(conn.query_df(sql))

    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        if "snowflake.connector" in type_name:
            return compact(cursor.fetch_pandas_all())
        if cursor.description is None:
            return pd.DataFrame()
        return read_cursor(cursor, [desc[0] for desc in cursor.description], compact_text)
    finally:
        try:
            cursor.close()
        except Exception:
            pass  # Closing a cursor must not replace execute/fetch failures.


def read_cursor(cursor: Any, columns: list, compact_text: bool = True) -> pd.DataFrame:
    """Read every row of an executed DB-API cursor into a DataFrame."""
    fetchmany = getattr(cursor, "fetchmany", None)
    if not compact_text or fetchmany is None:
        return _frame(list(cursor.fetchall()), columns, compact_text)
    batch = fetchmany(_FETCH_BATCH_ROWS)
    if not isinstance(batch, (list, tuple)) or not batch or type(batch[0]) not in (tuple, list):
        # An unusual driver or row type: read it the way it always was, then
        # compact the finished frame.
        head = list(batch) if isinstance(batch, (list, tuple)) else []
        return _frame(head + list(cursor.fetchall()), columns, compact_text)

    reader = _ColumnReader(len(columns), batch)
    while batch:
        reader.add(batch)
        batch = fetchmany(_FETCH_BATCH_ROWS)
        if not isinstance(batch, (list, tuple)):
            reader.add(list(cursor.fetchall()))
            break
    return reader.frame(columns)


def compact_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Turn repeated text columns of a finished frame into categories.

    For drivers that hand back a DataFrame themselves (BigQuery, Snowflake,
    ClickHouse), so a report sees the same column types whatever the source.
    """
    for col in df.columns:
        series = df[col]
        if series.dtype == object:
            texts = series.dropna()
            if not set(map(type, texts)) <= {str}:
                continue
        elif pd.api.types.is_string_dtype(series.dtype):
            texts = series.dropna()
        else:
            continue
        if len(texts) and texts.nunique() <= len(texts) * _CATEGORY_MAX_DISTINCT:
            df[col] = series.astype("category")
    return df


def is_decimal_column(series: pd.Series) -> bool:
    """True when every non-null value is a ``decimal.Decimal`` (or none are set).

    Stops at the first other value, so a text column costs one look instead of
    a call per row.
    """
    for value in series.dropna():
        if type(value) is not decimal.Decimal:
            return False
    return True


def _frame(rows: list, columns: list, compact_text: bool) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    return compact_text_columns(df) if compact_text else df


class _ColumnReader:
    """Collects batches column by column: codes for repeated text, objects
    for everything else."""

    def __init__(self, width: int, first_batch) -> None:
        self.width = width
        self.vocab: dict[int, dict] = {}
        self.codes: dict[int, list] = {}
        for i, column in enumerate(zip(*first_batch)):
            texts = [v for v in column if v is not None]
            if texts and set(map(type, texts)) == {str} and \
                    len(set(texts)) <= len(texts) * _CATEGORY_MAX_DISTINCT:
                self.vocab[i] = {}
                self.codes[i] = []
        self.objects: dict[int, list] = {i: [] for i in range(width) if i not in self.vocab}

    def add(self, batch) -> None:
        if not batch:
            return
        columns = list(zip(*batch))
        for i in range(self.width):
            column = columns[i]
            if i in self.vocab:
                if set(map(type, column)) <= _TEXT_TYPES:
                    self._encode(i, column)
                    continue
                self._demote(i)
            self.objects[i].append(_as_objects(column))

    def _encode(self, i: int, column) -> None:
        batch_codes, uniques = pd.factorize(_as_objects(column))
        vocab = self.vocab[i]
        to_global = np.fromiter((vocab.setdefault(u, len(vocab)) for u in uniques),
                                dtype=np.int32, count=len(uniques))
        # factorize marks a missing value -1, which indexes the appended -1.
        self.codes[i].append(np.append(to_global, np.int32(-1))[batch_codes])

    def _demote(self, i: int) -> None:
        """A later batch mixes in other types: from here on, plain objects."""
        values = _as_objects(list(self.vocab.pop(i)) + [None])
        codes = self.codes.pop(i)
        self.objects[i] = [values[np.concatenate(codes)]] if codes else []

    def frame(self, columns: list) -> pd.DataFrame:
        plain = sorted(self.objects)
        arrays = dict(zip(plain, _convert_like_rows(
            [np.concatenate(self.objects[i]) if self.objects[i] else np.empty(0, object)
             for i in plain])))
        for i, vocab in self.vocab.items():
            arrays[i] = _sorted_categorical(list(vocab), np.concatenate(self.codes[i]))
        df = pd.DataFrame({i: arrays[i] for i in range(self.width)}, copy=False)
        df.columns = columns
        return df


def _as_objects(values) -> np.ndarray:
    arr = np.empty(len(values), dtype=object)
    arr[:] = values
    return arr


def _sorted_categorical(values: list, codes: np.ndarray) -> pd.Categorical:
    """Categories in sorted order, as ``astype("category")`` makes them."""
    order = sorted(range(len(values)), key=values.__getitem__)
    remap = np.empty(len(values) + 1, dtype=np.int32)
    remap[order] = np.arange(len(values), dtype=np.int32)
    remap[-1] = -1  # missing stays missing
    return pd.Categorical.from_codes(remap[codes], categories=[values[j] for j in order])


def _convert_like_rows(arrays: list[np.ndarray]) -> list:
    """Give each object column the dtype ``pd.DataFrame(rows)`` would give it.

    That constructor turns rows into one object array per column and converts
    each with this function; calling it directly keeps the inference identical.
    """
    try:
        from pandas.core.internals.construction import convert_object_array
    except ImportError:  # pragma: no cover - a pandas that moved it
        frame = pd.DataFrame(list(zip(*arrays)), columns=range(len(arrays)))
        return [frame[i].array for i in range(len(arrays))]
    return convert_object_array(arrays, dtype=None)
