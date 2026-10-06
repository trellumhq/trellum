"""Generate the demo warehouse: a seeded SQLite database of invented data.

Everything here is fabricated. Two businesses share the warehouse -- Nova
Play, a mobile-games publisher (fact_* tables), and Northwind Threads, an
apparel shop (shop_* tables) -- so the demo can show reports shaped by
different kinds of questions rather than one schema wearing every chart.

The numbers are not random noise -- they are built from a multiplicative model
(base level x weekday x yearly seasonality x trend x promo x lognormal noise)
so that charts show recognisable structure: weekend peaks, a Q4 run-up, a title
launch, a promo cycle, and one incident. Demo dashboards are only useful if the
data looks like data.

Usage
-----
    python tools/make_fixtures.py                       # 420 days ending today
    python tools/make_fixtures.py --small               # 60 days, fast
    python tools/make_fixtures.py --anchor 2026-06-30   # pinned, reproducible
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

# ── Dimensions ──────────────────────────────────────────────────────────

TITLES = [
    # title,          genre,      base_dau, iap_strength, launch_offset_days
    ("Coral Quest",   "Puzzle",     52000,   1.00,  None),
    ("Iron Vanguard", "Strategy",   31000,   1.85,  None),
    ("Neon Racer",    "Racing",     18000,   0.70,   60),   # launches mid-window
]

PLATFORMS = [
    # platform,  share, arpdau_mult
    ("iOS",      0.38,  1.55),
    ("Android",  0.52,  1.00),
    ("Web",      0.10,  0.62),
]

COUNTRIES = [
    # country,          region,  share
    ("United States",   "NA",    0.30),
    ("Canada",          "NA",    0.08),
    ("United Kingdom",  "EMEA",  0.14),
    ("Germany",         "EMEA",  0.16),
    ("Japan",           "APAC",  0.19),
    ("Australia",       "APAC",  0.13),
]

SPENDER_TIERS = [
    # tier,          dau_share, rev_share
    ("non_spender",  0.905,     0.000),
    ("minnow",       0.062,     0.115),
    ("dolphin",      0.028,     0.365),
    ("whale",        0.005,     0.520),
]

# Revenue per user in a tier is (its share of revenue / its share of users), so
# that summed revenue lands on the declared rev_share split rather than being
# dragged toward whichever tier has the most users.
TIER_ARPDAU = {
    tier: (rev_share / dau_share if rev_share else 0.0)
    for tier, dau_share, rev_share in SPENDER_TIERS
}

# Scales overall ARPDAU to roughly $0.12, typical for this genre.
ARPDAU_BASE = 0.087

NETWORKS = [("organic", 1.0), ("paid", 1.0)]
CAMPAIGN_TYPES = ["ua", "retargeting", "branding"]

RETENTION_DAYS = [1, 3, 7, 14, 30]

DEVICES = [("Phone", 0.68), ("Tablet", 0.19), ("Desktop", 0.13)]

# Store funnel, in order. Each step keeps a fraction of the previous one;
# Desktop browses more and buys less, which makes the funnel comparison
# across devices actually say something.
FUNNEL_STEPS = [
    ("store_view",   1.00),
    ("item_view",    0.62),
    ("add_to_cart",  0.34),
    ("checkout",     0.21),
    ("purchase",     0.14),
]
DEVICE_FUNNEL_MULT = {"Phone": 1.00, "Tablet": 0.94, "Desktop": 0.78}

# Checkout A/B test: `variant_b` is the winner, with a true lift the
# statistics reports should be able to recover.
EXPERIMENT_NAME = "checkout_v2"
EXPERIMENT_USERS = 40000
EXPERIMENT_BASE_CONVERSION = 0.118
EXPERIMENT_TRUE_LIFT = 0.060          # relative; large enough to be detectable at n=40k
EXPERIMENT_WINDOW_DAYS = 28

# Starter-pack pricing A/B: $4.99 (control) vs $3.99 (variant_b). The lower
# price converts meaningfully more buyers, but each one spends less, so ARPU
# lands close to flat -- a pricing test a conversion-only readout would
# misread as an unambiguous win.
PRICING_EXPERIMENT_NAME = "starter_pack_price"
PRICING_USERS = 22000
PRICING_BASE_CONVERSION = 0.052
PRICING_TRUE_LIFT = 0.22              # relative; the lower price converts more
PRICING_CONTROL_PRICE = 4.99
PRICING_TEST_PRICE = 3.99
PRICING_WINDOW_DAYS = 28

# Onboarding-tutorial A/B: text tutorial (control) vs interactive tutorial
# (variant_b). The story is engagement, not revenue -- Day-1 return moves
# meaningfully, revenue stays roughly flat, since a tutorial does not sell
# anything by itself.
ONBOARDING_EXPERIMENT_NAME = "onboarding_v2"
ONBOARDING_USERS = 18000
ONBOARDING_BASE_D1_RETURN = 0.36
ONBOARDING_TRUE_LIFT = 0.14           # relative; interactive tutorial returns more
ONBOARDING_WINDOW_DAYS = 21

# Northwind checkout-button colour A/B: the smallest test in the warehouse --
# one button's colour, on a shop-sized audience -- so its lift sits close to
# the noise floor. A portfolio needs at least one test that does not clearly
# win, and this is that one.
CHECKOUT_UI_EXPERIMENT_NAME = "checkout_button_color"
CHECKOUT_UI_USERS = 9000
CHECKOUT_UI_BASE_CONVERSION = 0.031
CHECKOUT_UI_TRUE_LIFT = 0.05          # relative; small tweak, deliberately small lift
CHECKOUT_UI_WINDOW_DAYS = 14

# Weekday multipliers, Monday=0 .. Sunday=6. Games peak at the weekend.
WEEKDAY = np.array([0.94, 0.92, 0.95, 0.99, 1.06, 1.18, 1.14])


# ── Signal model ────────────────────────────────────────────────────────

def _yearly(days: pd.Series) -> np.ndarray:
    """Seasonality peaking mid-December, trough in late June."""
    doy = days.dt.dayofyear.to_numpy()
    return 1.0 + 0.17 * np.cos(2 * np.pi * (doy - 350) / 365.25)


def _trend(n: int) -> np.ndarray:
    """Slow organic growth, ~+4% per 100 days."""
    return np.power(1.0004, np.arange(n))


def _promo_flags(dates: pd.DatetimeIndex, anchor: date) -> np.ndarray:
    """5-day promo events roughly every 6 weeks, anchored to a fixed cadence."""
    days_from_anchor = (dates - pd.Timestamp(anchor)).days.to_numpy()
    return ((days_from_anchor % 42) >= 37).astype(int)


def _outage_factor(dates: pd.DatetimeIndex, regions: np.ndarray,
                   outage_start: date) -> np.ndarray:
    """A 3-day APAC incident: a sharp, region-specific dip."""
    start = pd.Timestamp(outage_start)
    in_window = (dates >= start) & (dates < start + pd.Timedelta(days=3))
    factor = np.ones(len(dates))
    hit = in_window & (regions == "APAC")
    factor[hit] = 0.42
    return factor


def _launch_factor(dates: pd.DatetimeIndex, titles: np.ndarray,
                   anchor: date) -> np.ndarray:
    """Titles with a launch offset ramp from zero, with a launch-week spike."""
    factor = np.ones(len(dates))
    for title, _genre, _dau, _iap, offset in TITLES:
        if offset is None:
            continue
        launch = pd.Timestamp(anchor - timedelta(days=offset))
        mask = titles == title
        age = (dates - launch).days.to_numpy().astype(float)
        ramp = np.clip(age / 21.0, 0.0, 1.0)          # 3-week ramp to steady state
        spike = 1.0 + 0.55 * np.exp(-np.clip(age, 0, None) / 6.0)
        f = ramp * spike
        f[age < 0] = 0.0                               # not launched yet
        factor[mask] = f[mask]
    return factor


# ── Table builders ──────────────────────────────────────────────────────

def build_dim_date(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({
        "event_date": dates.strftime("%Y-%m-%d"),
        "iso_week": dates.isocalendar().week.to_numpy().astype(int),
        "month": dates.strftime("%Y-%m"),
        "quarter": dates.year.astype(str) + "-Q" + dates.quarter.astype(str),
        "day_of_week": dates.day_name(),
        "is_weekend": (dates.dayofweek >= 5).astype(int),
    })


def build_fact_daily(dates: pd.DatetimeIndex, anchor: date,
                     rng: np.random.Generator) -> pd.DataFrame:
    """Denormalised daily fact: the primary DataSource for the dashboards.

    Grain: date x title x platform x country x spender_tier.
    """
    grid = pd.MultiIndex.from_product(
        [dates,
         [t[0] for t in TITLES],
         [p[0] for p in PLATFORMS],
         [c[0] for c in COUNTRIES],
         [s[0] for s in SPENDER_TIERS]],
        names=["event_date", "title", "platform", "country", "spender_tier"],
    ).to_frame(index=False)

    d = grid["event_date"]
    n = len(grid)

    title_meta = {t[0]: (t[2], t[3]) for t in TITLES}
    plat_meta = {p[0]: (p[1], p[2]) for p in PLATFORMS}
    country_meta = {c[0]: (c[1], c[2]) for c in COUNTRIES}
    tier_meta = {s[0]: (s[1], s[2]) for s in SPENDER_TIERS}

    grid["region"] = grid["country"].map(lambda c: country_meta[c][0])

    base_dau = grid["title"].map(lambda t: title_meta[t][0]).to_numpy()
    iap_mult = grid["title"].map(lambda t: title_meta[t][1]).to_numpy()
    plat_share = grid["platform"].map(lambda p: plat_meta[p][0]).to_numpy()
    arpdau_mult = grid["platform"].map(lambda p: plat_meta[p][1]).to_numpy()
    ctry_share = grid["country"].map(lambda c: country_meta[c][1]).to_numpy()
    tier_dau_share = grid["spender_tier"].map(lambda s: tier_meta[s][0]).to_numpy()
    tier_arpdau = grid["spender_tier"].map(TIER_ARPDAU).to_numpy()
    is_payer_tier = tier_arpdau > 0

    day_index = (d - d.min()).dt.days.to_numpy()
    seasonal = _yearly(d)
    weekday = WEEKDAY[d.dt.dayofweek.to_numpy()]
    trend = _trend(len(dates))[day_index]
    promo = _promo_flags(pd.DatetimeIndex(d), anchor)
    outage = _outage_factor(pd.DatetimeIndex(d), grid["region"].to_numpy(),
                            anchor - timedelta(days=31))
    launch = _launch_factor(pd.DatetimeIndex(d), grid["title"].to_numpy(), anchor)

    noise = rng.lognormal(mean=0.0, sigma=0.11, size=n)

    dau_mean = (base_dau * plat_share * ctry_share * tier_dau_share
                * seasonal * weekday * trend * outage * launch * noise)
    dau = rng.poisson(np.clip(dau_mean, 0, None))

    sessions = rng.poisson(np.clip(dau * rng.uniform(2.4, 4.1, n), 0, None))
    new_users = rng.poisson(np.clip(dau * rng.uniform(0.03, 0.09, n)
                                    * (1 + 0.9 * launch * (launch < 1)), 0, None))

    # Revenue concentrates in the paying tiers; promos lift transactions most.
    promo_rev = 1.0 + 0.22 * promo
    promo_txn = 1.0 + 0.35 * promo
    arpdau = ARPDAU_BASE * iap_mult * arpdau_mult * tier_arpdau
    iap_revenue = np.where(
        is_payer_tier,
        dau * arpdau * promo_rev * rng.lognormal(0, 0.16, n),
        0.0,
    )
    # Ad revenue runs the other way: free users see the most ads.
    ad_revenue = dau * 0.011 * np.where(is_payer_tier, 0.45, 1.0) \
        * rng.lognormal(0, 0.13, n)

    payers = np.where(is_payer_tier,
                      rng.poisson(np.clip(dau * rng.uniform(0.18, 0.34, n), 0, None)),
                      0)
    transactions = np.where(payers > 0,
                            rng.poisson(np.clip(payers * rng.uniform(1.2, 3.4, n)
                                                * promo_txn, 0, None)),
                            0)

    grid["dau"] = dau
    grid["new_users"] = new_users
    grid["sessions"] = sessions
    grid["payers"] = payers
    grid["transactions"] = transactions
    grid["iap_revenue"] = np.round(iap_revenue, 2)
    grid["ad_revenue"] = np.round(ad_revenue, 2)
    grid["is_promo"] = promo
    grid["event_date"] = d.dt.strftime("%Y-%m-%d")

    return grid[["event_date", "title", "platform", "country", "region",
                 "spender_tier", "dau", "new_users", "sessions", "payers",
                 "transactions", "iap_revenue", "ad_revenue", "is_promo"]]


def build_fact_retention(dates: pd.DatetimeIndex, anchor: date,
                         rng: np.random.Generator) -> pd.DataFrame:
    """Long-format retention curves, one row per cohort x platform x day_number."""
    grid = pd.MultiIndex.from_product(
        [dates,
         [t[0] for t in TITLES],
         [p[0] for p in PLATFORMS],
         RETENTION_DAYS],
        names=["cohort_date", "title", "platform", "day_number"],
    ).to_frame(index=False)

    n = len(grid)
    title_meta = {t[0]: t[2] for t in TITLES}
    plat_quality = {"iOS": 1.14, "Android": 1.0, "Web": 0.78}

    cohort_size = rng.poisson(
        np.clip(grid["title"].map(title_meta).to_numpy() * 0.045
                * grid["platform"].map(plat_quality).to_numpy()
                * rng.lognormal(0, 0.14, n), 0, None)
    )

    # Power-law decay: ret(d) = ret_d1 * d^-alpha
    ret_d1 = 0.38 * grid["platform"].map(plat_quality).to_numpy()
    alpha = rng.uniform(0.42, 0.52, n)
    day = grid["day_number"].to_numpy().astype(float)
    ret_rate = np.clip(ret_d1 * np.power(day, -alpha) * rng.lognormal(0, 0.07, n),
                       0.0, 0.95)

    grid["cohort_size"] = cohort_size
    grid["retained_users"] = rng.binomial(cohort_size, ret_rate)
    grid["retention_rate"] = np.round(
        np.divide(grid["retained_users"], np.maximum(cohort_size, 1)), 4)
    grid["cohort_date"] = grid["cohort_date"].dt.strftime("%Y-%m-%d")
    return grid


def build_fact_ua_spend(dates: pd.DatetimeIndex, anchor: date,
                        rng: np.random.Generator) -> pd.DataFrame:
    """User-acquisition spend, joined against ua_budget.csv in the demo report."""
    grid = pd.MultiIndex.from_product(
        [dates,
         [t[0] for t in TITLES],
         [p[0] for p in PLATFORMS],
         [n[0] for n in NETWORKS],
         CAMPAIGN_TYPES],
        names=["event_date", "title", "platform", "network_type", "campaign_type"],
    ).to_frame(index=False)

    d = grid["event_date"]
    n = len(grid)
    title_meta = {t[0]: t[2] for t in TITLES}

    is_paid = (grid["network_type"] == "paid").to_numpy()
    campaign_mult = grid["campaign_type"].map(
        {"ua": 1.0, "retargeting": 0.42, "branding": 0.28}).to_numpy()

    seasonal = _yearly(d)
    launch = _launch_factor(pd.DatetimeIndex(d), grid["title"].to_numpy(), anchor)

    spend = (grid["title"].map(title_meta).to_numpy() * 0.021 * campaign_mult
             * seasonal * np.maximum(launch, 0.15) * rng.lognormal(0, 0.19, n))
    spend = np.where(is_paid, spend, 0.0)

    cpi = np.where(is_paid, rng.uniform(1.1, 4.6, n), 0.0)
    installs = np.where(
        is_paid,
        rng.poisson(np.clip(np.divide(spend, np.maximum(cpi, 0.01)), 0, None)),
        rng.poisson(np.clip(grid["title"].map(title_meta).to_numpy() * 0.004
                            * seasonal * np.maximum(launch, 0.1), 0, None)),
    )
    impressions = rng.poisson(np.clip(installs * rng.uniform(180, 420, n), 0, None))
    clicks = rng.poisson(np.clip(impressions * rng.uniform(0.008, 0.031, n), 0, None))

    grid["spend"] = np.round(spend, 2)
    grid["installs"] = installs
    grid["impressions"] = impressions
    grid["clicks"] = clicks
    grid["event_date"] = d.dt.strftime("%Y-%m-%d")
    return grid


def build_fact_funnel(dates: pd.DatetimeIndex, anchor: date,
                      rng: np.random.Generator) -> pd.DataFrame:
    """Long-format store funnel: one row per date x title x device x step."""
    grid = pd.MultiIndex.from_product(
        [dates,
         [t[0] for t in TITLES],
         [d[0] for d in DEVICES],
         [s[0] for s in FUNNEL_STEPS]],
        names=["event_date", "title", "device", "step"],
    ).to_frame(index=False)

    d = grid["event_date"]
    n = len(grid)
    title_meta = {t[0]: t[2] for t in TITLES}
    device_share = {dev: share for dev, share in DEVICES}
    step_rate = dict(FUNNEL_STEPS)
    step_order = {name: i for i, (name, _) in enumerate(FUNNEL_STEPS)}

    seasonal = _yearly(d)
    weekday = WEEKDAY[d.dt.dayofweek.to_numpy()]
    launch = _launch_factor(pd.DatetimeIndex(d), grid["title"].to_numpy(), anchor)

    base = (grid["title"].map(title_meta).to_numpy() * 0.30
            * grid["device"].map(device_share).to_numpy()
            * seasonal * weekday * launch)

    # Compound the device penalty with depth so deeper steps diverge more.
    depth = grid["step"].map(step_order).to_numpy()
    dev_mult = np.power(grid["device"].map(DEVICE_FUNNEL_MULT).to_numpy(), depth)
    rate = grid["step"].map(step_rate).to_numpy() * dev_mult

    grid["users"] = rng.poisson(
        np.clip(base * rate * rng.lognormal(0, 0.09, n), 0, None))
    grid["step_order"] = depth
    grid["event_date"] = d.dt.strftime("%Y-%m-%d")
    return grid


def build_fact_experiment(anchor: date, rng: np.random.Generator) -> pd.DataFrame:
    """Per-user results for one checkout A/B test.

    ``pre_period_revenue`` is correlated with post-period revenue on purpose:
    it is the covariate CUPED needs to cut variance.
    """
    n = EXPERIMENT_USERS
    variant = rng.choice(["control", "variant_b"], size=n)
    is_test = variant == "variant_b"

    assigned = pd.to_datetime(anchor - timedelta(days=28)) + pd.to_timedelta(
        rng.integers(0, 21, size=n), unit="D")

    # Pre-period spend: a heavy-tailed user-quality signal.
    pre_revenue = rng.lognormal(mean=1.1, sigma=1.25, size=n)
    pre_revenue = np.where(rng.random(n) < 0.55, 0.0, pre_revenue)
    quality = np.clip(pre_revenue / (pre_revenue.mean() + 1e-9), 0, 6)

    # Keep the user-quality tilt mild: too much overdispersion and the
    # observed lift wanders far from the configured one, which makes the
    # demo look broken rather than instructive.
    p = EXPERIMENT_BASE_CONVERSION * (1 + EXPERIMENT_TRUE_LIFT * is_test)
    p = np.clip(p * (0.88 + 0.12 * quality), 0.001, 0.95)
    converted = rng.binomial(1, p)

    # Revenue only for converters. The dependence on pre-period spend is
    # deliberately strong -- that correlation is what CUPED exploits to cut
    # variance, so a weak one makes the technique look useless.
    revenue = np.where(
        converted == 1,
        rng.lognormal(mean=2.2 + 0.62 * np.log1p(quality), sigma=0.62, size=n),
        0.0,
    )
    whale_idx = rng.choice(n, size=max(1, n // 2000), replace=False)
    revenue[whale_idx] *= rng.uniform(9, 26, size=len(whale_idx))

    # Days active inside the test window -- the denominator for ARPDAU-style
    # metrics. Converters stick around longer, which is what makes per-user
    # normalisation worth doing at all.
    active_days = np.clip(
        rng.poisson(3.5 + 4.0 * converted + 0.8 * quality) + 1, 1,
        EXPERIMENT_WINDOW_DAYS,
    )

    return pd.DataFrame({
        "user_id": np.arange(1, n + 1),
        "experiment": EXPERIMENT_NAME,
        "variant": variant,
        "assigned_date": assigned.strftime("%Y-%m-%d"),
        "platform": rng.choice([p[0] for p in PLATFORMS], size=n,
                               p=[p[1] for p in PLATFORMS]),
        "region": rng.choice(sorted({c[1] for c in COUNTRIES}), size=n),
        "exposed": 1,
        "converted": converted,
        "active_days": active_days,
        "transactions": np.where(converted == 1,
                                 1 + rng.poisson(0.8, size=n), 0),
        "revenue": np.round(revenue, 2),
        "pre_period_revenue": np.round(pre_revenue, 2),
    })


def build_fact_experiment_pricing(anchor: date, rng: np.random.Generator) -> pd.DataFrame:
    """Per-user results for the starter-pack pricing A/B test.

    Same table, same columns as :func:`build_fact_experiment` -- fact_experiment
    is a generic assignment table discriminated by ``experiment``, the way one
    warehouse table would hold every test in a real deployment. ``variant_b``
    (the $3.99 price) converts more buyers; each converter's revenue is drawn
    around its own price point, so the two effects roughly cancel in ARPU.
    """
    n = PRICING_USERS
    variant = rng.choice(["control", "variant_b"], size=n)
    is_test = variant == "variant_b"

    assigned = pd.to_datetime(anchor - timedelta(days=PRICING_WINDOW_DAYS)) + pd.to_timedelta(
        rng.integers(0, 21, size=n), unit="D")

    pre_revenue = rng.lognormal(mean=1.0, sigma=1.2, size=n)
    pre_revenue = np.where(rng.random(n) < 0.6, 0.0, pre_revenue)
    quality = np.clip(pre_revenue / (pre_revenue.mean() + 1e-9), 0, 6)

    p = PRICING_BASE_CONVERSION * (1 + PRICING_TRUE_LIFT * is_test)
    p = np.clip(p * (0.9 + 0.1 * quality), 0.001, 0.95)
    converted = rng.binomial(1, p)

    # Revenue centres on the arm's own price point -- the lower list price is
    # the whole point of the test, not noise to average away.
    price = np.where(is_test, PRICING_TEST_PRICE, PRICING_CONTROL_PRICE)
    revenue = np.where(
        converted == 1,
        price * rng.lognormal(mean=0.0, sigma=0.35, size=n),
        0.0,
    )

    active_days = np.clip(
        rng.poisson(2.5 + 2.0 * converted + 0.5 * quality) + 1, 1,
        PRICING_WINDOW_DAYS,
    )

    return pd.DataFrame({
        "user_id": 100_000 + np.arange(1, n + 1),
        "experiment": PRICING_EXPERIMENT_NAME,
        "variant": variant,
        "assigned_date": assigned.strftime("%Y-%m-%d"),
        "platform": rng.choice([p[0] for p in PLATFORMS], size=n,
                               p=[p[1] for p in PLATFORMS]),
        "region": rng.choice(sorted({c[1] for c in COUNTRIES}), size=n),
        "exposed": 1,
        "converted": converted,
        "active_days": active_days,
        "transactions": np.where(converted == 1, 1 + rng.poisson(0.3, size=n), 0),
        "revenue": np.round(revenue, 2),
        "pre_period_revenue": np.round(pre_revenue, 2),
    })


def build_fact_experiment_onboarding(anchor: date, rng: np.random.Generator) -> pd.DataFrame:
    """Per-user results for the onboarding-tutorial A/B test.

    Reuses fact_experiment's per-user shape for a test whose story is
    engagement, not revenue: ``converted`` here means "returned on day 1"
    and ``transactions`` means "sessions logged on day 1" (see
    ``onboarding-experiment/queries.py`` for the column aliases). Revenue is
    a small, near-identical trickle in both arms -- a tutorial does not sell
    anything, so any gap there should read as noise, not signal.
    """
    n = ONBOARDING_USERS
    variant = rng.choice(["control", "variant_b"], size=n)
    is_test = variant == "variant_b"

    assigned = pd.to_datetime(anchor - timedelta(days=ONBOARDING_WINDOW_DAYS)) + pd.to_timedelta(
        rng.integers(0, 14, size=n), unit="D")

    pre_revenue = rng.lognormal(mean=0.6, sigma=1.1, size=n)
    pre_revenue = np.where(rng.random(n) < 0.7, 0.0, pre_revenue)
    quality = np.clip(pre_revenue / (pre_revenue.mean() + 1e-9), 0, 6)

    p = ONBOARDING_BASE_D1_RETURN * (1 + ONBOARDING_TRUE_LIFT * is_test)
    p = np.clip(p * (0.92 + 0.08 * quality), 0.01, 0.97)
    returned_d1 = rng.binomial(1, p)

    sessions_d1 = (1 + rng.poisson(0.4, size=n)
                   + returned_d1 * (1 + rng.poisson(0.6, size=n)))

    # A wide, thin spend distribution -- not too rare and not too heavy-
    # tailed -- so the noise floor stays narrow enough that "no true effect"
    # actually renders as "not significant" instead of a spurious swing from
    # a handful of whales on one side.
    small_spend = rng.random(n) < 0.15
    revenue = np.where(small_spend, rng.lognormal(mean=1.0, sigma=0.45, size=n), 0.0)

    active_days = np.clip(
        rng.poisson(3.0 + 3.0 * returned_d1 + 0.5 * quality) + 1, 1,
        ONBOARDING_WINDOW_DAYS,
    )

    return pd.DataFrame({
        "user_id": 200_000 + np.arange(1, n + 1),
        "experiment": ONBOARDING_EXPERIMENT_NAME,
        "variant": variant,
        "assigned_date": assigned.strftime("%Y-%m-%d"),
        "platform": rng.choice([p[0] for p in PLATFORMS], size=n,
                               p=[p[1] for p in PLATFORMS]),
        "region": rng.choice(sorted({c[1] for c in COUNTRIES}), size=n),
        "exposed": 1,
        "converted": returned_d1,
        "active_days": active_days,
        "transactions": sessions_d1,
        "revenue": np.round(revenue, 2),
        "pre_period_revenue": np.round(pre_revenue, 2),
    })


def build_fact_experiment_checkout_ui(anchor: date, rng: np.random.Generator) -> pd.DataFrame:
    """Per-user results for the Northwind checkout-button colour A/B test.

    Shop-sized audience, shop-sized revenue: ``platform`` carries device
    values (Mobile/Desktop/Tablet) rather than the Nova Play platforms, so
    the report can alias it to "Device" the way ``queries.py`` does for
    every other Northwind report. The lift is deliberately small relative to
    n, so this test is not guaranteed to reach significance -- a portfolio
    where every test wins is not a believable portfolio.
    """
    n = CHECKOUT_UI_USERS
    variant = rng.choice(["control", "variant_b"], size=n)
    is_test = variant == "variant_b"

    assigned = pd.to_datetime(anchor - timedelta(days=CHECKOUT_UI_WINDOW_DAYS)) + pd.to_timedelta(
        rng.integers(0, 10, size=n), unit="D")

    pre_revenue = rng.lognormal(mean=0.9, sigma=1.0, size=n)
    pre_revenue = np.where(rng.random(n) < 0.65, 0.0, pre_revenue)
    quality = np.clip(pre_revenue / (pre_revenue.mean() + 1e-9), 0, 6)

    p = CHECKOUT_UI_BASE_CONVERSION * (1 + CHECKOUT_UI_TRUE_LIFT * is_test)
    p = np.clip(p * (0.94 + 0.06 * quality), 0.001, 0.95)
    converted = rng.binomial(1, p)

    # Button colour does not change what is in the cart -- order value is
    # drawn from the same distribution in both arms.
    revenue = np.where(
        converted == 1,
        rng.lognormal(mean=4.0, sigma=0.55, size=n),
        0.0,
    )

    active_days = np.clip(
        rng.poisson(1.5 + 1.5 * converted + 0.3 * quality) + 1, 1,
        CHECKOUT_UI_WINDOW_DAYS,
    )

    return pd.DataFrame({
        "user_id": 300_000 + np.arange(1, n + 1),
        "experiment": CHECKOUT_UI_EXPERIMENT_NAME,
        "variant": variant,
        "assigned_date": assigned.strftime("%Y-%m-%d"),
        "platform": rng.choice([d[0] for d in SHOP_DEVICES], size=n,
                               p=[d[1] for d in SHOP_DEVICES]),
        "region": rng.choice(sorted({c[1] for c in COUNTRIES}), size=n),
        "exposed": 1,
        "converted": converted,
        "active_days": active_days,
        "transactions": np.where(converted == 1, 1, 0),
        "revenue": np.round(revenue, 2),
        "pre_period_revenue": np.round(pre_revenue, 2),
    })


# ── Northwind Threads: the e-commerce vertical ──────────────────────────
#
# A second business in the same warehouse, so the demo can show reports
# shaped by different questions -- a games publisher asks about retention and
# spend depth, a shop asks where carts die and what gets sent back. The shop
# data is deliberately NOT a reskin of the gaming tables: order-grain rows,
# a catalog to join, and a funnel measured in sessions rather than users.

SHOP_CHANNELS = [
    # channel,          share, conversion_mult
    ("Organic Search",  0.34,  1.00),
    ("Paid Social",     0.27,  0.68),   # cheap sessions, cold buyers
    ("Email",           0.14,  1.62),   # tiny, hot -- the contrast is the point
    ("Direct",          0.25,  1.18),
]

SHOP_DEVICES = [
    # device,   share, conversion_mult
    ("Mobile",  0.63,  0.72),   # browses most, buys least
    ("Desktop", 0.29,  1.42),
    ("Tablet",  0.08,  0.95),
]

#: sessions -> ... -> purchases, each stage as a fraction of the previous.
#: Lands overall conversion near 2.9% and cart abandonment near 70% -- the
#: unglamorous industry reality the cart-funnel report exists to show.
SHOP_FUNNEL_KEEP = [
    ("product_views", 0.56),
    ("add_to_cart",   0.19),
    ("checkouts",     0.47),
    ("purchases",     0.55),
]

#: The catalog. price/margin_pct set unit economics; return_rate is the
#: category's structural rate (apparel sizing drives it, so Footwear is high
#: and Accessories negligible) -- the product-returns report recovers these.
SHOP_PRODUCTS = [
    # product,                category,      price, margin_pct, return_rate
    ("Harbor Tee",            "Tops",         24.0,  0.62, 0.11),
    ("Meridian Henley",       "Tops",         38.0,  0.58, 0.12),
    ("Coastline Flannel",     "Tops",         54.0,  0.55, 0.13),
    ("Latitude Oxford",       "Tops",         62.0,  0.54, 0.14),
    ("Drift Chinos",          "Bottoms",      68.0,  0.51, 0.17),
    ("Ridge Denim",           "Bottoms",      84.0,  0.49, 0.19),
    ("Summit Joggers",        "Bottoms",      52.0,  0.56, 0.13),
    ("Breaker Shorts",        "Bottoms",      42.0,  0.57, 0.10),
    ("Squall Rain Shell",     "Outerwear",   148.0,  0.44, 0.15),
    ("Northgale Parka",       "Outerwear",   238.0,  0.41, 0.18),
    ("Ember Puffer",          "Outerwear",   172.0,  0.43, 0.16),
    ("Crosswind Bomber",      "Outerwear",   126.0,  0.46, 0.14),
    ("Wayfare Sneaker",       "Footwear",     92.0,  0.47, 0.24),
    ("Tidal Slip-On",         "Footwear",     74.0,  0.49, 0.21),
    ("Granite Boot",          "Footwear",    158.0,  0.45, 0.22),
    ("Pace Runner",           "Footwear",    118.0,  0.46, 0.25),
    ("Anchor Belt",           "Accessories",  32.0,  0.68, 0.04),
    ("Compass Beanie",        "Accessories",  22.0,  0.71, 0.03),
    ("Meridian Tote",         "Accessories",  48.0,  0.64, 0.05),
    ("Harbor Socks 3-pack",   "Accessories",  16.0,  0.74, 0.02),
]

#: Relative sales weight per product. Footwear sells hard, which together with
#: its return rate is the tension the product-returns report surfaces: the
#: best sellers are also the most sent back.
SHOP_PRODUCT_WEIGHT = {
    "Wayfare Sneaker": 2.6, "Pace Runner": 2.1, "Harbor Tee": 2.4,
    "Ridge Denim": 1.8, "Summit Joggers": 1.6, "Tidal Slip-On": 1.5,
    "Meridian Henley": 1.4, "Drift Chinos": 1.3, "Breaker Shorts": 1.1,
    "Coastline Flannel": 1.0, "Squall Rain Shell": 0.9, "Ember Puffer": 0.8,
    "Granite Boot": 0.8, "Latitude Oxford": 0.7, "Crosswind Bomber": 0.7,
    "Compass Beanie": 0.9, "Anchor Belt": 0.8, "Harbor Socks 3-pack": 1.2,
    "Meridian Tote": 0.6, "Northgale Parka": 0.5,
}

#: Retail weekday shape: strong early week, Friday/Saturday trough, Sunday
#: evening recovery -- deliberately different from the games' weekend peak.
SHOP_WEEKDAY = np.array([1.09, 1.07, 1.01, 0.97, 0.90, 0.86, 1.04])

SHOP_BASE_SESSIONS = 4200          # per day across all channels and devices
SHOP_PROMO_OFFSET_DAYS = 35        # mid-season sale starts this long before anchor
SHOP_PROMO_LEN_DAYS = 6
SHOP_STOCKOUT_OFFSET_DAYS = 12     # hero sneaker out of stock, 4 days
SHOP_STOCKOUT_LEN_DAYS = 4
SHOP_STOCKOUT_PRODUCT = "Wayfare Sneaker"


def _shop_promo_mult(dates: pd.DatetimeIndex, anchor: date) -> np.ndarray:
    """Session lift during the mid-season sale window."""
    start = pd.Timestamp(anchor - timedelta(days=SHOP_PROMO_OFFSET_DAYS))
    in_promo = (dates >= start) & (dates < start + pd.Timedelta(days=SHOP_PROMO_LEN_DAYS))
    return np.where(in_promo, 1.42, 1.0)


def build_shop_products() -> pd.DataFrame:
    return pd.DataFrame([
        {"product_id": f"NW-{i:03d}", "product": name, "category": cat,
         "price": price, "margin_pct": margin, }
        for i, (name, cat, price, margin, _ret) in enumerate(SHOP_PRODUCTS, 1)
    ])


def build_shop_traffic(dates: pd.DatetimeIndex, anchor: date,
                       rng: np.random.Generator) -> pd.DataFrame:
    """Daily funnel counts per (date, channel, device).

    Counts, not per-session rows: a shop this size sees ~6k sessions a day and
    nobody needs 2.6M rows to draw a funnel. The chain is drawn stage by stage
    from the previous stage's survivors, so every row is internally monotone
    -- a validator-friendly property a per-stage independent draw would break.
    """
    grid = pd.MultiIndex.from_product(
        [dates, [c[0] for c in SHOP_CHANNELS], [d[0] for d in SHOP_DEVICES]],
        names=["event_date", "channel", "device"],
    ).to_frame(index=False)

    d = pd.DatetimeIndex(grid["event_date"])
    n = len(grid)

    ch_share = {c[0]: c[1] for c in SHOP_CHANNELS}
    ch_conv = {c[0]: c[2] for c in SHOP_CHANNELS}
    dev_share = {v[0]: v[1] for v in SHOP_DEVICES}
    dev_conv = {v[0]: v[2] for v in SHOP_DEVICES}

    seasonal = 1.0 + 0.24 * np.cos(2 * np.pi * (d.dayofyear - 352) / 365.25)
    weekday = SHOP_WEEKDAY[d.weekday]
    promo = _shop_promo_mult(d, anchor)

    # Trend over DAYS, not rows: _trend(n) compounds per element, and this
    # grid has 12 rows per day -- feeding it the row count inflated the last
    # day by 7.5x before this was caught in the generated totals.
    day_trend = np.power(1.0004, (d - d.min()).days.to_numpy())
    base = (SHOP_BASE_SESSIONS
            * grid["channel"].map(ch_share).to_numpy()
            * grid["device"].map(dev_share).to_numpy()
            * seasonal * weekday * promo
            * day_trend * rng.lognormal(0, 0.08, n))
    sessions = rng.poisson(np.clip(base, 0, None))

    # Promo traffic converts a touch worse per session (bargain browsers), but
    # the volume more than compensates -- both effects should be visible.
    conv_mult = (grid["channel"].map(ch_conv).to_numpy()
                 * grid["device"].map(dev_conv).to_numpy()
                 * np.where(promo > 1.0, 0.92, 1.0))

    grid["sessions"] = sessions
    prev = sessions
    for stage, keep in SHOP_FUNNEL_KEEP:
        # Only the middle of the funnel is channel/device sensitive; browsing
        # depth is universal, checkout completion is where intent shows.
        stage_keep = np.clip(
            keep * (conv_mult ** (0.5 if stage != "product_views" else 0.1)),
            0.01, 0.97)
        prev = rng.binomial(prev, stage_keep)
        grid[stage] = prev

    grid["event_date"] = d.strftime("%Y-%m-%d")
    return grid


def build_shop_orders(traffic: pd.DataFrame, anchor: date,
                      rng: np.random.Generator) -> pd.DataFrame:
    """One row per order, drawn FROM the traffic table's purchase counts.

    Derived rather than generated independently so the two tables agree: the
    cart-funnel report's "purchases" and the store-performance report's order
    count are the same physical events, and a demo where they disagree teaches
    the reader to distrust the tool.
    """
    src = traffic.loc[traffic["purchases"] > 0,
                      ["event_date", "channel", "device", "purchases"]]
    orders = src.loc[src.index.repeat(src["purchases"])].drop(columns="purchases")
    orders = orders.reset_index(drop=True)
    n = len(orders)

    catalog = build_shop_products().set_index("product")
    names = [p[0] for p in SHOP_PRODUCTS]
    weights = np.array([SHOP_PRODUCT_WEIGHT[p] for p in names], dtype=float)

    d = pd.DatetimeIndex(orders["event_date"])
    promo = _shop_promo_mult(d, anchor) > 1.0

    # The stockout: the hero product cannot be bought for a few days. Its
    # weight goes to zero in that window, which both dents Footwear revenue
    # and shows up as a hole in the product-returns table.
    out_start = pd.Timestamp(anchor - timedelta(days=SHOP_STOCKOUT_OFFSET_DAYS))
    in_stockout = ((d >= out_start) &
                   (d < out_start + pd.Timedelta(days=SHOP_STOCKOUT_LEN_DAYS)))

    w = np.tile(weights, (n, 1))
    w[in_stockout, names.index(SHOP_STOCKOUT_PRODUCT)] = 0.0
    w = w / w.sum(axis=1, keepdims=True)
    # Vectorised weighted choice via inverse CDF, one draw per order.
    cdf = np.cumsum(w, axis=1)
    pick = (rng.random(n)[:, None] < cdf).argmax(axis=1)
    product = np.array(names)[pick]

    price = catalog.loc[product, "price"].to_numpy()
    margin_pct = catalog.loc[product, "margin_pct"].to_numpy()
    ret_rate = np.array([SHOP_PRODUCTS[i][4] for i in pick])

    qty = 1 + rng.binomial(2, 0.18, n)
    # Sale pricing: promo orders carry a real discount; a few off-promo orders
    # use a welcome code. Margin is computed on what was actually charged.
    discount = np.where(promo, rng.choice([0.15, 0.20, 0.25], n), 0.0)
    welcome = (~promo) & (rng.random(n) < 0.06)
    discount = np.where(welcome, 0.10, discount)

    gross = price * qty * (1.0 - discount)
    margin = gross - (price * (1.0 - margin_pct)) * qty

    # Returns: category-structural, slightly worse for discounted impulse
    # buys. Only delivered orders can come back, so the last few days before
    # the anchor return less -- the report should NOT read that dip as
    # improvement, which is exactly the trap its notes warn about.
    days_to_anchor = (pd.Timestamp(anchor) - d).days.to_numpy()
    deliverable = np.clip(days_to_anchor / 10.0, 0.0, 1.0)
    returned = rng.random(n) < (ret_rate * np.where(discount > 0, 1.25, 1.0)
                                * deliverable)

    countries = rng.choice([c[0] for c in COUNTRIES], n,
                           p=[c[2] for c in COUNTRIES])

    return pd.DataFrame({
        "order_id": [f"NW{100000 + i}" for i in range(n)],
        "order_date": orders["event_date"],
        "channel": orders["channel"],
        "device": orders["device"],
        "country": countries,
        "product_id": catalog.loc[product, "product_id"].to_numpy(),
        "product": product,
        "category": catalog.loc[product, "category"].to_numpy(),
        "qty": qty,
        "discount_pct": np.round(discount, 2),
        "gross_revenue": np.round(gross, 2),
        "margin": np.round(margin, 2),
        "returned": returned.astype(int),
    })


# ── The economy firehose ────────────────────────────────────────────────
#
# One deliberately enormous table: every currency source and sink in the
# Nova Play games, at date x title x platform x spender_tier x feature grain.
# It exists so one demo report can be honestly named "a lot of data" -- over a
# million warehouse rows, a seven-figure table alive in the browser -- because
# a claim about scale is only worth making against data big enough to hurt.
#
# The stories baked in, so the big report has something to find:
# - The forge cost rebalance (anchor-45) halves the biggest coin sink
#   overnight. Sources do not move, so net coin flow inflates from that day --
#   the classic economy bug, visible as a step in the net-flow chart.
# - A gacha banner runs 3 days in every 14: gem sinks and gem-pack sources
#   spike together, which is what a healthy monetisation loop looks like.
# - Whales dominate gem flows while non-spenders dominate the coin loops, so
#   the spender-tier filter reshapes every chart it touches.

#: (feature, kind, currency, daily_scale, tier_profile)
#: Scales are per-day economy-wide amounts before shares and noise; "premium"
#: features skew to paying tiers, "core" features to the free majority.
ECONOMY_FEATURES = [
    # Coin sources -- the earn loop.
    ("Quest rewards",        "source", "coins", 5_200_000, "core"),
    ("Level completion",     "source", "coins", 3_900_000, "core"),
    ("Daily login",          "source", "coins", 2_100_000, "core"),
    ("Daily streak bonus",   "source", "coins", 1_150_000, "core"),
    ("Achievement bonus",    "source", "coins",   760_000, "core"),
    ("Clan war victory",     "source", "coins", 1_480_000, "mixed"),
    ("Season rewards",       "source", "coins", 1_020_000, "mixed"),
    ("Event race payout",    "source", "coins",   890_000, "mixed"),
    ("Rewarded ads",         "source", "coins", 1_650_000, "free"),
    ("Comeback gift",        "source", "coins",   310_000, "free"),
    ("Referral bonus",       "source", "coins",   140_000, "core"),
    ("Mail compensation",    "source", "coins",   205_000, "core"),
    # Coin sinks -- where it is supposed to go.
    ("Forge upgrades",       "sink",   "coins", 6_400_000, "mixed"),
    ("Gear repairs",         "sink",   "coins", 2_300_000, "core"),
    ("Crafting materials",   "sink",   "coins", 2_950_000, "core"),
    ("Market tax",           "sink",   "coins", 1_240_000, "mixed"),
    ("Energy refills",       "sink",   "coins", 1_060_000, "core"),
    ("Build speedups",       "sink",   "coins", 1_890_000, "mixed"),
    ("Trait rerolls",        "sink",   "coins",   940_000, "premium"),
    ("Cosmetic dyes",        "sink",   "coins",   520_000, "mixed"),
    ("Guild donations",      "sink",   "coins",   680_000, "core"),
    ("Tournament entries",   "sink",   "coins",   450_000, "premium"),
    ("Loadout slots",        "sink",   "coins",   260_000, "mixed"),
    ("Map rerolls",          "sink",   "coins",   380_000, "core"),
    # Gem sources -- almost all bought.
    ("Gem pack purchases",   "source", "gems",    455_000, "premium"),
    ("Season pass bonus",    "source", "gems",     88_000, "premium"),
    ("Achievement gems",     "source", "gems",     36_000, "core"),
    ("Compensation grants",  "source", "gems",     19_000, "core"),
    ("First-clear gems",     "source", "gems",     41_000, "mixed"),
    # Gem sinks -- the monetisation loop.
    ("Gacha pulls",          "sink",   "gems",    340_000, "premium"),
    ("Premium cosmetics",    "sink",   "gems",    118_000, "premium"),
    ("Battle pass unlock",   "sink",   "gems",     72_000, "mixed"),
    ("Inventory expansion",  "sink",   "gems",     33_000, "mixed"),
    ("Gem energy refills",   "sink",   "gems",     54_000, "premium"),
    ("Pity counter breaks",  "sink",   "gems",     27_000, "premium"),
    ("Name changes",         "sink",   "gems",      4_000, "free"),
]

#: How each profile splits across SPENDER_TIERS (non_spender, minnow, dolphin,
#: whale). Rows sum to 1.
ECONOMY_TIER_WEIGHTS = {
    "core":    [0.58, 0.24, 0.13, 0.05],
    "free":    [0.83, 0.12, 0.04, 0.01],
    "mixed":   [0.34, 0.28, 0.24, 0.14],
    "premium": [0.05, 0.17, 0.36, 0.42],
}

#: The ledger keeps deeper history than the aggregated marts beside it, which
#: is how warehouses actually work: the rolled-up daily tables get trimmed,
#: the raw transaction ledger is the one nobody dares delete. Practically it
#: is what carries this table past two million rows (3.7 x 420 days x 1,296
#: rows per day = 2,013,984) without inventing dimensions the business
#: doesn't have -- and because the report chunks by month, deeper history is
#: nearly free on first paint: more months exist, the same three load inline.
#:
#: The odd-looking 3.7 is a measured ceiling, not a round number. Every row
#: of this table costs ~859 bytes of JS heap once it is objects in a browser
#: (19 keys: 9 from SQL plus the 10 derived zero-filled columns that keep
#: client-side aggregation a plain sum). Chrome's hard heap limit is 4 GB, so
#: 2M rows sits near 1.7 GB -- deliberately under half, because a demo has to
#: survive a laptop with a smaller cap, and a tab that exceeds it does not
#: slow down, it dies. 10M rows would project to ~8 GB and is not reachable
#: by loading rows into a page at all; that would need the report to window a
#: bigger table rather than hold it.
ECONOMY_HISTORY_MULT = 3.7

ECONOMY_FORGE_NERF_OFFSET_DAYS = 45   # forge sink halves from this day on
ECONOMY_BANNER_PERIOD_DAYS = 14       # a gacha banner runs 3 days in every 14
ECONOMY_BANNER_FEATURES = {
    "Gacha pulls": 2.6, "Gem pack purchases": 2.0, "Premium cosmetics": 1.35,
}


def build_fact_economy(dates: pd.DatetimeIndex, anchor: date,
                       rng: np.random.Generator) -> pd.DataFrame:
    """Every source and sink, at full grain, fully vectorised.

    ~2.01M rows at the default 420 days, because this table keeps
    ``ECONOMY_HISTORY_MULT`` times the marts' history (see that constant).
    Loops would take minutes here; everything is a numpy broadcast over the
    cross-product grid.
    """
    # Same end date as everything else, just a longer tail behind it.
    dates = pd.date_range(end=dates[-1],
                          periods=int(len(dates) * ECONOMY_HISTORY_MULT),
                          freq="D")
    features = [f[0] for f in ECONOMY_FEATURES]
    grid = pd.MultiIndex.from_product(
        [dates,
         [t[0] for t in TITLES],
         [p[0] for p in PLATFORMS],
         [s[0] for s in SPENDER_TIERS],
         features],
        names=["event_date", "title", "platform", "spender_tier", "feature"],
    ).to_frame(index=False)

    d = pd.DatetimeIndex(grid["event_date"])
    n = len(grid)

    f_kind = {f[0]: f[1] for f in ECONOMY_FEATURES}
    f_curr = {f[0]: f[2] for f in ECONOMY_FEATURES}
    f_scale = {f[0]: float(f[3]) for f in ECONOMY_FEATURES}
    f_prof = {f[0]: f[4] for f in ECONOMY_FEATURES}
    tier_ix = {s[0]: i for i, s in enumerate(SPENDER_TIERS)}
    title_share = {t[0]: t[2] / sum(x[2] for x in TITLES) for t in TITLES}
    plat_share = {p[0]: p[1] for p in PLATFORMS}

    tier_w = np.array([
        ECONOMY_TIER_WEIGHTS[f_prof[f]][tier_ix[s]]
        for f, s in zip(grid["feature"], grid["spender_tier"])
    ])

    base = (grid["feature"].map(f_scale).to_numpy()
            * grid["title"].map(title_share).to_numpy()
            * grid["platform"].map(plat_share).to_numpy()
            * tier_w)

    seasonal = _yearly(pd.Series(d))
    weekday = WEEKDAY[d.weekday]
    day_trend = np.power(1.0004, (d - d.min()).days.to_numpy())
    launch = _launch_factor(d, grid["title"].to_numpy(), anchor)

    # The forge rebalance: the biggest coin sink halves overnight and never
    # recovers. Sources do not move, so the economy starts inflating -- the
    # step the firehose report exists to make visible.
    nerf_start = pd.Timestamp(anchor - timedelta(days=ECONOMY_FORGE_NERF_OFFSET_DAYS))
    nerf = np.where((grid["feature"] == "Forge upgrades").to_numpy()
                    & np.asarray(d >= nerf_start), 0.55, 1.0)

    # Gacha banners: 3 days in every 14, spiking the paired features together.
    days_from_anchor = (d - pd.Timestamp(anchor)).days.to_numpy()
    in_banner = (days_from_anchor % ECONOMY_BANNER_PERIOD_DAYS) >= (
        ECONOMY_BANNER_PERIOD_DAYS - 3)
    banner_mult = grid["feature"].map(
        lambda f: ECONOMY_BANNER_FEATURES.get(f, 1.0)).to_numpy()
    banner = np.where(in_banner, banner_mult, 1.0)

    amount = (base * seasonal * weekday * day_trend * launch * nerf * banner
              * rng.lognormal(0, 0.13, n))

    # A transaction count that scales sub-linearly with amount, so per-txn
    # sizes differ by feature and the report can do amount/txn ratios.
    txns = rng.poisson(np.clip(np.sqrt(amount) * 0.9, 0, None))

    return pd.DataFrame({
        "event_date": d.strftime("%Y-%m-%d"),
        "title": grid["title"],
        "platform": grid["platform"],
        "spender_tier": grid["spender_tier"],
        "feature": grid["feature"],
        "kind": grid["feature"].map(f_kind),
        "currency": grid["feature"].map(f_curr),
        "amount": np.round(amount, 0),
        "transactions": txns,
    })


# ── Side files ──────────────────────────────────────────────────────────

#: Player behaviours that genuinely overlap, unlike every other dimension in
#: this warehouse. Platform, country and spender tier are all partitions -- a
#: player is in exactly one -- so none of them can demonstrate a chart whose
#: whole subject is intersection. These are independent behaviours a player can
#: do any combination of.
#:
#: Base rates are per-title-agnostic and deliberately unequal, so the diagram
#: has something to say: most spenders also play daily, but plenty of daily
#: players never spend.
SEGMENTS = [
    # segment,     P(member)
    ("Spent money",   0.115),
    ("Plays daily",   0.340),
    ("Joined a clan", 0.205),
]

#: How much each pair pulls together, as a multiplier on the independent
#: probability. Above 1.0 means the behaviours co-occur more than chance --
#: which is the interesting part, and the reason a Venn beats three bars.
SEGMENT_AFFINITY = {
    ("Spent money", "Plays daily"): 2.35,
    ("Spent money", "Joined a clan"): 1.70,
    ("Plays daily", "Joined a clan"): 1.55,
}

PLAYER_SEGMENT_USERS = 25000


def build_fact_player_segments(anchor: date,
                               rng: np.random.Generator) -> pd.DataFrame:
    """One row per (player, behaviour) -- long format, so a player recurs.

    Long rather than one boolean column per behaviour, for the same reason the
    rest of this warehouse is long: adding a fourth behaviour then costs a row
    rather than a schema change, and the framework's filters work on values in
    a column rather than on column names.
    """
    names = [s for s, _ in SEGMENTS]
    base = {s: p for s, p in SEGMENTS}

    # Draw a per-player latent "engagement" and let it lift every behaviour.
    # A shared driver is what produces realistic overlap: without it the sets
    # intersect at exactly the product of their rates and the diagram is dull
    # in a way real player data never is.
    engagement = rng.beta(2.0, 3.0, PLAYER_SEGMENT_USERS)
    titles = rng.choice([t[0] for t in TITLES], PLAYER_SEGMENT_USERS)
    platforms = rng.choice([p[0] for p in PLATFORMS], PLAYER_SEGMENT_USERS,
                           p=[p[1] for p in PLATFORMS])

    rows = []
    for i in range(PLAYER_SEGMENT_USERS):
        lift = 0.45 + 1.55 * engagement[i]
        member = []
        for name in names:
            p = min(0.97, base[name] * lift)
            # Nudge by affinity with behaviours already drawn for this player.
            for prior in member:
                key = (prior, name) if (prior, name) in SEGMENT_AFFINITY \
                    else (name, prior)
                p = min(0.97, p * SEGMENT_AFFINITY.get(key, 1.0) ** 0.5)
            if rng.random() < p:
                member.append(name)
        for name in member:
            rows.append({
                "user_id": f"u{i:06d}",
                "title": titles[i],
                "platform": platforms[i],
                "segment": name,
                "cohort_date": (anchor - timedelta(
                    days=int(rng.integers(0, 60)))).isoformat(),
            })
    return pd.DataFrame(rows)


# ── The journey graph ───────────────────────────────────────────────────
#: The places a session can be, and how it moves between them. The demo's
#: journey section renders exactly these node names; a drift guard in the
#: test suite keeps the two in step.
JOURNEY_SOURCES = ("paid", "organic", "retarget")
JOURNEY_LEVELS = ("easy", "mid", "hard")

#: Share of each tier's entering sessions per acquisition source.
#: Retargeting is how lapsed payers come back, so it skews to the big tiers.
JOURNEY_SOURCE_MIX = {
    "non_spender": {"paid": 0.40, "organic": 0.50, "retarget": 0.10},
    "minnow":      {"paid": 0.45, "organic": 0.40, "retarget": 0.15},
    "dolphin":     {"paid": 0.45, "organic": 0.30, "retarget": 0.25},
    "whale":       {"paid": 0.35, "organic": 0.25, "retarget": 0.40},
}
#: Where each source drops players. Retargeting returns them mid-game.
JOURNEY_ENTRY = {
    "paid":     {"easy": 0.70, "mid": 0.30, "hard": 0.00},
    "organic":  {"easy": 0.60, "mid": 0.30, "hard": 0.10},
    "retarget": {"easy": 0.15, "mid": 0.45, "hard": 0.40},
}
JOURNEY_P_WIN = {"easy": 0.72, "mid": 0.55, "hard": 0.36}
#: The monetisation story, per tier: buying after a win is celebration and
#: rare; buying after a loss is the whale move.
JOURNEY_BUY_AFTER_WIN = {
    "non_spender": 0.01, "minnow": 0.02, "dolphin": 0.05, "whale": 0.12,
}
JOURNEY_BUY_AFTER_LOSS = {
    "non_spender": 0.03, "minnow": 0.08, "dolphin": 0.22, "whale": 0.45,
}
JOURNEY_EXIT_AFTER_LOSS = {
    "non_spender": 0.45, "minnow": 0.34, "dolphin": 0.22, "whale": 0.10,
}
#: Non-spenders visit checkout (curiosity, gift screens) and never buy.
JOURNEY_CHECKOUT_BUY = {
    "non_spender": 0.00, "minnow": 0.38, "dolphin": 0.62, "whale": 0.86,
}


def build_fact_journey(dates: pd.DatetimeIndex, anchor: date,
                       rng: np.random.Generator) -> pd.DataFrame:
    """Session flows between the places of the game, as edge transitions.

    Grain: date x title x spender_tier x (from_node, to_node). Counts come
    from pushing each day's entering sessions through the graph for a few
    laps -- players retry, climb the ladder, buy and dive back in -- so the
    edges stay conservation-consistent: what enters a node leaves it.

    Stories baked in: retargeting re-enters the big tiers mid-game; the big
    tiers reach checkout overwhelmingly through LOSSES; promo days convert
    frustration at a higher rate; non-spenders reach checkout and never buy;
    purchases loop players back into the level that made them pay.
    """
    n = len(dates)
    weekday = WEEKDAY[dates.dayofweek.to_numpy()]
    seasonal = _yearly(pd.Series(dates))
    promo = _promo_flags(dates, anchor).astype(float)
    date_strs = dates.strftime("%Y-%m-%d")

    advance = {"easy": "mid", "mid": "hard", "hard": "hard"}
    frames = []
    for title, _genre, base_dau, _iap, _offset in TITLES:
        launch = _launch_factor(dates, np.full(n, title, dtype=object), anchor)
        for tier, dau_share, _rev in SPENDER_TIERS:
            noise = rng.lognormal(0.0, 0.10, n)
            entries = (base_dau * 0.15 * dau_share
                       * weekday * seasonal * launch * noise)
            buy_loss = np.minimum(
                0.9, JOURNEY_BUY_AFTER_LOSS[tier] * (1 + 0.6 * promo))

            edges: dict[tuple[str, str], np.ndarray] = {}

            def add(frm: str, to: str, cnt: np.ndarray) -> None:
                key = (frm, to)
                edges[key] = edges.get(key, 0.0) + cnt

            plays = {lvl: np.zeros(n) for lvl in JOURNEY_LEVELS}
            for src in JOURNEY_SOURCES:
                src_n = entries * JOURNEY_SOURCE_MIX[tier][src]
                for lvl, share in JOURNEY_ENTRY[src].items():
                    if share:
                        add(src, lvl, src_n * share)
                        plays[lvl] = plays[lvl] + src_n * share

            # A few laps around the loop: retries, ladder advances and
            # post-purchase returns feed the next lap. The flow decays
            # geometrically, so six laps capture effectively all of it.
            for _lap in range(6):
                nxt = {lvl: np.zeros(n) for lvl in JOURNEY_LEVELS}
                for lvl in JOURNEY_LEVELS:
                    p = plays[lvl]
                    wins = p * JOURNEY_P_WIN[lvl]
                    losses = p - wins
                    add(lvl, "win", wins)
                    add(lvl, "lose", losses)
                    # after a win: rare celebration buy, otherwise climb
                    win_buy = wins * JOURNEY_BUY_AFTER_WIN[tier]
                    add("win", "checkout", win_buy)
                    up = advance[lvl]
                    add("win", up, wins - win_buy)
                    nxt[up] = nxt[up] + (wins - win_buy)
                    # after a loss: buy, quit, or retry the same level
                    loss_buy = losses * buy_loss
                    loss_exit = losses * JOURNEY_EXIT_AFTER_LOSS[tier]
                    retry = losses - loss_buy - loss_exit
                    add("lose", "checkout", loss_buy)
                    add("lose", "exit", loss_exit)
                    add("lose", lvl, retry)
                    nxt[lvl] = nxt[lvl] + retry
                    # checkout resolves; a purchase returns to this level
                    reached = win_buy + loss_buy
                    bought = reached * JOURNEY_CHECKOUT_BUY[tier]
                    add("checkout", "purchase", bought)
                    add("checkout", "exit", reached - bought)
                    add("purchase", lvl, bought)
                    nxt[lvl] = nxt[lvl] + bought
                plays = nxt

            for (frm, to), counts in edges.items():
                sampled = rng.poisson(counts)
                mask = sampled > 0
                if not mask.any():
                    continue
                frames.append(pd.DataFrame({
                    "event_date": date_strs[mask],
                    "title": title,
                    "spender_tier": tier,
                    "from_node": frm,
                    "to_node": to,
                    "transitions": sampled[mask].astype(int),
                }))

    return pd.concat(frames, ignore_index=True)


# ── Per-user event log ──────────────────────────────────────────────────
#
# The one table in the warehouse at USER grain over TIME: raw event rows,
# the shape that is deliberately too big to compile into a report artifact.
# The user-event-log demo declares a live query over it instead — this
# table exists so that demo has something honest to look up.

USER_EVENT_USERS = 1000          # ids USER_EVENT_ID_BASE .. +USERS-1
USER_EVENT_ID_BASE = 1000        # so the demo's "e.g. 1042" always exists
#: The user the demo report bakes its snapshot with. Forced to a paying
#: tier below so the compiled page shows a log with purchases in it.
USER_EVENT_SNAPSHOT_USER = 1042

_USER_EVENT_PACKS = {
    # tier -> (pack label, price) choices; weights follow spend depth
    "minnow":  [("starter pack", 4.99), ("coin pouch", 1.99)],
    "dolphin": [("gem doubler", 9.99), ("starter pack", 4.99)],
    "whale":   [("vault key", 24.99), ("founders crate", 99.99)],
}


def build_fact_user_events(dates: pd.DatetimeIndex, anchor: date,
                           rng: np.random.Generator) -> pd.DataFrame:
    """Raw per-user event rows: sessions, level outcomes, purchases.

    Grain: one row per event, ``user_id`` + ``ts`` ordered within a session.
    The stories match the journey graph's: losses outnumber wins on hard
    levels, purchases follow losses for the paying tiers, and non-spenders
    never buy. Volume scales with the window (about 1.2 sessions per
    user-week), so ``--small`` stays fast.
    """
    n_days = len(dates)
    tiers = [t for t, _, _ in SPENDER_TIERS]
    tier_p = np.array([s for _, s, _ in SPENDER_TIERS])
    user_ids = np.arange(USER_EVENT_ID_BASE, USER_EVENT_ID_BASE + USER_EVENT_USERS)
    user_tier = rng.choice(tiers, USER_EVENT_USERS, p=tier_p)
    # The snapshot user gets a story worth reading: a spender with sessions.
    user_tier[USER_EVENT_SNAPSHOT_USER - USER_EVENT_ID_BASE] = "dolphin"
    user_title = rng.choice([t[0] for t in TITLES], USER_EVENT_USERS)
    user_platform = rng.choice([p[0] for p in PLATFORMS], USER_EVENT_USERS,
                               p=[p[1] for p in PLATFORMS])

    buy_after_loss = {t: JOURNEY_BUY_AFTER_LOSS[t] for t in tiers}
    date_strs = dates.strftime("%Y-%m-%d")
    rows: list[tuple] = []
    for i, uid in enumerate(user_ids):
        tier = user_tier[i]
        title = user_title[i]
        platform = user_platform[i]
        n_sessions = rng.poisson(1.2 * n_days / 7.0) + 1
        if uid == USER_EVENT_SNAPSHOT_USER:
            n_sessions = max(n_sessions, 8)
        day_idx = np.sort(rng.integers(0, n_days, n_sessions))
        secs = rng.integers(6 * 3600, 23 * 3600, n_sessions)
        for s in range(n_sessions):
            t0 = int(secs[s])

            def ts(offset: int) -> str:
                q, r = divmod(t0 + offset, 60)
                h, m = divmod(q % (24 * 60), 60)
                return f"{date_strs[day_idx[s]]} {h:02d}:{m:02d}:{r:02d}"

            rows.append((uid, ts(0), "session_start", title, platform,
                         f"{platform} session"))
            offset, lvl = 60, rng.choice(("easy", "mid", "hard"))
            for _ in range(int(rng.integers(1, 5))):
                won = rng.random() < JOURNEY_P_WIN[lvl]
                rows.append((uid, ts(offset),
                             "level_win" if won else "level_lose",
                             title, platform, f"{lvl} level"))
                offset += int(rng.integers(90, 400))
                if not won and rng.random() < buy_after_loss[tier] \
                        and tier in _USER_EVENT_PACKS:
                    pack, price = _USER_EVENT_PACKS[tier][
                        int(rng.random() < 0.35)]
                    rows.append((uid, ts(offset), "iap_purchase", title,
                                 platform, f"{pack} · ${price}"))
                    offset += 30
            rows.append((uid, ts(offset), "session_end", title, platform,
                         f"{offset // 60} min played"))
    return pd.DataFrame(rows, columns=[
        "user_id", "ts", "event", "title", "platform", "detail",
    ]).sort_values(["user_id", "ts"], ignore_index=True)


def write_events_yaml(path: str, anchor: date) -> None:
    """Annotations that line up with the anomalies baked into the data.

    Studios are the two businesses, not "shared": a shop report annotated with
    a game-title launch would demonstrate the opposite of what per-business
    events exist for. The loader admits an event to a report when the studios
    match (or the event says shared), so each vertical sees only its own.
    """
    outage = anchor - timedelta(days=31)
    launch = anchor - timedelta(days=60)
    coin_sale = anchor - timedelta(days=5)
    forge_nerf = anchor - timedelta(days=ECONOMY_FORGE_NERF_OFFSET_DAYS)
    # The banner windows are the last 3 days of every 14-day cycle counted
    # back from the anchor, so the most recent one is anchor-3 .. anchor-1
    # and the one before it is anchor-17 .. anchor-15.
    banner_start = anchor - timedelta(days=3)
    banner_prev = anchor - timedelta(days=17)
    # fact_experiment assigns users over anchor-28 .. anchor-8 -- the A/B
    # annotation matches the real assignment window.
    ab_start = anchor - timedelta(days=28)
    ab_end = anchor - timedelta(days=8)
    # The other three A/B tests, each annotated over its own report.yaml
    # ab_test window (concluded tests get an end_date, the running one does
    # not) -- see each report's report.yaml for the declared dates this
    # approximates.
    pricing_start = anchor - timedelta(days=49)
    pricing_end = anchor - timedelta(days=21)
    onboarding_start = anchor - timedelta(days=94)
    onboarding_end = anchor - timedelta(days=73)
    checkout_ui_start = anchor - timedelta(days=12)
    # The 42-day promo cadence's PREVIOUS window (the latest is the coin
    # sale above): days_from_anchor % 42 >= 37 puts it at anchor-47 .. -43.
    season_sale = anchor - timedelta(days=47)
    client_rollout = anchor - timedelta(days=32)
    prereg = anchor - timedelta(days=74)
    shop_sale = anchor - timedelta(days=SHOP_PROMO_OFFSET_DAYS)
    stockout = anchor - timedelta(days=SHOP_STOCKOUT_OFFSET_DAYS)
    lookbook = anchor - timedelta(days=21)
    loyalty = anchor - timedelta(days=66)

    content = f"""# Generated by tools/make_fixtures.py -- dates track the fixture anchor.
