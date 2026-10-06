# Install with Docker Compose

This is the supported way to run {{BRAND}}. The public installation is licensed
under AGPL-3.0-only and has no activation or subscription step. Build it from the source
checkout below, or use an image you built and verified yourself. Both produce
the same install:

- **A verified image** (fastest — nothing builds on your machine).
  Use an image you built and verified yourself.
- **Build it yourself from source** (AGPL-3.0-only, no registry required — this is what
  `git clone` gives you today). See
  [Option B](#option-b-build-from-source).

Either way, also check the requirements from the
[overview](/docs/latest/install/overview/) first.

## Option A: Pull a verified image

Use an image reference you have verified and set it in `.env`.

### 1. Download the deployment files

```bash
mkdir trellum && cd trellum
curl -O <verified-source>/docker-compose.yml
curl -O <verified-source>/.env.example
cp .env.example .env
```

## Option B: Build from source

The source build is the complete product. `docker-compose.yml` already
knows how to build the image itself; there is no separate Dockerfile step.

### Clone and prepare `.env`

```bash
git clone https://github.com/trellumhq/trellum.git
cd trellum
cp .env.example .env
```

### Fill in the settings (below), then build and start

Fill in `.env` using the same seven values as [step 2](#2-fill-in-the-settings)
below, then build and start with:

```bash
GIT_SHA=$(git rev-parse HEAD) \
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD) \
BUILD_TIME=$(date -u +%FT%TZ) \
  docker compose up -d --build
```

The `GIT_SHA` / `GIT_BRANCH` / `BUILD_TIME` variables are optional but worth
setting: without them a self-built image reports its own version as
`unknown` on `/api/version` and in `manage.py doctor`, which is the first
thing an upgrade or support conversation asks for. Leave `TRELLUM_IMAGE`
unset — the default (`trellum:dev`) is the tag `docker compose build` already
produces.

**Running a second, independent instance on the same host** (a staging
build, or validating a change before it replaces production)? Give it its
own compose project name, port and image tags so the two never share
containers, volumes or images:

```bash
docker compose -p trellum-staging up -d --build
# .env: TRELLUM_PORT=8060, TRELLUM_IMAGE=trellum-staging:dev,
#       TRELLUM_RUNNER_IMAGE=trellum-staging-runner:dev
```

`-p` always overrides the project name compose would otherwise pick, so this
is safe to run from the same checkout as an existing instance. Tear the extra
instance down with `docker compose -p trellum-staging down -v` — never bare
`down` from a directory that also serves a production instance.

## 2. Fill in the settings

Open `.env` and set these seven values. Everything else has a working default
and is covered in the
[configuration reference](/docs/latest/install/configuration/).

| Setting | What it is |
|---|---|
| `TRELLUM_IMAGE` | Which released version to run. Pin an exact version, so a restart can never silently move you to a different one. |
| `PORTAL_BASE_URL` | The public address people will visit, e.g. `https://bi.example.com`. Used to build links in invitation and password-reset emails, and to decide whether cookies are marked secure. |
| `ALLOWED_HOSTS` | The hostnames the portal will answer on. A request arriving under any other name is refused, which is what stops someone pointing their own domain at your instance. |
| `POSTGRES_PASSWORD` | The password for the bundled database. Any strong value; you will not type it again. |
| `SECRET_ENCRYPTION_KEY` | Encrypts the credentials the portal stores for you — data source passwords, repository tokens, SSO secrets. |
| `SESSION_SECRET_KEY` | Signs session cookies and the one-time links in invitation and password-reset emails, so they cannot be forged. |
| `DOCKER_GID` | A number identifying the group on your machine that is allowed to use Docker. The worker runs each report build in its own throwaway container, so it needs that permission. See [finding it](#docker-gid) below. |

Generate the two keys with:

```bash
# SECRET_ENCRYPTION_KEY
python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

# SESSION_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Finding DOCKER_GID {#docker-gid}

On most Linux hosts:

```bash
getent group docker | cut -d: -f3
```

That prints a number — often `988`, `999` or `130`. Put that number in `.env`.

If it prints nothing, the group has another name or does not exist. Ask the
Docker socket itself which group owns it, which is always correct:

```bash
stat -c '%g' /var/run/docker.sock
```

Platform notes:

- **Docker Desktop (macOS, Windows)** — there is no host `docker` group. Use
  the `stat` command above inside the Docker VM, or simply set `DOCKER_GID=0`;
  the socket is owned by root there. Docker Desktop is a development setup, not
  how you should run this in production.
- **Rootless Docker** — use `stat -c '%g' $XDG_RUNTIME_DIR/docker.sock`.
- **Podman** — use the socket path from `podman info --format '{{.Host.RemoteSocket.Path}}'`.

!!! note
    We ask for this rather than working it out ourselves because Docker grants
    group membership when it creates the container, before any of our code
    runs — and the portal deliberately starts as an unprivileged user, so it
    cannot give itself the permission afterwards.

!!! danger
    Back up `SECRET_ENCRYPTION_KEY` somewhere separate from your database
    backups. Lose it and every stored credential becomes unreadable; keep both
    in one place and a single leak exposes everything.

## 3. Start it

```bash
docker compose pull
docker compose up -d
```

If you built from source (Option B), you already started the stack with
`docker compose up -d --build` — there is nothing further to run here.

Services start in order: the database, then a one-shot schema migration, then
the sandbox image, then the web server and the report worker.

## 4. Harden the sandbox network

Report builds run your code in throwaway containers. On a cloud VM this step
is **mandatory** — it blocks those containers from the instance metadata
endpoint and from services on the host:

```bash
sudo docker/harden-sandbox-net.sh
```

!!! warning
    Skipping this on a cloud VM leaves the instance metadata endpoint
    reachable from report code. Run it before the first build.

See [Report sandboxing](/docs/latest/install/report-sandboxing/) for exactly
what a build container can and cannot reach.

## 5. First run

Open your address. A portal with no accounts sends you straight to a setup
wizard:

1. **Welcome** — what the next few steps do.
2. **Readiness** — the same checks as `manage.py doctor`. A failing check is
   shown with what to do about it, but never blocks setup — a portal whose
   worker hasn't started yet still installs.
3. **Organization** — name and slug. The slug is permanent: it keys the
   directory tree the organization's data lives under.
4. **Your account** — the instance operator, and an admin of the organization
   from the previous step.
5. **Instance settings** (skippable) — instance name, public URL, and mail
   delivery, with a button to send a test email. Everything here stays
   editable afterward under **System**.
6. **Done** — the hub, with a getting-started checklist.

The wizard is open to whoever reaches a user-less instance first, so set the
portal up before exposing its port — or keep it behind your reverse proxy
until step 4 is done. It closes permanently the moment an account exists.

For an unattended install, skip the wizard entirely:

```bash
docker compose exec web python manage.py bootstrap \
  --email ops@example.com --password '...' --org-name "Acme" --org-slug acme
```

Then verify the install end to end:

```bash
docker compose exec web python manage.py doctor
```

`doctor` checks database connectivity, encryption keys, the data volume, the
sandbox image, and the worker heartbeat, and prints what to fix when something
is off.

!!! note
    A **`retention: cleanup has never run`** failure right after a fresh
    install is expected, not a problem: cleanup runs on the nightly
    `CLEANUP_CRON` schedule, so a brand-new instance goes green on that check
    after its first scheduled run. To clear it immediately instead of
    waiting, run `docker compose exec web python manage.py cleanup` once by
    hand (plain, not `--dry-run` — a dry run deliberately does not record
    that cleanup ran, so it will not clear this check).

## Next

- [Configuration reference](/docs/latest/install/configuration/) — every setting
- [Verify what you received](/docs/latest/install/verify-images/) — signatures and SBOM
- [Connecting a repository](/docs/latest/portal/connecting-a-repository/) — your first reports
