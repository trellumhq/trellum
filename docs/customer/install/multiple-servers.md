# Run across multiple servers

Use this topology when runner capacity must live on more than one machine.
Operators choose the host layout that fits their network and storage services.

## Intended workload

Keep the portal on one web host while builds run on two or more runner hosts.
PostgreSQL and the persistent data directory are shared services.

## Service and host layout

| Location | Process | Purpose |
|---|---|---|
| Control host | web and one active coordinator | Portal requests, report reads, scheduling, and maintenance |
| Runner host A | runner | Claims and builds reports |
| Runner host B | runner | Claims and builds reports |
| Shared services | PostgreSQL and persistent filesystem | Queue, state, checkouts, uploads, logs, and files |

Every role uses the same `TRELLUM_DATA_DIR` path (normally `/data`). A Docker
named volume is local to its host and does not provide this sharing.

## Shared dependencies

Provide one reachable PostgreSQL database and one filesystem mounted at the same
path on web, coordinator, and runner hosts. Use matching image versions, the
same encryption and report-storage configuration, and consistent web signing
keys. Optional object storage holds built report output; it does not replace the
shared filesystem. See [Storage](/docs/latest/install/storage/).

## Provisioning checklist

1. Provision the control host and runner hosts with the same release image and compatible Docker/Compose tooling.
2. Mount the shared filesystem at the same `TRELLUM_DATA_DIR` path everywhere.
3. Configure one PostgreSQL connection and identical encryption, storage, and session-signing settings.
4. Give each runner host its own Docker daemon and sandbox image.
5. Apply the [sandbox host setup](/docs/latest/install/report-sandboxing/) on each runner host.
6. Run migrations and readiness checks from the web service before accepting traffic.

## Role configuration

After provisioning, the process contracts are:

```bash
python manage.py runworker --role=coordinator
python manage.py runworker --role=runner
```

The entrypoint's `coordinator` and `runner` commands map to these roles. The
`all` role combines them; an additional `all` process that cannot obtain the
coordinator lock demotes itself to runner. Do not treat several combined
workers as independent schedulers.

## Verify each host

- Confirm every role can reach the same PostgreSQL database and `/data` filesystem.
- Confirm a runner can start its local sandbox and write its build directories.
- Queue a report, observe it claim once, and verify logs and output from the web host.
- Check `/system` for one active coordinator and the expected runners.

## Operations boundary

Cross-host provisioning, network access, and shared-filesystem operations are
managed by the operator. The named Compose volume remains suitable for one host
only; use NFS, EFS, or an equivalent shared filesystem across hosts. Additional
coordinators stand by until the coordinator lock is available.

## Standalone Compose runner host

`docker-compose.runner.yml` is a standalone preset, used **instead of** the base
Compose file on each runner host. It runs only runners and a one-shot sandbox
image setup service: no web server, database, migrations or coordinator. The
normal single-host `docker compose up -d` is unchanged.

