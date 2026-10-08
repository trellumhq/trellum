# Build failures

A failed build is recorded with its cause, its logs, and its memory figures.
Start in the studio's **Operations** tab, which shows the queue, running
builds, and per-run logs that survive restarts.

## Read the classification first

| Outcome | Means | Usual fix |
|---|---|---|
| `success` | It built | — |
| `error` | The report code raised | Read the traceback in the run log |
| `timeout` | Exceeded the operator's build timeout | Make the query cheaper, or ask the operator to raise `TRELLUM_RUN_TIMEOUT` |
| `oom_killed` | Killed for memory | Reduce the data loaded or the size of in-memory transformations |
| `stopped` | Stopped by a person or by a worker drain | Re-run it |

The distinction between `timeout` and `oom_killed` matters: they look identical
from the dashboard ("it didn't finish") and have opposite fixes.

## Common causes

**It works locally but not in the portal.** Report code runs in a sandbox with
an environment allowlist — it cannot see portal secrets or arbitrary
environment variables. Anything the report needs must come through a declared
data source.

**A data source fails to connect.** Use *Test* on the data source; it does a
real driver round-trip with a short deadline and reports the real error with
credentials scrubbed.

**The report never started.** A report whose data sources are not ready is
held rather than run: its page says which source it is waiting for, and the
studio's Data sources page says what that source needs. It is queued
automatically once the source connects — see
[Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/).
Nothing has to be re-run by hand.

**Nothing builds at all after a push.** Check the studio's repository
settings. The token may have expired or the branch may have been renamed, and
the sync error is shown there directly. If the studio publishes manually, the
push has been fetched but not published: the pending panel holds it until
someone presses **Publish** — see
[Publishing from git](/docs/latest/portal/publishing-from-git/). A publish
that failed — an invalid `report.yaml`, say — is recorded in the publish
history with its error, and the studio keeps serving the previous commit.

**Only some reports rebuilt.** That is by design — builds are scoped to what
changed in git. Trigger a run manually to force one.

## Validation warnings

The validation drawer on a report shows diagnostics the framework produced —
filters that cover no chart, columns referenced but never selected, and
similar. These do not fail a build by default, but they are usually the reason
a chart is unexpectedly empty.

## Still stuck

Collect a [support bundle](/docs/latest/troubleshooting/support-bundle/).
