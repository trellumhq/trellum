#!/usr/bin/env bash
# Go back to the previous version after an upgrade went wrong.
#
#   scripts/rollback.sh ./upgrade-backups/pre-v1.2.3-20260816-120000.dump v1.2.2
#   scripts/rollback.sh <dump> <previous-image-or-version> --yes
#
# THIS RESTORES THE DATABASE FROM THE DUMP. Everything written since that dump
# was taken is discarded — every report run, every audit row, every setting
# changed in the meantime.
#
# That is not over-caution, it is the shape of the problem: migrations are
# forward-only. Putting the old image back on a database whose schema has moved
# gives you old code reading new columns, which fails in ways that look like
# corruption. The dump is the only way back, so the data written after it is the
# price of going back at all.
#
# If the upgrade merely looks wrong rather than IS wrong, prefer rolling
# forward: fix the fault, upgrade again. Reach for this when the new version
# cannot serve.
set -euo pipefail

cd "$(dirname "$0")/.."

DUMP="${1:?usage: rollback.sh <dump-file> <previous-image-or-version> [--yes]}"
PREVIOUS="${2:?usage: rollback.sh <dump-file> <previous-image-or-version> [--yes]}"
ASSUME_YES=""
[ "${3:-}" = "--yes" ] && ASSUME_YES=1

REGISTRY="${TRELLUM_REGISTRY:-ghcr.io}"
REPO="${TRELLUM_REPO:-trellum}"
COMPOSE="${TRELLUM_COMPOSE:-docker compose}"
HEALTH_TIMEOUT="${TRELLUM_HEALTH_TIMEOUT:-180}"

# Accept either a full image reference or a bare version.
case "$PREVIOUS" in
    *:*|*/*) PREVIOUS_IMAGE="$PREVIOUS" ;;
    *)       PREVIOUS_IMAGE="${REGISTRY}/${REPO}:${PREVIOUS}" ;;
esac

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f .env ] || die ".env not found. Run this from the deployment directory."
[ -f "$DUMP" ] || die "dump not found: $DUMP"

docker run --rm -i -v "$(cd "$(dirname "$DUMP")" && pwd)":/b postgres:16-alpine \
    pg_restore --list "/b/$(basename "$DUMP")" > /dev/null \
    || die "that dump is unreadable. Do not proceed — find a good backup first."

say "Rolling back"
echo "    image: ${PREVIOUS_IMAGE}"
echo "    dump:  ${DUMP}"
warn "This DISCARDS every database change made since that dump was taken."

if [ -z "$ASSUME_YES" ]; then
    printf '\nType the word ROLLBACK to continue: '
    read -r reply
    [ "$reply" = "ROLLBACK" ] || { echo "Aborted."; exit 1; }
fi

# Stop everything that writes before restoring under it.
say "Stopping application containers"
$COMPOSE stop web worker coordinator runner 2>/dev/null || $COMPOSE stop web worker
$COMPOSE up -d db
until $COMPOSE exec -T db pg_isready -U "${POSTGRES_USER:-trellum}" >/dev/null 2>&1; do sleep 2; done

say "Restoring the database"
# --clean --if-exists so the restore is not fighting the newer schema's objects.
$COMPOSE exec -T db pg_restore -U "${POSTGRES_USER:-trellum}" -d "${POSTGRES_DB:-trellum_portal}" \
    --clean --if-exists --no-owner < "$DUMP" \
    || warn "pg_restore reported errors. Some are normal with --clean on a
    database whose schema moved (DROP of an object that no longer exists).
    Verify below before trusting it."

say "Pinning ${PREVIOUS_IMAGE}"
if grep -qE '^TRELLUM_IMAGE=' .env; then
    sed -i.bak "s|^TRELLUM_IMAGE=.*|TRELLUM_IMAGE=${PREVIOUS_IMAGE}|" .env
else
    printf '\nTRELLUM_IMAGE=%s\n' "$PREVIOUS_IMAGE" >> .env
fi

say "Starting the previous version"
$COMPOSE up -d

say "Verifying"
# Parsed inside the container: a Docker host is not required to have python,
# jq or curl. See the same note in upgrade.sh.
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
    [ "$(date +%s)" -lt "$deadline" ] \
        || die "web did not come back within ${HEALTH_TIMEOUT}s. The database has
     been restored; investigate the container logs before retrying."
    sleep 3
done

echo "    version: $(probe /api/version version)"
echo "    schema:  $(probe /api/version schema)"

$COMPOSE exec -T web python manage.py doctor || warn "doctor reports a problem."

say "Rolled back to ${PREVIOUS_IMAGE}"
echo "    The schema now matches the dump, so this is a consistent state."
echo "    Work written between the dump and the rollback is gone by design."
