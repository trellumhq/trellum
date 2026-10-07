# Configuration reference

Every setting, for when you need to go past the values the
[install](/docs/latest/install/docker-compose/) asks for.

Instance configuration lives in `.env` next to `docker-compose.yml`. Anything
tenant-specific — data source credentials, repository tokens, SSO secrets —
lives encrypted in the database instead and is managed from the UI, never from
a file.

## Required

| Setting | What it is |
|---|---|
| `TRELLUM_IMAGE` | Image reference, preferably version-pinned |
| `PORTAL_BASE_URL` | Public address, e.g. `https://bi.example.com`; used for links in outbound email and to decide whether cookies are marked secure |
| `ALLOWED_HOSTS` | Hostnames the portal answers on; requests under any other name are refused |
| `POSTGRES_PASSWORD` | Password for the bundled database |
| `SECRET_ENCRYPTION_KEY` | Encrypts stored credentials — data source passwords, repository tokens, SSO secrets |
| `SESSION_SECRET_KEY` | Signs session cookies and the one-time links in invitation and password-reset email |
| `DOCKER_GID` | Host `docker` group id, so the worker can start build sandboxes |

Generate the two keys with:

```bash
python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

!!! danger
    Losing `SECRET_ENCRYPTION_KEY` means every stored credential becomes
    unreadable. Back it up separately from the database backup — a backup that
    contains both is a single point of compromise.

## Optional

| Setting | Default | Effect |
|---|---|---|
| `DATABASE_URL` | bundled database | Point at a database you operate yourself. Backups follow it |
| `TRELLUM_BIND_ADDRESS` | `127.0.0.1` | Interface the portal binds to; leave on loopback and let your proxy handle the internet |
| `TRELLUM_DATA_DIR` | `/data` | Where all durable file state lives — see below before changing it |
| `TRELLUM_ORG_SELF_SIGNUP` | `False` | Keep organization self-signup disabled unless this is an intentionally open development instance |
| `TRELLUM_QUOTAS_ENABLED` | `False` | Enable per-organization limits configured by the operator. Runner admission and sandbox limits remain active when this is off |
| `TRELLUM_IMPERSONATION_ENABLED` | `True` | Enables the audited operator support workflow |
| `TRELLUM_SSO_DOMAIN_VERIFICATION` | `True` | Require verified DNS claims before SSO routing |

### Build memory and time limits

Build limits belong to the operator. A report cannot set `resources.memory_mb`
or `resources.timeout` in `report.yaml`, so every report receives the same
memory allocation and wall-clock deadline unless the operator changes these
settings for the installation.

| Setting | Default | Effect |
|---|---|---|
| `WORKER_MAX_CONCURRENT` | `3` | Maximum builds admitted to one runner at once |
| `TRELLUM_RUNNER_ROLE` | `all` | Combined worker, or `coordinator` / `runner` when running split roles; an explicit `runworker --role` overrides it |
| `TRELLUM_RUNNER_POOLS` | empty (all pools) | Comma-separated studio pools this runner accepts: `small`, `standard`, `large`. Configure per runner; see [runner pools](/docs/latest/operations/sizing/#assign-studios-to-runner-pools) |
| `TRELLUM_RUNNER_MEMORY_BUDGET_MB` | `0` | Total memory the runner may reserve across builds. `0` disables memory-based admission, leaving the concurrency limit in control |
| `TRELLUM_DEFAULT_JOB_MEMORY_MB` | `1024` | Memory in MB reserved for every admitted build and used to derive its hard cap |
| `TRELLUM_JOB_MEMORY_HEADROOM` | `1.5` | Multiplier between the reserved amount and the process/container hard cap, allowing for mapped libraries and allocator overhead |
| `TRELLUM_JOB_MEMORY_ENFORCE` | `True` | Apply the address-space cap to unsandboxed POSIX builds. Sandboxed Docker builds always receive a container memory cap |
| `TRELLUM_RUN_TIMEOUT` | `600` | Wall-clock seconds before a build receives `SIGTERM`; it is force-killed five seconds later if still running |

Size the runner budget from memory left after the database, web process, and
host overhead. Admission reserves `TRELLUM_DEFAULT_JOB_MEMORY_MB` per running
build; the hard cap is that value multiplied by
`TRELLUM_JOB_MEMORY_HEADROOM`. This separates predictable scheduling from the
larger address-space ceiling needed by healthy pandas workloads. See
[Sizing](/docs/latest/operations/sizing/) for worked examples.

### Source provenance for custom builds

Official release images bake the exact public source URL for their commit. A
fork or custom build can set `SOURCE_URL` as a Docker build argument to provide
the equivalent link. A process started outside the image can instead set
`TRELLUM_SOURCE_URL` at runtime.

Both values must identify the exact public source tree that produced the code.
Only `http` and `https` URLs without embedded credentials are accepted; unsafe
values are rejected rather than exposed through `/api/version` or generated
reports.

### The data volume

`TRELLUM_DATA_DIR` is the root of everything the portal keeps on disk: studio git
checkouts, the project root each build runs against, built report output, and
uploaded data-source files. The bundled `docker-compose.yml` mounts a named
volume at `/data`, which matches the default baked into the image, so a standard
install never sets this.

The portal, coordinator, and every runner must see the **same** `TRELLUM_DATA_DIR`.
This applies across hosts too: mount the same persistent filesystem at the same
container path on every web, coordinator, and runner host. The Compose
example's named volume is local to its Docker host; it does not share files
across hosts. See
[Sizing](/docs/latest/operations/sizing/#when-one-machine-is-not-enough).

!!! warning
    Changing `TRELLUM_DATA_DIR` without moving the volume mount to match points the
    portal at an empty directory. Existing studios keep their database rows but
    their files are gone, and builds fail on a missing project root.

For a multi-host deployment, all hosts must also use the same PostgreSQL
database. S3-compatible storage holds built report output only; studio
checkouts, project files, uploaded data-source files, and live run logs remain
under `TRELLUM_DATA_DIR` and require shared persistent storage. The coordinator
also uses that directory for cleanup, audit archives, and scheduled delivery.

To keep the data somewhere specific on the host, change the *mount source* and
leave the container path — and so `TRELLUM_DATA_DIR` — alone:

```yaml
volumes:
  - /srv/trellum-data:/data      # host path : container path, unchanged
