#!/bin/sh
# Role dispatcher. Waits for Postgres, then execs the requested role so
# signals (SIGTERM drain) reach the real process as PID 1.
set -e

wait_for_db() {
    echo "waiting for database..."
    python - <<'PY'
import os, sys, time
import psycopg

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit("DATABASE_URL is not set")
for attempt in range(60):
    try:
        with psycopg.connect(url, connect_timeout=3):
            sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit("database never became reachable")
PY
}

case "$1" in
    web)
        wait_for_db
        # The timeout has to cover a large data-source upload streaming in on a
        # slow link, not just how long a view takes to think.
        exec gunicorn trellum_portal.wsgi:application \
            --bind 0.0.0.0:8050 \
            --workers "${GUNICORN_WORKERS:-2}" \
            --threads "${GUNICORN_THREADS:-8}" \
            --timeout "${GUNICORN_TIMEOUT:-300}" \
            --access-logfile - \
            --error-logfile -
        ;;
    worker)
        # Role comes from TRELLUM_RUNNER_ROLE (default "all" — coordinator and
        # runner in one process, which is the single-node topology).
        wait_for_db
        exec python manage.py runworker
        ;;
    coordinator)
        wait_for_db
        exec python manage.py runworker --role=coordinator
        ;;
    runner)
        wait_for_db
        exec python manage.py runworker --role=runner
        ;;
    migrate)
        wait_for_db
        exec python manage.py migrate --no-input
        ;;
    *)
        exec "$@"
        ;;
esac
