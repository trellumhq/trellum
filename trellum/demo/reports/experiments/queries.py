"""SQL queries for the experiments page.

One row per user: the grain the variance-reduction helpers in
``trellum.stats`` expect. fact_experiment is one generic assignment table
discriminated by ``experiment``, not one table per test, so the same query
serves every section -- each aliases the generic columns to its own
vocabulary where they are read (onboarding's ``converted`` is "returned on
day 1", its ``transactions`` is "sessions logged on day 1").
"""

EXPERIMENT_USERS = """
SELECT user_id,
       variant,
       assigned_date,
       platform,
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
