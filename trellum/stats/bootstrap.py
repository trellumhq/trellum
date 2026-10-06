"""Bootstrap confidence intervals for A/B test deltas.

Non-parametric CIs computed by resampling the per-user records (the
randomization unit) with replacement. Robust to non-normality, but caller
should winsorize first for heavy-tailed distributions — naive bootstrap
breaks on infinite-variance distributions.
"""

from __future__ import annotations

import numpy as np


def bootstrap_mean_delta_pct(
    control: np.ndarray,
    test: np.ndarray,
    n_iters: int = 1000,
    ci: float = 0.95,
    rng: np.random.Generator | None = None,
) -> tuple[float, float, float]:
    """Bootstrap the relative delta of two means: (mean(test) / mean(control) - 1).

    Returns ``(point, lo, hi)`` as fractions. Use for metrics that are a
    plain mean over independent users (revenue per user, sessions, etc.).
    """
    if rng is None:
        rng = np.random.default_rng()
    cv = np.asarray(control, dtype=float)
    tv = np.asarray(test, dtype=float)
    if cv.size == 0 or tv.size == 0:
        return 0.0, 0.0, 0.0
    c_mean = cv.mean()
    point = (tv.mean() / c_mean - 1.0) if c_mean != 0 else 0.0
    deltas = np.empty(n_iters, dtype=float)
    n_c, n_t = cv.size, tv.size
    for i in range(n_iters):
        c_r = rng.choice(cv, size=n_c, replace=True).mean()
        t_r = rng.choice(tv, size=n_t, replace=True).mean()
        deltas[i] = (t_r / c_r - 1.0) if c_r != 0 else 0.0
    alpha = (1.0 - ci) / 2.0
    return point, float(np.quantile(deltas, alpha)), float(np.quantile(deltas, 1.0 - alpha))


def bootstrap_ratio_delta_pct(
    control_num: np.ndarray,
    control_den: np.ndarray,
    test_num: np.ndarray,
    test_den: np.ndarray,
    n_iters: int = 1000,
    ci: float = 0.95,
    rng: np.random.Generator | None = None,
) -> tuple[float, float, float]:
    """Bootstrap the relative delta of a per-row ratio metric.

    Variant metric = sum(numer) / sum(denom) over resampled rows. Use for
    rate metrics like ARPDAU (revenue / DAU days), where the unit of
    randomization is a single user but the metric is a ratio.

    Arrays must be aligned per row (i.e. ``control_num[i]`` and
    ``control_den[i]`` refer to the same user).
    """
    if rng is None:
        rng = np.random.default_rng()
    cn = np.asarray(control_num, dtype=float)
    cd = np.asarray(control_den, dtype=float)
    tn = np.asarray(test_num, dtype=float)
    td = np.asarray(test_den, dtype=float)
    if cn.size == 0 or tn.size == 0:
        return 0.0, 0.0, 0.0

    def _ratio(num: np.ndarray, den: np.ndarray) -> float:
        s = den.sum()
        return float(num.sum() / s) if s > 0 else 0.0

    c_ratio, t_ratio = _ratio(cn, cd), _ratio(tn, td)
    point = (t_ratio / c_ratio - 1.0) if c_ratio != 0 else 0.0
    deltas = np.empty(n_iters, dtype=float)
    n_c, n_t = cn.size, tn.size
    for i in range(n_iters):
        idx_c = rng.integers(0, n_c, size=n_c)
        idx_t = rng.integers(0, n_t, size=n_t)
        c_r = _ratio(cn[idx_c], cd[idx_c])
        t_r = _ratio(tn[idx_t], td[idx_t])
        deltas[i] = (t_r / c_r - 1.0) if c_r != 0 else 0.0
    alpha = (1.0 - ci) / 2.0
    return point, float(np.quantile(deltas, alpha)), float(np.quantile(deltas, 1.0 - alpha))
