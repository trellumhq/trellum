"""Per-user frames: assignment, validation, and variance reduction.

Everything here takes one row per user and returns one row per user.
The aggregation into groups happens in aggregate.py.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from trellum.stats.cuped import cuped_adjust, cuped_variance_reduction
from trellum.stats.winsor import pooled_threshold, winsorize_series


def assign_by_user_id(
    user_ids: "pd.Series | list",
    salt: str,
    split: tuple[int, int] = (50, 50),
    labels: tuple[str, str] = ("control", "test"),
) -> pd.Series:
    """Deterministic hash-bucket assignment by user id.

    For backfills and holdout analyses where exposure logs are missing.
    **Logged exposure is always the better source of truth** — an id-hash
    recomputation cannot know about users excluded at serve time, staged
    rollouts, or mid-test salt changes. Use this only when there is nothing
    logged, and say so on the page.

    Stable across runs and machines: bucket = md5(salt:user_id) % 100.
    """
    ids = pd.Series(user_ids)
    cut = split[0] * 100 // (split[0] + split[1])

    def bucket(uid: Any) -> str:
        h = hashlib.md5(f"{salt}:{uid}".encode("utf-8")).hexdigest()
        return labels[0] if int(h[:8], 16) % 100 < cut else labels[1]

    return ids.map(bucket)

# ---------------------------------------------------------------------------
# Sanity validator — run once before winsor / CUPED to catch shape mistakes
# ---------------------------------------------------------------------------
def validate_user_df(
    df: pd.DataFrame,
    *,
    n_days: int | None = None,
    pre_window_days: int | None = None,
    variant_col: str = "variant",
    control_label: str = "Control",
    test_label: str = "Test",
    gross_col: str = "gross_revenue",
    net_col: str | None = "net_revenue",
    dau_col: str = "active_days",
    payer_col: str = "is_payer",
    ftd_col: str | None = "is_ftd",
    txns_col: str | None = "transactions",
    strict: bool = False,
    print_findings: bool = True,
) -> list[tuple[str, str, str]]:
    """Sanity-check a per-user A/B DataFrame before winsor / CUPED.

    Catches the shape bugs we've actually hit in past A/B reports:

    - missing required columns
    - ``is_payer`` not a binary indicator (e.g. derived from a CUPED-adjusted
      gross_revenue, which can be slightly positive for non-payers)
    - row-explosion from a missing merge key (counted via ``active_days >
      n_days``)
    - empty / heavily imbalanced variants
    - users present only in the DAU CTE but missing from the revenue CTE
      (or vice-versa) — surfaces as ``is_payer == 1`` with ``gross == 0`` or
      ``gross > 0`` with ``is_payer == 0``
    - NaN / Inf in numeric columns
    - CUPED preconditions: pre-window column present and not constant; warn
      when pre-window length differs from test window (theta inflation)

    Returns a list of ``(severity, code, message)`` tuples. Severity is one of
    ``"ERROR"``, ``"WARN"``, ``"INFO"``. With ``strict=True``, raises
    ``ValueError`` if any ERROR finding is present.
    """
    findings: list[tuple[str, str, str]] = []

    def add(sev: str, code: str, msg: str) -> None:
        findings.append((sev, code, msg))

    # ── Required columns ───────────────────────────────────────────────────
    required = ["user_id", variant_col, dau_col, gross_col, payer_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        add("ERROR", "missing-columns", f"required column(s) absent: {missing}")
    # Optional columns we'll spot-check if present
    optional = {"net": net_col, "ftd": ftd_col, "txns": txns_col}

    if df.empty:
        add("ERROR", "empty-df", "per-user DataFrame is empty")
        _emit(findings, print_findings, strict)
        return findings

    # ── Variant column ─────────────────────────────────────────────────────
    if variant_col in df.columns:
        variants = df[variant_col].astype(str).unique().tolist()
        unexpected = [v for v in variants if v not in (control_label, test_label)]
        if unexpected:
            add("WARN", "unexpected-variants",
                f"variant column has values outside {{{control_label!r}, {test_label!r}}}: {unexpected[:5]}")
        n_ctrl = int((df[variant_col] == control_label).sum())
        n_test = int((df[variant_col] == test_label).sum())
        if n_ctrl == 0:
            add("ERROR", "empty-control", f"no rows with variant == {control_label!r}")
        if n_test == 0:
            add("ERROR", "empty-test", f"no rows with variant == {test_label!r}")
        if n_ctrl and n_test:
            ratio = max(n_ctrl, n_test) / max(min(n_ctrl, n_test), 1)
            if ratio > 3.0:
                add("WARN", "variant-imbalance",
                    f"variant size ratio {ratio:.1f}× (Control={n_ctrl:,}, Test={n_test:,}) — verify split is intentional")

    # ── DAU / active_days range ────────────────────────────────────────────
    if dau_col in df.columns:
        s = df[dau_col]
        if (s < 0).any():
            add("ERROR", "negative-dau", f"{int((s < 0).sum()):,} row(s) have {dau_col} < 0")
        if n_days is not None and (s > n_days).any():
            bad = int((s > n_days).sum())
            add("ERROR", "dau-exceeds-window",
                f"{bad:,} row(s) have {dau_col} > n_days={n_days} — likely row-explosion from a missing merge key")

    # ── Revenue ranges ─────────────────────────────────────────────────────
    for col in (gross_col, net_col):
        if col and col in df.columns:
            if df[col].isna().any():
                add("ERROR", "nan-in-revenue", f"{col} contains NaN")
            if np.isinf(df[col]).any():
                add("ERROR", "inf-in-revenue", f"{col} contains Inf")
            if (df[col] < 0).any():
                add("WARN", "negative-revenue",
                    f"{int((df[col] < 0).sum()):,} row(s) have {col} < 0 — fine post-CUPED, suspicious pre-CUPED")

    # ── is_payer must be a binary indicator ────────────────────────────────
    if payer_col in df.columns:
        unique_vals = pd.unique(df[payer_col].dropna())
        if not set(unique_vals).issubset({0, 1}):
            add("ERROR", "payer-not-binary",
                f"{payer_col} has values outside {{0,1}}: {list(unique_vals)[:5]} — must be a raw identity indicator")

    # ── Payer / gross consistency ──────────────────────────────────────────
    # Note: we DON'T flag (gross_revenue > 0 AND is_payer == 0) — that's
    # legitimate when gross_revenue is IAP + Ad revenue (ad-watchers have
    # gross > 0 but is_payer must reflect IAP identity, not ad watching).
    # We only flag the opposite: is_payer = 1 should always imply gross > 0,
    # since a payer can't have less revenue than their IAP.
    if payer_col in df.columns and gross_col in df.columns:
        payer_no_gross = int(((df[gross_col] <= 0) & (df[payer_col] == 1)).sum())
        if payer_no_gross:
            add("WARN", "payer-no-gross",
                f"{payer_no_gross:,} row(s) have {payer_col} == 1 but {gross_col} <= 0 — payer in DAU CTE without revenue row (CTE join gap)")

    # ── Other numeric NaN / Inf checks ─────────────────────────────────────
    for label, col in optional.items():
        if col and col in df.columns:
            if df[col].isna().any():
                add("WARN", "nan-in-optional", f"{col} contains NaN")

    # ── CUPED preconditions ────────────────────────────────────────────────
    pre_col = f"pre_{gross_col}"
    if pre_col in df.columns:
        if df[pre_col].isna().any():
            add("ERROR", "nan-in-pre", f"{pre_col} contains NaN — merge_pre_window fills with 0, did you skip it?")
        var_pre = float(df[pre_col].var())
        if var_pre == 0:
            add("WARN", "cuped-zero-variance",
                f"{pre_col} has zero variance — CUPED will fit theta=0 (no variance reduction)")
        if n_days is not None and pre_window_days is not None and pre_window_days != n_days:
            add("INFO", "cuped-window-mismatch",
                f"pre-window ({pre_window_days}d) != test window ({n_days}d) — CUPED still valid; expect a different θ scale")

    _emit(findings, print_findings, strict)
    return findings

def _emit(
    findings: list[tuple[str, str, str]],
    print_findings: bool,
    strict: bool,
) -> None:
    if print_findings and findings:
        for sev, code, msg in findings:
            print(f"  [{sev}] validate_user_df:{code} — {msg}", flush=True)
    if strict:
        errors = [f for f in findings if f[0] == "ERROR"]
        if errors:
            raise ValueError(
                "validate_user_df: ERROR findings: "
                + "; ".join(f"{code}: {msg}" for _, code, msg in errors)
            )

# ---------------------------------------------------------------------------
# Winsorization
# ---------------------------------------------------------------------------
def winsorize_user_df(
    df: pd.DataFrame,
    gross_col: str = "gross_revenue",
    net_col: str | None = "net_revenue",
    pct: float = 99.0,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Cap per-user gross + net revenue at the pooled ``pct``-th percentile.

    The threshold is computed from the union of both variants (pooled),
    then applied identically — never per-variant. Returns
    ``(winsorized_df, info)`` where ``info`` has the thresholds used.
    """
    out = df.copy()
    gross_thr = pooled_threshold(out[gross_col], pct)
    out[gross_col] = winsorize_series(out[gross_col], gross_thr)
    info: dict[str, float] = {"gross_threshold": gross_thr, "winsor_pct": pct}
    if net_col and net_col in out.columns:
        net_thr = pooled_threshold(out[net_col], pct)
        out[net_col] = winsorize_series(out[net_col], net_thr)
        info["net_threshold"] = net_thr
    return out, info

