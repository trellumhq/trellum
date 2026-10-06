# Load simulation

Dev-only tooling for exercising the queue the way production will: deep
queues, memory admission, OOM kills, timeouts, org fairness — and the
per-report duration/memory statistics the dashboard shows for them.

Three commands (all refuse to run unless `DEBUG=True`) plus one compose
override:

| Piece | What it does |
|---|---|
| `seed_synthetic_datasource` | Builds a GB-scale Postgres `events` table server-side and registers it as an org-shared DataSource. |
| `seed_stress_reports` | Writes parameterized stress reports (sleep / memory / dbscan / mixed / failing / memhog / oom / timeout) or a `--showcase` studio of ~20 realistic reports. |
| `simulate_load` | Enqueues bursts across `sim-NN` orgs, shows live progress, and prints a summary: throughput, queue-wait/duration percentiles, peak-memory spread, per-org fairness with Jain's index. |
| `docker-compose.sim.yml` | A Linux worker with a 2 GB `mem_limit`, a 1536 MB runner budget, and the dev encryption key — real RLIMIT_AS, real `/proc` peak-RSS, real kernel OOM kills. |

Why Docker for the worker: memory *measurement* works on Linux
(`/proc/<pid>/status`) and Windows (`PeakWorkingSetSize`), but memory
*enforcement* (the RLIMIT_AS cap) is POSIX-only and kernel OOM kills need a
container `mem_limit` — so the memhog/oom profiles only behave for real on
the Linux worker. The enqueue/driver side is just Postgres writes and runs
fine from the host.

## One-time setup

```powershell

# .env needs at least: POSTGRES_PASSWORD=bi, SESSION_SECRET_KEY=<anything>, plus
# whatever docker-compose.yml already requires in your setup
docker compose -f docker-compose.yml -f docker-compose.sim.yml build worker
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d db migrate
```

Host-side commands use dev settings (Postgres on `127.0.0.1:5433`, the
compose override publishes it there):

```powershell
$env:DJANGO_SETTINGS_MODULE = 'trellum_portal.settings.dev'
```

## Automated load run

```powershell
# 1. ~2 GB of synthetic rows + the DataSource row. --ds-host db is what the
#    WORKER dials (inside the compose network); the admin connection this
#    command opens defaults to the portal DB at 127.0.0.1:5433.
python manage.py seed_synthetic_datasource --org sim-01 --gb 2 --ds-host db --ds-port 5432

# 2. Four sim orgs x ~12 stress reports each (idempotent)
python manage.py simulate_load --orgs 4 --reports-per-org 12 --setup-only

# 3. Start the Linux worker and watch it
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d worker
docker compose logs -f worker

# 4. Open the dashboard FIRST if you want to watch live:
#    python manage.py runserver  ->  http://127.0.0.1:8000/s/sim-01/stress/
#    The Operations view polls and updates rows in place while the driver runs.

# 5. Drive: 5 bursts of 40 enqueues, 30 s apart, then drain + summary
python manage.py simulate_load --orgs 4 --no-setup --bursts 5 --burst-size 40 --burst-interval 30
```

What the summary should show on a healthy system:

- queue depth well above `WORKER_MAX_CONCURRENT`, draining steadily;
- Jain's index near 1.0 for symmetric orgs (fair round-robin);
- `error` count == failing+memhog seeds, `timeout` == timeout seeds,
  `oom_killed` > 0 (the 2 GB container cap doing its job);
- a real peak-memory distribution ("no reading: 0 runs") — if every run has
  no reading, the builds ran somewhere without `/proc` (not the sim worker).

Useful variants:

```powershell
python manage.py simulate_load --since 2026-08-15T12:00:00+00:00   # summary-only
python manage.py simulate_load --no-wait ...                        # don't drain
python manage.py simulate_load --cleanup    # delete sim-NN orgs + data dirs
```

`--cleanup` only touches orgs matching `sim-<two digits>`; the showcase org
below is untouched.

## Interactive showcase (drive it yourself)

A realistic org/studio for pressing Run/Run All by hand and watching the
queue, stats, and report output — reports are named like real analytics
(Retention Overview, Monetisation Deep Dive, …), query the synthetic
Postgres for real, and render the results as HTML tables.

```powershell
python manage.py seed_synthetic_datasource --org sim-acme --gb 2 --ds-host db --ds-port 5432
python manage.py seed_stress_reports --org sim-acme --studio analytics --showcase
# open /s/sim-acme/analytics/ -> Operations -> Run All
```

Grant yourself access if the org is new: sim orgs have no members, so either
browse as a superuser or add your user to the org/studio from the admin
surfaces.

## Where to look while it runs

- `/s/sim-01/stress/` Operations: Duration and Memory columns (last run +
  p95/max), filter pills, live status updates.
- Expand any report row: aggregate tiles (runs, success rate, avg/p95
  duration, avg/max peak memory) plus the last-20-run history table with
  queue wait, duration, peak memory, and status badges.
- `/operator/`: fleet heartbeats with reserved/budget MB, queue depth, 24 h
  status histogram, heaviest builds by peak memory.

## Notes and limits

- The `oom` profile needs the container `mem_limit`; on an unconstrained
  host it simply succeeds with a very large peak. `memhog` errors everywhere
  (RLIMIT_AS MemoryError on POSIX; an explicit failure elsewhere).
- Peak memory is measured on Linux and Windows workers; it stays `None` on
  macOS and for runs built before measurement existed — the stats tolerate
  it, the UI shows an em-dash.
- The bind mount (`./.data-dev:/data`) assumes the container user can write
  it (true on Docker Desktop). On a strict Linux host, drop the mount from
  `docker-compose.sim.yml` and seed inside the container instead:
  `docker compose -f docker-compose.yml -f docker-compose.sim.yml run --rm `
  `-e DJANGO_SETTINGS_MODULE=trellum_portal.settings.dev worker python manage.py seed_stress_reports ...`
- Timings: `seed_synthetic_datasource --gb 2` takes a couple of minutes
  (server-side `generate_series` fill); re-runs resume where they left off.
