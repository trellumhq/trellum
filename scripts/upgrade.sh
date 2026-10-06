#!/usr/bin/env bash
# Upgrade a running trellum to a new version, safely and reversibly.
#
#   scripts/upgrade.sh v1.2.3
#   scripts/upgrade.sh v1.2.3 --yes        # no confirmation prompt
#
# Why this exists rather than the two lines that used to be in OPERATIONS.md
# (`docker pull && docker compose up -d`):
#
#   * Compose starts the one-shot `migrate` service while the OLD web and
#     worker containers are still serving. For the length of the migration the
#     running code and the schema disagree, and nobody chose that.
#   * Nothing took a backup first. Migrations are forward-only, so an upgrade
#     that goes wrong after the schema has moved cannot be undone by putting
#     the old image back — it needs the dump that was never taken.
#   * Nothing checked afterwards. `docker compose up -d` returning 0 means the
#     containers started, not that the schema applied or that the new version
#     is the one now serving.
#
# The order below is the point: stop serving, migrate, start the new version,
# then prove it. Downtime is a small number of seconds longer than the
# careless version, in exchange for never running new schema under old code.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:?usage: upgrade.sh <version> [--yes]   e.g. upgrade.sh v1.2.3}"
ASSUME_YES=""
[ "${2:-}" = "--yes" ] && ASSUME_YES=1

REGISTRY="${TRELLUM_REGISTRY:-ghcr.io}"
REPO="${TRELLUM_REPO:-trellum}"
TARGET_IMAGE="${TRELLUM_TARGET_IMAGE:-${REGISTRY}/${REPO}:${VERSION}}"
COMPOSE="${TRELLUM_COMPOSE:-docker compose}"
BACKUP_DIR="${TRELLUM_BACKUP_DIR:-./upgrade-backups}"
HEALTH_TIMEOUT="${TRELLUM_HEALTH_TIMEOUT:-180}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f .env ] || die ".env not found. Run this from the deployment directory."

# ── 0. What are we coming from? ────────────────────────────────────────────
PREVIOUS_IMAGE="$(grep -E '^TRELLUM_IMAGE=' .env | tail -1 | cut -d= -f2- || true)"
PREVIOUS_VERSION="$($COMPOSE exec -T web python -c \
    'import trellum_portal; print(trellum_portal.__version__)' 2>/dev/null || echo "unknown")"

say "Upgrading trellum"
echo "    from: ${PREVIOUS_VERSION} (${PREVIOUS_IMAGE:-image unpinned in .env})"
echo "      to: ${VERSION} (${TARGET_IMAGE})"

if [ -z "$ASSUME_YES" ]; then
    printf '\nProceed? [y/N] '
    read -r reply
    case "$reply" in [yY]*) ;; *) echo "Aborted."; exit 1 ;; esac
fi

# ── 1. Preflight ───────────────────────────────────────────────────────────
# Upgrading an instance that is already unwell turns one problem into two.
say "Preflight"
if ! $COMPOSE exec -T web python manage.py doctor; then
    die "doctor reported a problem. Fix it before upgrading, or the upgrade
     will be blamed for it afterwards."
fi

# ── 2. Backup, verified ────────────────────────────────────────────────────
# An unverified dump is not a backup. pg_dump can exit 0 having written a
# truncated file if the disk fills, and you find out at restore time.
say "Backing up"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="${BACKUP_DIR}/pre-${VERSION}-${STAMP}.dump"

$COMPOSE exec -T db pg_dump -U "${POSTGRES_USER:-trellum}" -d "${POSTGRES_DB:-trellum_portal}" \
    --format=custom > "$DUMP" || die "pg_dump failed; nothing has changed yet."

docker run --rm -i -v "$(cd "$(dirname "$DUMP")" && pwd)":/b postgres:16-alpine \
    pg_restore --list "/b/$(basename "$DUMP")" > /dev/null \
    || die "the dump is unreadable; refusing to migrate on top of a backup
     that would not restore. Nothing has changed yet."

echo "    $DUMP ($(du -h "$DUMP" | cut -f1), verified readable)"

# ── 3. Fetch before stopping ───────────────────────────────────────────────
# A slow pull should not extend the outage, and a pull that fails should not
# leave the stack down.
say "Fetching ${TARGET_IMAGE}"
docker pull "$TARGET_IMAGE" || die "could not pull the image. Nothing has changed."

