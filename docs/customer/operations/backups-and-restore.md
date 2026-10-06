# Backups & restore

## What is worth backing up

| Where | What | Back it up? |
|---|---|---|
| The database | organizations, studios, users, permissions, encrypted secrets (SSO, repository tokens, data-source credentials), run history, favorites, audit log | **Yes** |
| Data volume — uploaded files | data-source files uploaded through the portal, at studio or organization level | **Yes** — no other copy exists |
| Data volume — audit archives | `/data/archive/audit/`, when `RETENTION_AUDIT_ARCHIVE=true` | **Back up separately** — the built-in backup job does not include this directory |
| Data volume — built output | materialized project roots, rendered report output | No — rebuilds from your repository |
| Object storage — built output | the same, when `TRELLUM_STORAGE_BACKEND=s3` | No — same reason, and your bucket has its own durability |
| Data volume — git checkouts | working copies of each studio's report repository | No — re-cloned on demand |
| Containers | nothing | Disposable |

## They run by default

There is nothing to enable. The `backup` service starts with the stack, because
the bundled database has no replication and no point-in-time recovery, so these
dumps are the entire recovery story — and a backup you have to remember to turn
on is one that is off on the instances that needed it.

Nightly, into a **separate `backups` volume** — not the data volume it protects,
which would not survive the failure it exists for:

| File | What |
|---|---|
| `db-<stamp>.dump` | `pg_dump --format=custom`, **verified** with `pg_restore --list` before it counts |
| `studios-<stamp>.tar.gz` | Studio state, excluding git checkouts and query caches, both reproducible |
| `orgs-<stamp>.tar.gz` | Organization-level uploaded data-source files |
| `status.json` | What the last run did — read by the `backups` health check |

A dump that fails verification is deleted rather than kept, so a bad one can
never displace a good one during retention. Retention (`BACKUP_KEEP_DAYS`,
default 14) only runs after a verified success, so a run of failures cannot age
out your last good backup.

If you moved the database to one you operate yourself, backups follow it — the
service uses `DATABASE_URL` rather than assuming the bundled container.

## Getting them off the host

```bash
BACKUP_REMOTE_TARGET=backups@nas.internal:/trellum
```

Set it in `.env`. Backups that live only on the host do not survive losing the
host, which is the failure they exist for. Unset, the service says so on every
run and the health check reports `kept on this host only`.

!!! danger
    Store `SECRET_ENCRYPTION_KEY` somewhere separate from the backups. A backup
    plus the key in one place is a single point of compromise — and without the
    key, a restored database cannot decrypt a single stored credential.

## Knowing they still work

```bash
docker compose exec web python manage.py doctor
```

The `backups` check fails when the newest verified backup is older than
`BACKUP_MAX_AGE_HOURS` (default 36). That is the alert that catches a backup
service which died six weeks ago — otherwise discovered by needing it.

It also fails if the bundled database is in use and **no** verified backup
exists at all. The bundled database is a supported way to run; running it with
nothing to restore from is not.

### Prove a backup actually restores

```bash
docker compose run --rm --entrypoint /bin/sh backup /restore_drill.sh
```

`pg_restore --list` proves a file is a well-formed archive. Only a restore
proves it contains your portal. The drill restores the newest dump into a
scratch database, counts the essential tables, drops it, and records the result
— which `doctor` then reports as `restore-tested N days ago`. If a drill fails,
the `backups` check goes red: backups that restore empty look like protection
right up until the day they are needed.

Run it after setting backups up, and on a schedule if you want to keep knowing.
An untested backup is a hope.

## Restore

These commands restore the bundled Postgres service and assume its default
database user and name. If `DATABASE_URL` points to an external database,
restore the dump to that configured database using your database operator's
procedure; the Compose `db` container is not running in that setup. Before
restoring, stop web, the combined `worker` or split coordinator and runners,
and the backup service on all hosts so nothing is using the database or shared
data directory. These commands show the single-host Compose case; in a
multi-host installation, use your orchestrator to apply the stop and selected
topology start across all hosts.

```bash
docker compose stop web worker coordinator runner backup
docker compose exec -T db pg_restore -U trellum -d trellum_portal --clean --if-exists \
    < /path/to/db-<stamp>.dump
docker compose run --rm --no-deps web sh -c "cd /data && tar -xzf /backups/studios-<stamp>.tar.gz"
docker compose run --rm --no-deps web sh -c "cd /data && tar -xzf /backups/orgs-<stamp>.tar.gz"
```

For the default combined-worker topology, stop any split services left running
and start the base stack. For a split topology, enable the profile and start
web, coordinator, and the configured number of runners (three in this
example):

```bash
# Default combined worker
docker compose stop coordinator runner
docker compose up -d

# Or split coordinator and runners; adjust runner count to match your install
docker compose --profile split up -d --scale runner=3 web coordinator runner backup
```

Then run the health check:

```bash
docker compose exec web python manage.py doctor
```

Afterwards, rebuild report output from each studio dashboard (Run All) or simply
wait for the schedules to come round.

!!! note
    To undo a bad **upgrade** rather than recover from a disaster, use
    `scripts/rollback.sh`, which does the above and re-pins the previous image.
    See [Upgrades](/docs/latest/install/upgrades/).
