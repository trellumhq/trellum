"""High-level A/B test variance-reduction helpers.

Composes the lower-level primitives from ``winsor``, ``bootstrap``, and
``cuped`` into the operations a typical A/B report needs:

Primitives (operate on a per-user DataFrame):
- :func:`winsorize_user_df` — unified-threshold capping of per-user totals
- :func:`cuped_user_df`     — covariate adjustment using a pre-experiment column
- :func:`bootstrap_ab_cis`  — 95% bootstrap CIs for the standard delta set
                              (revenue, ARPDAU, conversion, ARPPU)
- :func:`merge_pre_window`  — merge per-user pre-window totals as covariates

Report-side composers (call the report's row builder):
- :func:`agg_from_users`    — per-variant aggregates as the canonical dict
- :func:`rows_and_ts_winsor` — winsorize → aggregate → build rows
- :func:`rows_and_ts_cuped`  — CUPED-adjust → aggregate → build rows

All work on a per-user DataFrame where each row is one user in the test
window. The expected columns are configurable, but the defaults match
the convention used in the reports' user-totals SQL:

    user_id, variant, active_days, gross_revenue, net_revenue, is_payer,
    transactions, is_ftd  (+ pre_gross_revenue / pre_net_revenue for CUPED)
"""

from trellum.stats.ab.aggregate import (
    agg_from_users,
    bootstrap_ab_cis,
    overlay_volume_metrics,
    srm_check,
)
from trellum.stats.ab.modes import (
    Metric,
    build_modes_from_metrics,
    build_variance_modes,
    default_build_rows,
    rows_and_ts_cuped,
    rows_and_ts_winsor,
)
from trellum.stats.ab.users import (
    assign_by_user_id,
    cuped_user_df,
    merge_pre_window,
    validate_user_df,
    winsorize_user_df,
)

__all__ = [
    "Metric",
    "agg_from_users",
    "assign_by_user_id",
    "bootstrap_ab_cis",
    "build_modes_from_metrics",
    "build_variance_modes",
    "cuped_user_df",
    "default_build_rows",
    "merge_pre_window",
    "overlay_volume_metrics",
    "rows_and_ts_cuped",
    "rows_and_ts_winsor",
    "srm_check",
    "validate_user_df",
    "winsorize_user_df",
]