```

### Built report output in object storage

By default a build writes its output to the data volume and the portal serves it
from there. It can instead be published to an S3 bucket, or to anything that
speaks the S3 API — MinIO, Ceph, Wasabi — which is the option for running this
against storage you operate yourself.

Object storage is available to every installation. Local storage remains the
explicit default. Trellum never silently falls back between backends: run the
storage preflight and migrate existing artifacts deliberately before changing
this setting.

| Setting | Default | Effect |
|---|---|---|
| `TRELLUM_STORAGE_BACKEND` | `local` | `s3` publishes built output to a bucket |
| `TRELLUM_REPORTS_BUCKET` | — | Bucket to publish under. Required when the backend is `s3` |
| `TRELLUM_STORAGE_ENDPOINT_URL` | — | Point at a non-AWS S3-compatible store |
| `TRELLUM_STORAGE_ACCESS_KEY` / `TRELLUM_STORAGE_SECRET_KEY` | — | Credentials, when an endpoint is set. Without an endpoint the usual AWS credential chain applies |
| `TRELLUM_STORAGE_REGION` | — | Region |

Builds still write to the data volume first and are uploaded once they succeed,
so a build never depends on the network to produce output — only to publish it.
A publish that fails marks the run **failed** rather than successful: output
that never reached the bucket is output nobody can open, and reporting that as a
success would leave a green report that 404s.

In the bucket, every build is immutable and a tiny pointer decides what serves:

```
{org}/{studio}/{report}/_meta.json          status, updated after every run
{org}/{studio}/{report}/_current            which build is live
{org}/{studio}/{report}/builds/{build}/...  one build's complete output
```

Each build uploads into its own fresh `builds/` prefix and `_current` is flipped
only once the upload is complete, so a reader always sees a whole build — never
a mixture of two, no matter when it reads relative to a publish. A failed run
updates only `_meta.json`; the pointer stays on the last good build, which keeps
serving. Superseded builds are cleaned up automatically (the most recent
previous one is kept briefly for readers mid-download).

Reads go through a local cache under `<TRELLUM_DATA_DIR>/cache/`, refreshed when a
report is rebuilt. It is derived data — deleting it costs a re-download and
nothing else.

The cache saves transfers; it is not a second copy of the truth. Every read asks
the bucket which build is current before serving, so once you point the portal at
object storage the bucket is on the critical path for reading reports as well as
writing them. If it becomes unreachable, reports stop serving rather than
quietly falling back to whatever a node downloaded last — treat its availability
the way you treat the database's.

Running MinIO on your own hardware, with no AWS account involved:

```bash
TRELLUM_STORAGE_BACKEND=s3
TRELLUM_REPORTS_BUCKET=trellum-reports
TRELLUM_STORAGE_ENDPOINT_URL=https://minio.internal:9000
TRELLUM_STORAGE_ACCESS_KEY=...
TRELLUM_STORAGE_SECRET_KEY=...
```

`manage.py doctor` reports a `report storage` check that proves the bucket is
reachable and writable by this process, so a wrong name or a missing permission
surfaces immediately rather than at the end of the first build.

### Serving report content: the access model

With object storage on, report bytes flow through the web process by default —
the portal reads them from the bucket and serves them behind its own permission
check. That is the right shape for a self-hosted install and it is **safe by
construction**: the bucket is never exposed to a browser, so there is no public
surface to misconfigure.

At hosted scale that changes: a handful of viewers pulling large `data.json`
files can occupy the whole web tier. `TRELLUM_REPORT_ACCESS_MODEL` chooses how
report content reaches the browser.

| Value | Who serves the bytes | Who authenticates |
|---|---|---|
| `proxy` (default) | the portal | the portal (its own permission check) |
| `edge-signed` | a CDN/edge worker in front of the bucket | the portal issues a short-lived signed grant; the edge verifies it |
| `edge-external` | a CDN/edge in front of the bucket | **your own** identity layer (Azure AD / Entra, an identity-aware proxy) |

In **every** mode the portal runs the same permission check when a viewer opens
a report. What differs is only who ships the bytes afterward.

!!! danger "The bucket must never be readable without authentication"
    In an edge mode the bucket (or a CDN in front of it) faces the internet. If
    it is left publicly readable, **anyone with a URL can read report data**,
    and no amount of portal configuration prevents it — the exposure is in your
    cloud account, not the app.

    The portal guards against this: `manage.py doctor`'s **`report access`**
    check fires an anonymous, credential-less request at your content origin
    (`TRELLUM_CDN_BASE_URL`) and **fails hard** unless it is refused. At runtime, if
    the portal cannot prove a stranger is denied, it returns `503` for report
    content rather than emit a URL to an open bucket — it never silently falls
    back to proxying the bytes itself. **Run `doctor` before pointing viewers at
    an edge install**, and keep it green.

#### `edge-signed` — the portal is the authority (recommended for hosted)

The portal signs an **Ed25519** grant, scoped to one report's content prefix,
and sets it as the `trellum_grant` cookie. The edge worker holds only the *public*
key: it can verify a grant but never mint one, so a leak of the worker's config
cannot forge access.

| Setting | Default | Effect |
|---|---|---|
| `TRELLUM_REPORT_ACCESS_MODEL` | `proxy` | set to `edge-signed` |
| `TRELLUM_CDN_BASE_URL` | — | public origin content is served from, e.g. `https://reports.example.com` |
| `TRELLUM_CDN_SIGNING_KEY_FILE` | — | path to the Ed25519 private key (PEM). `TRELLUM_CDN_SIGNING_KEY` takes it inline |
| `TRELLUM_CDN_COOKIE_TTL_SECONDS` | `3600` | grant lifetime — **also the revocation latency**: a viewer whose access is removed can keep fetching already-granted content for at most this long |
| `TRELLUM_REPORT_SCOPED_ACCESS_READY` | `false` | operator confirmation that selected-report permissions are safe to activate |

