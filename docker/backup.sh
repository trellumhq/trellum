#!/bin/sh
# Nightly backup loop (compose service `backup`).
#
# Produces, under $BACKUP_DIR (default /backups):
#   db-YYYYMMDD-HHMM.dump         pg_dump custom format, VERIFIED readable
#   studios-YYYYMMDD-HHMM.tar.gz  studio state (uploads, output, project config;
#                                 git checkouts + caches excluded — reproducible)
#   orgs-YYYYMMDD-HHMM.tar.gz     organization-level uploaded data-source files
#   status.json                   what the last run did, read by the health check
#
# Three things this is careful about, each learned the expensive way by
# somebody:
#
#   * A dump is not a backup until something has read it back. pg_dump can exit
#     0 having written a truncated file when the disk fills, and you find out at
#     restore time. Every dump is checked with `pg_restore --list` and deleted
#     if it fails, so a bad dump never displaces a good one in retention.
#   * Backups on the volume they protect are not backups. $BACKUP_DIR is its own
#     volume, and BACKUP_REMOTE_TARGET copies off the host entirely. If it is
#     unset this says so on every run — quietly having no disaster recovery is
#     worse than loudly having none.
#   * status.json exists so `manage.py doctor` and /system can fail when backups
#     silently stopped. A backup system nobody is watching is a backup system
#     that stopped six weeks ago.
set -eu

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DEST="${BACKUP_DIR:-/backups}"
REMOTE="${BACKUP_REMOTE_TARGET:-}"
mkdir -p "$DEST"

# Written after every attempt, success or failure, so "the backup container
# died three weeks ago" is visible rather than inferred from an absence.
write_status() {
    cat > "$DEST/status.json" <<EOF
{
  "last_attempt_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "last_success_at": ${1:-null},
  "ok": ${2:-false},
  "detail": "${3:-}",
  "keep_days": ${KEEP_DAYS},
  "off_host_copy": $([ -n "$REMOTE" ] && echo true || echo false)
}
EOF
}

LAST_SUCCESS=null
[ -f "$DEST/status.json" ] && LAST_SUCCESS="$(
    sed -n 's/.*"last_success_at": \([^,]*\).*/\1/p' "$DEST/status.json" | head -1
)"
[ -z "$LAST_SUCCESS" ] && LAST_SUCCESS=null

if [ -z "$REMOTE" ]; then
    echo "[backup] WARNING: BACKUP_REMOTE_TARGET is not set. Backups stay on this"
    echo "[backup]          host, so losing the host loses them too. Set it to an"
    echo "[backup]          rsync/ssh target you control."
fi

while true; do
    STAMP="$(date +%Y%m%d-%H%M)"
    echo "[backup] $STAMP starting"
    DUMP="$DEST/db-$STAMP.dump"

    if ! pg_dump "$DATABASE_URL" --format=custom --file="$DUMP"; then
        echo "[backup] $STAMP FAILED: pg_dump errored"
        write_status "$LAST_SUCCESS" false "pg_dump failed"
        sleep "$INTERVAL"
        continue
    fi

    # The step that turns a file into a backup.
    if ! pg_restore --list "$DUMP" > /dev/null 2>&1; then
        echo "[backup] $STAMP FAILED: the dump is not readable; deleting it so it"
        echo "[backup]         cannot displace a good one during retention."
        rm -f "$DUMP"
        write_status "$LAST_SUCCESS" false "dump failed verification"
        sleep "$INTERVAL"
        continue
    fi

    tar -czf "$DEST/studios-$STAMP.tar.gz" \
        --exclude='*/output/.query_cache' \
        --exclude='*/repo' \
        -C /data studios 2>/dev/null || [ ! -d /data/studios ]

    # Org-level uploads are not rebuildable from git either — a report that
    # reads a shared 200 MB extract has no other copy of it.
    tar -czf "$DEST/orgs-$STAMP.tar.gz" \
        -C /data orgs 2>/dev/null || [ ! -d /data/orgs ]

    if [ -n "$REMOTE" ]; then
        # Best effort: a failed copy must not lose the local backup we just
        # verified, but it does mean this run is not disaster recovery.
        if rsync -a --delete "$DEST/" "$REMOTE/" 2>&1; then
            echo "[backup] $STAMP copied to $REMOTE"
        else
            echo "[backup] $STAMP WARNING: copy to $REMOTE failed; the local"
            echo "[backup]         backup is good but there is no off-host copy."
            write_status "$LAST_SUCCESS" false "off-host copy to $REMOTE failed"
            sleep "$INTERVAL"
            continue
        fi
    fi

    # Retention runs only after a verified success, so a run of failures can
    # never age out the last good backup.
    find "$DEST" -name '*.dump' -mtime +"$KEEP_DAYS" -delete
    find "$DEST" -name '*.tar.gz' -mtime +"$KEEP_DAYS" -delete

    LAST_SUCCESS="\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
    write_status "$LAST_SUCCESS" true "verified"
    echo "[backup] $STAMP done, verified ($(du -h "$DUMP" | cut -f1))"
    sleep "$INTERVAL"
done
