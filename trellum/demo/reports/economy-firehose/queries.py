"""SQL for the Economy Firehose.

Full grain, no GROUP BY, on purpose: this report's job is to demonstrate that
seven figures of rows survive the trip to the browser and stay interactive.
Everything the page shows is re-aggregated client-side from these rows.
"""

ECONOMY = """
SELECT event_date,
       title,
       platform,
       spender_tier,
       feature,
       kind,
       currency,
       amount,
       transactions
FROM fact_economy
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""