On the control host, first configure the existing
[host-folder storage override](/docs/latest/install/docker-compose/#docker-25-with-host-folder-storage)
to use your shared filesystem, and the
[external PostgreSQL configuration](/docs/latest/install/configuration/).
Use the release overlay with the same `TRELLUM_VERSION` everywhere. Run migrations
there before starting remote runners; keep one active coordinator there. A
combined worker can coexist with remote runners, but its pool list must also
be restricted if you require dedicated routing.

On each runner host, use deployment files from that same release. Mount the
**same shared filesystem**, at the same absolute host path (for example
`/srv/trellum/data`) everywhere, with read/write access for UID/GID 10001.
Inside every application container the path is `/data`; leave
`TRELLUM_DATA_VOLUME` unset. An existing empty local directory passes the bind
check but is **not** shared storage: verify a test file written from one host
is visible from all other hosts before starting any runner. Do not create a new
empty directory over missing shared data. The preset refuses to create missing
host directories, but cannot detect an unmounted shared filesystem.

Create a private `.env` next to the preset (`chmod 600 .env`). Configure:

```dotenv
COMPOSE_FILE=docker-compose.runner.yml
# Replace with the control host's exact published tag, not latest:
TRELLUM_VERSION=vX.Y.Z
DATABASE_URL=postgres://USER:PASSWORD@SHARED_DB:5432/trellum_portal
SECRET_KEY=CONTROL_HOST_SIGNING_KEY
SECRET_ENCRYPTION_KEY=CONTROL_HOST_ENCRYPTION_KEY
TRELLUM_DATA_HOST_PATH=/srv/trellum/data
DOCKER_GID=LOCAL_SOCKET_GROUP_ID
# Empty accepts every pool; restrict every runner for dedicated routing:
TRELLUM_RUNNER_POOLS=standard,small
```

Replace all placeholders. Copy the control host's report-storage settings as
well, including any object-storage credentials and endpoint configuration.
Keep per-runner capacity settings such as `TRELLUM_RUNNER_MEMORY_BUDGET_MB`
appropriate to the machine; see [sizing and pools](/docs/latest/operations/sizing/).
The `runner` command enforces the runner role even if `.env` contains an old
`TRELLUM_RUNNER_ROLE`. The preset enforces Docker sandboxing and the matching
release sandbox image, ignoring stale image overrides in `.env`.

Use a local Linux Docker daemon with its socket at `/var/run/docker.sock`;
get its group ID with `stat -c %g /var/run/docker.sock`. Do not point the Docker
client or the runner's `DOCKER_HOST`/`DOCKER_CONTEXT` at another machine. Each
runner needs its own local sandbox image and daemon. Follow the
[sandbox host setup](/docs/latest/install/report-sandboxing/), including network
hardening on **each** runner host, before admitting builds.

```bash
bash scripts/check-docker.sh --bind-data
# Validate without printing interpolated secrets:
docker compose -f docker-compose.runner.yml config --quiet
docker compose -f docker-compose.runner.yml pull
docker compose -f docker-compose.runner.yml up -d --no-build --scale runner=2
docker compose -f docker-compose.runner.yml logs --tail=100 runner
# Drain only this runner host when removing capacity:
docker compose -f docker-compose.runner.yml stop runner
```

Explicit `-f` selects this preset even if an inherited `COMPOSE_FILE` points at
the control stack. There are no published ports or host-local data volumes.
The application discovers the bind source from its own container and passes
only authorized subdirectories to local sandbox containers. Do not set a fixed
container hostname or container name when scaling runners.

### Coordinated upgrades

Drain and stop runners on **all** hosts before upgrading the control plane.
Upgrade helpers operate on the local combined-worker stack; they do not
coordinate remote runners and must not be run against this runner-only preset.
A shared/external database, split roles and release overlays require an operator
upgrade/restore procedure; the local helpers do not cover this topology.
After the control host's migration and verification succeed, set the same exact
`TRELLUM_VERSION` on every runner host, pull both images and start runners again.
Verify each host's image IDs and sandbox image version. Coordinate rollback
across all hosts too; follow the [upgrade guide](/docs/latest/install/upgrades/)
and never run an old runner against a newly migrated database by accident.

## Separate-machine acceptance drill

Use an isolated installation with disposable PostgreSQL and shared storage,
one control machine and **two actual runner machines**. Use a finite batch of
six small offline fixture reports (no external providers), with one run per
report, and record the machines' identities, release/image IDs, pool settings,
run IDs and worker heartbeat IDs. Allow five minutes per step; on timeout record
a failure and stop the drill rather than retrying indefinitely.

1. Verify database identity and bidirectional shared-file visibility from all
   three machines. Verify UID/GID access, local Docker API compatibility and
   sandbox image availability on both runners.
2. Set runner A to `standard` and runner B to `large`, with no unrestricted
   combined worker. Assign three fixture reports to studios in each pool, queue
   each once, and record the resulting run IDs. Confirm each run reaches success
   once, on its matching runner. Check runner/sandbox logs for a single claim
   and build per run; confirm output and logs are readable from the web host.
3. Queue a controlled long-running offline fixture on runner A. After recording
   its worker/run/container IDs and running state, kill **only that disposable
   runner container**. Confirm the coordinator fences the stale run as
   unconfirmed (`termination_confirmed=False`, diagnostic cause
   `worker_outcome_unconfirmed`) after the heartbeat deadline. The run
   stays active: missing heartbeats do not prove a remote sandbox stopped.
   Confirm no duplicate run or automatic replay appears. Preserve the recorded
   sandbox on A so the owning runner's restart can establish termination.
4. Restart A's runner against its original local daemon. Verify that it inspects
   and stops the exact recorded orphan, sets `termination_confirmed=True` and
   finalizes the original run. Retain container events, runner logs and the
   run's unknown-write-outcome diagnostics. A remote daemon's missing container
   is not termination evidence. If the fence remains or the step times out,
   record the failure and stop this part of the drill. Manual cleanup is not
   evidence of successful restart recovery; do not clear the fence with ad hoc
   SQL. After confirmed termination and finalization, explicitly queue a new
   run of that fixture and confirm success and visible output. Verify B
   continues serving only its own pool and the coordinator remains active.
   Record final run states and recovery lineage; do not replay the old run.
5. Drain the fixture runners and remove only the drill's recorded resources;
   retain evidence and remove the shared-file probe. Do not prune Docker or
   remove any production volumes.

The existing split-topology CI exercises several processes on one machine.
Pool, claim and recovery tests establish application contracts on that host;
neither process isolation nor multiple containers with different hostnames
establishes separate-machine acceptance. A completed drill requires evidence
from the actual machines, their shared filesystem and their local sandboxes.
