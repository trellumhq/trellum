# Sizing

Size the machine for **building** reports, not for reading them. Viewers are
served pre-built static output, so viewer count barely moves the numbers.

## Starting points

| Situation | Rough shape |
|---|---|
| Evaluation, a handful of reports | 2 CPU, 4 GB RAM |
| A team, tens of reports | 4 CPU, 8 GB RAM |
| Several teams, heavy dataframes | 8 CPU, 16 GB+ RAM |

The variable that matters is the largest amount of data a single report pulls
into memory at once, not how many reports exist.

## What consumes what

- **CPU** — concurrent builds; the default combined worker processes the queue
  alone, while additional runner processes can build in parallel
- **RAM** — the biggest single build. A report that loads a very large frame
  sets the floor for the whole instance
- **Disk** — built output, the database, and backups; report output is small
  relative to source data because it is aggregated. Output can be moved to
  [object storage](/docs/latest/install/configuration/#built-report-output-in-object-storage);
  checkouts and uploaded files stay on the volume either way

## Sizing report-build memory precisely

The starting points above get you running; once you have real traffic, size
memory from evidence instead. Every build gets the same limit,
`TRELLUM_DEFAULT_JOB_MEMORY_MB` (1024 by default), and the same wall-clock
limit, `TRELLUM_RUN_TIMEOUT` (600 seconds by default). `report.yaml` carries
no sandbox limits, so size both for the largest report on the instance. Give
the runner a budget sized to what's left once the rest of the stack has what
it needs:

```
TRELLUM_RUNNER_MEMORY_BUDGET_MB = total RAM
                              - RAM for the database
                              - RAM for the portal
                              - headroom
```

| VM size | Suggested `TRELLUM_RUNNER_MEMORY_BUDGET_MB` | Concurrent 1 GB builds |
| --- | --- | --- |
| 4 GB | 1536 | 1 |
| 8 GB | 5632 | 5 |
| 16 GB | 13824 | 13 |

Leaving the budget at `0` (the default) keeps the simpler behaviour: only a
concurrency limit (`WORKER_MAX_CONCURRENT`) caps how many builds run at once,
with no memory accounting. Set a budget before relying on memory isolation
between tenants.

Two details worth knowing:

- The hard cap enforced on a build is `TRELLUM_DEFAULT_JOB_MEMORY_MB x
  TRELLUM_JOB_MEMORY_HEADROOM` (default 1.5) and limits *address space*, which
  legitimately runs above resident memory for dataframe-heavy workloads. The
  limit is what the budget packs against; the cap is a runaway guard above it.
  Raise the headroom if healthy builds fail with an out-of-memory error; lower
  it to catch runaways sooner.
- This enforcement needs a POSIX host. On Windows the cap is simply absent
  and the timeout is the only guard against a runaway build.

A build that exceeds its cap ends as `error`, naming the limit and the
observed peak. Every run also records its peak memory, so after a week of
real traffic you can size the limit from evidence rather than guesses.

## When one machine is not enough

The default deployment runs scheduling, claiming work and building reports in
one worker process, which is a simple fit for a single VM. Two
independent levers exist before you need more than one host:

- **More parallelism on the same host** — raise `WORKER_MAX_CONCURRENT` (and
  set `TRELLUM_RUNNER_MEMORY_BUDGET_MB`, above) to run more builds at once.
- **Split roles across processes** — the same image runs as either a
  `coordinator` (schedules and reclaims runs from dead workers — exactly
  one, a second stands by inert) or a `runner` (claims runs and builds
  reports, as many as you like). No new infrastructure to run — the queue is
  already Postgres:

```bash
docker compose --profile split up -d --scale runner=3
docker compose stop worker      # the combined process is now redundant
```

`/system` shows every live worker with its role and memory usage. Dropping
back to `docker compose up -d` with no profile returns to the topology you
started with.

Reach for either lever when the build queue is persistently backed up, not
before. The Compose split profile scales processes on one Docker host; its
default named volume is local to that host.

Multi-host deployments are supported when every web and runner host connects
to the same PostgreSQL database and mounts the same persistent storage at the
same `TRELLUM_DATA_DIR` path (normally `/data`). That shared storage holds
studio checkouts and project files, uploaded data-source files, and live run
logs, as well as local report output and caches. Use shared storage such as
NFS, EFS, or an equivalent service, and configure it for every host. The
Compose named volume in the single-host example does not provide this sharing.

S3-compatible object storage is optional and stores built report output. It
does not replace the shared data directory: the portal and runners still need
the shared checkouts, uploads, and live run-log files. A multi-host deployment
therefore needs both shared PostgreSQL and shared persistent file storage;
S3-compatible output storage may be added separately. Configure the
coordinator and runner roles in your deployment environment; the Compose split
example above is for processes on one host.

## Uploaded data-source files

A data source can be a file the portal stores directly instead of a git
repository — the way to use a large extract without committing it. Two limits
bound this, since uploads are otherwise the only tenant-controlled consumer of
the data volume:

- **Per file** — *Max upload size (MB)* on the operator `/system` page, 512 MB
  by default. `0` removes the limit.
- **Per organization** — a *Max uploaded files (MB)* quota, set per
  organization in the operator console. `0` means unlimited.

An upload is also refused outright if it would leave under 1 GB free on the
data volume, regardless of either setting above — a full volume takes down
every tenant, not just whoever filled it. `manage.py doctor` reports free
space.

### Reverse proxy: request size

Whatever you set above, your reverse proxy sees the upload first, and most
proxies cap request bodies well below it:

- **nginx** defaults to `client_max_body_size 1m` — raise it
  (`client_max_body_size 512m;`) to match, and give `proxy_read_timeout` room
  too (`300s`), or every upload over 1 MB fails with a 413 before it reaches
  the portal.
- **Traefik** has no body limit by default.
- **Caddy** has no body limit by default; only `request_body max_size` adds
  one.

## Watching it

`/system` (operators) shows live workers and recent runs across all studios,
including memory figures per run. A report whose memory figure creeps upward
release over release is the usual early warning.

`GET /healthz` is a lightweight liveness check — the web process and the
database — for whatever uptime monitor or load balancer you point at the
instance.
