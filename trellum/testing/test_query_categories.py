"""query_df returns repeated text as category columns, and nothing else changes.

These pin that the values, the row order and every non-text column's dtype
are exactly what ``pd.DataFrame(cursor.fetchall())`` gives; that only text
which repeats becomes a category; that ``compact_text=False`` is the plain
frame; and that reading repeated text this way needs a fraction of the memory.
"""

from __future__ import annotations

import collections
import decimal
import sqlite3
import tracemalloc

import pandas as pd
import pytest

import trellum.data.fetch as f
import trellum.data.query as q

TITLES = ("Coral Quest", "Iron Vanguard", "Neon Racer", "héllo — ünïcode", "O'Brien")


@pytest.fixture(autouse=True)
def _no_cache():
    q.disable_cache()
    yield
    q.enable_cache()


def _table(n: int) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (id INTEGER, title TEXT, uid TEXT, amount REAL, note TEXT)")
    con.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?, ?)",
        (
            (i, TITLES[i % len(TITLES)], f"user-{i}", i * 0.25 if i % 7 else None,
             None if i % 3 else "check")
            for i in range(n)
        ),
    )
    con.commit()
    return con


def _plain(con, sql="SELECT * FROM t") -> pd.DataFrame:
    cur = con.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def _values(series: pd.Series) -> list:
    return series.astype(object).where(series.notna(), None).tolist()


class _Cursor:
    """A DB-API cursor over fixed rows, for shapes sqlite cannot produce."""

    def __init__(self, rows):
        self._rows = list(rows)

    def fetchmany(self, size):
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def fetchall(self):
        out, self._rows = self._rows, []
        return out


def test_repeated_text_comes_back_as_sorted_categories():
    df = q.query_df(_table(25_000), "SELECT * FROM t")
    assert isinstance(df["title"].dtype, pd.CategoricalDtype)
    assert isinstance(df["note"].dtype, pd.CategoricalDtype)
    assert list(df["title"].cat.categories) == sorted(TITLES)
    assert not df["title"].cat.ordered
    # Mostly distinct text stays ordinary text.
    assert not isinstance(df["uid"].dtype, pd.CategoricalDtype)


def test_values_order_and_other_dtypes_match_the_plain_frame():
    con = _table(f._FETCH_BATCH_ROWS * 2 + 17)
    got = q.query_df(con, "SELECT * FROM t")
    expected = _plain(con)
    assert list(got.columns) == list(expected.columns)
    for col in expected.columns:
        assert _values(got[col]) == _values(expected[col]), col
        if not isinstance(got[col].dtype, pd.CategoricalDtype):
            assert got[col].dtype == expected[col].dtype, col
    pd.testing.assert_index_equal(got.index, expected.index)


def test_compact_text_false_is_the_plain_frame():
    con = _table(25_000)
    pd.testing.assert_frame_equal(
        q.query_df(con, "SELECT * FROM t", compact_text=False), _plain(con), check_exact=True)


def test_missing_text_is_a_missing_category():
    df = q.query_df(_table(30), "SELECT * FROM t")
    assert df["note"].isna().sum() == 20
    assert list(df["note"].cat.categories) == ["check"]


def test_text_mixed_with_other_types_later_is_read_as_before():
    rows = [("a" if i % 2 else "b", i) for i in range(f._FETCH_BATCH_ROWS)]
    rows += [(7, 1), ("a", 2), (None, 3)]
    df = f.read_cursor(_Cursor(rows), ["label", "n"])
    expected = pd.DataFrame(rows, columns=["label", "n"])
    assert df["label"].dtype == expected["label"].dtype
    assert _values(df["label"]) == _values(expected["label"])
    assert df["n"].dtype == expected["n"].dtype


@pytest.mark.parametrize("value", [1, 1.5, True, decimal.Decimal("2.50")])
def test_a_column_that_is_not_text_keeps_its_type(value):
    rows = [(value, "x") for _ in range(10)]
    df = f.read_cursor(_Cursor(rows), ["v", "label"])
    expected = pd.DataFrame(rows, columns=["v", "label"])
    assert df["v"].dtype == expected["v"].dtype
    assert df["v"].tolist() == expected["v"].tolist()


