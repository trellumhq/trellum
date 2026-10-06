# Upgrades

!!! warning "Current upgrade helper limits"
    The upgrade and rollback helpers update `TRELLUM_IMAGE` only. They do not
    update or verify `TRELLUM_RUNNER_IMAGE`, so using them alone can leave
    report builds on a different release from the portal. Their default image
    repository is also outdated; set `TRELLUM_REPO=trellumhq/trellum` when
    using the published images. Adapt and test the procedure on a separate
    instance, including both image pins and their rollback, before upgrading
    an installation with real data. See the matching-image settings in the
    [Compose guide](/docs/latest/install/docker-compose/).

The existing portal-image helper is invoked with:

```bash
TRELLUM_REPO=trellumhq/trellum scripts/upgrade.sh v1.2.3
```

That takes a verified backup, stops the application, migrates, starts the new
version, and checks the portal's reported version and schema. It prints a
rollback command on failure; that command also needs the matching runner
configuration described above.

## Before you start

- **Know which version you are on.** `curl -s https://bi.example.com/api/version`,
  or the operator `/system` page.
- **Let `/system` tell you there is a newer one.** Set `TRELLUM_UPDATE_CHECK=true`
  and the page lists the latest releases and marks the one this instance runs.
  It is off by default; on, it is one outbound request a day.
- **Read the release notes** for every version between yours and the target.
  Anything needing a manual step is a major release and says so.
- **Upgrade one version at a time** unless the notes say a jump is safe.
  Migrations are written to be applied in order.
- **Be healthy first.** `docker compose exec web python manage.py doctor` should
  pass. The script checks this too and stops otherwise — upgrading an instance
  that is already unwell turns one problem into two, and the upgrade gets the
  blame.

## What the script does, and why in that order

| Step | Why |
|---|---|
| `doctor` preflight | Refuses to upgrade an unhealthy instance |
| `pg_dump`, then verify it with `pg_restore --list` | An unverified dump is not a backup. `pg_dump` can exit successfully having written a truncated file if the disk filled |
| `docker pull` | Fetch before stopping, so a slow or failed pull does not extend the outage |
| **Stop** web and worker roles | The important one — see below. The worker drains first, so no in-flight build is killed |
| `migrate` | New schema, with no old code running against it |
| Start | The new version comes up on a schema it agrees with |
| Verify | `/healthz`, then assert `/api/version` reports the expected version and `schema: ok` |

The helper operates on the current Compose project. For a multi-host install,
coordinate the upgrade across every web and runner host; the helper does not
orchestrate remote hosts or update `TRELLUM_RUNNER_IMAGE`.

Expect a short outage: seconds, plus however long your migrations take, plus up
to ten minutes if a report build is mid-flight when the worker drains.

!!! warning "Why not just `docker compose up -d`"
    Compose starts the one-shot `migrate` service while the **old** `web` and
    `worker` containers are still serving. For the length of the migration the
    running code and the schema disagree, and nobody chose that. It also takes
    no backup, and reports success when the containers start — not when the new
    version is actually serving a schema it agrees with.

## Air-gapped sites

The current bundle helper is incomplete; see
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
TRELLUM_REPO=trellumhq/trellum scripts/rollback.sh ./upgrade-backups/pre-v1.2.3-<stamp>.dump v1.2.2
```

**This discards every database change made since that dump was taken** — report
runs, audit rows, settings, invitations. That is not caution, it is arithmetic:
the dump is a snapshot, and going back to it means going back to it.

So:

- **Prefer rolling forward.** If the new version is merely misbehaving, fix the
  fault and upgrade again. Reach for rollback when the new version cannot serve.
- **Keep the dump** until you are satisfied. The script leaves it in
  `upgrade-backups/` and prints the path.
- Report output and uploaded data-source files are untouched by either script.

## If an upgrade fails midway

The script prints the rollback command at every failure point. By stage:

- **Preflight or backup failed** — nothing has changed. Fix and retry.
- **Pull failed** — nothing has changed. Check the registry and retry.
- **Migration failed** — the schema may be partly moved. Roll back using the
  dump the script just took.
- **Verification failed** — the containers are up but wrong: the old image is
  still serving, or `schema` is not `ok`. Check `docker compose logs web`, then
  either fix forward or roll back.

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

- **Docker Engine 26+** — the worker checks this at boot and refuses to start
  on an older engine; `manage.py doctor` and the boot log both name the
  requirement if you forget.
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
