"""Unified-threshold winsorization for A/B test metrics.

Winsorization caps individual values at a chosen percentile of the POOLED
(treatment + control combined) distribution, then applies the same cap to
both arms. This is more rigorous than dropping users above a per-variant
percentile (the "Excl. Top X%" pattern), which biases comparisons because
the cap itself differs between arms.

Caller is responsible for aggregating to per-user totals first; this
module just operates on Series of those totals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def pooled_threshold(per_user_values: pd.Series, pct: float = 99.0) -> float:
    """The ``pct``-th percentile of the strictly-positive pooled values.

    Strictly-positive: zeros (non-payers) don't shift the percentile cap.
    """
    pos = per_user_values[per_user_values > 0]
    if pos.empty:
        return 0.0
    return float(np.percentile(pos.to_numpy(dtype=float), pct))


def winsorize_series(s: pd.Series, threshold: float) -> pd.Series:
    """Cap each value in ``s`` at ``threshold``. Returns a new Series."""
    if threshold <= 0:
        return s.copy()
    return s.clip(upper=threshold)