Before enabling selected-report permissions, deploy this version to **every** web
and worker process. Older processes interpret a Viewer group grant as access to
the whole studio. Keep `TRELLUM_REPORT_SCOPED_ACCESS_READY=false` until the
rollout is complete. In `edge-signed` mode, wait at least
`TRELLUM_CDN_COOKIE_TTL_SECONDS` after the last old process is removed before
setting it to `true`; this lets every legacy studio-wide grant cookie expire.
The default wait is 3600 seconds. `manage.py doctor` fails if selected grants
exist while readiness is disabled or while `edge-external` is configured.

Do not roll back to a version without selected-report enforcement while selected
grants exist. Remove or disable those limited memberships first, or restore a
compatible version before old servers receive traffic. The portal never widens
selected grants to all reports during a rollback or configuration error.

Generate the key pair once:

```bash
openssl genpkey -algorithm ed25519 -out cdn-signing.pem
```

Point `TRELLUM_CDN_SIGNING_KEY_FILE` at it; `manage.py doctor`'s `report access`
line prints the matching **public** key (base64) to configure the worker with.

**Example: Cloudflare R2 + Worker** (an S3-compatible object store):

1. Create a private R2 bucket (no public access, no `r2.dev` URL). Point the
   portal's storage at it — R2 speaks the S3 API, so `TRELLUM_STORAGE_ENDPOINT_URL`,
   `TRELLUM_STORAGE_ACCESS_KEY`/`SECRET_KEY` and `TRELLUM_REPORTS_BUCKET` are all it needs.
