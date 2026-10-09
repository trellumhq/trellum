# Logs & monitoring

## Where logs are

Open **System → Server logs**, or the permanent **Server logs** sidebar link,
as an instance operator. This page retains application events from the web,
worker and coordinator processes in the shared database. It includes worker
startup settings, queue activity, build outcomes, stop and timeout signals,
orphan cleanup and application exceptions. The older report drawer is named
**Build activity**: it summarizes report history.

Filter by minimum severity, time, service, worker, report, run or request ID,
or search message text. Pause live updates while investigating, expand an
event for its traceback and context, and open its run for build output and
memory evidence. Copy an event or download filtered NDJSON (at most 1,000
events). Global logs and exports are unavailable while impersonating a user.

Application logs also continue to container stdout, capped by the supplied
Compose configuration at 10 MB × 5 files per service:

```bash
docker compose logs -f web worker
```

With the split topology, include the `coordinator` and `runner` services too.
For multi-host deployments, collect logs from each host's container runtime.

### Coverage and retention

Apply database migrations and restart the web/worker/coordinator processes
after installing this feature. Capture begins in each updated process; it
does not import earlier container logs. Defaults retain seven days and at
most 20,000 events, with periodic pruning. Configure
`SERVER_LOG_CAPTURE_ENABLED`, `SERVER_LOG_RETENTION_DAYS`,
`SERVER_LOG_MAX_ROWS` and optional `SERVER_LOG_SERVICE` in the environment.
The root `LOG_LEVEL` controls which application messages are emitted.

Capture uses a bounded asynchronous queue so a database failure or log burst
does not block report execution. Events can be lost during overload, database
outages or abrupt process termination. The collector reports loss when it
recovers and falls back to stderr during persistence failures. Console logs
remain a separate diagnostic source.

The page does **not** collect Linux kernel OOM records, Docker daemon logs,
reverse-proxy/access logs or arbitrary process stdout. Report stdout/stderr
are available through linked runs: active files where accessible, otherwise
the bounded tails retained with the run. These tails may omit earlier output.
For a suspected host OOM, inspect host/kernel and Docker state alongside the
application timeline. A connection-close message alone does not establish
which component caused the failure.

Stored events and build-output responses redact common credential keys and
patterns. Redaction cannot recognize every possible secret in arbitrary
report output; report code should never print credentials.

## Following one report build

A build crosses four processes: `web` accepts the request, a row lands in the
run queue, the worker claims it, and a throwaway sandbox container runs the
report code. Comparing timestamps across four logs is not a diagnosis.

Every response carries an **`X-Request-ID`**, and log lines written while
serving that request carry the same ID. Search for it to find the enqueue
event, then follow its **run ID** through worker and build events. The request
ID is request-scoped; it is not automatically propagated into the asynchronous
report process. For console logs, search for the run ID:

```bash
docker compose logs web worker | grep '<run-id>'
```

If your reverse proxy already stamps `X-Request-ID`, the portal keeps yours
rather than inventing a new one, so the id in their logs and these agree.

## Shipping logs somewhere

```bash
LOG_FORMAT=json      # one JSON object per line
LOG_LEVEL=INFO
```

Text stays the default because `docker compose logs` is read by a person. JSON
lines carry the request id, and the fields a build cares about — run id, studio,
slug, duration, exit code — as real fields rather than buried in a message.

The JSON console formatter excludes common secret-named extra fields. Avoid
putting credentials in message strings; console formatting is not a guarantee
of complete redaction.

## Health

```bash
docker compose exec web python manage.py doctor
```

The same checks appear on the operator `/system` page. They cover the database,
the encryption key, disk space, the framework pin, the worker fleet, the report
sandbox, the storage configuration, **whether the schema matches the running code**, whether
**backups** are recent and verified, and whether **retention** is still running.

