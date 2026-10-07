# Deploy on one server

This is the complete production shape for one server: web, database, persistent
data, and a combined worker share one Docker host. Trellum is distributed under
AGPL-3.0-only. You can use the published release images or build the images
from the matching source tag:

- **Pull the release images** (fastest — nothing builds on your machine). The
  Compose file and operational scripts come from the same tagged source
  checkout, so the install has everything it needs.
- **Build from source** (AGPL-3.0-only; no image registry required). Compose
  builds both images from the checkout.

Either way, also check the requirements from the
[overview](/docs/latest/install/overview/) first.

The default storage needs Docker Engine 26+. Docker Engine 25 can use the
[host-folder setup](#docker-25-with-host-folder-storage) below with report
isolation enabled. Configure that setup before any build-and-start command.

## Option A: Pull the published images

Start from the source tag matching the published images. The checkout includes
the Compose file, backup and upgrade scripts, and other files needed to operate
the installation.

Set `TRELLUM_VERSION` in `.env` to the exact release tag used by these commands;
the Compose image settings use that same value.

### 1. Get the matching Compose files

```bash
TRELLUM_VERSION=v0.3.0
git clone --depth 1 --branch "$TRELLUM_VERSION" https://github.com/trellumhq/trellum.git
cd trellum
cp .env.example .env
cat >> .env <<EOF
TRELLUM_VERSION=$TRELLUM_VERSION
TRELLUM_IMAGE=ghcr.io/trellumhq/trellum:$TRELLUM_VERSION
TRELLUM_RUNNER_IMAGE=ghcr.io/trellumhq/trellum-runner:$TRELLUM_VERSION
EOF
```

Fill in the other required settings in [step 2](#2-fill-in-the-settings), then
verify the images as described in
[Verify what you received](/docs/latest/install/verify-images/).

## Option B: Build from source

The source build is the complete product. `docker-compose.yml` already
knows how to build the image itself; there is no separate Dockerfile step.

### Clone and prepare `.env`

```bash
TRELLUM_VERSION=v0.3.0
git clone --depth 1 --branch "$TRELLUM_VERSION" https://github.com/trellumhq/trellum.git
cd trellum
cp .env.example .env
```

### Fill in the settings (below), then build and start

Run `bash scripts/check-docker.sh` before starting (or add `--bind-data` for
the host-folder setup). Fill in `.env` using the settings required for this
install path from
[step 2](#2-fill-in-the-settings)
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

Open `.env` and set these required values. Everything else has a working default
and is covered in the
[configuration reference](/docs/latest/install/configuration/).

| Setting | What it is |
|---|---|
| `TRELLUM_IMAGE` | Which released version to run. Pin an exact version, so a restart can never silently move you to a different one. |
| `TRELLUM_RUNNER_IMAGE` | The matching report-build sandbox image. Use the runner image with the same release tag as `TRELLUM_IMAGE`. |
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

Check the daemon before changing the stack. For default named-volume storage:

```bash
bash scripts/check-docker.sh
```

For the host-folder setup, use `bash scripts/check-docker.sh --bind-data`.

For Option A, pull and start the release images without building them locally:

```bash
docker compose pull
docker compose up -d --no-build
```

For Option B, the `docker compose up -d --build` command in that section has
already started the stack.

Services start in order: the database, then a one-shot schema migration, then
the sandbox image, then the web server and the report worker.

### Docker 25 with host-folder storage

Use matching deployment files and images from a release containing this feature.
Adding this override to an earlier image does not change its worker version check.

This Linux setup keeps report builds in isolated containers. Each build gets
only its own directories, its studio project, and authorized shared data.
The worker accepts API 1.44 (Docker 25) for bind mounts; named volumes still
need API 1.45 (Docker 26) for their subdirectory mounts. Use a maintained,
patched engine; versions older than Docker 25 are not supported.

For a **new installation**, prepare an empty directory on the Docker daemon's
host, owned by the application's user. Use a separate directory for each
independent installation:

```bash
bash scripts/check-docker.sh --bind-data
sudo install -d -o 10001 -g 10001 -m 0750 /srv/trellum/data
```

Add these settings to `.env`:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.bind-data.yml
TRELLUM_DATA_HOST_PATH=/srv/trellum/data
```

Leave `TRELLUM_DATA_VOLUME` unset. The override clears it on workers and
runners so they discover the host folder. `TRELLUM_DATA_HOST_PATH` must be
an absolute path on the daemon host, not a path on a remote client machine.
If the host enforces SELinux, ensure its policy allows all Trellum containers
to share this directory; do not disable SELinux to work around permissions.

Verify the merged configuration with `docker compose --profile split config`
before starting. Every `/data` mount must be a bind to the same directory;
the backup service's mount remains read-only. The override preserves the
backup volume and the worker/runner Docker sockets. It refuses to create a
missing host directory automatically.

Then continue with the normal pull/build and startup commands. Keeping
`COMPOSE_FILE` in `.env` ensures later backups, upgrades and restarts keep using
this storage. Explicit `-f` arguments override that setting: if you use them,
include `-f docker-compose.bind-data.yml` as well. With an external database,
append `:docker-compose.external-db.yml` to `COMPOSE_FILE`.

#### Moving an existing named volume

Changing the mount does **not** move existing files. Before adding the override:

1. Verify a current database backup and data archive, following
   [Backups](/docs/latest/operations/backups-and-restore/). Keep the existing named volume.
2. Record the volume backing `/data` from the existing web container:
   `docker inspect "$(docker compose ps -q web)" --format '{{json .Mounts}}'`.
   Use the actual volume name; a custom Compose project changes its prefix.
3. Stop every service that can write data, including workers and runners on
   other hosts. Drain active builds first. Use `docker compose --profile split stop`
   for a single-host installation. Do not use `down -v`.
4. Prepare an **empty** destination directory. Copy the recorded volume into
   it while stopped, preserving ownership, permissions and symlinks. For
   example, replace `ACTUAL_DATA_VOLUME` below and use an already available
   Trellum image that includes `cp` and `diff`:

   ```bash
   sudo install -d -o 10001 -g 10001 -m 0750 /srv/trellum/data
   docker run --rm --user 0 --entrypoint sh \
     --mount type=volume,src=ACTUAL_DATA_VOLUME,dst=/from,readonly \
     --mount type=bind,src=/srv/trellum/data,dst=/to \
     YOUR_TRELLUM_IMAGE -ec 'cp -a /from/. /to/; diff -qr /from /to'
   ```

5. After verifying the copy and its ownership, add the `.env` settings above,
   run the compatibility check and inspect the merged Compose configuration.
   Start with an image containing this compatibility change, then verify
   `manage.py doctor`, a report build and publication, and a new backup.

Retain the old volume until validation finishes. If reverting after writes
have resumed, stop the services and reconcile the newer files first; simply
pointing back at the old volume would discard those newer writes.

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
