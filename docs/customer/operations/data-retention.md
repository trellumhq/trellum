# Data retention

Run history, audit rows, assistant transcripts, sessions and expired invitations
have no natural ceiling. On a single VM the disk they share is the same one
holding the database and every studio's data, so something has to delete them.

Cleanup runs nightly on the coordinator. There is nothing to enable.

## See what it would remove

```bash
docker compose exec web python manage.py cleanup --dry-run
```

Changes nothing, reports exactly what a real run would delete. Worth doing once
after you change any of the windows below.

## The policy

| Setting | Default | What it removes |
|---|---|---|
| `RETENTION_RUN_DAYS` | 90 | Finished runs older than this |
| `RETENTION_RUN_KEEP_PER_REPORT` | 10 | …but always keep the newest N per report, at any age |
| `RETENTION_RUN_OUTPUT_DAYS` | 14 | Blanks the stdout/stderr tails of older runs, keeping the row |
| `RETENTION_AUDIT_DAYS` | 365 | Audit rows — authorization, administration, and system events |
| `RETENTION_AUDIT_ACCESS_DAYS` | 90 | Audit rows — authentication and data-access events (higher volume, shorter forensic half-life; see [Logs & monitoring](logs-and-monitoring.md#the-audit-log)) |
| `RETENTION_AUDIT_ARCHIVE` | `true` | Export the doomed audit window to gzip NDJSON under `DATA_DIR/archive/audit/` before deleting it, so a retention purge never destroys the record outright. Set `false` for hard delete. |
| `RETENTION_ASSISTANT_SESSION_DAYS` | 180 | Assistant conversation transcripts |
| `RETENTION_INVITATION_DAYS` | 30 | Accepted or expired invitations |
| `RETENTION_TMP_RUN_HOURS` | 48 | Build scratch directories on disk |
| `RETENTION_BUILT_DATA_DAYS` | `0` (off) | Built report output. Off by default: your reports keep their data until you delete the report. Set a number of days only if your own policy requires it |
| `RETENTION_ABANDONED_UPLOAD_DAYS` | 90 | Uploaded data-source files that nothing points at any more |
| `RETENTION_DORMANT_DAYS` | `0` (off) | Days without a sign-in before an account is warned by email. `730` (24 months) is the intended value |
| `RETENTION_DORMANT_DISABLE_DAYS` | 30 | Days after that warning before sign-in is switched off |
| `RETENTION_DORMANT_ERASE_DAYS` | `0` (off) | Opt-in: days after *that* before the account is erased automatically. Off means erasure stays a human decision |
| `CLEANUP_CRON` | `17 3 * * *` | When it runs |
| `CLEANUP_ENABLED` | `true` | Whether it runs at all |

Every window is in days and **every one accepts `0`, meaning keep forever**. A
compliance hold needs that, and a retention policy you cannot turn off is one
people work around.

### The two windows an organization can set

`RETENTION_BUILT_DATA_DAYS` and `RETENTION_ABANDONED_UPLOAD_DAYS` are also
settable per organization, and they behave differently from each other on
purpose. The dormancy windows below are instance-wide only.

**`RETENTION_BUILT_DATA_DAYS` is off out of the box, and that is deliberate.**
A report's data belongs to whoever made it and stays until they delete the
report — the same way a document stays in a document editor. Expiring it on a
clock would be data loss with a schedule: a report somebody opens every week
would lose its data because the *build* aged, which is not what "no longer
needed" means. Storage limitation is about purpose, and a report in use is
still serving its purpose.

Trellum builds, then serves what it built — there is no live query behind a
report page. So removing that output does not make the next view slower, it
makes the report unavailable until someone rebuilds it. The rebuild needs the
source system to still be reachable and to still hold the same rows, which for
a quarterly snapshot it often will not.

**If your own policy does require built data to expire, set a number of days.**
It then acts as both the default and a ceiling: an organization may choose a
shorter window, never a longer one, and an organization setting its own value
to `0` inherits the ceiling rather than meaning "keep forever". Turning it back
off instance-wide restores "keep until deleted" everywhere.

Turning it off never means bytes pile up forever. Data whose owner is gone — a
deleted studio's output, an upload nothing points at — is swept on
`RETENTION_ABANDONED_UPLOAD_DAYS` regardless of this setting.

Expiring built output removes the report's *output*, not the report. Its
history, schedule, favourites and share links all survive, and the next build
fills it back in.

**`RETENTION_ABANDONED_UPLOAD_DAYS` has no ceiling**, because no public claim
rides on it. It is housekeeping: an organization may set it to anything,
including `0` for forever. The clock does not start when the file was uploaded —
it starts when nothing references the file any more.

### Closing accounts nobody uses

Everything else here deletes data. This one closes people's accounts, so it is
**off by default** — an upgrade that quietly started deleting your staff
accounts would be indistinguishable from a bug. Set `RETENTION_DORMANT_DAYS=730`
to run it.

Once on, an account nobody has signed into for that long is **warned, then
disabled, and that is the end of the pipeline as shipped**. Any sign-in at any
point resets all of it:

1. **Warned.** An email to the address on the account, naming the date below
   and saying that signing in once cancels it. Nothing else changes.
2. **Sign-in switched off**, `RETENTION_DORMANT_DISABLE_DAYS` later. Nothing is
   deleted; an operator can turn the account back on from the built-in
   admin site at `/admin` (clear `dormancy_warned_at` there too, or the next
   sweep switches it off again). Erasing a disabled dormant account is a human decision, made on
   [the erase screen](#deleting-one-person-on-request) — with one edge worth
   knowing: if the disabled account was an organization's only admin, nobody
   in that org can reach that screen, and an operator promotes someone first.
   The last-admin guard stays, because an adminless organization is the worse
   failure.
3. **Erased automatically — only if you opt in** by setting
   `RETENTION_DORMANT_ERASE_DAYS`. Then, that many days after stage two, the
   account gets exactly the erasure described under
   [Deleting one person on request](#deleting-one-person-on-request), equally
   permanent — except not scoped to one organization: dormancy is a fact
   about the account, so the account goes everywhere it exists at once.

The stage clocks run from the day the **warning was actually sent**, not from
the threshold. If mail is not configured or delivery fails, no warning is
recorded, and an account with no recorded warning is never disabled and never
erased — the policy stalls instead of running silently. Setting
`RETENTION_DORMANT_DISABLE_DAYS=0` stops it after the warning and turns erasure
off with it: an account is never erased without its dead sign-in first giving
somebody the chance to notice.

Two kinds of account are never touched at any age. **Operator accounts**
(`is_superuser`, `is_staff`, operator-console access) — the break-glass account
is exactly the one that legitimately goes years unused, and erasing the last one
would lock you out of your own instance. **Already-erased accounts**, whose
clock stopped when they were erased.

An account that has never been signed into at all ages from the day it was
created. A pending invitation is not an account and is not covered here — it
expires on its own and is swept by `RETENTION_INVITATION_DAYS`.

Org admins see who is approaching all this under Organization settings → Data
retention, in time to tell a colleague on long leave to sign in once.

## What it will never delete

These are not configurable, because getting them wrong loses something you
cannot get back:

- **Runs that are still active.** Queued, starting or running, at any age.
- **The newest runs per report.** A quarterly report would otherwise lose its
  entire history to a 90-day window.
- **Anything in the current billing month.** Monthly build minutes are derived
  from run records, so deleting a recent run would quietly hand quota back.
  Shortening `RETENTION_RUN_DAYS` below a month does not change this.
- **LLM spend records.** That is a billing ledger, not a log. Assistant
  *transcripts* age out; what they cost does not.
- **An uploaded file a data source still points at.** At any age. Uploads are
  yours, not ours to expire; only a file nothing references any more is
  eventually tidied away, and `RETENTION_ABANDONED_UPLOAD_DAYS` counts from the
  day it stopped being referenced.
- **A report whose last successful build is unknown.** The built-data window
  reads the last build that *succeeded*, so a report failing nightly still ages
  out on schedule. Where that date is not known — a report built before this
  window existed — the output is left alone rather than guessed at.

Run log tails are blanked rather than deleted: the tails are most of the bytes,
while the row itself is what history and quota accounting need.

## Knowing it is still happening

```bash
docker compose exec web python manage.py doctor
```

The `retention` check fails if cleanup has not run for `CLEANUP_MAX_AGE_HOURS`
(default 48). The failure mode this guards against is not dramatic — the disk
fills months later on an instance nobody was watching — which is exactly why it
needs to be a check rather than something you notice.

It also reports how much is sitting there past its window right now. On an
instance where cleanup is running that number stays near zero; a large one is
the sign to look at the check above it.

Each run records what it deleted, so "what went last night" is answerable
without reading container logs.

## Retention and your backups

Deleting data from the volume does not delete it from backups that already
contain it, and no retention setting here reaches inside a backup archive.

`docker/backup.sh` archives `studios/` — which includes built output and
uploads — and `orgs/`, keeping `BACKUP_KEEP_DAYS` (default 14) of them. So a
report purged on day 30 can still exist inside backup archives until the last
one containing it rolls off, up to 14 days later.

This matters if you are answering a question about your own retention policy:
the accurate statement is that data is deleted within its window and then
disappears from backups as those age out — not that it is gone everywhere the
moment the sweep runs. If your policy needs a tighter guarantee, lower
`BACKUP_KEEP_DAYS`; the two windows are independent and you must set both.

A restore needs nothing special. The clock lives in the database, so anything
restored past its window is purged by the next nightly sweep rather than
starting a fresh 30 days.

!!! warning
    Restoring the database from one point and the data volume from another can
    leave a report marked as already expired while its files are back on disk.
    The sweep skips rows it has already purged, so that combination is not
    self-healing. Restore both halves from the same timestamp.

## Deleting one person on request

Everything above is automatic and about *time*. A subject-access or erasure
request is neither: it is about one person, and it arrives on a date you do not
choose. Organization settings → Members → the person gives you both halves.

Often nobody has to ask you at all: **a person can delete their own account**
from their Account page. It runs the same erasure described below, across every
organization they belong to at once, after they type their email address (and
password, on password accounts) to confirm. Two cases still need an admin: an
organization's only admin must hand the role to someone else first, and
instance operators are removed through the built-in admin site at `/admin`
rather than a button on their own page.

**Export** produces a JSON file of everything this installation holds about
them that your organization is entitled to see. That answers an access request
and a portability request together, and it is worth running *before* an
erasure, because afterwards there is nothing left to export.

**Erase** removes their personal data. It does not delete the account row — it
scrubs the identifying columns and marks the account erased. That distinction
is deliberate and is what lets both obligations be met at once:

- The person is gone. Name, address, credentials, sessions, second factors,
  memberships, favourites and assistant conversations are deleted, and
  addresses recorded inside audit entries are scrubbed along with them.
- The record survives. Audit entries, report views and run history keep their
  shape and lose their subject, so "how many actions did the erased account
  take" is still answerable and still distinguishable from anonymous or
  system activity. Deleting those rows to satisfy an erasure request would
  destroy the accountability record you are separately obliged to keep.

Some things stay because they were never personal data: reports the person
wrote, delivery schedules they configured, and LLM spend records.
Those belong to the organization, not to them.

**Erasure is scoped to your organization.** If the person belongs only to
yours, the account is erased across the installation. If they also belong to
another organization, only your organization's copy goes — you cannot erase an
identity another tenant still employs, and you are not told which ones those
are. The confirmation screen says which of the two is about to happen, and
lists exactly what will be deleted, anonymised and kept, before you confirm.

The erasure itself is written to the audit log. That record is how you evidence
later that the request was honoured.

## Files left by deleted studios and organizations

Deleting a studio — or a whole organization, which an org admin can do from
Organization settings → Studios — removes database rows but leaves files on
the data volume, deliberately: an accidental deletion should be recoverable
for a while. Members of a deleted organization keep their accounts; only the
memberships go, and the audit history survives with the organization's name
on it.

Cleanup reclaims the files on `RETENTION_ABANDONED_UPLOAD_DAYS`: once nothing
owns them, built output and uploads alike are just abandoned bytes, whatever
they used to be. The window counts from the deletion, not from when the files
were written, so a studio deleted yesterday keeps its full window however old
its contents are.

The git checkout under `/data/studios/<org>/<studio>/repo/` is not swept — it is
a copy of a repository you still have. To reclaim it, or to reclaim everything
immediately rather than waiting out the window:

```bash
docker compose run --rm web du -sh /data/studios/<org>/<studio>
docker compose run --rm --user root web rm -rf /data/studios/<org>/<studio>
```

!!! warning
    There is no undo. Check the path twice — the directory above it holds every
    other studio in that organization.
