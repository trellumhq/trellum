"""SQL queries for player-overview.

Raw rows only -- no GROUP BY. Aggregation happens in pandas (for snapshots)
or client-side in the filter engine (for live charts), so that filters can
re-aggregate without another round trip.
"""

PLAYER_DAILY = """
SELECT event_date,
       title,
       platform,
       country,
       region,
       spender_tier,
       dau,
       new_users,
       sessions,
       payers,
       transactions,
       iap_revenue,
       ad_revenue,
       is_promo
FROM fact_daily
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""

RETENTION = """
SELECT cohort_date,
       title,
       platform,
       day_number,
       cohort_size,
       retained_users
FROM fact_retention
WHERE cohort_date BETWEEN :start_date AND :end_date
ORDER BY cohort_date
"""

# No date filter: cohort_date on that table is when the player joined, and
# the segments are a snapshot of who is currently in each set.
PLAYER_SEGMENTS = """
SELECT user_id,
       title,
       platform,
       segment
FROM fact_player_segments
ORDER BY user_id
"""
