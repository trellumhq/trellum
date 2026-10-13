# Source summary verification

Workers project complete report metric claims into PostgreSQL. HTTP requests
must not trigger reconciliation. `ReportSourceSummary.state` and `quality` are
part of the consumer contract: `pending`, `uncertain`, `unavailable`, `expiring`,
`expired` and `removed` are not complete current zero-claim results. `missing`
means an observed absent serving source. `ready/observed` means a historical
local observation, not a verified portal publication. `ready/verified` names a
known local successful run or an uncached remote immutable serving pointer.
Historical claims may remain in rows while their source is uncertain/expired;
consumers must join authoritative present/expiry flags and summary state.

`served_at` belongs to the serving metadata; `attempt_at/status` describes the
latest known attempt. Failed attempts can move top-level metadata while a
remote serving pointer retains its old build. Local failure can partially
modify serving files, so prior successful claims remain historical/uncertain.
`Report.last_built_at` keeps its independent successful-publish retention role.
Generation/token checks prevent stale inspectors overwriting newer operations.
A worker crash during expiry or inspection is repaired after its lease expires.
Deletion failure retains the expiry uncertainty fence until rebuild or observed
absence. Definition and summary generations are persisted per studio for
cross-process consumers; no process-local cache is authoritative.

The background service starts automatically in `runworker` (not `--once`). A
remote coordinator inspects shared storage; local runners inspect their pools.
Its separate thread never blocks build ticks/heartbeats and holds no DB locks
while reading artifacts. An optional command inspects one batch:

```sh
python manage.py reconcile_source_summaries --limit 25
```

Bounds: 25 inspections, 64 MiB decoded-input budget per batch, 2 MiB metadata,
32 MiB generated-preview input, 60 points per preview. Byte admission reserves
the maximum possible next inspection; failed reads report a conservative byte
upper bound. The 5-second batch admission window is not a hard runtime deadline:
a single storage operation/parsing job may exceed it. Remote summary reads use
2-second socket read timeout, existing 5-second connect timeout and up to two
SDK retries. These bound silent network waits, not total wall time under a
trickling provider. Poll interval is 5 seconds, lease 180 seconds, ordinary
recheck 60 seconds, exponential per-row failure backoff 10–3600 seconds. A batch
stops on provider failure. Missing rows sort before due existing rows; active
builds are excluded before selecting the batch. No fixed convergence-time claim
is made during provider outages or a continuously growing catalog.

Generated previews use the existing pure sparkline calculation and preserve
complete claims even when preview data is malformed/oversized. Preview status
is explicit. Full metadata/data payloads are not persisted.

Focused PostgreSQL tests (point `DATABASE_URL` at a disposable test database):

```sh
python -m pytest apps/reports/tests/test_source_summary.py apps/runner/tests/test_worker.py -q
```

Live tests launch actual worker subprocesses and framework builds. They require
PostgreSQL and an explicitly supplied disposable loopback S3-compatible HTTP
provider. A Moto 5.1.20 HTTP server is sufficient for protocol integration; this
is not evidence about real object-store latency, consistency or IAM. Never use
production credentials or resources. Example provider setup in an isolated
package directory:

```sh
uv pip install --target /tmp/source-provider --python /path/to/python 'moto[server]==5.1.20'
PYTHONPATH=/tmp/source-provider /path/to/python -m moto.server -H 127.0.0.1 -p 32776
PORTAL_SOURCE_LIVE=1 PORTAL_SOURCE_LIVE_S3_URL=http://127.0.0.1:32776 \
  python -m pytest apps/reports/tests/test_source_summary_live.py -q --basetemp=/tmp/source-live-results
```

Use distinct database names for concurrently running test suites. Live evidence
includes automatic historical 2000-claim reconciliation, real build success and
failure, independent-process DB visibility, graceful restart, SIGKILL while
inspection holds a committed lease, and real remote publication before/after
pointer faults. Fault injection is confined to child test settings around the
real pointer write; it does not replace framework execution or storage. The
crash test advances lease expiry explicitly instead of waiting180seconds.
Worker logs and per-backend result JSON are retained below the selected pytest
base temp directory. Ordinary test collection skips these live cases unless
explicitly opted in; skips are not verification.
