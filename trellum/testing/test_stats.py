"""Property tests for the A/B variance-reduction stack.

Covers the invariants that previously broke silently:
  - CUPED sum-preservation at the pooled level
  - Winsor symmetry (both arms get the same cap)
  - Bootstrap reproducibility under a fixed seed
  - validate_user_df detects each failure mode it claims to

Run with:  python3 -m pytest trellum/testing/test_stats.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trellum.stats import (
    bootstrap_ab_cis,
    build_variance_modes,
    cuped_user_df,
    merge_pre_window,
    validate_user_df,
    winsorize_user_df,
)
from trellum.stats.cuped import cuped_adjust, cuped_theta


# ---------------------------------------------------------------------------
# Test fixture — a synthetic per-user DataFrame with a heavy-tail revenue
# distribution that mimics the games-revenue shape (few whales, many zeros).
# ---------------------------------------------------------------------------
def _make_user_df(n: int = 5000, seed: int = 7, with_pre: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # 5% payers, log-normal revenue among payers
    is_payer = (rng.uniform(size=n) < 0.05).astype(int)
    gross = is_payer * rng.lognormal(mean=2.5, sigma=1.5, size=n)
    # Variants: 50/50 split
    variant = np.where(rng.uniform(size=n) < 0.5, "Control", "Test")
    active = rng.integers(1, 15, size=n)
    transactions = is_payer * rng.integers(1, 8, size=n)
    is_ftd = (is_payer & (rng.uniform(size=n) < 0.3)).astype(int)
    df = pd.DataFrame({
        "user_id": np.arange(n),
        "variant": variant,
        "active_days": active,
        "gross_revenue": gross,
        "net_revenue": gross * 0.7,
        "transactions": transactions,
        "is_ftd": is_ftd,
        "is_payer": is_payer,
    })
    if with_pre:
        # Pre-window: correlated with test window for ~30% of users
        pre = is_payer * rng.lognormal(mean=2.5, sigma=1.5, size=n)
        # Correlate ~ 0.5 by blending pre with current
        df["pre_gross_revenue"] = 0.5 * pre + 0.5 * gross + rng.normal(0, 0.1, n)
        df["pre_net_revenue"]   = df["pre_gross_revenue"] * 0.7
    return df


# ===========================================================================
# Winsor — pooled threshold, both arms get the same cap
# ===========================================================================
class TestWinsor:
    def test_pooled_threshold_applied_identically(self) -> None:
        df = _make_user_df(n=3000)
        out, info = winsorize_user_df(df, pct=99.0)
        # The max value in each arm must be <= threshold
        gross_thr = info["gross_threshold"]
        assert out.loc[out.variant == "Control", "gross_revenue"].max() <= gross_thr + 1e-9
        assert out.loc[out.variant == "Test",    "gross_revenue"].max() <= gross_thr + 1e-9

    def test_threshold_unaffected_by_variant_split(self) -> None:
        """Same data, different variant labels → same threshold (pooled, not per-variant)."""
        df1 = _make_user_df(n=3000, seed=7)
        df2 = df1.copy()
        df2["variant"] = np.where(np.arange(len(df2)) < 1500, "Control", "Test")
        _, info1 = winsorize_user_df(df1, pct=99.0)
        _, info2 = winsorize_user_df(df2, pct=99.0)
        assert info1["gross_threshold"] == info2["gross_threshold"]

    def test_zeros_dont_dilute_percentile(self) -> None:
        # Threshold is computed on strictly-positive values only
        df = _make_user_df(n=2000)
        # Sanity: lots of zeros, but threshold should reflect payer tail
        _, info = winsorize_user_df(df, pct=99.0)
        positive = df.loc[df["gross_revenue"] > 0, "gross_revenue"]
        expected = float(np.percentile(positive.to_numpy(), 99.0))
        assert info["gross_threshold"] == pytest.approx(expected)


# ===========================================================================
# CUPED — sum preservation and theta identity
# ===========================================================================
class TestCuped:
    def test_pooled_sum_preserved(self) -> None:
        """sum(Y_adj) == sum(Y) at the pooled level (within float precision)."""
        df = _make_user_df(n=4000)
        out, info = cuped_user_df(df)
        # Pooled sum must match (this is CUPED's defining property post-fix)
        assert out["gross_revenue"].sum() == pytest.approx(df["gross_revenue"].sum(), rel=1e-9)
        # Theta should be non-zero given we constructed correlated pre-window data
        assert info["theta_gross"] != 0.0

    def test_zero_variance_pre_returns_theta_zero(self) -> None:
        df = _make_user_df(n=1000)
        df["pre_gross_revenue"] = 0.0
        _, info = cuped_user_df(df)
        assert info["theta_gross"] == 0.0

    def test_missing_pre_column_is_noop(self) -> None:
        df = _make_user_df(n=1000, with_pre=False)
        out, info = cuped_user_df(df)
        # No pre column → no adjustment, info is empty
        assert info == {}
        assert out["gross_revenue"].equals(df["gross_revenue"])

    def test_theta_matches_ols_formula(self) -> None:
        df = _make_user_df(n=2000)
        y = df["gross_revenue"].to_numpy()
        x = df["pre_gross_revenue"].to_numpy()
        theta_direct = cuped_theta(y, x)
        # cuped_adjust returns the same theta
        _, theta_adjust = cuped_adjust(df, "gross_revenue", "pre_gross_revenue", "gross_revenue_cuped")
        assert theta_direct == pytest.approx(theta_adjust)

    def test_adjusted_mean_unchanged_pooled(self) -> None:
        """mean(Y_adj) == mean(Y) at the pooled level."""
        df = _make_user_df(n=4000)
        out, _ = cuped_user_df(df)
        assert out["gross_revenue"].mean() == pytest.approx(df["gross_revenue"].mean(), rel=1e-9)


# ===========================================================================
# Bootstrap — reproducibility and shape
# ===========================================================================
class TestBootstrap:
    def test_reproducible_with_fixed_seed(self) -> None:
        df = _make_user_df(n=2000)
        cis_a = bootstrap_ab_cis(df, iters=200, seed=42)
        cis_b = bootstrap_ab_cis(df, iters=200, seed=42)
        for key in cis_a:
            assert cis_a[key] == cis_b[key], f"non-deterministic CI for {key}"

    def test_returns_all_expected_keys(self) -> None:
        df = _make_user_df(n=2000)
        cis = bootstrap_ab_cis(df, iters=100)
        # Standard 8 CIs when all columns are present
        expected = {
            "rev_normalised", "arpdau", "payers_per_dau", "arppu",
            "net_arpdau", "ftd_conv", "txns_per_dau", "avg_txn_value",
        }
        assert expected.issubset(set(cis.keys()))

    def test_ci_lo_le_hi(self) -> None:
        df = _make_user_df(n=2000)
        cis = bootstrap_ab_cis(df, iters=200)
        for key, (lo, hi) in cis.items():
            assert lo <= hi, f"{key}: lo {lo} > hi {hi}"

    def test_empty_df_returns_empty_dict(self) -> None:
        df = _make_user_df(n=0)
        assert bootstrap_ab_cis(df) == {}


# ===========================================================================
# validate_user_df — each finding code triggers as documented
# ===========================================================================
class TestValidateUserDf:
    def test_clean_df_has_no_errors(self) -> None:
        df = _make_user_df(n=1000)
        findings = validate_user_df(df, n_days=14, print_findings=False)
        errors = [f for f in findings if f[0] == "ERROR"]
        assert errors == []

    def test_missing_column_is_error(self) -> None:
        df = _make_user_df(n=100).drop(columns=["gross_revenue"])
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "ERROR"]
        assert "missing-columns" in codes

    def test_empty_df_is_error(self) -> None:
        df = _make_user_df(n=100).iloc[0:0]
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "ERROR"]
        assert "empty-df" in codes

    def test_empty_variant_is_error(self) -> None:
        df = _make_user_df(n=500)
        df["variant"] = "Control"
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "ERROR"]
        assert "empty-test" in codes

    def test_dau_exceeds_window_is_error(self) -> None:
        """The exact row-explosion bug we hit in da-ab-test."""
        df = _make_user_df(n=200)
        df.loc[0, "active_days"] = 100  # window is 14 days
        findings = validate_user_df(df, n_days=14, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "ERROR"]
        assert "dau-exceeds-window" in codes

    def test_payer_no_gross_warn(self) -> None:
        """Catches the CTE-join-gap class of bug (is_payer=1 but gross=0)."""
        df = _make_user_df(n=500)
        # Force is_payer=1 on a user whose gross is 0 (CTE join gap signature)
        zero_idx = df.index[df["gross_revenue"] == 0][0]
        df.loc[zero_idx, "is_payer"] = 1
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "WARN"]
        assert "payer-no-gross" in codes

    def test_ads_revenue_with_no_payer_is_silent(self) -> None:
        """gross > 0 + is_payer = 0 (ad watchers) must NOT trigger a warning."""
        df = _make_user_df(n=500)
        non_payer_idx = df.index[df["is_payer"] == 0][:5]
        # Ad-watching non-IAP users: gross > 0 but is_payer stays 0
        df.loc[non_payer_idx, "gross_revenue"] = 10.0
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings]
        assert "payer-gross-mismatch" not in codes

    def test_non_binary_payer_is_error(self) -> None:
        df = _make_user_df(n=200)
        df.loc[0, "is_payer"] = 2
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "ERROR"]
        assert "payer-not-binary" in codes

    def test_variant_imbalance_warn(self) -> None:
        df = _make_user_df(n=1000)
        # Make Control 5× Test
        df.loc[:800, "variant"] = "Control"
        df.loc[800:, "variant"] = "Test"
        findings = validate_user_df(df, print_findings=False)
        codes = [c for sev, c, _ in findings if sev == "WARN"]
        assert "variant-imbalance" in codes

    def test_strict_raises_on_error(self) -> None:
        df = _make_user_df(n=100).drop(columns=["gross_revenue"])
        with pytest.raises(ValueError):
            validate_user_df(df, strict=True, print_findings=False)

    def test_strict_does_not_raise_on_warn(self) -> None:
        df = _make_user_df(n=1000)
        df.loc[:800, "variant"] = "Control"
        df.loc[800:, "variant"] = "Test"
        # Imbalance is WARN — should NOT raise
        validate_user_df(df, strict=True, print_findings=False)


# ===========================================================================
# build_variance_modes — one-shot integration test
# ===========================================================================
class TestBuildVarianceModes:
    def test_emits_all_three_modes_with_pre_window(self) -> None:
        df = _make_user_df(n=2000)
        df_pre = df.copy()
        df_pre["gross_revenue"] = df["pre_gross_revenue"]
        df_pre["net_revenue"]   = df["pre_net_revenue"]
        # Strip pre cols from df (build_variance_modes should re-merge)
        df = df.drop(columns=["pre_gross_revenue", "pre_net_revenue"])
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            df_user_pre=df_pre, iters=100,
        )
        assert "all_users" in modes
        assert "all_users_winsor" in modes
        assert "all_users_cuped" in modes
        for k, v in modes.items():
            assert v["label"]
            assert isinstance(v["rows"], list)
            assert v["timeseries"] == {}

    def test_skips_cuped_without_pre_window(self) -> None:
        df = _make_user_df(n=1500, with_pre=False)
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            iters=100,
        )
        assert "all_users" in modes
        assert "all_users_winsor" in modes
        assert "all_users_cuped" not in modes

    def test_returns_empty_on_empty_df(self) -> None:
        df = _make_user_df(n=0)
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            iters=50,
        )
        assert modes == {}

    def test_labels_use_pop_label(self) -> None:
        df = _make_user_df(n=1000, with_pre=False)
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            pop_key="payers_only", pop_label="Payers",
            iters=50,
        )
        assert "payers_only" in modes
        assert modes["payers_only"]["label"] == "Payers"
        assert modes["payers_only_winsor"]["label"].startswith("Payers · Winsor")

    def test_include_raw_false_skips_raw(self) -> None:
        df = _make_user_df(n=1500, with_pre=False)
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            include_raw=False, iters=50,
        )
        # Raw key absent; winsor still emitted
        assert "all_users" not in modes
        assert "all_users_winsor" in modes

    def test_cuped_volume_matches_raw_observed(self) -> None:
        df = _make_user_df(n=2000)
        df_pre = df.copy()
        df_pre["gross_revenue"] = df["pre_gross_revenue"]
        df = df.drop(columns=["pre_gross_revenue", "pre_net_revenue"])
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            df_user_pre=df_pre, iters=100,
        )
        raw_rows = {r["metric"]: r for r in modes["all_users"]["rows"]}
        cuped_rows = {r["metric"]: r for r in modes["all_users_cuped"]["rows"]}
        for metric in (
            "Gross Revenue (total)",
            "Payers (total)",
            "ARPPU (revenue / payer)",
            "Avg Transaction Value",
        ):
            assert metric in raw_rows and metric in cuped_rows
            assert cuped_rows[metric]["control"] == raw_rows[metric]["control"]
            assert cuped_rows[metric]["test"] == raw_rows[metric]["test"]

    def test_rows_carry_ci_pct(self) -> None:
        df = _make_user_df(n=2000)
        df_pre = df.copy()
        df = df.drop(columns=["pre_gross_revenue", "pre_net_revenue"])
        modes = build_variance_modes(
            df, n_days=14,
            control_label="Control", test_label="Test",
            df_user_pre=df_pre, iters=100,
        )
        rows = modes["all_users"]["rows"]
        # At least one row should have a ci_pct attached (KPI rows do)
        ci_rows = [r for r in rows if "ci_pct" in r]
        assert ci_rows, "expected ci_pct on at least one KPI row"
        for r in ci_rows:
            assert isinstance(r["ci_pct"], list)
            assert len(r["ci_pct"]) == 2


# ===========================================================================
# Integration: merge_pre_window + winsor + CUPED end-to-end
# ===========================================================================
def test_end_to_end_pipeline_runs_clean() -> None:
    df = _make_user_df(n=2000)
    df_pre = df.copy()
    df_pre["gross_revenue"] = df["pre_gross_revenue"]
    df_pre["net_revenue"]   = df["pre_net_revenue"]
    df = df.drop(columns=["pre_gross_revenue", "pre_net_revenue"])

    merged = merge_pre_window(df, df_pre)
    assert "pre_gross_revenue" in merged.columns

    # Validate first — should pass
    findings = validate_user_df(merged, n_days=14, print_findings=False)
    assert not [f for f in findings if f[0] == "ERROR"]

    # Run full pipeline
    modes = build_variance_modes(
        merged, n_days=14,
        control_label="Control", test_label="Test",
        iters=100,
    )
    assert {"all_users", "all_users_winsor", "all_users_cuped"} <= set(modes.keys())