# ---------------------------------------------------------------------------
# CUPED
# ---------------------------------------------------------------------------
def cuped_user_df(
    df: pd.DataFrame,
    gross_col: str = "gross_revenue",
    pre_gross_col: str = "pre_gross_revenue",
    net_col: str | None = "net_revenue",
    pre_net_col: str | None = "pre_net_revenue",
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Apply CUPED adjustment to per-user gross + net revenue.

    The pre-experiment column ``pre_gross_col`` (and optionally
    ``pre_net_col``) must already be merged onto ``df``. Returns
    ``(adjusted_df, info)``.

    **Adjusted per-user values can be negative** for users whose pre-window
    revenue is far above the cohort mean. That is by design: CUPED's
    guarantees (preserved mean / total, reduced variance) require we NOT
    clip them. The negative values are a statistical artefact for variance
    reduction — they're not "real revenue" per-user but they make the SUM
    across users equal sum(Y) (with no clipping bias). ARPDAU, deltas, and
    CIs are correct because they're computed from sums.

    ``info`` contains the fitted ``theta_gross`` / ``theta_net`` and the
    expected ``variance_reduction_pct`` (R² of gross on pre_gross,
    expressed as a percentage).
    """
    if pre_gross_col not in df.columns:
        return df.copy(), {}
    out = df.copy()
    out, theta_g = cuped_adjust(out, gross_col, pre_gross_col, gross_col)
    info: dict[str, float] = {"theta_gross": theta_g}
    if net_col and net_col in out.columns and pre_net_col and pre_net_col in out.columns:
        out, theta_n = cuped_adjust(out, net_col, pre_net_col, net_col)
        info["theta_net"] = theta_n
    info["variance_reduction_pct"] = (
        cuped_variance_reduction(df, gross_col, pre_gross_col) * 100
    )
    return out, info

# ---------------------------------------------------------------------------
# Pre-window merge
# ---------------------------------------------------------------------------
def merge_pre_window(
    df_user: pd.DataFrame,
    df_user_pre: pd.DataFrame,
    *,
    gross_col: str = "gross_revenue",
    net_col: str | None = "net_revenue",
) -> pd.DataFrame:
    """Merge per-user pre-window totals onto the test-window per-user table.

    Missing pre-window rows (users not active in the pre window — typically
    new users) get 0 for the pre-columns. Returns a copy of ``df_user``
    with ``pre_gross_revenue`` (and optionally ``pre_net_revenue``) added.
    """
    keep_cols = ["user_id", gross_col]
    rename_map = {gross_col: f"pre_{gross_col}"}
    if net_col and net_col in df_user_pre.columns:
        keep_cols.append(net_col)
        rename_map[net_col] = f"pre_{net_col}"
    pre = df_user_pre[keep_cols].rename(columns=rename_map)
    out = df_user.merge(pre, on="user_id", how="left")
    out[f"pre_{gross_col}"] = out[f"pre_{gross_col}"].fillna(0)
    if net_col and f"pre_{net_col}" in out.columns:
        out[f"pre_{net_col}"] = out[f"pre_{net_col}"].fillna(0)
    return out