# Reports render these as chart annotations. Regenerate after changing --anchor.
#
# `studio` scopes an event to one business; the loader shows a report only its
# own studio's events (plus `shared`).

events:
  # ── Nova Play ─────────────────────────────────────
  - date: "{launch:%Y-%m-%d}"
    label: "Neon Racer launch"
    type: release
    studio: nova-play

  - date: "{outage:%Y-%m-%d}"
    end_date: "{outage + timedelta(days=2):%Y-%m-%d}"
    label: "APAC login outage"
    type: incident
    studio: nova-play

  - date: "{coin_sale:%Y-%m-%d}"
    end_date: "{coin_sale + timedelta(days=4):%Y-%m-%d}"
    label: "Weekend coin sale"
    type: campaign
    studio: nova-play

  - date: "{forge_nerf:%Y-%m-%d}"
    label: "Forge cost rebalance"
    type: release
    studio: nova-play

  - date: "{banner_start:%Y-%m-%d}"
    end_date: "{banner_start + timedelta(days=2):%Y-%m-%d}"
    label: "Starfall gacha banner"
    type: campaign
    studio: nova-play

  - date: "{banner_prev:%Y-%m-%d}"
    end_date: "{banner_prev + timedelta(days=2):%Y-%m-%d}"
    label: "Twin Blades gacha banner"
    type: campaign
    studio: nova-play

  - date: "{ab_start:%Y-%m-%d}"
    end_date: "{ab_end:%Y-%m-%d}"
    label: "Checkout v2 A/B test"
    type: ab_test
    studio: nova-play

  - date: "{pricing_start:%Y-%m-%d}"
    end_date: "{pricing_end:%Y-%m-%d}"
    label: "Starter pack pricing A/B test"
    type: ab_test
    studio: nova-play

  - date: "{onboarding_start:%Y-%m-%d}"
    end_date: "{onboarding_end:%Y-%m-%d}"
    label: "Onboarding tutorial A/B test"
    type: ab_test
    studio: nova-play

  - date: "{client_rollout:%Y-%m-%d}"
    label: "Client 2.7 rollout"
    type: release
    studio: nova-play

  - date: "{season_sale:%Y-%m-%d}"
    end_date: "{season_sale + timedelta(days=4):%Y-%m-%d}"
    label: "Season pass sale"
    type: campaign
    studio: nova-play

  - date: "{prereg:%Y-%m-%d}"
    end_date: "{prereg + timedelta(days=6):%Y-%m-%d}"
    label: "Neon Racer pre-registration"
    type: campaign
    studio: nova-play

  # ── Northwind Threads ─────────────────────────────────────
  - date: "{shop_sale:%Y-%m-%d}"
    end_date: "{shop_sale + timedelta(days=SHOP_PROMO_LEN_DAYS - 1):%Y-%m-%d}"
    label: "Mid-season sale"
    type: campaign
    studio: northwind

  - date: "{stockout:%Y-%m-%d}"
    end_date: "{stockout + timedelta(days=SHOP_STOCKOUT_LEN_DAYS - 1):%Y-%m-%d}"
    label: "Wayfare Sneaker stockout"
    type: incident
    studio: northwind

  - date: "{lookbook:%Y-%m-%d}"
    end_date: "{lookbook + timedelta(days=2):%Y-%m-%d}"
    label: "Summer lookbook email"
    type: campaign
    studio: northwind

  - date: "{checkout_ui_start:%Y-%m-%d}"
    label: "Checkout button colour A/B test"
    type: ab_test
    studio: northwind

  - date: "{loyalty:%Y-%m-%d}"
    label: "Loyalty program launch"
    type: release
    studio: northwind
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def write_ua_budget_csv(path: str) -> bool:
    """Monthly UA budget by title and region.

    Written only when missing, and spanning a fixed wide date range rather than
    the fixture window, so it stays anchor-independent and can be committed.
    Returns True if it wrote the file.
    """
    if os.path.exists(path):
        return False

    rng = np.random.default_rng(7)
    months = pd.date_range("2025-01-01", "2027-12-01", freq="MS")
    regions = sorted({c[1] for c in COUNTRIES})
    rows = []
    for month in months:
        seasonal = 1.0 + 0.17 * np.cos(2 * np.pi * (month.dayofyear - 350) / 365.25)
        for title, _genre, base_dau, _iap, _offset in TITLES:
            for region in regions:
                share = {"NA": 0.38, "EMEA": 0.34, "APAC": 0.28}[region]
                budget = base_dau * 0.62 * share * seasonal * rng.uniform(0.9, 1.1)
                rows.append({
                    "month": month.strftime("%Y-%m"),
                    "title": title,
                    "region": region,
                    "budget_usd": round(float(budget), 2),
                })

    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return True


