"""SQL queries for monetization.

Raw rows only -- aggregation happens in pandas or client-side, so the
FilterBar can re-aggregate without another round trip.
"""

REVENUE_DAILY = """
SELECT event_date,
       title,
       platform,
       country,
       region,
       spender_tier,
       dau,
       payers,
       transactions,
       iap_revenue,
       ad_revenue
FROM fact_daily
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""

# Raw journey transitions: the flow-map section re-aggregates client-side,
# so the Markov chain it walks is exactly the filtered data.
JOURNEY = """
SELECT event_date,
       title,
       spender_tier,
       from_node,
       to_node,
       transitions
FROM fact_journey
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""

# Paid only, in the query rather than a filter: this section is about what
# acquisition SPEND buys, and organic rows all sit at spend = 0 -- on the
# spend-vs-installs scatter they pile up on the axis and say nothing. A
# filter default could hide them, but a user clearing filters would get the
# nonsense back; excluding them at the source states what the section means.
UA_SPEND = """
SELECT event_date,
       title,
       platform,
       campaign_type,
       spend,
       installs,
       impressions,
       clicks
FROM fact_ua_spend
WHERE event_date BETWEEN :start_date AND :end_date
  AND network_type = 'paid'
ORDER BY event_date
"""
