"""Dev-only: build a GB-scale synthetic Postgres datasource for load testing.

Fills a wide ``events`` table server-side (batched ``INSERT ... SELECT FROM
generate_series``; no client bandwidth involved) until it reaches the
requested size, then registers it as an org-shared DataSource so the stress
reports seeded by ``seed_stress_reports`` can query it.

    manage.py seed_synthetic_datasource --org sim-01 --gb 2
    manage.py seed_synthetic_datasource --org sim-01 --schema synth --reuse-portal-db --gb 0.01
    manage.py seed_synthetic_datasource --org sim-01 --drop

Address asymmetry, spelled out because it bites: the DataSource row stores
the address THE WORKER's child process dials (``--ds-host``/``--ds-port``,
default ``db:5432`` for the Docker sim worker), while this command connects
as an admin over ``--host``/``--port`` (default: the portal's DATABASE_URL,
e.g. ``127.0.0.1:5433`` from a Windows host). Only the worker ever uses the
stored address.

Idempotent: re-running resumes from ``max(id)`` and stops once the target
size is reached. ``--drop`` removes the database (or schema) and the
DataSource row.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.datasources.models import DataSource
from apps.orgs.models import Organization

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id       bigint PRIMARY KEY,
    ts       timestamptz NOT NULL,
    user_id  bigint NOT NULL,
    category text NOT NULL,
    amount   numeric(12, 2) NOT NULL,
    payload  text NOT NULL
)
"""

# ~900 bytes/row (payload = 28 repeated md5 hex digests); deterministic
# pseudo-randomness derived from the row id so re-runs are reproducible.
FILL_SQL = """
INSERT INTO events (id, ts, user_id, category, amount, payload)
SELECT g,
       now() - (g %% 90) * interval '1 day' - (g %% 86400) * interval '1 second',
       (g * 2654435761) %% 1000000,
       (ARRAY['retention','acquisition','monetisation','liveops',
              'marketing','finance','economy','social'])[1 + g %% 8],
       round((((g * 37) %% 1000000)::numeric) / 100, 2),
       repeat(md5(g::text), 28)
FROM generate_series(%(lo)s, %(hi)s) AS g
"""


