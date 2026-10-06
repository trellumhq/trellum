# itest -- real-connection integration harness

## What this is, and why it's not in the default suite

`testing/test_connections.py` (the default suite) proves the driver
registry, resolver chain, and dispatch logic entirely with
`unittest.mock.patch.dict(sys.modules)` -- it never opens a socket. That's
correct for a suite with a hard "no skipped tests" CI gate: a real database
either isn't there in CI (fails) or has to be spun up for every PR (slow,
and vertica CE alone is multi-GB). So the mock suite proves *the code*, and
this directory proves *the wiring* -- that `connect()` -> `query_df()` ->
a DataFrame with the right shape actually happens against a real server,
for every engine the framework supports, plus a full report build.

It lives outside `testing/` and is never invoked by the default `pytest`
run:

- Root `pytest.ini` sets `testpaths = testing` -- a bare `pytest` from the
  repo root never descends into `itest/`.
- CI's `tests` job runs `python -m pytest ../testing` explicitly.
- This directory self-roots via its own `itest/pytest.ini`
  (`testpaths = .`) and has **no `itest/__init__.py`** -- it must never
  become importable as part of the `trellum` package.

Engine selection here is **parametrization, not skipping**. An engine you
don't pass to `--engines` is simply never collected. Nothing in this
harness ever reports a "skipped" test -- a *selected* engine that can't be
reached fails outright, after a connect-retry window, with a message
naming the compose command that should have been run first.

## Prerequisites

```
pip install -r itest/requirements-itest.txt
```

Docker Desktop, running, for anything beyond the embedded engines
(sqlite, duckdb, fakesnow, file, api never need it).

## Quick start

```
python itest/run.py                     # light engines: containers up -> pytest -> down
python itest/run.py --engines postgres,duckdb
python itest/run.py --profile heavy     # adds vertica
python itest/run.py --profile s3        # adds the S3 emulator
python itest/run.py --no-docker         # embedded-only, zero Docker
python itest/run.py --keep              # leave containers up for iteration
python itest/run.py --engines postgres -- -k test_query_df_round_trip
```

`run.py` derives which compose services to bring up from the engines you
selected (`EngineSpec.compose_service` in `engines.py`), runs
`docker compose ... up -d --wait`, runs pytest with whatever Python
interpreter invoked `run.py` (so a venv "just works"), then tears the
containers back down (`docker compose down -v`) unless you pass `--keep`.
Everything after a literal `--` passes straight through to pytest.

## Engine matrix

| engine | container | notes |
|---|---|---|
| sqlite | none (embedded) | tmp file per run |
| duckdb | none (embedded) | tmp file per run |
| fakesnow | none (in-process, DuckDB-backed) | emulates the `snowflake` driver type; see fidelity note below |
| postgres | `postgres:16-alpine` | |
| postgres_ssh | `linuxserver/openssh-server` bastion, *in front of the postgres container* | the `postgres` driver through an SSH tunnel; see the ssh note |
| redshift | *shares the postgres container* | psycopg2 speaks Redshift's wire protocol; see fidelity note |
| mysql | `mysql:8.0` | |
| clickhouse | `clickhouse/clickhouse-server` | plain HTTP (`secure: false`); see fidelity note |
| sqlserver | `mcr.microsoft.com/mssql/server:2022-latest` | Developer edition, EULA accepted at container start; see auth note |
| bigquery | `ghcr.io/goccy/bigquery-emulator` | REST-only emulator; see fidelity note |
| trino | `trinodb/trino` | stock `memory` catalog, no authenticator, plain HTTP; see fidelity note |
| vertica | `vertica/vertica-ce` (or `$ITEST_VERTICA_IMAGE`) | **gated image**, `--profile heavy`, opt-in |
| file | none | csv/xlsx/parquet through `tmp_path`; `read_api` against a real stdlib `http.server` |
| s3 | `rustfs/rustfs` | `--profile s3`, opt-in; s3:// file source |