def test_list_rows_and_tuple_batches():
    rows = [["x" if i % 2 else "y", i] for i in range(25_000)]
    cursor = _Cursor(rows)
    cursor.fetchmany = lambda size, _c=cursor: tuple(_Cursor.fetchmany(_c, size))
    df = f.read_cursor(cursor, ["label", "n"])
    assert isinstance(df["label"].dtype, pd.CategoricalDtype)
    assert _values(df["label"]) == [r[0] for r in rows]
    assert df["n"].tolist() == [r[1] for r in rows]


def test_driver_row_objects_are_read_whole_then_compacted():
    Row = collections.namedtuple("Row", "label n")
    rows = [Row("a" if i % 2 else "b", i) for i in range(50)]
    df = f.read_cursor(_Cursor(rows), ["label", "n"])
    assert isinstance(df["label"].dtype, pd.CategoricalDtype)
    assert _values(df["label"]) == [r.label for r in rows]


def test_a_cursor_without_fetchmany_still_gets_categories():
    class FetchallOnly:
        def fetchall(self):
            return [("a", 1), ("a", 2), ("b", 3), ("a", 4)]

    df = f.read_cursor(FetchallOnly(), ["label", "n"])
    assert isinstance(df["label"].dtype, pd.CategoricalDtype)
    assert df["n"].tolist() == [1, 2, 3, 4]


def test_a_cursor_whose_fetchmany_is_not_a_row_list_falls_back_to_fetchall():
    from unittest.mock import MagicMock

    cursor = MagicMock()
    cursor.fetchall.return_value = [(1, "a"), (2, "b"), (3, "a"), (4, "a")]
    df = f.read_cursor(cursor, ["n", "label"])
    assert df["n"].tolist() == [1, 2, 3, 4]
    assert _values(df["label"]) == ["a", "b", "a", "a"]


def test_empty_result_matches_the_plain_result():
    con = _table(0)
    pd.testing.assert_frame_equal(q.query_df(con, "SELECT * FROM t"),
                                  q.query_df(con, "SELECT * FROM t", compact_text=False))


def test_frames_from_dataframe_drivers_are_compacted_the_same_way():
    df = pd.DataFrame({
        "title": ["a", "b", "a", "a"],
        "uid": ["u1", "u2", "u3", "u4"],
        "mixed": ["a", 1, "a", "a"],
        "price": [decimal.Decimal("1")] * 4,
        "n": [1, 2, 3, 4],
    })
    out = f.compact_text_columns(df.copy())
    assert isinstance(out["title"].dtype, pd.CategoricalDtype)
    for col in ("uid", "mixed", "price", "n"):
        assert out[col].dtype == df[col].dtype, col


def test_the_cache_keeps_compact_and_plain_results_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_cache_dir", lambda: tmp_path)
    q.enable_cache()
    con = _table(30)
    compact = q.query_df(con, "SELECT * FROM t", cache_ttl=3600)
    plain = q.query_df(con, "SELECT * FROM t", cache_ttl=3600, compact_text=False)
    assert isinstance(compact["title"].dtype, pd.CategoricalDtype)
    assert not isinstance(plain["title"].dtype, pd.CategoricalDtype)
    assert len(list(tmp_path.glob("*.pkl"))) == 2


def test_repeated_text_needs_a_fraction_of_the_memory():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (d TEXT, title TEXT, platform TEXT, tier TEXT, amount REAL)")
    con.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?, ?)",
        ((f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}", TITLES[i % 3], ("iOS", "Android", "Web")[i % 3],
          ("non_spender", "minnow", "dolphin", "whale")[i % 4], i * 0.5) for i in range(200_000)),
    )
    con.commit()

    def peak(fn):
        tracemalloc.start()
        try:
            fn()
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    plain = peak(lambda: q.query_df(con, "SELECT * FROM t", compact_text=False))
    compact = peak(lambda: q.query_df(con, "SELECT * FROM t"))
    assert compact < 0.4 * plain, (compact, plain)
