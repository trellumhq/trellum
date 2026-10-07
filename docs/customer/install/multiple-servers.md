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
