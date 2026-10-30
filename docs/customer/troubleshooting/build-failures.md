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

## Automatic recovery

Bounded whole-report recovery is enabled by default for valid report
configurations. Reports whose generator or helpers have external side effects
must explicitly disable it in `report.yaml`:

```yaml
recovery:
  replay_safe: false
```

Disable recovery for database writes, payments, messages, file uploads or other
non-idempotent actions, and for detached/background work that outlives the build.
A read-only SQL query does not establish that the surrounding Python is safe.
The portal snapshots the recovery policy at admission and checks the current
configuration before admitting a retry. Previously admitted runs retain their
recorded policy. Managed connections also record unsafe/session access; that
evidence vetoes recovery even when recovery is enabled in the configuration.

For an eligible transient connection failure or overall build timeout, the
coordinator records up to two further attempts, after one and five minutes
plus up to ten percent jitter. Queue delays count toward a four-hour chain
age; retry execution is capped by the remaining age and the ordinary build
limit. Each attempt has its own history and query retries stay within that
attempt's deadline. Intent and final status are committed together. Restarting
the coordinator resumes pending work without resetting the budget. A queued
retry uses the same tenant quotas, pool fairness and memory admission as any
other build. Manual or cron work already active defers recovery; its success
supersedes the pending chain and its eligible failure consumes a remaining
attempt. A newer permanent or unsafe failure closes the older retry intent;
an overall deadline never overrides observed permanent-error evidence.
Exhaustion never disables future ordinary cron occurrences.

Operations run history shows the last known connect, execute, fetch, render or
publish stage, bounded timings, driver classes/codes, query attempt and managed-read retry counts with bounded reasons,
effective timeout and recovery disposition. Identifiers and traceback locations
are hashed; new structured evidence contains no SQL, parameters, result data,
connection strings or exception messages. A hard kill retains the last snapshot,
which is evidence of progress, not proof of the underlying cause. Existing log
access and retention rules continue to apply. Older framework builds may provide
no structured stage evidence; the cause remains unknown.

Authentication, invalid SQL/configuration, memory failures, deliberate stops,
unsafe operations and unknown publication/write outcomes are not automatically
replayed. A driver-side cancellation that cannot be distinguished from a query
timeout remains ineligible. Source-readiness blocks use the existing connection
check/unblock flow. Canceling a report also cancels its pending recovery.

Missing heartbeats never prove termination. The portal keeps an active admission
fence and shows an unconfirmed outcome until the owning worker can confirm its
process/container has stopped. POSIX builds use a dedicated process group;
remaining group members are killed and checked again before completion.
Unsandboxed group confirmation cannot detect descendants that deliberately
leave that group; such reports must set `recovery: {replay_safe: false}`. Docker provides the
container boundary for untrusted process trees. Direct Windows builds use a
kill-on-close Job Object and do not start report code until the process is
assigned to it. The runner checks that no job processes remain before
publishing output or admitting recovery. Code that deliberately starts work
outside the process group or job must set `recovery: {replay_safe: false}`. Missing
Docker containers cannot provide termination confirmation here and stay
unconfirmed. An operator must investigate a lost worker; do not
clear its run fence before independently confirming termination. Boot catch-up
only starts reports with no run history, so coordinator restarts cannot become
an unbounded replay mechanism.

Failed builds retain the previous successful artifact and freshness timestamp.
New output is staged separately and promoted on success (an atomic directory
exchange on Linux; a rollback-protected rename on other hosts, which can briefly
leave the path absent). A storage publication failure has an unknown outcome and
is not automatically retried. Existing failure/recovery transition notifications
are retained; failed retry attempts do not invoke success alert evaluation.
Notification execution remains best effort after the terminal database commit;
a crash there can omit a notification rather than replay an uncertain delivery.
