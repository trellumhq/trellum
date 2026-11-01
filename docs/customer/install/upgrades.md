# Upgrades

The online helpers switch the portal and report-runner images together. The
[verification record](upgrade-drill.md) lists the tested checks and the
infrastructure blocker that prevented the complete worker-build drill. They
require **Python 3.11+ on the Docker host**, Docker Compose v2, the bundled
Postgres service, and the default single-host combined-worker topology:

```bash
scripts/upgrade.sh v1.2.3
# Noninteractive, after reviewing release notes:
scripts/upgrade.sh v1.2.3 --yes
```

Published defaults are `ghcr.io/trellumhq/trellum:<version>` and
`ghcr.io/trellumhq/trellum-runner:<version>`. For a mirror, set
`TRELLUM_REGISTRY` and `TRELLUM_REPO` (the runner repository defaults to the
portal repository plus `-runner`). To select arbitrary repositories or digests,
set **both** `TRELLUM_TARGET_IMAGE` and `TRELLUM_TARGET_RUNNER_IMAGE`. Each image
must report the requested product version. The helper inspects and smoke-runs
both images before downtime, then pins their immutable local image IDs in
`.env`. This is a version/import check, **not signature verification**; verify
release signatures and attestations using [Verify what you received](/docs/latest/install/verify-images/).

`TRELLUM_COMPOSE` selects the Compose command, including project/file options.
The command must select this deployment's `.env` and services. Do not export
`TRELLUM_IMAGE` or `TRELLUM_RUNNER_IMAGE` in the calling shell: they can override
`.env`. A preflight configuration check rejects image overrides that prevent the
paired pins from taking effect. The release-only overlay selects images through
`TRELLUM_VERSION` and is **excluded**; migrate it to the base image-pin Compose
configuration in a separately tested operator procedure first.

External Postgres, offline upgrades, split workers, profiles, and multi-host
installations are **excluded**. These require a database/operator-specific
backup, drain, migration and restore procedure across all hosts. The helpers
reject external database URLs and active split services rather than backing up
an unused bundled database. Custom overlays must preserve the base service
roles, database and shared-data mounts; arbitrary overlay semantics are not
validated. An existing local image does not bypass the online target pull.

## Before you start

- **Know which version you are on.** `curl -s https://bi.example.com/api/version`,
  or the operator `/system` page.
- **Let `/system` tell you there is a newer one.** Set `TRELLUM_UPDATE_CHECK=true`
  and the page lists the latest releases and marks the one this instance runs.
  It is off by default; on, it is one outbound request a day.
- **Read the release notes** for every version between yours and the target.
  Anything needing a manual step says so under **Upgrade notes**.
- **Upgrade one version at a time** unless the notes say a jump is safe.
  Migrations are written to be applied in order.
- **Be healthy first.** `docker compose exec web python manage.py doctor` should
  pass. The script checks this too and stops otherwise — upgrading an instance
  that is already unwell turns one problem into two, and the upgrade gets the
  blame.
- **Upgrading from v0.7.0 or earlier: run cleanup once first.** Those releases
  never ran the scheduled retention cleanup, so `doctor` fails with
  `cleanup has never run`. Preview with
  `docker compose exec web python manage.py cleanup --dry-run`, then run it
  without `--dry-run`. That first run deletes everything past its
  [retention window](/docs/latest/operations/data-retention/) since
  installation.

## What the script does, and why in that order

| Step | Why |
|---|---|
| `doctor` preflight | Refuses to upgrade an unhealthy instance |
| Pull, inspect and import/version-check **both** images | Fail before downtime; select immutable IDs |
| **Stop** web, worker and backup | Drain writers before the database snapshot; stop failures abort the operation |
| `pg_dump`, then `pg_restore --list` | Save a readable custom-format dump plus an adjacent manifest containing both prior image IDs, original configured pins, version and dump hash |
| `migrate` | New schema, with no old code running against it |
| Start | The new version comes up on a schema it agrees with |
| Verify | `/healthz`, then assert `/api/version` reports the expected version and `schema: ok` |

