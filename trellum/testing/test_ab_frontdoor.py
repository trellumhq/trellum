"""The ABCompare front door: groups of users in, visualisation out.

The design contract (issue #30): ``from_users`` / ``from_groups`` are sugar
above the existing variance-reduction machinery -- same winsorisation, same
CUPED, same seeded bootstrap -- so their output must be *identical* to the
hand-wired path they replace. These tests pin that equivalence, the slotting
rules, the SRM verdict, and the deterministic bucketing helper.
"""

import os

import numpy as np
import pandas as pd
import pytest

from trellum.components import ABCompare
from trellum.stats import (
    Metric,
    assign_by_user_id,
    build_variance_modes,
    srm_check,
)

_ITERS = 60          # keep the bootstrap cheap; determinism is seeded anyway


def _users(n=3000, seed=11):
    rng = np.random.default_rng(seed)
    revenue = np.where(rng.random(n) < 0.6, 0.0,
                       rng.lognormal(1.5, 1.2, n)).round(2)
    pre = np.where(rng.random(n) < 0.55, 0.0,
                   (revenue * rng.uniform(0.3, 1.4, n)
                    + rng.lognormal(0.5, 1.0, n))).round(2)
    return pd.DataFrame({
        "uid": np.arange(n),
        "bucket": rng.choice(["ctl", "new"], n),
        "days_active": rng.integers(1, 15, n),
        "rev": revenue,
        "pre_rev": pre,
        "bought": (revenue > 0).astype(int),
        "orders": (revenue > 0).astype(int) * rng.integers(1, 5, n),
    })


def _hand_wired(df):
    """The path every report used before the front door."""
    mapped = pd.DataFrame({
        "user_id": df["uid"],
        "variant": df["bucket"].map({"ctl": "Control", "new": "Test"}),
        "active_days": df["days_active"],
        "gross_revenue": df["rev"],
        "is_payer": df["bought"],
        "transactions": df["orders"],
        "pre_gross_revenue": df["pre_rev"],
    }).dropna(subset=["variant"])
    return build_variance_modes(
        mapped, n_days=14, control_label="Control", test_label="Test",
        iters=_ITERS, validate=False,
    )


def _front_door(df):
    return ABCompare.from_users(
        df,
        variant_col="bucket", control="ctl", test="new",
        control_label="Control", test_label="Test",
        user_col="uid", exposure_col="days_active", n_days=14,
        metrics=[
            Metric("rev", kind="mean", fmt="currency", pre_col="pre_rev"),
            Metric("bought", kind="rate"),
            Metric("orders", kind="mean"),
        ],
        iters=_ITERS, validate=False,
    )


def test_from_users_is_the_hand_wired_path():
    df = _users()
    comp = _front_door(df)
    expected = _hand_wired(df)
    assert sorted(comp.modes) == sorted(expected)
    for mode, payload in expected.items():
        assert comp.modes[mode]["rows"] == payload["rows"], mode
    # the raw rows on the component are the raw mode's rows
    assert comp.rows == expected["all_users"]["rows"]
    assert comp.default_mode == "all_users"


def test_from_groups_equals_from_users():
    df = _users()
    comp_users = _front_door(df)
    comp_groups = ABCompare.from_groups(
        {"Control": df[df["bucket"] == "ctl"].drop(columns=["bucket"]),
         "Test": df[df["bucket"] == "new"].drop(columns=["bucket"])},
        user_col="uid", exposure_col="days_active", n_days=14,
        metrics=[
            Metric("rev", kind="mean", fmt="currency", pre_col="pre_rev"),
            Metric("bought", kind="rate"),
            Metric("orders", kind="mean"),
        ],
        iters=_ITERS, validate=False,
    )
    # Concatenating the groups reorders the rows, and float summation is not
    # associative -- so compare numerics to 1e-9 relative, everything else
    # exactly. A real logic difference is orders of magnitude larger.
    for mode in comp_users.modes:
        for got, want in zip(comp_groups.modes[mode]["rows"],
                             comp_users.modes[mode]["rows"]):
            assert set(got) == set(want), (mode, want.get("metric"))
            for key, w in want.items():
                g = got[key]
                if isinstance(w, float):
                    assert g == pytest.approx(w, rel=1e-9), (mode, key)
                elif isinstance(w, list) and w and isinstance(w[0], float):
                    assert g == pytest.approx(w, rel=1e-9), (mode, key)
                else:
                    assert g == w, (mode, key)