# ── Entry point ─────────────────────────────────────────────────────────

# ── Staleness ───────────────────────────────────────────────────────────
#
# The fixture database is generated, gitignored, and until now only rebuilt
# when it was missing. That rule breaks the moment this file grows a table:
# everyone with an existing checkout keeps their old database, and the first
# report to query the new table dies with `no such table: ...` -- an error
# that points at the report rather than at the stale file. A fresh clone works
# fine, so the failure is invisible in exactly the setup you would test it in.

#: Bump when an existing table gains, loses or renames a column. Added and
#: removed *tables* are caught on their own via TABLE_NAMES, so this constant
#: only has to move for changes inside a table.
SCHEMA_VERSION = "4"

#: Every table :func:`main` writes. A declaration rather than something derived
#: from the build, because the point is to know what should be there without
#: spending seconds generating it to find out. ``main`` asserts it built
#: exactly this, so the two cannot drift apart.
TABLE_NAMES = (
    "dim_date",
    "fact_daily",
    "fact_retention",
    "fact_ua_spend",
    "fact_funnel",
    "fact_experiment",
    "fact_player_segments",
    "fact_economy",
    "fact_journey",
    "fact_user_events",
    "shop_products",
    "shop_traffic",
    "shop_orders",
    "meta",
)


def fixture_status(path: str) -> tuple[str, str, dict]:
    """Is the database at ``path`` usable, and if not, why not?

    Returns ``(status, reason, params)`` where status is ``missing``, ``stale``
    or ``current``, and ``params`` is the meta table of an existing file -- the
    anchor, seed and day count it was built with, so a regeneration can reuse
    them instead of silently re-anchoring someone's pinned baseline.
    """
    if not os.path.exists(path):
        return "missing", "no fixture database yet", {}

    try:
        # Read-only: a check must never be the thing that creates or truncates
        # the file it is checking.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return "stale", f"it cannot be opened ({exc})", {}

    try:
        present = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "meta" not in present:
            return ("stale",
                    "it has no meta table, so it predates fixture versioning",
                    {})
        params = {str(k): str(v) for k, v in conn.execute(
            "SELECT key, value FROM meta")}
    except sqlite3.Error as exc:
        return "stale", f"it cannot be read ({exc})", {}
    finally:
        conn.close()

    absent = [t for t in TABLE_NAMES if t not in present]
    if absent:
        return "stale", f"it is missing {', '.join(absent)}", params

    found = params.get("schema_version")
    if found != SCHEMA_VERSION:
        return ("stale",
                f"it was built to schema v{found or '?'} and this generator "
                f"writes v{SCHEMA_VERSION}",
                params)

    return "current", "", params


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the demo SQLite warehouse.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=420,
                        help="Days of history to generate (default: 420)")
    parser.add_argument("--small", action="store_true",
                        help="Shorthand for --days 60")
    parser.add_argument("--anchor", default=None, metavar="YYYY-MM-DD",
                        help="Last day of generated data (default: today)")
    parser.add_argument("--out", default=None,
                        help="Output .sqlite path (default: data-sources/demo.sqlite)")
    parser.add_argument("--show-anchor", action="store_true",
                        help="Print the last day of data in the existing "
                             "database and exit. Callers pin the report clock "
                             "to this, so date-relative sections land inside "
                             "the data instead of past the end of it.")
    parser.add_argument("--ensure", action="store_true",
                        help="Generate only if the database is missing or was "
                             "built by an older generator. Reuses the anchor, "
                             "seed and day count already recorded in it, so a "
                             "pinned dataset is not re-anchored to today.")
    parser.add_argument("--project-root", default=None, metavar="DIR",
                        help="Project to generate into. Defaults to the demo project "
                             "this script lives in, which is right for a source "
                             "checkout and wrong once the framework is pip-installed "
                             "-- there this file sits in site-packages, so events.yaml "
                             "and the budget CSV would be written there.")
    args = parser.parse_args(argv)

    project_root = args.project_root or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(project_root, "data-sources")):
        print(f"error: no data-sources/ directory under {project_root}.", file=sys.stderr)
        print("Run this from the demo project: python tools/make_fixtures.py",
              file=sys.stderr)
        return 1

    days = 60 if args.small else args.days
    seed = args.seed
    anchor = (datetime.strptime(args.anchor, "%Y-%m-%d").date()
              if args.anchor else date.today())
    out_path = args.out or os.path.join(project_root, "data-sources", "demo.sqlite")

    if args.show_anchor:
        status, reason, params = fixture_status(out_path)
        if not params.get("anchor"):
            print(f"error: no anchor recorded -- {reason}", file=sys.stderr)
            return 1
        print(params["anchor"])
        return 0

    if args.ensure:
        status, reason, params = fixture_status(out_path)
        if status == "current":
            print(f"Fixtures are current: schema v{SCHEMA_VERSION}, "
                  f"{params.get('days', '?')} days ending "
                  f"{params.get('anchor', '?')}.")
            return 0
        print(f"Regenerating fixtures -- {reason}.")
        # Rebuild with the parameters the existing file recorded. Regenerating
        # at today's date instead would quietly move every number in a dataset
        # someone pinned for visual baselines, and the diff would look like the
        # framework changed. Explicit flags still win.
        if params and not args.out:
            if params.get("anchor") and args.anchor is None:
                anchor = date.fromisoformat(params["anchor"])
            if params.get("seed") and args.seed == parser.get_default("seed"):
                seed = int(params["seed"])
            if params.get("days") and not args.small \
                    and args.days == parser.get_default("days"):
                days = int(params["days"])

    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp(anchor), periods=days, freq="D")

    print(f"Generating {days} days ending {anchor} (seed {seed})")

    shop_traffic = build_shop_traffic(dates, anchor, rng)
    # One assignment table for every A/B test, discriminated by `experiment`
    # -- the way a real warehouse collapses many tests into one table rather
    # than growing a new one per test. Each builder draws from the same
    # shared `rng`, so the whole fixture stays reproducible from one seed.
    fact_experiment = pd.concat([
        build_fact_experiment(anchor, rng),
        build_fact_experiment_pricing(anchor, rng),
        build_fact_experiment_onboarding(anchor, rng),
        build_fact_experiment_checkout_ui(anchor, rng),
    ], ignore_index=True)
    tables = {
        "dim_date": build_dim_date(dates),
        "fact_daily": build_fact_daily(dates, anchor, rng),
        "fact_retention": build_fact_retention(dates, anchor, rng),
        "fact_ua_spend": build_fact_ua_spend(dates, anchor, rng),
        "fact_funnel": build_fact_funnel(dates, anchor, rng),
        "fact_experiment": fact_experiment,
        "fact_player_segments": build_fact_player_segments(anchor, rng),
        "fact_economy": build_fact_economy(dates, anchor, rng),
        "fact_journey": build_fact_journey(dates, anchor, rng),
        "fact_user_events": build_fact_user_events(dates, anchor, rng),
        "shop_products": build_shop_products(),
        "shop_traffic": shop_traffic,
        "shop_orders": build_shop_orders(shop_traffic, anchor, rng),
        "meta": pd.DataFrame([
            {"key": "anchor", "value": anchor.isoformat()},
            {"key": "seed", "value": str(seed)},
            {"key": "days", "value": str(days)},
            {"key": "schema_version", "value": SCHEMA_VERSION},
            {"key": "generated_at", "value": datetime.now().isoformat(timespec="seconds")},
        ]),
    }

    # TABLE_NAMES is what --ensure checks an existing file against, so it has
    # to be what this actually writes. Catching the drift here means a new
    # table cannot ship without the staleness check learning about it.
    if tuple(tables) != TABLE_NAMES:
        print(f"error: TABLE_NAMES is out of date with main().\n"
              f"  declared: {', '.join(TABLE_NAMES)}\n"
              f"  building: {', '.join(tables)}", file=sys.stderr)
        return 1

    if os.path.exists(out_path):
        os.remove(out_path)
    conn = sqlite3.connect(out_path)
    try:
        for name, df in tables.items():
            df.to_sql(name, conn, if_exists="replace", index=False)
            print(f"  {name:<16} {len(df):>8,} rows")
        conn.execute("CREATE INDEX idx_daily_date ON fact_daily(event_date)")
        conn.execute("CREATE INDEX idx_ua_date ON fact_ua_spend(event_date)")
        conn.execute("CREATE INDEX idx_ret_cohort ON fact_retention(cohort_date)")
        conn.execute("CREATE INDEX idx_funnel_date ON fact_funnel(event_date)")
        conn.execute("CREATE INDEX idx_exp_variant ON fact_experiment(variant)")
        conn.execute("CREATE INDEX idx_shop_traffic_date ON shop_traffic(event_date)")
        conn.execute("CREATE INDEX idx_shop_orders_date ON shop_orders(order_date)")
        conn.execute("CREATE INDEX idx_economy_date ON fact_economy(event_date)")
        conn.execute("CREATE INDEX idx_journey_date ON fact_journey(event_date)")
        # The live-lookup access path: WHERE user_id = :user_id
        conn.execute("CREATE INDEX idx_user_events_user ON fact_user_events(user_id)")
        conn.commit()
    finally:
        conn.close()

    events_path = os.path.join(project_root, "events.yaml")
    write_events_yaml(events_path, anchor)

    budget_path = os.path.join(project_root, "data-sources", "uploads",
                               "ua_budget.csv")
    wrote_budget = write_ua_budget_csv(budget_path)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"\nWrote {out_path} ({size_mb:.1f} MB)")
    print(f"Wrote {events_path}")
    print(f"{'Wrote' if wrote_budget else 'Kept '} {budget_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
