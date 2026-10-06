"""Statistical helpers for A/B test analysis.

Variance-reduction utilities that any report can opt into. Generic by design:
no project-specific column names, schema knowledge, or business logic.

Low-level primitives:
- ``winsor``    — unified-threshold winsorization (caps per-user totals)
- ``bootstrap`` — non-parametric confidence intervals for ratio metrics
- ``cuped``     — covariate adjustment using pre-experiment data

High-level A/B composers (recommended entry points for reports):
- ``ab.winsorize_user_df`` — apply winsor to gross + net revenue at once
- ``ab.cuped_user_df``     — apply CUPED to gross + net using pre-window
- ``ab.bootstrap_ab_cis``  — produce the standard CI dict
                             (rev_normalised, arpdau, payers_per_dau, arppu)
"""

from trellum.stats.ab import (
    Metric,
    agg_from_users,
    assign_by_user_id,
    bootstrap_ab_cis,
    build_modes_from_metrics,
    build_variance_modes,
    cuped_user_df,
    default_build_rows,
    merge_pre_window,
    overlay_volume_metrics,
    rows_and_ts_cuped,
    rows_and_ts_winsor,
    srm_check,
    validate_user_df,
    winsorize_user_df,
)
from trellum.stats.bootstrap import (
    bootstrap_mean_delta_pct,
    bootstrap_ratio_delta_pct,
)
from trellum.stats.cuped import (
    cuped_adjust,
    cuped_theta,
    cuped_variance_reduction,
)
from trellum.stats.winsor import pooled_threshold, winsorize_series

__all__ = [
    # the front door: declared metrics, groups in, visualisation out
    "Metric",
    "build_modes_from_metrics",
    "srm_check",
    "assign_by_user_id",
    # high-level A/B per-user composers
    "winsorize_user_df",
    "cuped_user_df",
    "bootstrap_ab_cis",
    "merge_pre_window",
    "overlay_volume_metrics",
    # report-side mode builders
    "agg_from_users",
    "default_build_rows",
    "rows_and_ts_winsor",
    "rows_and_ts_cuped",
    "build_variance_modes",
    "validate_user_df",
    # winsor primitives
    "pooled_threshold",
    "winsorize_series",
    # bootstrap primitives
    "bootstrap_ratio_delta_pct",
    "bootstrap_mean_delta_pct",
    # cuped primitives
    "cuped_theta",
    "cuped_adjust",
    "cuped_variance_reduction",
]
