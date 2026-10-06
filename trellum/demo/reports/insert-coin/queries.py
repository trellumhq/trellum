"""SQL for Insert Coin.

Raw rows only, same rule as every other report: the level generator in the
browser re-aggregates from row grain on every filter change, which is what
makes the game world honest -- the terrain you run on is the same filtered
frame the KPI row sums.
"""

DAILY = """
SELECT event_date,
       title,
       platform,
       region,
       spender_tier,
       dau,
       payers,
       iap_revenue,
       ad_revenue
FROM fact_daily
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""
