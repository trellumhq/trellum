# Scale builds on one server

Use this guide when the build queue stays backed up while the server still has
capacity. These options add concurrency on one Docker host; they do not spread
work across machines.

## Keep the combined worker

The simplest path is to keep the combined worker and raise
`WORKER_MAX_CONCURRENT` while the host has CPU and memory headroom. Set
`TRELLUM_RUNNER_MEMORY_BUDGET_MB` when builds need admission control; the
[Sizing](/docs/latest/operations/sizing/#sizing-report-build-memory-precisely)
guide shows the per-process budget and required host headroom.

## Start with memory

Set `WORKER_MAX_CONCURRENT` for simultaneous builds and budget memory first.
Each build can use `TRELLUM_DEFAULT_JOB_MEMORY_MB` (1024 MB by default), with
sandbox headroom applied. A memory budget controls admission, not a physical
reservation:

```
runner budget = total RAM - database RAM - portal RAM - host headroom
```

Divide the available budget between runner processes sharing this host. Use
`/system` observations and [Sizing](/docs/latest/operations/sizing/) to tune it.

## Split coordinator and runner processes

Start independent runner processes on the same host and stop the combined
worker:

```bash
docker compose --profile split up -d --scale runner=3
docker compose stop worker
```

The `--scale runner=3` replicas are all on this one Docker host. They share the
same PostgreSQL queue and persistent data volume. Keep exactly one active
coordinator; another stands by if it cannot acquire the lock.

Return to the combined worker topology:

```bash
docker compose stop coordinator runner
docker compose up -d worker
```

Use [Deploy on one server](/docs/latest/install/docker-compose/) for the full
install, storage, backups, and sandbox policy. When one host is no longer
enough, continue to [Run across multiple servers](/docs/latest/install/multiple-servers/).
