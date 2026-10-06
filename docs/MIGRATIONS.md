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

**Data migrations.** There are none in this repository yet, which is worth
keeping in mind — the first one sets the pattern. When you write one:

- Batch it. A single `UPDATE` over millions of rows holds locks and bloats WAL.
- Make it idempotent and resumable; it will be interrupted somewhere.
- Never import models directly — use `apps.get_model()` so the migration sees
  the schema as it was, not as it is now.
- Put it in its own migration, separate from schema changes.

**Renaming.** Don't, in one step. Add the new name, backfill, switch readers,
drop the old one next release. `RenameField` breaks the old code instantly.

**Deleting.** Contract phase only, and only after a release where nothing read it.

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
yours, then runs the previous release's tests against the new schema. If that
fails, your migration is a contract-phase change — which is allowed, but it has
to be a MAJOR release with a changelog entry, not a surprise on someone's
production instance at 2am.
