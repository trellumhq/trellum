"""From per-user rows to per-group rows, with confidence intervals."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from trellum.stats.bootstrap import (
    bootstrap_mean_delta_pct,
    bootstrap_ratio_delta_pct,
)


# ---------------------------------------------------------------------------
# Per-user aggregator → canonical dict
# ---------------------------------------------------------------------------
def agg_from_users(
    g: pd.DataFrame,
    n_days: int,
    *,
    gross_col: str = "gross_revenue",
    net_col: str | None = "net_revenue",
    dau_col: str = "active_days",
    txns_col: str | None = "transactions",
    ftd_col: str | None = "is_ftd",
    payer_col: str = "is_payer",
) -> dict[str, float]:
    """Per-variant aggregates from a per-user DataFrame.

    Returns the dict shape consumed by A/B report ``_build_rows`` functions:
    ``mean_dau``, ``sum_dau``, ``sum_revenue``, ``sum_net_revenue``,
    ``sum_payers``, ``sum_ftd``, ``sum_transactions``, ``arpdau``,
    ``net_arpdau``, ``rev_normalised``, ``payers_per_dau``, ``txns_per_dau``,
    ``conv_rate``, ``ftd_conv``, ``arppu``, ``arppdau``, ``avg_txn_value``.

    ``payer_col`` must be the IDENTITY indicator (raw ``is_payer`` flag),
    not derived from ``gross_col`` — important when ``gross_col`` is a
    CUPED-adjusted column where small post-adjustment positives would
    otherwise miscount non-payers as payers.
    """
    z = 0.0
    keys = [
        "mean_dau", "sum_dau", "sum_revenue", "sum_net_revenue",
        "sum_payers", "sum_ftd", "sum_transactions",
        "arpdau", "net_arpdau", "rev_normalised",
        "payers_per_dau", "txns_per_dau", "conv_rate",
        "ftd_conv", "arppdau", "avg_txn_value", "arppu",
    ]
    if g.empty or n_days <= 0:
        return {k: z for k in keys}

    s_dau    = float(g[dau_col].sum())
    mean_dau = s_dau / n_days if n_days else z
    s_rev    = float(g[gross_col].sum())
    s_net    = float(g[net_col].sum()) if net_col and net_col in g.columns else z
    s_txn    = float(g[txns_col].sum()) if txns_col and txns_col in g.columns else z
    s_pay    = int(g[payer_col].sum()) if payer_col in g.columns else int((g[gross_col] > 0).sum())
    s_ftd    = int(g[ftd_col].sum()) if ftd_col and ftd_col in g.columns else 0

    return {
        "mean_dau":         mean_dau,
        "sum_dau":          s_dau,
        "sum_revenue":      s_rev,
        "sum_net_revenue":  s_net,
        "sum_payers":       s_pay,
        "sum_ftd":          s_ftd,
        "sum_transactions": s_txn,
        "arpdau":           s_rev / s_dau if s_dau else z,
        "net_arpdau":       s_net / s_dau if s_dau else z,
        "rev_normalised":   s_rev / mean_dau if mean_dau else z,
        "payers_per_dau":   s_pay / s_dau if s_dau else z,
        "txns_per_dau":     s_txn / s_dau if s_dau else z,
        "conv_rate":        s_pay / s_dau if s_dau else z,
        "ftd_conv":         s_ftd / s_dau if s_dau else z,
        "arppdau":          s_rev / s_pay if s_pay else z,
        "arppu":            s_rev / s_pay if s_pay else z,
        "avg_txn_value":    s_rev / s_txn if s_txn else z,
    }

# ---------------------------------------------------------------------------
# Bootstrap CIs
# ---------------------------------------------------------------------------
def bootstrap_ab_cis(
    df: pd.DataFrame,
    *,
    variant_col: str = "variant",
    control_label: str = "Control",
    test_label: str = "Test",
    gross_col: str = "gross_revenue",
    dau_col: str | None = "active_days",
    payer_col: str | None = "is_payer",
    iters: int = 2000,
    seed: int = 42,
) -> dict[str, tuple[float, float]]:
    """Compute 95% bootstrap CIs for the standard A/B delta set.

    Returns a dict keyed by row ``key`` values used in ``_build_rows``:

    - ``rev_normalised`` — relative delta of per-user mean gross revenue
    - ``arpdau``         — relative delta of gross / DAU person-day ratio
    - ``payers_per_dau`` — relative delta of payer / DAU person-day ratio
                           (a.k.a. Conversion)
    - ``arppu``          — relative delta of per-payer mean gross revenue

    Keys are omitted when their input columns aren't available (e.g.
    if ``dau_col`` is ``None`` or absent, ``arpdau`` is skipped).

    Each value is ``(lo_pct, hi_pct)`` in percentage points so a 95% CI of
    ``[-5%, +10%]`` returns ``(-5.0, 10.0)``.
    """
    cis: dict[str, tuple[float, float]] = {}
    if df.empty:
        return cis
    rng = np.random.default_rng(seed)
    c = df[df[variant_col] == control_label]
    t = df[df[variant_col] == test_label]
    if c.empty or t.empty:
        return cis

    # Revenue (per-user mean) → drives "Gross Revenue" / "rev_normalised".
    _, lo, hi = bootstrap_mean_delta_pct(
        c[gross_col].to_numpy(), t[gross_col].to_numpy(),
        n_iters=iters, rng=rng,
    )
    cis["rev_normalised"] = (lo * 100, hi * 100)

    # ARPDAU = sum(revenue) / sum(active_days), per-row resample.
    if dau_col and dau_col in df.columns:
        _, lo, hi = bootstrap_ratio_delta_pct(
            c[gross_col].to_numpy(), c[dau_col].to_numpy(),
            t[gross_col].to_numpy(), t[dau_col].to_numpy(),
            n_iters=iters, rng=rng,
        )
        cis["arpdau"] = (lo * 100, hi * 100)

    # Conversion = sum(is_payer indicator) / sum(active_days). is_payer
    # must be the IDENTITY indicator (raw, pre-adjustment) — see report
    # generators for the why.
    if dau_col and payer_col and dau_col in df.columns and payer_col in df.columns:
        _, lo, hi = bootstrap_ratio_delta_pct(
            c[payer_col].astype(float).to_numpy(), c[dau_col].to_numpy(),
            t[payer_col].astype(float).to_numpy(), t[dau_col].to_numpy(),
            n_iters=iters, rng=rng,
        )
        cis["payers_per_dau"] = (lo * 100, hi * 100)

    # ARPPU = per-payer mean revenue, restricted to is_payer=1.
    if payer_col and payer_col in df.columns:
        c_pay = c[c[payer_col] == 1]
        t_pay = t[t[payer_col] == 1]
        if not c_pay.empty and not t_pay.empty:
            _, lo, hi = bootstrap_mean_delta_pct(
                c_pay[gross_col].to_numpy(), t_pay[gross_col].to_numpy(),
                n_iters=iters, rng=rng,
            )
            cis["arppu"] = (lo * 100, hi * 100)

    # Net ARPDAU = net_revenue / DAU person-day.
    if "net_revenue" in df.columns and dau_col and dau_col in df.columns:
        _, lo, hi = bootstrap_ratio_delta_pct(
            c["net_revenue"].to_numpy(), c[dau_col].to_numpy(),
            t["net_revenue"].to_numpy(), t[dau_col].to_numpy(),
            n_iters=iters, rng=rng,
        )
        cis["net_arpdau"] = (lo * 100, hi * 100)

    # FTD conversion = is_ftd indicator / DAU person-day.
    if "is_ftd" in df.columns and dau_col and dau_col in df.columns:
        _, lo, hi = bootstrap_ratio_delta_pct(
            c["is_ftd"].astype(float).to_numpy(), c[dau_col].to_numpy(),
            t["is_ftd"].astype(float).to_numpy(), t[dau_col].to_numpy(),
            n_iters=iters, rng=rng,
        )
        cis["ftd_conv"] = (lo * 100, hi * 100)

    # Transactions per DAU = transactions / DAU person-day.
    if "transactions" in df.columns and dau_col and dau_col in df.columns:
        _, lo, hi = bootstrap_ratio_delta_pct(
            c["transactions"].astype(float).to_numpy(), c[dau_col].to_numpy(),
            t["transactions"].astype(float).to_numpy(), t[dau_col].to_numpy(),
            n_iters=iters, rng=rng,
        )
        cis["txns_per_dau"] = (lo * 100, hi * 100)

    # Avg transaction value = revenue / transactions (restricted to payers).
    if "transactions" in df.columns:
        c_tx = c[c["transactions"] > 0]
        t_tx = t[t["transactions"] > 0]
        if not c_tx.empty and not t_tx.empty:
            _, lo, hi = bootstrap_ratio_delta_pct(
                c_tx[gross_col].to_numpy(),
                c_tx["transactions"].astype(float).to_numpy(),
                t_tx[gross_col].to_numpy(),
                t_tx["transactions"].astype(float).to_numpy(),
                n_iters=iters, rng=rng,
            )
            cis["avg_txn_value"] = (lo * 100, hi * 100)

    return cis

def srm_check(
    n_control: int,
    n_test: int,
    expected_split: tuple[float, float] = (50, 50),
) -> dict[str, Any]:
    """Sample-ratio-mismatch verdict for two group sizes.

    A binomial z-test of the observed split against the expected one;
    ``|z| > 3`` (p ~ 0.003, the conventional SRM alarm) fails. Returns the
    dict the ABCompare header badge renders:
    ``{"pass", "observed", "expected", "n", "z"}``.
    """
    n = n_control + n_test
    if n == 0:
        return {"pass": False, "observed": "0/0", "expected":
                f"{expected_split[0]:g}/{expected_split[1]:g}", "n": 0, "z": 0.0}
    p_exp = expected_split[1] / (expected_split[0] + expected_split[1])
    z = (n_test - n * p_exp) / max(1e-9, (n * p_exp * (1 - p_exp)) ** 0.5)
    obs_t = n_test / n * 100
    return {
        "pass": abs(z) <= 3.0,
        "observed": f"{100 - obs_t:.1f}/{obs_t:.1f}",
        "expected": f"{expected_split[0]:g}/{expected_split[1]:g}",
        "n": int(n),
        "z": round(float(z), 2),
    }

def overlay_volume_metrics(
    rows_kpi: list[dict],
    rows_observed: list[dict],
) -> list[dict]:
    """Use observed control/test in the volume table; keep KPI deltas/CIs from ``rows_kpi``.

    Winsor/CUPED modes build ``rows_kpi`` from adjusted per-user revenue so KPI
    % and bootstrap CIs are variance-reduced. Dollar/count volumes must stay on
    raw observed totals — otherwise e.g. ARPPU mixes adjusted revenue with raw
    payer counts and no longer matches the daily (finance) view.
    """
    obs = {r["metric"]: r for r in rows_observed}
    out: list[dict] = []
    for r in rows_kpi:
        nr = dict(r)
        o = obs.get(nr["metric"])
        if o is not None:
            nr["control"] = o["control"]
            nr["test"] = o["test"]
        out.append(nr)
    return out