# ── 4. The actual switch ───────────────────────────────────────────────────
say "Stopping web and worker"
# The worker drains: running report builds finish (compose gives it 10m).
$COMPOSE stop web worker coordinator runner 2>/dev/null || $COMPOSE stop web worker

# Pin the new image before migrating, so `migrate` runs the NEW code's
# migrations against the database with nothing else connected to it.
if grep -qE '^TRELLUM_IMAGE=' .env; then
    sed -i.bak "s|^TRELLUM_IMAGE=.*|TRELLUM_IMAGE=${TARGET_IMAGE}|" .env
else
    printf '\nTRELLUM_IMAGE=%s\n' "$TARGET_IMAGE" >> .env
fi

say "Migrating"
if ! $COMPOSE run --rm migrate; then
    warn "Migration failed. The database may be partially migrated."
    warn "Restore and go back with:"
    warn "  scripts/rollback.sh ${DUMP} ${PREVIOUS_IMAGE:-<previous image>}"
    die "migration failed"
fi

say "Starting ${VERSION}"
$COMPOSE up -d

# ── 5. Prove it ────────────────────────────────────────────────────────────
# "The containers started" is not "the upgrade worked".
#
# Everything that parses runs INSIDE the app container. A portal host is a
# Docker host: it is not required to have python, jq or curl, and assuming one
# is how an upgrade script breaks on the machine it was written for. The
# container has a Python interpreter by definition.
say "Verifying"

probe() {
    $COMPOSE exec -T web python -c "
import json, sys, urllib.request
try:
    body = urllib.request.urlopen('http://127.0.0.1:8050' + sys.argv[1], timeout=10).read()
except Exception as exc:
    sys.exit('unreachable: %s' % exc)
if len(sys.argv) > 2:
    print(json.loads(body)[sys.argv[2]])
" "$@" 2>/dev/null
}

deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
until probe /healthz >/dev/null 2>&1; do
    [ "$(date +%s)" -lt "$deadline" ] || die "web never became healthy within
     ${HEALTH_TIMEOUT}s. Roll back with:
       scripts/rollback.sh ${DUMP} ${PREVIOUS_IMAGE:-<previous image>}"
    sleep 3
done
echo "    /healthz ok"

# The published port is what users actually reach, so check it too when the
# host has curl — a container that is healthy but unreachable is still an
# outage. Absence of curl is not a failure.
if command -v curl >/dev/null 2>&1; then
    if curl -sf "http://127.0.0.1:${TRELLUM_PORT:-8050}/healthz" >/dev/null 2>&1; then
        echo "    published port ${TRELLUM_PORT:-8050} ok"
    else
        warn "container is healthy but port ${TRELLUM_PORT:-8050} did not answer on the host"
    fi
fi

GOT_VERSION="$(probe /api/version version)" || die "/api/version did not answer"
GOT_SCHEMA="$(probe /api/version schema)"

echo "    version: ${GOT_VERSION}"
echo "    schema:  ${GOT_SCHEMA}"

EXPECTED="${VERSION#v}"
[ "$GOT_VERSION" = "$EXPECTED" ] || die "expected version ${EXPECTED}, got ${GOT_VERSION}.
     The old image may still be running. Roll back with:
       scripts/rollback.sh ${DUMP} ${PREVIOUS_IMAGE:-<previous image>}"

[ "$GOT_SCHEMA" = "ok" ] || die "schema reports '${GOT_SCHEMA}', not 'ok' — the
     database is not in step with this image. Roll back with:
       scripts/rollback.sh ${DUMP} ${PREVIOUS_IMAGE:-<previous image>}"

$COMPOSE exec -T web python manage.py doctor || warn "doctor reports a problem
    after the upgrade — the version and schema are correct, so this is likely
    an unrelated service (worker still starting, backup age). Check /system."

say "Upgraded to ${VERSION}"
echo "    Backup kept at ${DUMP}"
echo "    To go back:  scripts/rollback.sh ${DUMP} ${PREVIOUS_IMAGE:-<previous image>}"
echo
echo "    Keep that backup until you are satisfied. Migrations are forward-only:"
echo "    once the schema has moved, putting the old image back is not enough."
