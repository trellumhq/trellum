# Writing migrations

For people changing this repository. Customers should read the published
[upgrade guide](https://trellum.dev/docs/latest/install/upgrades/) instead.

Every schema change ships to installations we do not operate, cannot inspect,
and cannot roll back for. Two constraints follow, and everything below is a
consequence of them:

1. **Migrations are forward-only.** There is no supported downgrade path. A
   customer who needs to go back restores a dump and loses everything written
   since. So a migration that cannot be reverted is a decision about *their*
   data, not ours.
2. **A migration runs against a table you have never seen.** Our `Run` and
   `AuditLog` tables have a few thousand rows. Theirs may have millions. A
   statement that is instant here can hold a lock for minutes there, and a lock
   on `Run` stops every report build on the instance.

## Expand / contract

Never change a column's meaning in one release. Split it across two:

| Release | Phase | What lands |
|---|---|---|
| N | **expand** | Add the new column/table, nullable. Write to both old and new. Backfill in batches. Read from old. |
| N | *(deploy, verify)* | The old code still runs against this schema — that is the property that makes it safe |
| N+1 | **contract** | Read from new. Drop the old column. |

The reason is the upgrade sequence in `scripts/upgrade.sh`: the migration runs
while the application is stopped, but the *previous release's code* has been
running against the pre-migration schema, and if the upgrade is abandoned it
will run again. An expand-phase migration is safe to abandon. A contract-phase
one is not — and that makes the release **MAJOR**, called out in the changelog.

## Rules

**Adding a column.** Nullable, no default. Postgres 11+ makes a non-null default
metadata-only, but `null=True` avoids the question entirely and is what almost
every case wants.

**Adding an index to a large table** — `Run`, `AuditLog`, `django_session`, or
anything else that only grows:

```python
from django.contrib.postgres.operations import AddIndexConcurrently

class Migration(migrations.Migration):
    atomic = False          # CONCURRENTLY cannot run inside a transaction
    operations = [
        AddIndexConcurrently("run", models.Index(fields=["..."], name="...")),
    ]
```

A plain `AddIndex` takes a lock that blocks writes for the whole build. On a
table the size of a busy customer's `Run` that is an outage.

Note what this means for foreign keys: Django indexes them by default. If you do
not need to query by the new FK, say `db_index=False` and the migration becomes a
metadata-only `ADD COLUMN`. `AuditLog.impersonator` is the worked example.

**Adding a constraint.** `AddConstraint` with a `CheckConstraint` validates the
whole table under an `ACCESS EXCLUSIVE` lock. On a big table, add it `NOT VALID`
via `RunSQL` and `VALIDATE` in a later migration.

**Data migrations.** There are a few already; the latest is the
`last_viewed_at` backfill in `reports.0026_report_idle_refresh`, which runs in
the same migration as its schema changes and in one pass. Don't copy that shape
for a table that can be large. When you write one:

- Batch it. A single `UPDATE` over millions of rows holds locks and bloats WAL.
- Make it idempotent and resumable; it will be interrupted somewhere.
- Never import models directly — use `apps.get_model()` so the migration sees
  the schema as it was, not as it is now.
- Put it in its own migration, separate from schema changes.

**Renaming.** Don't, in one step. Add the new name, backfill, switch readers,
drop the old one next release. `RenameField` breaks the old code instantly.

**Deleting.** Contract phase only, and only after a release where nothing read it.

## Rebasing unreleased migrations

Migrations added since the last release tag have been applied by nobody outside
this repository's own databases, so they may be rewritten before the next
release. Collapse them so that customers upgrade from the last release through
one migration per app, instead of replaying every intermediate step of
development (a model added, reshaped and dropped again; a merge migration; a
data migration for rows that no release ever wrote).

1. Settle the models first: remove fields and tables nothing uses, and move
   one-off data work out of migrations (a coordinator job recorded in
   `OpsState` is one example). Generated migrations then already have their
   final shape.
2. Delete only the migration files that are absent from the previous tag. A
   file that exists in the tag has been applied by customers and must never be
   edited, renamed or removed.
3. Regenerate one migration per affected app with
   `python manage.py makemigrations <app> -n <name>`. Each one must depend
   linearly on the previous release's last migration for that app: no merge
   migration, no `RunPython`, no create-then-drop.
4. Check the result: `git diff --stat <last-tag> -- '*/migrations/*'` lists
   only added files; `makemigrations --check --dry-run` is clean; `sqlmigrate`
   reads as new tables, indexes and nullable columns; and the real upgrade
   works. In a worktree of the last tag run `manage.py migrate` against an
   empty database, then run `manage.py migrate` from the branch against the
   same database.

A database that already applied the old, unreleased migrations (a development
or preview instance) now disagrees with the migration files: `django_migrations`
names migrations that no longer exist, and some of the new files reuse a name
whose old row says "applied". With the application stopped, either restore the
dump taken before it was upgraded, or put the schema back by hand and let
`migrate` build it again:

1. Take a dump.
2. Drop every table and column that the removed migrations created.
3. Delete their rows from `django_migrations`, including the rows for files
   that were regenerated under the same name.
4. Run `python manage.py migrate`.

Rebasing the 0.8.0 development migrations looked like this. The annotation
tables exist only on a database that stopped before the migration that dropped
them; `IF EXISTS` covers both.

```sql
BEGIN;
DROP TABLE IF EXISTS
    reports_reportexperimentsummary, reports_reportmetricclaim, reports_reportmetricpreview,
    reports_reportsourcesummary, reports_metriccatalogstate,
    reports_annotationevent, reports_annotationfiltermembership, reports_annotationfilterreceipt,
    reports_annotationfilterstate, reports_annotationsourcestate, reports_annotationcatalogstate,
    runner_studiomaintenanceaction, datasources_unblockrequest CASCADE;
ALTER TABLE reports_metricdefinition DROP COLUMN IF EXISTS binding;
ALTER TABLE orgs_orgssoconfig
    DROP COLUMN IF EXISTS last_test_at, DROP COLUMN IF EXISTS last_test_by_id,
    DROP COLUMN IF EXISTS last_test_ok, DROP COLUMN IF EXISTS last_test_summary;
DELETE FROM django_migrations WHERE
    (app = 'reports' AND name IN (
        '0026_source_summaries', '0027_metric_binding_workspace_layout',
        '0028_report_runtime_experiment_projection', '0029_annotation_projection',
        '0030_annotation_filter_state', '0031_source_summary_idle_checks',
        '0032_merge_idle_refresh_and_source_summaries', '0033_source_summary_change_stamp',
        '0034_drop_unused_annotation_tables', '0035_source_summary_legacy_rebuild_queued_for'))
    OR (app = 'datasources' AND name IN ('0007_unblockrequest', '0008_unblockrequest_cursor_report'))
    OR (app = 'orgs' AND name = '0025_orgssoconfig_test_signin')
    OR (app = 'runner' AND name = '0007_studiomaintenanceaction');
COMMIT;
```

What this removes is only what those migrations added: worker-built source
summaries (the workers build them again), maintenance and unblock requests
(short-lived), and the last SSO test sign-in result.

Do not use `migrate reports 0026_report_idle_refresh` to get back. Django can
only reverse migrations it finds on disk, so it skips the ones that were
removed and leaves their tables and columns in place; the next `migrate` then
fails on "already exists".

## Before you commit

```bash
python manage.py makemigrations --check --dry-run   # no drift (CI enforces this)
python manage.py sqlmigrate <app> <number>          # read the actual SQL
python -m pytest apps -q
```

`sqlmigrate` is the one people skip. Read it. It is the only way to see that
Django turned your innocuous field change into a table rewrite.

## Why CI runs the previous release against your schema

The `migration-safety` job applies the previous release's migrations, then
yours, then runs the previous release's tests against the new schema. A failure
caused by schema incompatibility means a contract-phase change — which is allowed, but it has
to be a MAJOR release with a changelog entry, not a surprise on someone's
production instance at 2am.

The current-code and previous-release suites use the same pinned Playwright
container for consistent system libraries and font fallback. The previous
release still installs its own Python requirements and browser revisions, and
its source and assertions remain unchanged. Keep both job image pins aligned
when updating the browser environment. Real-daemon `docker_sandbox` tests run in
the dedicated host jobs; container jobs cannot supply the host daemon with their
temporary filesystem paths for bind mounts. Diagnose environment or existing test
failures separately before classifying a failed job as a schema incompatibility.
A release's tests are frozen with its tag, so a timing flake fixed on `main` keeps
failing here until the next release. List such a test under its tag in
`FROZEN_RELEASE_FLAKES` in `.github/workflows/previous_schema.py`; the entry
stops applying once a newer release becomes the previous one. Never list a
failure that could be a schema incompatibility.
