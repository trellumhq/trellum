"""SQL queries for acquisition-funnel."""

FUNNEL = """
SELECT event_date,
       title,
       device,
       step,
       step_order,
       users
FROM fact_funnel
WHERE event_date BETWEEN :start_date AND :end_date
ORDER BY event_date, step_order
"""
