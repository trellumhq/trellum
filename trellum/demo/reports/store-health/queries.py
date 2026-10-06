"""SQL for Store Health.

Order-grain rows, no GROUP BY: every number on the page -- including every
return rate -- is a live ratio or sum re-aggregated client-side, and that
only works when numerators and denominators travel as raw columns.
"""

ORDERS = """
SELECT order_date AS event_date,
       channel,
       device,
       country,
       product,
       category,
       qty,
       discount_pct,
       gross_revenue,
       margin,
       returned
FROM shop_orders
WHERE order_date BETWEEN :start_date AND :end_date
ORDER BY order_date
"""