class Command(BaseCommand):
    help = "Dev-only: create and fill a synthetic Postgres datasource for load testing."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="sim-01")
        parser.add_argument("--name", default="synthetic",
                            help="DataSource name reports reference.")
        parser.add_argument("--database", default="trellum_synth",
                            help="Dedicated database to create (default mode).")
        parser.add_argument("--schema", default="synth",
                            help="Schema name used with --reuse-portal-db.")
        parser.add_argument("--reuse-portal-db", action="store_true",
                            help="Create a schema in the portal DB instead of a new database.")
        parser.add_argument("--gb", type=float, default=2.0)
        parser.add_argument("--batch-rows", type=int, default=500_000)
        parser.add_argument("--ds-host", default="db",
                            help="Host stored in the DataSource row (worker-visible).")
        parser.add_argument("--ds-port", type=int, default=5432,
                            help="Port stored in the DataSource row (worker-visible).")
        parser.add_argument("--host", default=None, help="Admin connection host override.")
        parser.add_argument("--port", type=int, default=None)
        parser.add_argument("--user", default=None)
        parser.add_argument("--password", default=None)
        parser.add_argument("--drop", action="store_true")

    def handle(self, *args, **opts):  # noqa: ARG002
        if not settings.DEBUG:
            raise CommandError("seed_synthetic_datasource is a development helper; DEBUG is off.")

        org, _ = Organization.objects.get_or_create(
            slug=opts["org"], defaults={"name": opts["org"]}
        )

        if opts["drop"]:
            self._drop(org, opts)
            return

        if opts["reuse_portal_db"]:
            rows, size_mb = self._fill_schema(opts)
            db_name = settings.DATABASES["default"]["NAME"]
        else:
            rows, size_mb = self._fill_database(opts)
            db_name = opts["database"]

        source, created = DataSource.objects.update_or_create(
            org=org,
            name=opts["name"],
            defaults={
                "studio": None,
                "type": "postgres",
                "description": f"Synthetic load-test data ({size_mb:.0f} MB). "
                               "seeded-by: seed_synthetic_datasource",
                "config": {
                    "host": opts["ds_host"],
                    "port": str(opts["ds_port"]),
                    "database": db_name,
                },
                "credentials": {
                    "user": opts["user"] or self._admin_params()["user"],
                    "password": opts["password"] or self._admin_params()["password"],
                },
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"events table: {rows:,} rows, {size_mb:.0f} MB; "
            f"{'created' if created else 'updated'} DataSource {source} "
            f"(worker dials {opts['ds_host']}:{opts['ds_port']}/{db_name})"
        ))

    # ── connections ─────────────────────────────────────────────────────────

    def _admin_params(self) -> dict:
        db = settings.DATABASES["default"]
        return {
            "host": db.get("HOST") or "127.0.0.1",
            "port": int(db.get("PORT") or 5432),
            "user": db.get("USER") or "",
            "password": db.get("PASSWORD") or "",
            "dbname": db.get("NAME") or "",
        }

    def _admin_connect(self, opts: dict, dbname: str | None = None):
        import psycopg

        p = self._admin_params()
        return psycopg.connect(
            host=opts["host"] or p["host"],
            port=opts["port"] or p["port"],
            user=opts["user"] or p["user"],
            password=opts["password"] or p["password"],
            dbname=dbname or p["dbname"],
            autocommit=True,
        )

    # ── fill modes ──────────────────────────────────────────────────────────

    def _fill_database(self, opts: dict) -> tuple[int, float]:
        target = opts["database"]
        with self._admin_connect(opts) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone() is None:
                # CREATE DATABASE cannot run inside a transaction; the admin
                # connection is autocommit for exactly this statement.
                cur.execute(f'CREATE DATABASE "{target}"')
                self.stdout.write(f"created database {target}")
        with self._admin_connect(opts, dbname=target) as conn, conn.cursor() as cur:
            return self._fill(cur, opts, relation="events")

    def _fill_schema(self, opts: dict) -> tuple[int, float]:
        from django.db import connection

        schema = opts["schema"]
        with connection.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            try:
                return self._fill(cur, opts, relation=f'"{schema}".events')
            finally:
                cur.execute("SET search_path TO public")

    def _fill(self, cur, opts: dict, relation: str) -> tuple[int, float]:
        target_bytes = int(opts["gb"] * 1024**3)
        batch = max(1000, opts["batch_rows"])
        cur.execute(TABLE_DDL)
        cur.execute("SELECT coalesce(max(id), 0) FROM events")
        next_id = cur.fetchone()[0] + 1
        while True:
            cur.execute(f"SELECT pg_total_relation_size('{relation}')")
            size = cur.fetchone()[0]
            if size >= target_bytes:
                break
            # Cap the batch by the bytes still missing (~900 B/row) so a small
            # target isn't overshot by a whole default-size batch.
            step = min(batch, max(1000, (target_bytes - size) // 900))
            cur.execute(FILL_SQL, {"lo": next_id, "hi": next_id + step - 1})
            next_id += step
            self.stdout.write(
                f"  {next_id - 1:,} rows, {size / 1024**2:.0f} MB of "
                f"{target_bytes / 1024**2:.0f} MB"
            )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_category_ts ON events (category, ts)"
        )
        cur.execute("SELECT count(*) FROM events")
        rows = cur.fetchone()[0]
        return rows, size / 1024**2

    # ── teardown ────────────────────────────────────────────────────────────

    def _drop(self, org: Organization, opts: dict) -> None:
        deleted, _ = DataSource.objects.filter(org=org, name=opts["name"]).delete()
        if opts["reuse_portal_db"]:
            from django.db import connection

            with connection.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{opts["schema"]}" CASCADE')
            what = f"schema {opts['schema']}"
        else:
            with self._admin_connect(opts) as conn, conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{opts["database"]}" WITH (FORCE)')
            what = f"database {opts['database']}"
        self.stdout.write(self.style.SUCCESS(
            f"dropped {what}; removed {deleted} DataSource row(s)"
        ))
