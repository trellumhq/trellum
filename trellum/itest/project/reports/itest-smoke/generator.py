"""itest-smoke report.

Proves data-sources/config.yaml -> LocalEnvResolver -> driver -> query_df
-> report HTML end to end against real postgres and clickhouse containers.
This generator contains no test-specific logic -- it queries the two
sources exactly like any other report would; itest/test_report_build.py
seeds the tables beforehand and inspects the built output afterward.
"""

from trellum import BaseReport
from trellum.components import DataSource, DataTable, KpiRow
from trellum.data import query_df

from . import queries

# Matched by itest/test_report_build.py's seed data and its assertion
# against the built output -- keep the two in sync if either changes.
PG_SENTINEL_LABEL = "itest-pg-sentinel-row"


class ItestSmokeReport(BaseReport):
    """Exactly one BaseReport subclass per generator.py -- the runner picks
    the first one it finds."""

    def generate(self, ctx):
        pg_conn = ctx.get_connection("pg_db")
        pg_df = query_df(pg_conn, queries.PG_ROWS, params={"who": PG_SENTINEL_LABEL})

        ch_conn = ctx.get_connection("ch_db")
        ch_df = query_df(ch_conn, queries.CH_ROWS)

        ctx.add_section("Postgres", [
            DataSource("pg", pg_df),
            DataTable(pg_df, title="Postgres rows (:param filtered)"),
            KpiRow([
                {"label": "Postgres row count", "agg": "count", "column": "id"},
            ], dataset_id="pg"),
        ])

        ctx.add_section("ClickHouse", [
            DataSource("ch", ch_df),
            DataTable(ch_df, title="ClickHouse rows"),
            KpiRow([
                {"label": "ClickHouse row count", "agg": "count", "column": "id"},
            ], dataset_id="ch"),
        ])
