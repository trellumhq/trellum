"""SQL for the Live Ops Monitor -- the live-queries teaching report.

Two declared live queries share one FilterBar (severity, service, actor,
date range): OPS_EVENTS returns the matching raw event rows and drives a
KPI row + two charts + a table; TOP_ACTORS answers a grouped top-N the raw
rows can't cheaply answer client-side, sharing the service + date-range
filters via FilterBar.propagate_to (one filter change, two queries).

Both run against the SAME fact_user_events table the rest of the demo
warehouse seeds (see demo/tools/make_fixtures.py) -- the "ops" framing on
top of it (severity/service/actor) is a deterministic reinterpretation of
event/platform/user_id, not a new fixture table, so severity filtering is
STABLE across repeated live calls (same row, same bucket, every time) rather
than randomized per request.
"""

# `n`/`is_warn`/`is_error` are 1/0 helper columns: BarChart sums its `y`
# column per `x` group (no separate "count" aggregation mode), so `n` turns
# a per-service SUM into a per-service COUNT; the KpiRow's "Warnings"/
# "Errors" cards do the same trick with `agg: "sum"` over is_warn/is_error.
# `day` truncates `ts` to a date for both the date_range filter binding and
# the daily LineChart -- comparing on it (rather than the full timestamp)
# is also what keeps :since/:until (day-granularity `date` params) correct
# at the day boundary.
OPS_EVENTS = """
WITH events AS (
    SELECT
        ts,
        substr(ts, 1, 10) AS day,
        CAST(user_id AS TEXT) AS actor,
        user_id,
        platform AS service,
        CASE
            WHEN event = 'level_lose' AND user_id % 7 = 0 THEN 'error'
            WHEN event = 'level_lose' THEN 'warn'
            ELSE 'info'
        END AS severity,
        event || ' — ' || detail AS message
    FROM fact_user_events
)
SELECT ts, day, actor, service, severity, message,
       1 AS n,
       CASE WHEN severity = 'warn' THEN 1 ELSE 0 END AS is_warn,
       CASE WHEN severity = 'error' THEN 1 ELSE 0 END AS is_error
FROM events
WHERE day >= :since AND day <= :until
  AND (:severity = 'all' OR severity = :severity)
  AND (:service = 'all' OR service = :service)
  AND (:actor_id = 0 OR user_id = :actor_id)
ORDER BY ts DESC
LIMIT 300
"""

# A grouped top-N the raw OPS_EVENTS rows can't cheaply answer client-side
# (it needs every matching row across the whole window, not just the most
# recent 300) -- the textbook case for a second live query rather than a
# client-side aggregation of the first one's response.
TOP_ACTORS = """
SELECT CAST(user_id AS TEXT) AS actor,
       COUNT(*)              AS events
FROM fact_user_events
WHERE substr(ts, 1, 10) BETWEEN :since AND :until
  AND (:service = 'all' OR platform = :service)
GROUP BY user_id
ORDER BY events DESC
LIMIT 15
"""

# Context the artifact CAN afford to compile: the whole log's daily volume.
DAILY_VOLUME = """
SELECT substr(ts, 1, 10) AS event_date,
       COUNT(*)          AS events
FROM fact_user_events
WHERE ts >= :start_date
GROUP BY substr(ts, 1, 10)
ORDER BY event_date
"""
