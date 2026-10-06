"""CUPED — Controlled-experiment Using Pre-Experiment Data.

Reduces variance in A/B test estimates by subtracting the part of the
outcome explainable by a pre-experiment covariate. Typical real-world
variance reduction for revenue metrics: 30–50%.

Formula:
    Y_adjusted = Y - θ * (X - mean(X))
    θ          = cov(Y, X) / var(X)

θ is fit on the pooled (treatment + control) sample. The covariate ``X``
must be measured **before the experiment started** so it cannot be
influenced by the treatment.

Caveats:
- New users (no pre-experiment activity) get X = 0; CUPED leaves their
  outcomes alone in expectation. Variance reduction comes from
  returning users where prior-period spend predicts experiment-period
  spend.
- Optimal pre-window: 1–2 weeks per Microsoft / Statsig guidance.
  Longer windows add noise; shorter ones don't capture enough behavior.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def cuped_theta(y: np.ndarray | pd.Series, x: np.ndarray | pd.Series) -> float:
    """OLS estimate of θ = cov(Y, X) / var(X)."""
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    if x_arr.size == 0:
        return 0.0
    vx = float(np.var(x_arr))
    if vx == 0.0:
        return 0.0
    return float(np.cov(y_arr, x_arr, ddof=0)[0, 1] / vx)


def cuped_adjust(
    df: pd.DataFrame,
    metric_col: str,
    covariate_col: str,
    out_col: str | None = None,
) -> tuple[pd.DataFrame, float]:
    """Add ``Y_adj = Y - θ * (X - mean(X))`` as a new column.

    ``θ`` is fit on the pooled sample (both variants combined). Returns
    ``(df_with_adj_col, theta)``. The adjusted column has the same mean
    as the original (within numerical precision) but typically lower
    variance.
    """
    if out_col is None:
        out_col = f"{metric_col}_cuped"
    out = df.copy()
    y = out[metric_col].to_numpy(dtype=float)
    x = out[covariate_col].to_numpy(dtype=float)
    theta = cuped_theta(y, x)
    out[out_col] = y - theta * (x - x.mean())
    return out, theta


def cuped_variance_reduction(
    df: pd.DataFrame, metric_col: str, covariate_col: str
) -> float:
    """Expected fraction of variance removed: R² of Y on X (in [0, 1])."""
    y = df[metric_col].to_numpy(dtype=float)
    x = df[covariate_col].to_numpy(dtype=float)
    if y.size == 0 or x.size == 0:
        return 0.0
    if np.var(x) == 0.0 or np.var(y) == 0.0:
        return 0.0
    rho = np.corrcoef(y, x)[0, 1]
    if not np.isfinite(rho):
        return 0.0
    return float(rho * rho)
