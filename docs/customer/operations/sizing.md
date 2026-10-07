# Sizing

Report builds usually drive CPU and memory needs. Most viewers read pre-built
static output; allow for serving that traffic too. Reports with opt-in live
queries also need web and data-source capacity for interactive requests.

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
- **Disk** — built output, the database, and backups. Output size depends on
  the data included: reports can contain row-level datasets as well as
  aggregates. Output can be moved to
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
with no memory accounting. A budget controls admission to that runner, not
physical memory reservation. Allow headroom for builds reaching their caps,
and divide the available memory between runner processes sharing a host.

Two details worth knowing:

- Docker sandboxes enforce a container memory cap of
  `TRELLUM_DEFAULT_JOB_MEMORY_MB x TRELLUM_JOB_MEMORY_HEADROOM` (headroom defaults
  to 1.5, with a minimum container cap of 256 MB) and disable swap. Admission
  uses the base amount, so simultaneous builds can consume more than the
  configured budget.
- Unsandboxed builds on POSIX instead use an *address-space* limit when
  `TRELLUM_JOB_MEMORY_ENFORCE=true`. Address space can be larger than resident
  memory for dataframe workloads. Unsandboxed Windows builds have no such
  memory cap; their wall-clock timeout still applies.

A Docker memory kill is recorded as `oom_killed`; a Python `MemoryError` is
recorded as `error`. Runs record sampled peak memory when it is available.
Use those measurements and failures to size limits from real workloads.

## When one machine is not enough

Choose the topology in [Scale builds on one server](/docs/latest/install/scale-builds/)
when the queue needs more capacity on one host, or in [Run across multiple
servers](/docs/latest/install/multiple-servers/) when runner capacity spans
hosts. This page keeps the sizing tables and runner-pool details used by both
guides.

### Assign studios to runner pools

Runner pools route builds to selected runners, for example a larger machine
for a studio with heavy reports. An instance operator opens the organization's
detail page under `/operator/orgs/` and changes the studio's **Pool** in the
**Studios** table. The choices are `small`, `standard` (the default), and
`large`. The assignment applies to the whole studio; already queued runs keep
the pool recorded when they were queued.

Set `TRELLUM_RUNNER_POOLS` separately for each runner service: `large` accepts
only large-pool jobs, while `small,standard` accepts those two pools. Leaving
it empty accepts all pools. For dedicated routing, configure every runner's
pool list; an unrestricted runner can otherwise claim any job. Keep at least
one runner serving each pool you use, or its jobs stay queued.

Pool names do not set memory or CPU limits. Configure the runner's capacity
with the build settings above; shared storage is still required across pools.

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