2. Deploy `edge/report-access-worker.js` (in this repo) with the bucket bound as
   `REPORTS` and the public key in `SIGNING_PUBLIC_KEY`. Route it at
   `https://<content-host>/content/*`.
3. The worker verifies the grant, strips `/content/`, and serves from R2 or
   returns `403`. Build URLs are immutable, so it caches them long; the mutable
   `_current` pointer is never served through it.

The same design works with AWS S3 + CloudFront, or any S3-compatible store
behind any edge that can verify an Ed25519 grant — the worker is the only piece
that changes.

#### `edge-external` — your identity layer is the authority

If you already front the bucket with Azure AD / Entra or an identity-aware
proxy, set `TRELLUM_REPORT_ACCESS_MODEL=edge-external` and `TRELLUM_CDN_BASE_URL`. The
portal emits content URLs and issues **no** grant of its own — your layer
authenticates. Because the portal cannot see that layer, the `report access`
check treats an anonymous request that is redirected to login (or refused) as
protected, and a `200` as a leak. **If your layer is not actually in front, the
check fails — which is exactly what you want.**

One operational note (either edge mode): reports built before the portal
embedded its widget and breadcrumb at build time rely on serve-time HTML
injection, which the edge path does not do. Re-run such reports once before
switching viewers over.

