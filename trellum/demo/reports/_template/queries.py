"""SQL queries for the template report.

Keep queries as module-level constants and select RAW ROWS -- no GROUP BY.
Aggregation belongs in pandas (for snapshots) or in the client-side filter
engine (for live charts), so filters can re-aggregate without re-querying.

Use :param_name placeholders; they are substituted as quoted literals.
"""

DAILY = """
SELECT event_date,
       title,
       platform,
       region,
       dau,
       iap_revenue
FROM fact_daily
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""