def test_from_groups_rejects_overlap_and_wrong_arity():
    df = _users(n=200)
    with pytest.raises(ValueError, match="BOTH groups"):
        ABCompare.from_groups(
            {"Control": df, "Test": df},
            user_col="uid",
            metrics=[Metric("rev", kind="mean", fmt="currency")],
            iters=_ITERS, validate=False,
        )
    with pytest.raises(ValueError, match="exactly two"):
        ABCompare.from_groups(
            {"A": df}, user_col="uid",
            metrics=[Metric("rev", kind="mean", fmt="currency")],
        )


def test_srm_verdicts():
    ok = srm_check(20_000, 20_100, (50, 50))
    assert ok["pass"] and ok["n"] == 40_100
    bad = srm_check(28_000, 12_000, (50, 50))
    assert not bad["pass"]
    # 90/10 declared and observed -> fine
    assert srm_check(90_000, 10_050, (90, 10))["pass"]


def test_srm_badge_absent_when_no_variants_match():
    df = _users(n=300)
    df["bucket"] = "something_else"          # a mock-data shape
    comp = ABCompare.from_users(
        df, variant_col="bucket", control="ctl", test="new",
        user_col="uid",
        metrics=[Metric("rev", kind="mean", fmt="currency")],
        iters=_ITERS, validate=False,
    )
    assert comp.srm is None
    assert comp.modes == {}


def test_exposure_synthesised_relabels_users_row():
    df = _users(n=800)
    comp = ABCompare.from_users(
        df, variant_col="bucket", control="ctl", test="new", user_col="uid",
        metrics=[Metric("rev", kind="mean", fmt="currency",
                        label="Bookings")],
        iters=_ITERS, validate=False,
    )
    metrics_shown = [r["metric"] for r in comp.rows]
    assert "Users" in metrics_shown            # not "Avg DAUs"
    assert "Bookings (total)" in metrics_shown  # declared label applied


def test_family_limits_raise_clearly():
    df = _users(n=200)
    with pytest.raises(ValueError, match="primary"):
        ABCompare.from_users(
            df, variant_col="bucket", control="ctl", test="new",
            user_col="uid", metrics=[Metric("bought", kind="rate")],
            iters=_ITERS, validate=False,
        )
    with pytest.raises(ValueError, match="canonical family"):
        ABCompare.from_users(
            df, variant_col="bucket", control="ctl", test="new",
            user_col="uid",
            metrics=[Metric("rev", kind="mean", fmt="currency"),
                     Metric("bought", kind="rate"),
                     Metric("orders", kind="rate")],
            iters=_ITERS, validate=False,
        )


def test_assign_by_user_id_is_deterministic_and_salted():
    ids = [f"u{i}" for i in range(20_000)]
    a = assign_by_user_id(ids, salt="checkout_v2", split=(90, 10))
    b = assign_by_user_id(ids, salt="checkout_v2", split=(90, 10))
    assert (a == b).all(), "same salt must be stable"
    share = (a == "test").mean()
    assert 0.08 < share < 0.12, f"90/10 split off: {share:.3f}"
    c = assign_by_user_id(ids, salt="other_experiment", split=(90, 10))
    assert (a != c).any(), "a new salt must reshuffle"


def test_checkout_experiment_uses_the_front_door():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "demo", "reports", "experiments",
                        "generator.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "ABCompare.from_users" in src
    assert "_to_user_frame" not in src, "the hand-wired bridge is back?"