!!! note
    This moves *built report output* off the data volume. Git checkouts and
    uploaded data-source files still live there, so it does not by itself make
    a node stateless — see
    [Sizing](/docs/latest/operations/sizing/#when-one-machine-is-not-enough).

## License

Trellum is distributed under the terms in the repository's
[AGPL-3.0-only license](https://github.com/trellumhq/trellum/blob/main/LICENSE).
Configuration and security controls are documented throughout this guide.

### Backups

See [Backups & restore](/docs/latest/operations/backups-and-restore/).

| Setting | Default | Effect |
|---|---|---|
| `BACKUP_REMOTE_TARGET` | — | rsync/ssh target for off-host copies. Without it, backups die with the machine they protect |
| `BACKUP_KEEP_DAYS` | `14` | How long nightly backups are retained |
| `BACKUP_MAX_AGE_HOURS` | `36` | How stale the newest verified backup may get before `doctor` goes red |

### Retention

See [Data retention](/docs/latest/operations/data-retention/). Setting an age
window to `0` disables that age-based purge. `RETENTION_RUN_KEEP_PER_REPORT`
is a count: `0` removes the protection for the newest runs.

| Setting | Default | Effect |
|---|---|---|
| `CLEANUP_ENABLED` | `true` | Whether nightly cleanup runs |
| `CLEANUP_CRON` | `17 3 * * *` | When it runs |
| `RETENTION_RUN_DAYS` | `90` | Finished runs older than this |
| `RETENTION_RUN_KEEP_PER_REPORT` | `10` | Newest N per report survive at any age |
| `RETENTION_RUN_OUTPUT_DAYS` | `14` | Blank older run log tails, keep the rows |
| `RETENTION_AUDIT_DAYS` | `365` | Audit rows |
| `RETENTION_ASSISTANT_SESSION_DAYS` | `180` | Assistant transcripts (spend records are never purged) |
| `RETENTION_INVITATION_DAYS` | `30` | Accepted or expired invitations |

### Release check

Off by default: the portal does not contact the release feed unless enabled. When
on, the coordinator asks the public releases feed on GitHub once a day and the
operator's `/system` page lists the latest releases, marking the one this
instance runs. A feed it cannot reach changes nothing and logs nothing.

| Setting | Default | Effect |
|---|---|---|
| `TRELLUM_UPDATE_CHECK` | `false` | Whether the daily check runs. The feed it asks is fixed |

### Logging

See [Logs & monitoring](/docs/latest/operations/logs-and-monitoring/).

| Setting | Default | Effect |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` for one object per line, with the request id as a field |
| `LOG_LEVEL` | `INFO` | Root log level |
| `TRELLUM_TRUSTED_PROXIES` | `0` | How many proxies **you** operate. Needed for audit rows to record the real client address rather than your proxy |

### Security

| Setting | Default | Effect |
|---|---|---|
| `TRELLUM_SANDBOX_EGRESS` | `deny` | Whether report code can reach the internet. `open` also lets stored credentials leave — `doctor` says so on every check |
| `SESSION_COOKIE_AGE` | `43200` | Idle session lifetime in seconds, refreshed on activity |
| `LOGIN_MAX_ATTEMPTS` | `10` | Failed logins per email and per address before further attempts are ignored. `0` disables |
| `LOGIN_ATTEMPT_WINDOW_SECONDS` | `900` | How long that window lasts |
| `SECURE_HSTS_SECONDS` | 1 year on https | HSTS max-age; `0` when `PORTAL_BASE_URL` is plain HTTP |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `false` | Opt-in: this host may share a parent domain with hosts you have not hardened |
| `SECURE_HSTS_PRELOAD` | `false` | Opt-in, and close to irreversible |
| `SECURE_SSL_REDIRECT` | `false` | Your proxy does this in the documented deployment |
| `SESSION_COOKIE_SECURE` | follows `PORTAL_BASE_URL` | Override when TLS terminates in front in a way the URL does not describe |
| `CSP_REPORT_ONLY` | `true` | The policy covers the management surface; built report pages are tenant-authored and exempt |

## Settings that live in the UI instead

Instance name, the public URL used for outbound links, and SMTP are managed on
the operator `/system` page and take effect without a restart — no file edit,
no downtime.

Secrets, `DATABASE_URL`, `ALLOWED_HOSTS` and `PORTAL_BASE_URL` stay in `.env`
because they are read at startup, before the database is reachable.
`PORTAL_BASE_URL` is also what cookie security keys off, so if you change the
hostname, change it in both places.

## Using a database you already operate

An overlay file points the stack at your own Postgres instead of the bundled
one (needs compose plugin v2.24+):

```bash
docker compose -f docker-compose.yml -f docker-compose.external-db.yml up -d
```

Set `DATABASE_URL` in `.env` and leave the bundled database service out.