Every container's port/credentials are hardcoded defaults in both
`docker-compose.yml` and `engines.py`'s `EngineSpec.conn_info` callables --
override either side with the same `ITEST_<ENGINE>_<FIELD>` env var
(`ITEST_POSTGRES_HOST`, `ITEST_CLICKHOUSE_PASS`, ...). Host ports in
`docker-compose.yml` read the same `ITEST_<ENGINE>_PORT` vars, which
matters on Windows: WinNAT reserves shifting "excluded port ranges" after
reboots, so a hardcoded port that bound fine yesterday can be denied today
(`netsh interface ipv4 show excludedportrange protocol=tcp` to see the
current ranges -- mysql's default already dodged one such range, which is
why it's 54306 rather than 53306).

## Fidelity caveats

Real containers prove the wire protocol and `query_df` dispatch, not
necessarily every dialect quirk of the product they stand in for:

- **fakesnow** is DuckDB-backed and never validates credentials or takes
  the real Snowflake connector's `fetch_pandas_all()` code path (see
  `engines.py`'s `FAKESNOW` docstring) -- it proves the generic DBAPI path
  and `query_df` dispatch, not Snowflake's actual SQL dialect.
- **bigquery-emulator** implements a subset of the real REST API.
  `google-cloud-bigquery-storage` isn't installed in this harness, so
  `to_dataframe()` uses the REST fallback path -- which is also what a lot
  of real-world BigQuery client setups do, so this isn't purely an
  emulator artifact. The emulator's `/jobs` endpoint doesn't implement
  BigQuery "load" jobs, only "query" jobs, so this harness seeds bigquery
  via DML `INSERT` statements run as query jobs rather than
  `load_table_from_json` -- see the comment in `engines.py`'s
  `_bigquery_seed` for the full story.
- **redshift** reuses the postgres container: psycopg2 speaks Redshift's
  wire protocol, so `connect()`/`query_df()` dispatch is genuinely proven,
  but Redshift-only SQL (its DISTKEY/SORTKEY DDL, system tables, COPY/
  UNLOAD) is not exercised anywhere here.
- **clickhouse** connects over plain HTTP (`secure: false`) -- this proves
  the driver against a real ClickHouse server, just not TLS-terminated
  ClickHouse the way most production deployments run it.
- **trino** runs the stock image's `memory` connector with no
  authenticator over plain HTTP (`secure: false`). That proves the driver,
  the session `catalog`/`schema` defaults and `query_df` dispatch against
  a real coordinator -- not TLS, not HTTP basic auth (which Trino only
  accepts over HTTPS), and no real catalog connector.
- **databricks** has no local emulator, so it has no lane here at all:
  the driver's kwargs mapping and its backslash string-escaping dialect
  are proven only by the mocked unit tests in `testing/test_connections.py`.
  Proving the wire path needs a real SQL warehouse.

## SQL Server: EULA and the sa/Developer-edition workaround

`ACCEPT_EULA=Y` + `MSSQL_PID=Developer` accepts Microsoft's Developer
Edition license for this container at start time -- fine for local/CI
testing, not for production use of that image.

SQL Server enforces password complexity on every login, including ones
seeded via environment variables at container start. `engines.py`'s own
default SQL Server credentials (`itest` / `itest_pw`, matching every other
engine's convention) fail that policy, and there's no environment variable
that seeds a *non-sa* login at container start-up -- only `MSSQL_SA_PASSWORD`
for the built-in `sa` account. Rather than touch `engines.py` (every other
engine's DDL/seed logic lives there, this is purely an auth-shape
difference), this harness standardizes on connecting as `sa` with a
complex password:

```
ITEST_SQLSERVER_USER=sa
ITEST_SQLSERVER_PASS=Itest!Passw0rd
```

`run.py` exports both (as defaults -- an existing value in your
environment wins) before starting containers and running pytest. Running
things manually (see below), export them yourself first, or override with
your own values -- `docker-compose.yml`'s `MSSQL_SA_PASSWORD` reads from
the same `ITEST_SQLSERVER_PASS` var (defaulting to the same value), so the
container and the test client always agree.

The `sqlserver` spec also defaults its `database` to `master`, not
`itest`: a fresh mssql container has only the system databases, there's no
`POSTGRES_DB`-style env var to create one, and the spec's connection must
succeed before any seed SQL could. Beware when debugging: SQL Server
reports "database does not exist" as the *same* error 18456 login failure
as a wrong password.

`sqlserver` also has no compose healthcheck: `sqlcmd`'s path has moved
around across image revisions (`mssql-tools` vs `mssql-tools18`), making a
`CMD-SHELL` healthcheck unstable. `conftest.py`'s connect-retry loop is
the real gate here, backed by a 120s `startup_timeout` in `engines.py`.

## ClickHouse container quirks

Two things worth knowing if you're debugging this container by hand:

- Creating a non-`default` user via `CLICKHOUSE_USER`/`CLICKHOUSE_PASSWORD`
  makes the entrypoint script boot a temporary server to run the
  `CREATE USER`/`CREATE DATABASE` statements, then hands off to the real
  one. That handoff is not instant -- `docker compose up --wait` with the
  healthcheck below is what makes this reliable rather than racy.
- The healthcheck probes `http://127.0.0.1:8123/ping`, not `localhost`:
  this image's container has no IPv6 loopback, and `localhost` resolves to
  `::1` first in Alpine/musl's resolver order, so a probe against
  `localhost` gets a spurious "connection refused" even once the server is
  actually serving requests on `127.0.0.1`/`0.0.0.0`.

## The postgres_ssh lane (SSH tunnel)

`postgres_ssh` is the `postgres` spec with `ssh_host`/`ssh_port`/`ssh_user`/
`ssh_key_path` added and `host: postgres`, `port: 5432` -- the database as
the bastion sees it, by compose service name, which this machine cannot
resolve at all. So the contract tests can only pass if the framework's
tunnel (`trellum/data/ssh_tunnel.py`, opened by `drivers.connect`) really
carries the connection; `itest/test_ssh_tunnel.py` adds that a pinned
`ssh_host_key` is honoured (the bastion's real key connects, a wrong one is
refused) and that closing the connection frees the local port. The bastion's
own host/port/user read `ITEST_SSH_HOST` / `ITEST_SSH_PORT` / `ITEST_SSH_USER`
(`ssh` is its own compose service, and `ITEST_SSH_PORT` is what
`docker-compose.yml` maps the host port from); the database fields read
`ITEST_POSTGRES_SSH_*` like any other engine.

The bastion is `linuxserver/openssh-server` with key-only auth. `run.py`
generates an ed25519 pair per run into the temp directory
(`engines.ensure_ssh_keypair`) -- nothing is checked in -- and hands the
public half to compose as `ITEST_SSH_PUBLIC_KEY` and the private half to
pytest as `ITEST_SSH_KEY_PATH`. The image ships `AllowTcpForwarding no`;
`itest/ssh-sshd.conf` is mounted as an `sshd_config.d` drop-in to turn it
back on. For a manual run, make a pair yourself and export both before
`docker compose up`:

```
ssh-keygen -t ed25519 -N "" -f /tmp/itest_ssh
set ITEST_SSH_KEY_PATH=/tmp/itest_ssh
set ITEST_SSH_PUBLIC_KEY=<contents of /tmp/itest_ssh.pub>
```

## The s3 lane (RustFS)

`itest/test_file_sources.py::test_read_source_csv_s3` is only collected
when `s3` is passed to `--engines` (equivalently, `--profile s3`, which
`run.py` adds automatically). It seeds a bucket via `boto3` pointed at
RustFS's S3-compatible endpoint, then reads it back through
`trellum.data.query.read_source("csv", "s3://...", storage_options={...})`
-- this needs zero framework changes, since `read_source` forwards
`**kwargs` straight to pandas, which hands `storage_options` to `s3fs`.

```
python itest/run.py --profile s3 --engines s3,file
```

## The report-build lane

`itest/test_report_build.py` is only collected when both `postgres` and
`clickhouse` are selected. It seeds two tables directly, then runs
`python -m trellum.run --all --no-serve --no-cache` as a subprocess
against `itest/project/` -- a minimal project with its own
`trellum/__init__.py` shim (same idea as `demo/trellum/__init__.py`,
one directory deeper) and a `reports/itest-smoke/` report that queries
both containers through the normal
`report.yaml -> data-sources/config.yaml -> LocalEnvResolver -> driver ->
query_df` path, driven entirely by `BI_ITEST_PG_*`/`BI_ITEST_CH_*` env
vars (nothing is hardcoded in `data-sources/config.yaml` -- that's the
whole point, it's exercising the same resolver path a real deployment
uses). The test then asserts on `output/itest-smoke/data.json`: component
data (`DataSource`, `DataTable`, `KpiRow`) is written there and fetched
client-side (see `rendering/html_builder.py`) rather than inlined into
`index.html`, so that's where the seeded sentinel rows have to show up to
prove the round trip actually happened.

```
python itest/run.py --engines postgres,clickhouse -- -k test_report_build
```

## Running containers by hand

```
docker compose -f itest/docker-compose.yml -p bi-framework-itest up -d --wait postgres ssh mysql clickhouse sqlserver bigquery trino
python -m pytest itest -q --engines=postgres,postgres_ssh,mysql,clickhouse,sqlserver,redshift,bigquery,trino,fakesnow,sqlite,duckdb,file,api
docker compose -f itest/docker-compose.yml -p bi-framework-itest down -v
```

Add `--profile heavy` (vertica) or `--profile s3` (minio) to the `up`
command as needed, and the matching engine names to `--engines`.

## Vertica: gated image

Vertica Community Edition's Docker Hub image (`vertica/vertica-ce`) is
gated behind an OpenText account since the acquisition -- an anonymous
`docker pull` is denied. `docker-compose.yml`'s `vertica` service (behind
the `heavy` profile) exists so anyone with pull access can use it:

```
docker login                                    # your OpenText/Docker Hub credentials
set ITEST_VERTICA_IMAGE=your-registry/vertica-ce:tag   # only if not vertica/vertica-ce:latest
python itest/run.py --profile heavy --engines vertica
```

Nothing in this harness attempts to pull or run that image automatically
-- `run.py` only ever brings up services for engines you explicitly
selected. Vertica CE is also multi-GB and takes 1-3 minutes to start
(`startup_timeout=300` in `engines.py`), which is why it's opt-in rather
than part of the light default set.

## Follow-ups (tracked outside this repo)

Not in scope here -- product/cross-repo planning tracks these separately:

- A seam test on the embedding-application side: materialize a data source ->
  env resolution -> live connect, plus surfacing the new driver types
  (duckdb, sqlserver, redshift) in whatever type list an embedding
  application maintains.
- Real-endpoint stubs for `google_sheets`/`onedrive` (both hardcode https
  URLs today; they stay on mocks in the default suite for now).
- `read_api` as a declarable source type with externally managed credentials.
