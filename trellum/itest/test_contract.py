"""The shared per-engine contract.

Every test here is parametrized over the ``engine`` fixture (see
conftest.py's ``pytest_generate_tests``) and contains no per-engine
if/elif branching -- differences between engines live entirely in
``engines.py``'s ``EngineSpec`` definitions (DDL/seed data, quirks,
bad_conn_info). Engines not passed to ``--engines`` are not collected at
all; a selected engine that can't be reached fails the ``engine_conn``
fixture (see conftest.py) rather than skipping.
"""

from __future__ import annotations

import contextlib
from datetime import date

import pandas as pd
import pytest
from engines import COLUMNS


def _colmap(engine) -> dict[str, str]:
    """Map contract column names to the names actually returned by this engine.

    Handles the "upper-cols" quirk: Snowflake (and fakesnow's emulation of
    it) upper-cases unquoted identifiers, so a table created with lowercase
    column names comes back with uppercase ones in the result set, even
    though the SQL that created and queries it is written lowercase
    throughout (see engines.py's SELECT_ALL_SQL docstring).
    """
    if "upper-cols" in engine.quirks:
        return {c: c.upper() for c in COLUMNS}
    return {c: c for c in COLUMNS}


def test_query_df_round_trip(engine, engine_conn):
    """connect -> query_df -> DataFrame, checked against the shared contract."""
    from trellum.data.query import query_df

    df = query_df(engine_conn, engine.select_all_sql, cache_ttl=0)
    cols = _colmap(engine)

    assert len(df) == 4, f"{engine.name}: expected 4 seeded rows, got {len(df)}"

    # amount_dec: float64 either because query_df's Decimal->float pass
    # converted it (the generic DBAPI path, most engines here) or because
    # the engine never produced a Decimal in the first place (sqlite has
    # no such type and returns REAL/float natively) -- either way, by the
    # time it reaches the caller it must be a plain float column.
    amount_col = df[cols["amount_dec"]]
    assert amount_col.dtype == "float64", (
        f"{engine.name}: amount_dec should be float64 (proves the Decimal "
        f"cast in query_df, or a native float return), got {amount_col.dtype}"
    )
    assert sorted(amount_col.tolist()) == pytest.approx(
        [-99.99, 0.0, 1234.56, 100000.0]
    )

    ratio_col = df[cols["ratio"]]
    assert ratio_col.isna().sum() == 1, (
        f"{engine.name}: row 4's NULL ratio should round-trip as NaN, "
        f"got {ratio_col.tolist()}"
    )

    note_col = df[cols["note"]]
    assert note_col.isna().sum() == 1, (
        f"{engine.name}: row 2's NULL note should round-trip as NaN/None, "
        f"got {note_col.tolist()}"
    )

    label_col = df[cols["label"]]
    labels = set(label_col.tolist())
    assert "héllo — ünïcode" in labels, f"{engine.name}: unicode round-trip failed: {labels}"
    assert "O'Brien" in labels, f"{engine.name}: embedded-quote round-trip failed: {labels}"

    # Dates: some drivers return datetime.date/datetime objects (object
    # dtype), others datetime64[ns] -- assert semantically via
    # pd.to_datetime rather than pinning a dtype.
    day_col = pd.to_datetime(df[cols["day"]])
    assert set(day_col.dt.date) == {
        date(2026, 1, 1), date(2026, 1, 15), date(2026, 2, 1), date(2026, 3, 1),
    }, f"{engine.name}: day values didn't round-trip: {day_col.tolist()}"

    ts_col = pd.to_datetime(df[cols["ts"]])
    assert ts_col.notna().all(), f"{engine.name}: ts should never be NULL: {ts_col.tolist()}"


def test_param_binding_with_quote(engine, engine_conn):
    """`:param` binding, using the embedded-quote row -- proves bind_params
    escapes single quotes correctly for this engine rather than breaking
    the query or (worse) being exploitable."""
    from trellum.data.query import query_df

    df = query_df(engine_conn, engine.param_sql, params={"who": "O'Brien"}, cache_ttl=0)

    assert len(df) == 1, f"{engine.name}: expected exactly the O'Brien row, got {len(df)}"
    cols = _colmap(engine)
    assert df[cols["label"]].iloc[0] == "O'Brien"


def test_bad_conn_info_raises(engine):
    """Wrong credentials must raise from connect() itself.

    ``bad_conn_info`` is None for engines with no real authentication to
    get wrong (embedded sqlite/duckdb) and for engines where a "wrong
    password" can't be exercised meaningfully (fakesnow never validates
    credentials; bigquery.Client() construction is lazy and never touches
    the network, see engines.py for both). For those, this test
    intentionally does nothing and passes -- it does NOT skip. Skipping is
    reserved for engines that were never selected via --engines; every
    selected engine's tests report pass or fail, never skipped.
    """
    if engine.bad_conn_info is None:
        return

    from trellum.data.drivers import connect

    ctx = engine.setup() if engine.setup is not None else contextlib.nullcontext()
    with ctx:
        with pytest.raises(Exception):
            connect(engine.name, engine.bad_conn_info())