Instance operators also have a permanent **System health** link in the console
sidebar. A warning appears in the header when the local worker, migration, disk,
backup, or retention checks need attention and refreshes about every 30 seconds.
Worker and coordinator warnings call out when repository sync, report builds, or
scheduled reports are paused. The header stays quiet when these quick checks are
clear; `/system` remains the full diagnosis. The summary endpoint at
`GET /api/system/health` is session-authenticated, operator-only, and not cached.

`GET /healthz` is the liveness endpoint the compose healthcheck uses: database
reachable, 503 if not.

`GET /api/version` reports what is running and whether the schema matches it —
see [Upgrades](/docs/latest/install/upgrades/).

## The audit log

Every action worth a compliance answer is recorded per organization: sign-ins
and sign-in failures, **SSO refusals** (wrong domain, no invitation, auto-signup
off, the seat ceiling — the six ways an SSO attempt can be turned away, each
with its own reason), role and group changes, data access (report views,
export downloads, data-source downloads, live queries), and administration
(studio/org changes, SSO configuration, impersonation). Written automatically,
never editable, and not deletable — including by an instance operator through
the admin.

Recording runs on every installation, so the full history is available for
review. **Reading** the trail — the dashboard at **Organization settings →
Audit log** — is restricted to operators and organization administrators.

When an operator acts as a user, every row from that session names **both** —
the action carried the customer's permissions, and the operator who took it.

### The dashboard

Filterable by category (authentication / authorization / data access /
administration / system), action, actor, outcome (success / denied /
failure), and date range — every filter composes with the others and is
linkable. A text search box scans the visible window's action, target, and
metadata. A "recent denials" panel surfaces refusals — SSO or otherwise — above
the fold, and each row expands in place to its full detail: metadata, IP,
user agent, and the impersonator when there was one.

**Export CSV** streams the currently filtered rows (capped at 100,000; a
full-history handoff is what the archive below is for) and — because
exporting the trail is itself worth knowing about — records its own
`audit.export` row.

### Retention

Two windows, both in days, both accepting `0` for "keep forever":

| Setting | Default | Governs |
|---|---|---|
| `RETENTION_AUDIT_ACCESS_DAYS` | 90 | Authentication and data-access events — sign-ins, SSO denials, report views, exports, downloads, live queries. The highest-volume categories, and the ones with the shortest forensic half-life. |
| `RETENTION_AUDIT_DAYS` | 365 | Authorization, administration, and system events — role/group changes, config edits, impersonation, retention's own summary row. The compliance record; it stays long. |

With the default `RETENTION_AUDIT_ARCHIVE=true`, cleanup exports the doomed
window as gzip NDJSON under
`<DATA_DIR>/archive/audit/<org>/<YYYYMM>.ndjson.gz` before it is deleted. Set
it to `false` for hard deletion without an archive.

### GDPR posture

The trail stores IP, user agent, identity, and — for a few actions — an
attempted or asserted email address; that data *is* the feature, kept under a
lawful-interest / security-obligation basis. The controls: **bounded
retention** (above), **purpose limitation** (an org admin sees only their own
organization's rows), **no secrets** (`audit()` redacts any metadata key that
looks like a password, token, secret, cookie, or credential before the row is
ever written), and **erasure posture** — deleting a user account clears the
actor reference on their rows (`SET_NULL`) while the row itself, and any email
string already in its metadata, survives for its retention window. `auth.sso_denied`
stores the email an identity provider asserted, for someone who may never have
had an account at all — the shorter (access) window applies to it.

### Recording the right client address

```bash
TRELLUM_TRUSTED_PROXIES=1
```

Behind a reverse proxy, the address the portal sees is the proxy's — the same
value for every user, which makes the column useless for an investigation. Set
this to the number of proxies **you** operate and the client address is read
from `X-Forwarded-For`.

Only the hops your proxy appended are trusted. Anything a client puts at the
front of that header is ignored, because it is client-supplied: trusting it
blindly would let anyone write any address into your audit trail, which is worse
than a consistently wrong one. Left at `0`, the connection address is used.
