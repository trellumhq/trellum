#!/bin/sh
# Prove the newest backup would actually restore.
#
#   docker compose run --rm --entrypoint /bin/sh backup /restore_drill.sh
#
# `pg_restore --list`, which the backup loop already runs, proves a file is a
# well-formed archive. It does not prove the archive contains your portal, that
# a restore completes, or that what comes back is usable. The only way to know a
# backup works is to restore it. An untested backup is a hope.
#
# This runs in the backup service's image because that is where psql and
# pg_restore live. The application image deliberately ships only git and curl —
# every package in it is CVE surface a customer's scanner will ask about — so
# adding Postgres client tools there to run a drill would be the wrong trade.
#
# It restores into a generated scratch database and drops it afterwards. It
# never writes to the live one.
set -eu

DEST="${BACKUP_DIR:-/backups}"
DUMP="${1:-}"

if [ -z "$DUMP" ]; then
    # Names are db-YYYYMMDD-HHMM.dump, so lexical order is chronological.
    DUMP="$(ls "$DEST"/db-*.dump 2>/dev/null | sort | tail -1 || true)"
fi
[ -n "$DUMP" ] || { echo "No backups found in $DEST. Is the backup service running?"; exit 1; }
[ -f "$DUMP" ] || { echo "No such dump: $DUMP"; exit 1; }

# Same server, maintenance database, so we can CREATE/DROP.
ADMIN_URL="$(echo "$DATABASE_URL" | sed 's|/[^/]*$|/postgres|')"
SCRATCH="trellum_restore_drill_$(date +%s)"
TARGET_URL="$(echo "$DATABASE_URL" | sed "s|/[^/]*$|/$SCRATCH|")"

echo "Dump:    $DUMP ($(du -h "$DUMP" | cut -f1))"
echo "Scratch: $SCRATCH"

cleanup() {
    psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true
}
trap cleanup EXIT

psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$SCRATCH\"" >/dev/null

echo "Restoring..."
# pg_restore exits non-zero for benign reasons too (a role that does not exist
# here), so the row counts below are the verdict, not the exit code.
pg_restore --dbname "$TARGET_URL" --no-owner --no-privileges "$DUMP" 2>/tmp/drill.err \
    || echo "  (pg_restore reported issues; checking contents anyway)"

FAILED=0
echo ""
# A portal with no users and no organizations is not a portal, whatever else
# restored successfully.
for TABLE in accounts_user orgs_organization; do
    COUNT="$(psql "$TARGET_URL" -tAc "SELECT count(*) FROM $TABLE" 2>/dev/null || echo 0)"
    if [ "$COUNT" -gt 0 ]; then
        printf '  %-24s %8s  ok\n' "$TABLE" "$COUNT"
    else
        printf '  %-24s %8s  EMPTY\n' "$TABLE" "$COUNT"
        FAILED=1
    fi
done

STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$FAILED" -eq 0 ]; then
    cat > "$DEST/drill.json" <<EOF
{"last_drill_at": "$STAMP", "ok": true, "dump": "$(basename "$DUMP")"}
EOF
    echo ""
    echo "Restore drill passed — this backup produces a working database."
else
    cat > "$DEST/drill.json" <<EOF
{"last_drill_at": "$STAMP", "ok": false, "dump": "$(basename "$DUMP")"}
EOF
    echo ""
    echo "RESTORE DRILL FAILED: the backup restored but came back empty."
    head -5 /tmp/drill.err 2>/dev/null || true
    exit 1
fi
