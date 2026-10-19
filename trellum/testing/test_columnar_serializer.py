"""_serialize_columnar output for text columns, pinned value by value."""

from __future__ import annotations

import pandas as pd
import pytest

import trellum.components.filterable as filterable
from trellum.components.filterable import _serialize_columnar

NAN = float("nan")


def test_repeated_text_with_none_is_dictionary_encoded_in_first_seen_order():
    df = pd.DataFrame({"a": ["x", None, "x", "y"] * 10, "n": list(range(40))})
    out = _serialize_columnar(df)
    assert out["_cols"] == ["a", "n"]
    assert out["_dict"] == {"a": ["x", None, "y"]}
    assert out["_data"] == [[[0, 1, 0, 2][i % 4], i] for i in range(40)]


def test_mostly_distinct_text_passes_through():
    df = pd.DataFrame({"u": [f"u{i}" for i in range(10)]})
    out = _serialize_columnar(df)
    assert "_dict" not in out
    assert out["_data"] == [[f"u{i}"] for i in range(10)]


def test_nan_in_a_text_column_becomes_null():
    df = pd.DataFrame({"a": ["x", NAN] * 10})
    out = _serialize_columnar(df)
    assert out["_dict"] == {"a": ["x", None]}
    assert out["_data"] == [[0], [1]] * 10


def test_date_objects_among_text_are_formatted():
    df = pd.DataFrame({"d": [pd.Timestamp("2026-01-02 03:04:05"), "z", None, NAN]})
    out = _serialize_columnar(df)
    assert out["_data"] == [["2026-01-02T03:04"], ["z"], [None], [None]]


def test_rows_are_lists():
    out = _serialize_columnar(pd.DataFrame({"a": ["x", "y"], "b": [1.5, 2.5]}))
    assert out["_data"] == [["x", 1.5], ["y", 2.5]]
    assert all(type(row) is list for row in out["_data"])


@pytest.mark.slow
def test_text_columns_are_not_inspected_value_by_value(monkeypatch):
    """The bound: a text-and-None column costs no per-value attribute check."""
    calls = 0

    def counting_hasattr(obj, name):
        nonlocal calls
        calls += 1
        return hasattr(obj, name)

    monkeypatch.setattr(filterable, "hasattr", counting_hasattr, raising=False)
    n = 50_000
    df = pd.DataFrame({
        "title": ["Coral Quest", "Iron Vanguard", None] * (n // 3) + ["Neon Racer"] * (n % 3),
        "uid": [f"user-{i}" for i in range(n)],
    })
    _serialize_columnar(df)
    assert calls == 0
    mixed = pd.DataFrame({"d": [pd.Timestamp("2026-01-01"), "x"] * 10})
    _serialize_columnar(mixed)
    assert calls > 0  # the guard above is live: other columns still check