The helper operates on the selected Compose project. It starts only the
combined-worker services, without rebuilding, pulling or implicitly rerunning
migrations. Verification checks the serving version/schema, actual web and
worker image IDs, worker runner configuration and `doctor`. It does not submit
a report build automatically. Build a representative report and inspect its
sandbox image before accepting the upgrade.

Expect a short outage: seconds, plus however long your migrations take, plus up
to ten minutes if a report build is mid-flight when the worker drains.

!!! warning "Why not just `docker compose up -d`"
    Compose starts the one-shot `migrate` service while the **old** `web` and
    `worker` containers are still serving. For the length of the migration the
    running code and the schema disagree, and nobody chose that. It also takes
    no backup, and reports success when the containers start — not when the new
    version is actually serving a schema it agrees with.

## Air-gapped sites

No offline installation or upgrade bundle is provided; see
[Verify what you received](/docs/latest/install/verify-images/#air-gapped-installs).
An offline upgrade must transfer and verify both images and the operational
files, and test the full upgrade and rollback procedure without network
access. The upgrade helper still calls `docker pull`, so an already-loaded
portal image alone does not make that script an offline upgrade path.

## How you know it worked

The script tells you, but to check by hand at any time:

```bash
curl -s http://127.0.0.1:8050/api/version
```

```json
{"version": "1.2.3", "sha": "a1b2c3d…", "build_time": "…",
 "branch": "", "source_url": "https://github.com/trellumhq/trellum/tree/v1.2.3",
 "framework": {"tag": "v1.2.3", "meta_schema": 1}, "schema": "ok"}
```

The values to check are:

- **`version`** is the release actually serving. If it still shows the old one,
  the old container is still up.
- **`framework.tag`** mirrors the same product version for compatibility with
  existing clients. The framework and platform are released together.
- **`framework.meta_schema`** is the generated-report compatibility number. It
  can remain unchanged across product releases.
- **`source_url`** identifies the exact source for the running release.
- **`schema`** is whether the database matches that code:
    - `ok` — in step.
    - `pending` — **the code is newer than the database.** The migration did not
      run or did not finish. The instance may work until something touches a
      column that is not there. Run `docker compose run --rm migrate`.
    - `error` — the database could not be read at all.

`manage.py doctor` and `/system` report the same thing, and name the missing
migrations.

!!! note
    Report output is derived data. After an upgrade, existing reports keep
    serving their last build; they refresh on their normal schedule or when you
    trigger a run.

## Rolling back

!!! danger "Migrations are forward-only"
    Putting the old image back on a database whose schema has moved gives you
    old code reading new columns. The database has to go back too, and the only
    way back is the dump.

```bash
scripts/rollback.sh ./upgrade-backups/pre-<stamp>-<pid>.dump
```

**This discards every database change made since that dump was taken** — report
runs, audit rows, settings, invitations. That is not caution, it is arithmetic:
the dump is a snapshot, and going back to it means going back to it.

So:

- **Prefer rolling forward.** If the new version is merely misbehaving, fix the
  fault and upgrade again. Reach for rollback when the new version cannot serve.
- **Keep the dump and its `.images.json` manifest** until you are satisfied.
  The script leaves them in `upgrade-backups/` (override with `TRELLUM_BACKUP_DIR`)
  and prints the recovery command. Keep the recorded local images too; do not
  prune them during the recovery window. Rollback uses their immutable IDs
  and requires both to remain available locally. A historical dump without the
  paired manifest requires a manual restore procedure; the old image/version
  positional argument is no longer accepted.
- Report output and uploaded data-source files are untouched by either script.

Rollback checks the dump hash/readability and both prior image versions before
stopping writers. It first saves a verified `before-rollback` database dump,
then restores into a fresh scratch database with
`--exit-on-error --single-transaction`. Only after that succeeds does a separate
transaction rename the current database to a retained recovery name and the
scratch database to the configured name. This requires bundled-Postgres
superuser privileges and space for both databases plus their dumps. New tables
from the abandoned release cannot survive in the restored database. The
retained database name is printed and recorded in the recovery marker during
the operation, and in a sidecar beside the safety dump; remove it only after
accepting the rollback. A restore or
database-switch error is fatal. It restores both image pins, checks serving
version/schema and worker configuration, and retains all backups. It never restores or deletes uploaded
files, encryption keys, report output, or other shared-volume data; protect
those separately with [Backups & restore](/docs/latest/operations/backups-and-restore/).

## If an upgrade fails midway

Before stopping services, pull, image-version, configuration and preflight
failures leave the deployment running and its pins unchanged. After the stop
begins, failures leave application services stopped and retain
`.trellum-deployment-incomplete.json`. A dump that failed verification is not a
usable recovery backup. If migration has begun, use the printed dump and paired
manifest to roll back; never just restart the old code on a possibly new schema.

A failed migration is not retried; its named one-shot container is terminated
on error/timeout. If termination cannot be confirmed, investigate that container
before restoring. A failed restore or timeout may have an unknown outcome:
inspect Postgres and the retained recovery marker before considering a manual
retry. The helper refuses a second rollback after an unconfirmed rollback.
It never automatically restores, retries a write, or restarts after failure.

The directory lock `.trellum-deployment-lock` prevents concurrent helper runs.
After a killed host process, check that no helper or migration/restore is still
running before manually clearing a stale lock. Clear an incomplete marker only
after reconciling database, both pins and service state. Do not delete these
files simply to get past a failed check. The default per-command timeout is
900 seconds (`TRELLUM_COMMAND_TIMEOUT`); startup verification has a 180-second
polling budget (`TRELLUM_HEALTH_TIMEOUT`) with individually bounded commands.
A slow command can extend that polling budget by one command timeout.
Bundled Postgres commands also use its container-side `timeout` utility so
terminating a Docker client cannot leave a database write running indefinitely.

See the [isolated verification record](upgrade-drill.md) for the tested path and
its limits.

## Versioning

Trellum follows semver. The Python framework and self-hosted platform have one
product version and are released together.

- **MAJOR** — a breaking configuration change, an upgrade that needs a manual
  step, or a migration that isn't rollback-safe. Always called out in the
  release notes.
- **MINOR** — new backwards-compatible features or report-building behaviour.
- **PATCH** — fixes only; no schema or configuration change.

`/api/version` keeps a `framework` object for compatibility, but its `tag`
matches the product `version`. Its separate `meta_schema` number decides
whether report output built by an older release can still be read.

## If you're upgrading from before report sandboxing

Report builds now run in disposable per-build containers instead of inside
the worker process. Crossing that change needs two things on the host, the
first time only:

- **Docker Engine 26+** for named-volume storage, or **25+** with
  [host-folder storage](/docs/latest/install/docker-compose/#docker-25-with-host-folder-storage).
  Before changing the stack, run `bash scripts/check-docker.sh` (add
  `--bind-data` only for the host-folder setup). The worker also checks its
  actual storage type and API version at startup.
- **`DOCKER_GID`** — add it to `.env` (see
  [Configuration](/docs/latest/install/configuration/)); without it the
  worker cannot reach the socket it needs to start build containers.

Then run the [firewall rule](/docs/latest/install/report-sandboxing/) once,
if you haven't already.

If your data volume predates this change, it may still be owned by root from
an older image that ran as root — the current, unprivileged process can't
write to it until you fix ownership once, with the stack stopped:

```bash
docker compose down
docker compose run --rm --user root web chown -R 10001:10001 /data
docker compose up -d
```

`manage.py doctor` names this exact command if it detects the problem.
