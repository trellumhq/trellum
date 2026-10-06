"""Report-build lane: proves the whole pipeline end to end.

``data-sources/config.yaml -> LocalEnvResolver -> driver -> query_df ->
report HTML``, run for real against the postgres and clickhouse containers
via ``python -m trellum.run --all --no-serve`` in a subprocess -- the
harness analogue of CI's demo-build step.

Only collected when both "postgres" and "clickhouse" are selected via
--engines (see conftest.py's ``_CONDITIONAL_TESTS`` /
``pytest_collection_modifyitems`` -- this module is deselected, never
skipped, when either is missing).

The report itself (``itest/project/reports/itest-smoke/``) contains no
test-specific logic; this module seeds the two tables it queries, runs the
build, and inspects the output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from engines import _env  # same ITEST_<ENGINE>_<FIELD> env convention

_ITEST_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _ITEST_DIR / "project"
_OUTPUT_DIR = _PROJECT_DIR / "output"

# Kept in sync with itest/project/reports/itest-smoke/generator.py's
# PG_SENTINEL_LABEL -- the postgres row that query.py's :param binding
# filters for.
_PG_SENTINEL = "itest-pg-sentinel-row"
_CH_SENTINEL = "itest-ch-sentinel-row"


def _pg_conn_info() -> dict:
    return {
        "host": _env("postgres", "HOST", "localhost"),
        "port": int(_env("postgres", "PORT", "55432")),
        "database": _env("postgres", "DB", "itest"),
        "user": _env("postgres", "USER", "itest"),
        "password": _env("postgres", "PASS", "itest_pw"),
    }


def _ch_conn_info() -> dict:
    return {
        "host": _env("clickhouse", "HOST", "localhost"),
        "port": int(_env("clickhouse", "PORT", "58123")),
        "database": _env("clickhouse", "DB", "itest"),
        "user": _env("clickhouse", "USER", "itest"),
        "password": _env("clickhouse", "PASS", "itest_pw"),
        "secure": _env("clickhouse", "SECURE", "false").lower() in ("1", "true", "yes"),
    }


def _seed_postgres() -> None:
    from trellum.data.drivers import connect

    conn = connect("postgres", _pg_conn_info())
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS itest_smoke_pg")
        cur.execute(
            "CREATE TABLE itest_smoke_pg (id INT, label VARCHAR(200), amount DECIMAL(12,2))"
        )
        cur.execute(
            "INSERT INTO itest_smoke_pg (id, label, amount) VALUES "
            f"(1, '{_PG_SENTINEL}', 1234.56), (2, 'other-row', 1.00)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_clickhouse() -> None:
    from trellum.data.drivers import connect

    conn = connect("clickhouse", _ch_conn_info())
    conn.command("DROP TABLE IF EXISTS itest_smoke_ch")
    conn.command(
        "CREATE TABLE itest_smoke_ch (id Int32, label String, amount Decimal(12,2)) "
        "ENGINE = MergeTree ORDER BY id"
    )
    conn.command(
        "INSERT INTO itest_smoke_ch (id, label, amount) VALUES "
        f"(1, '{_CH_SENTINEL}', 42.00), (2, 'other-row', 2.00)"
    )


def _build_env() -> dict:
    env = dict(os.environ)
    pg = _pg_conn_info()
    ch = _ch_conn_info()
    env.update({
        "BI_ITEST_PG_HOST": pg["host"],
        "BI_ITEST_PG_PORT": str(pg["port"]),
        "BI_ITEST_PG_DB": pg["database"],
        "BI_ITEST_PG_USER": pg["user"],
        "BI_ITEST_PG_PASS": pg["password"],
        "BI_ITEST_CH_HOST": ch["host"],
        "BI_ITEST_CH_PORT": str(ch["port"]),
        "BI_ITEST_CH_DB": ch["database"],
        "BI_ITEST_CH_USER": ch["user"],
        "BI_ITEST_CH_PASS": ch["password"],
        "BI_ITEST_CH_SECURE": "true" if ch["secure"] else "false",
        # The build's once-a-day release check must not reach out from here.
        "FW_UPDATE_CHECK": "0",
    })
    return env


def test_report_build_end_to_end():
    _seed_postgres()
    _seed_clickhouse()

    if _OUTPUT_DIR.exists():
        shutil.rmtree(_OUTPUT_DIR)

    result = subprocess.run(
        [sys.executable, "-m", "trellum.run", "--all", "--no-serve", "--no-cache"],
        cwd=str(_PROJECT_DIR),
        env=_build_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"trellum.run exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

    report_out = _OUTPUT_DIR / "itest-smoke"
    index_html = report_out / "index.html"
    assert index_html.is_file(), f"{index_html} was not created"
    html = index_html.read_text(encoding="utf-8")
    assert "<html" in html.lower(), "index.html missing <html> tag"

    # Component data (DataSource, DataTable, KpiRow) is written to data.json
    # and fetched client-side (see rendering/html_builder.py) -- it is not
    # inlined into index.html -- so the sentinel proof that real rows made
    # it all the way from each container through query_df lives there.
    data_json = report_out / "data.json"
    assert data_json.is_file(), f"{data_json} was not created"
    report_data = json.loads(data_json.read_text(encoding="utf-8"))

    pg_rows = report_data["_ds_pg"]["_data"]
    ch_rows = report_data["_ds_ch"]["_data"]
    pg_flat = json.dumps(pg_rows)
    ch_flat = json.dumps(ch_rows)

    assert _PG_SENTINEL in pg_flat, (
        f"postgres sentinel row {_PG_SENTINEL!r} not found in the pg data "
        f"source -- the pg_db connection did not round-trip through query_df."
    )
    assert _CH_SENTINEL in ch_flat, (
        f"clickhouse sentinel row {_CH_SENTINEL!r} not found in the ch data "
        f"source -- the ch_db connection did not round-trip through query_df."
    )
    # The :param-bound postgres query is filtered to the sentinel row only --
    # the other postgres row must be absent, proving the WHERE clause (and
    # therefore bind_params) actually ran server-side. Clickhouse's query
    # has no filter, so both seeded rows should come back.
    assert len(pg_rows) == 1, f"expected exactly the :param-filtered row, got {pg_rows}"
    assert len(ch_rows) == 2, f"expected both unfiltered clickhouse rows, got {ch_rows}"
