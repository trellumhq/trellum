"""SQL for Cart Funnel.

Wide daily counts, no GROUP BY: the funnel stages are columns in
shop_traffic, and the generator melts them to long form so FunnelChart can
re-aggregate per stage when the user narrows to a channel or device. Rolling
the melt into SQL (a UNION per stage) would work too, but pandas says the
same thing in three lines.
"""

TRAFFIC = """
SELECT event_date,
       channel,
       device,
       sessions,
       product_views,
       add_to_cart,
       checkouts,
       purchases
FROM shop_traffic
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date
"""

# One row per visitor: the grain trellum.stats expects. Same generic
# fact_experiment table every A/B test in this warehouse shares; ``platform``
# is aliased to ``device`` because this is a Northwind Threads test and the
# fixture stores its device values (Mobile/Desktop/Tablet) in that column
# rather than the Nova Play platform names.
EXPERIMENT_USERS = """
SELECT user_id,
       variant,
       assigned_date,
       platform AS device,
       region,
       exposed,
       converted,
       active_days,
       transactions,
       revenue,
       pre_period_revenue
FROM fact_experiment
WHERE experiment = :experiment
"""
