"""Declared metrics in, the visualisation's modes out.

This is the front door reports use: name the metrics, hand over the
user frame, and get back the rows and series ABCompare renders.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from trellum.stats.ab.aggregate import (
    agg_from_users,
    bootstrap_ab_cis,
    overlay_volume_metrics,
)
from trellum.stats.ab.users import (
    cuped_user_df,
    merge_pre_window,
    validate_user_df,
    winsorize_user_df,
)


# ---------------------------------------------------------------------------
# Unified mode builder — one call replaces per-report mode wiring
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The front door (issue #30): groups of users in, visualisation out
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    """One declared A/B metric, in YOUR column names.

    The front door maps declarations onto the canonical schema the (frozen)
    machinery above expects, so nothing statistical changes — only where the
    column names come from.

    Args:
        name: the column in your per-user frame, aggregated to the user over
              the test window by YOUR query.
        label: display label; empty keeps the canonical row label.
        kind: ``"mean"`` (per-user average — revenue-like) or ``"rate"``
              (0/1 per user — conversion-like).
        fmt: ``"currency"`` | ``"number"``. The first currency mean is the
             primary metric (winsorised, CUPED target); a second currency
             mean fills the net-revenue slot; a plain-number mean fills the
             transactions slot.
        pre_col: same aggregate over a pre-assignment window, per user.
             Declaring it on the primary mean is what makes the CUPED mode
             appear; no pre_col, no CUPED — never silently faked.
    """
    name: str
    label: str = ""
    kind: str = "mean"
    fmt: str = "number"
    pre_col: str | None = None

# ---------------------------------------------------------------------------
# Default row builder — canonical A/B comparison rows + CIs
# ---------------------------------------------------------------------------
#: A report's own row builder: takes the group frames, returns rows.
RowsBuilder = Callable[..., list[dict]]

#: Canonical volume-row labels, for relabelling when a Metric declares one.
_FRONTDOOR_LABELS = {
    "gross": "Gross Revenue (total)",
    "net": "Net Revenue (total)",
    "rate": "Payers (total)",
    "count": "Transactions (total)",
}


def _ab_row(
    metric: str,
    ctrl: float,
    tst: float,
    fmt: str,
    higher_is_better: bool = True,
    key: str | None = None,
    *,
    delta_control: float | None = None,
    delta_test: float | None = None,
    kpi: bool = True,
    kpi_metric: str | None = None,
    ci_pct: tuple[float, float] | None = None,
) -> dict:
    """Build a single ABCompare row dict — same shape every report uses."""
    r: dict = {
        "metric": metric,
        "control": float(ctrl),
        "test": float(tst),
        "format": fmt,
        "higher_is_better": higher_is_better,
        "key": key or metric.lower().replace(" ", "_")[:48],
        "kpi": kpi,
    }
    if delta_control is not None:
        r["delta_control"] = float(delta_control)
    if delta_test is not None:
        r["delta_test"] = float(delta_test)
    if kpi_metric:
        r["kpi_metric"] = kpi_metric
    if ci_pct is not None:
        r["ci_pct"] = [float(ci_pct[0]), float(ci_pct[1])]
    return r

def default_build_rows(
    c: dict,
    t: dict,
    cis: dict[str, tuple[float, float]] | None = None,
) -> list[dict]:
    """Canonical row set for an A/B comparison from per-variant aggregate dicts.

    ``c`` and ``t`` are the dicts returned by :func:`agg_from_users`. Produces
    volume rows (DAU, revenue, payers, FTDs, transactions, ARPDAU, Avg Txn
    Value) plus the two-stage-decomposition KPI rows (Conversion, ARPPU).
    ``cis`` is the dict returned by :func:`bootstrap_ab_cis`; CIs are attached
    to matching KPI rows when present.

    Use this for any A/B report's winsorized / CUPED-adjusted modes. Reports
    can also use it for raw modes if the standard row set is sufficient,
    or keep a custom ``_build_rows`` for the raw mode and use this for the
    variance-reduction modes.
    """
    cis = cis or {}
    def ci(key: str) -> tuple[float, float] | None:
        return cis.get(key)

    rows = [
        _ab_row(
            "Avg DAUs",
            c["mean_dau"], t["mean_dau"],
            "number", True, "dau", kpi=False,
        ),
        _ab_row(
            "Gross Revenue (total)",
            c["sum_revenue"], t["sum_revenue"],
            "currency", True, "rev_normalised",
            delta_control=c["rev_normalised"], delta_test=t["rev_normalised"],
            kpi_metric="Revenue Normalised vs Control",
            ci_pct=ci("rev_normalised"),
        ),
        _ab_row(
            "Net Revenue (total)",
            c["sum_net_revenue"], t["sum_net_revenue"],
            "currency", True, "net_arpdau",
            delta_control=c["net_arpdau"], delta_test=t["net_arpdau"],
            kpi_metric="Net ARPDAU vs Control",
            ci_pct=ci("net_arpdau"),
        ),
        _ab_row(
            "Payers (total)",
            c["sum_payers"], t["sum_payers"],
            "number", True, "payers_per_dau",
            delta_control=c["payers_per_dau"], delta_test=t["payers_per_dau"],
            kpi_metric="Payers Normalised vs Control",
            ci_pct=ci("payers_per_dau"),
        ),
        _ab_row(
            "FTDs (total)",
            c["sum_ftd"], t["sum_ftd"],
            "number", True, "ftd_conv",
            delta_control=c["ftd_conv"], delta_test=t["ftd_conv"],
            kpi_metric="FTD Conversion vs Control",
            ci_pct=ci("ftd_conv"),
        ),
        _ab_row(
            "Transactions (total)",
            c["sum_transactions"], t["sum_transactions"],
            "number", True, "txns_per_dau",
            delta_control=c["txns_per_dau"], delta_test=t["txns_per_dau"],
            kpi_metric="Transactions Normalised vs Control",
            ci_pct=ci("txns_per_dau"),
        ),
        _ab_row(
            "ARPDAU",
            c["arpdau"], t["arpdau"],
            "currency", True, "arpdau",
            kpi_metric="ARPDAU vs Control",
            ci_pct=ci("arpdau"),
        ),
        _ab_row(
            "Avg Transaction Value",
            c["avg_txn_value"], t["avg_txn_value"],
            "currency", True, "avg_txn_value",
            kpi_metric="Avg Transaction Value vs Control",
            ci_pct=ci("avg_txn_value"),
        ),
    ]
    if "arppu" in c and "arppu" in t:
        rows.append(_ab_row(
            "Conversion (payers / DAU)",
            c["payers_per_dau"], t["payers_per_dau"],
            "percent", True, "conv_rate",
            kpi_metric="Conversion vs Control",
            ci_pct=ci("payers_per_dau"),
        ))
        rows.append(_ab_row(
            "ARPPU (revenue / payer)",
            c["arppu"], t["arppu"],
            "currency", True, "arppu",
            kpi_metric="ARPPU vs Control",
            ci_pct=ci("arppu"),
        ))
    return rows

def rows_and_ts_winsor(
    df_user: pd.DataFrame,
    n_days: int,
    control_label: str,
    test_label: str,
    build_rows_fn: RowsBuilder,
    *,
    pct: float = 99.0,
    iters: int = 2000,
) -> tuple[list[dict], dict, dict[str, float]]:
    """Winsorize per-user revenue at pooled pct percentile, aggregate, build rows.

    ``build_rows_fn`` is the report's row-builder (typically ``_build_rows``);
    must accept ``(control_dict, test_dict, cis=ci_dict)`` and return a list
    of row dicts. Returns ``(rows, timeseries, info)`` where ``info`` has the
    thresholds used.
    """
    if df_user.empty:
        return [], {}, {}
    n_rows = len(df_user)
    print(f"  -> Winsor p99 + bootstrap CIs ({n_rows:,} users, {iters} iters) ...",
          end="", flush=True)
    t0 = time.time()
    g, info = winsorize_user_df(df_user, pct=pct)
    cis = bootstrap_ab_cis(g, control_label=control_label, test_label=test_label, iters=iters)
    c_kpi = agg_from_users(g[g["variant"] == control_label], n_days)
    t_kpi = agg_from_users(g[g["variant"] == test_label],    n_days)
    c_vol = agg_from_users(df_user[df_user["variant"] == control_label], n_days)
    t_vol = agg_from_users(df_user[df_user["variant"] == test_label],    n_days)
    rows = overlay_volume_metrics(
        build_rows_fn(c_kpi, t_kpi, cis=cis),
        build_rows_fn(c_vol, t_vol, cis=None),
    )
    info["elapsed_seconds"] = time.time() - t0
    print(f" {info['elapsed_seconds']:.1f}s", flush=True)
    return rows, {}, info

def rows_and_ts_cuped(
    df_user: pd.DataFrame,
    n_days: int,
    control_label: str,
    test_label: str,
    build_rows_fn: RowsBuilder,
    *,
    iters: int = 2000,
) -> tuple[list[dict], dict, dict[str, float]]:
    """CUPED-adjust per-user revenue using pre-window covariate, aggregate, build rows.

    ``df_user`` must already have ``pre_gross_revenue`` (and optionally
    ``pre_net_revenue``) merged on ``user_id`` — see :func:`merge_pre_window`.
    Returns ``(rows, ts, info)`` where ``info`` has the fitted θ and expected
    variance reduction.
    """
    if df_user.empty or "pre_gross_revenue" not in df_user.columns:
        return [], {}, {}
    n_rows = len(df_user)
    print(f"  -> CUPED + bootstrap CIs ({n_rows:,} users, {iters} iters) ...",
          end="", flush=True)
    t0 = time.time()
    g, info = cuped_user_df(df_user)
    cis = bootstrap_ab_cis(g, control_label=control_label, test_label=test_label, iters=iters)
    c_kpi = agg_from_users(g[g["variant"] == control_label], n_days)
    t_kpi = agg_from_users(g[g["variant"] == test_label],    n_days)
    c_vol = agg_from_users(df_user[df_user["variant"] == control_label], n_days)
    t_vol = agg_from_users(df_user[df_user["variant"] == test_label],    n_days)
    rows = overlay_volume_metrics(
        build_rows_fn(c_kpi, t_kpi, cis=cis),
        build_rows_fn(c_vol, t_vol, cis=None),
    )
    info["elapsed_seconds"] = time.time() - t0
    print(f" {info['elapsed_seconds']:.1f}s  (variance reduction: {info.get('variance_reduction_pct', 0):.0f}%)",
          flush=True)
    return rows, {}, info

def build_modes_from_metrics(
    df: pd.DataFrame,
    metrics: list[Metric],
    *,
    variant_col: str = "variant",
    control: str = "Control",
    test: str = "Test",
    control_label: str | None = None,
    test_label: str | None = None,
    user_col: str = "user_id",
    exposure_col: str | None = None,
    n_days: int | None = None,
    winsor_pct: float = 99.0,
    iters: int = 2000,
    include: tuple[str, ...] = ("raw", "winsor", "cuped"),
    pre_window_days: int | None = None,
    validate: bool = True,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Declared metrics + a variant column -> ABCompare ``modes``.

    Maps the declarations onto the canonical schema and delegates wholesale
    to :func:`build_variance_modes` — the winsorisation, CUPED and bootstrap
    code paths are exactly the ones every existing report uses, with the
    same deterministic seed. Returns ``(modes, exposure_synthesised)``.

    Slotting rules (v1 — the canonical family): up to two ``mean``s with
    ``fmt="currency"`` (primary, then net), at most one ``rate``, at most
    one plain-number ``mean`` (transactions). A missing rate is synthesised
    as ``primary > 0``. Anything beyond that family raises with a pointer
    to the follow-up in issue #30.
    """
    means_cur = [m for m in metrics if m.kind == "mean" and m.fmt == "currency"]
    rates = [m for m in metrics if m.kind == "rate"]
    counts = [m for m in metrics if m.kind == "mean" and m.fmt != "currency"]
    bad_kinds = [m.name for m in metrics if m.kind not in ("mean", "rate")]
    if bad_kinds:
        raise ValueError(f"Metric kind must be 'mean' or 'rate': {bad_kinds}")
    if not means_cur:
        raise ValueError(
            "Declare at least one Metric(kind='mean', fmt='currency') -- the "
            "primary metric that winsorisation and CUPED operate on.")
    if len(means_cur) > 2 or len(rates) > 1 or len(counts) > 1:
        raise ValueError(
            "v1 supports the canonical family: up to 2 currency means "
            "(primary + net), 1 rate, 1 count mean. Arbitrary metric "
            "engines are the follow-up tracked in issue #30.")

    gross, net = means_cur[0], (means_cur[1] if len(means_cur) > 1 else None)
    rate = rates[0] if rates else None
    count = counts[0] if counts else None

    rename: dict[str, str] = {user_col: "user_id", gross.name: "gross_revenue"}
    if gross.pre_col:
        rename[gross.pre_col] = "pre_gross_revenue"
    if net:
        rename[net.name] = "net_revenue"
        if net.pre_col:
            rename[net.pre_col] = "pre_net_revenue"
    if rate:
        rename[rate.name] = "is_payer"
    if count:
        rename[count.name] = "transactions"
    if exposure_col:
        rename[exposure_col] = "active_days"
        if n_days is None:
            raise ValueError(
                "exposure_col given but n_days missing -- the per-day "
                "normalisations need the window length.")

    out = df.rename(columns=rename).copy()
    out["variant"] = df[variant_col].map({
        control: control_label or control,
        test: test_label or test,
    })
    out = out.dropna(subset=["variant"])
    if rate is None:
        out["is_payer"] = (out["gross_revenue"] > 0).astype(int)
    exposure_synth = not exposure_col
    if exposure_synth:
        # No exposure column: every per-DAU normalisation degrades cleanly
        # to per-USER (active_days=1, n_days=1), and the caller relabels
        # the Avg DAUs row to "Users".
        out["active_days"] = 1.0
        n_days = 1

    modes = build_variance_modes(
        out,
        n_days=int(n_days or 1),
        control_label=control_label or control,
        test_label=test_label or test,
        winsor_pct=winsor_pct,
        iters=iters,
        pre_window_days=pre_window_days,
        validate=validate,
        include_raw="raw" in include,
    )
    if "winsor" not in include:
        modes = {k: v for k, v in modes.items() if not k.endswith("_winsor")}
    if "cuped" not in include:
        modes = {k: v for k, v in modes.items() if not k.endswith("_cuped")}

    # Display relabels only -- keys, math and CIs untouched.
    relabel: dict[str, str] = {}
    for slot, metric in (("gross", gross), ("net", net),
                         ("rate", rate), ("count", count)):
        if metric is not None and metric.label:
            relabel[_FRONTDOOR_LABELS[slot]] = f"{metric.label} (total)"
    if exposure_synth:
        relabel["Avg DAUs"] = "Users"
    if relabel:
        for payload in modes.values():
            for row in payload.get("rows", []):
                if row.get("metric") in relabel:
                    row["metric"] = relabel[row["metric"]]

    return modes, exposure_synth

def build_variance_modes(
    df_user: pd.DataFrame,
    n_days: int,
    control_label: str,
    test_label: str,
    *,
    df_user_pre: pd.DataFrame | None = None,
    build_rows_fn: RowsBuilder | None = None,
    pop_key: str = "all_users",
    pop_label: str = "All users",
    winsor_pct: float = 99.0,
    iters: int = 2000,
    pre_window_days: int | None = None,
    validate: bool = True,
    strict: bool = False,
    include_raw: bool = True,
) -> dict[str, dict[str, Any]]:
    """One-shot builder for the three canonical A/B modes (raw / winsor / CUPED).

    Returns the dict shape consumed by ``ABCompare.modes``::

        {
          "{pop_key}":        {"label": "{pop_label}",                "rows": [...], "timeseries": {}},
          "{pop_key}_winsor": {"label": "{pop_label} · Winsor p{99}", "rows": [...], "timeseries": {}},
          "{pop_key}_cuped":  {"label": "{pop_label} · CUPED",        "rows": [...], "timeseries": {}},
        }

    The CUPED mode is only emitted when a usable pre-window covariate is
    available — either ``df_user`` already carries ``pre_gross_revenue`` (from
    a prior :func:`merge_pre_window` call) or ``df_user_pre`` is passed and is
    merged here.

    Replaces the per-report block that used to wire ``rows_and_ts_winsor`` and
    ``rows_and_ts_cuped`` by hand — same output, but the ``iters``,
    ``build_rows_fn``, and labels stay consistent across modes by construction.
    ``validate=True`` runs :func:`validate_user_df` first; pass ``strict=True``
    to abort the run on shape errors (recommended in CI builds).

    Set ``include_raw=False`` to skip the raw mode — useful when the caller
    already builds the raw mode from a different DataFrame (e.g. a daily
    aggregate that carries a timeseries the per-user table can't reproduce).
    """
    if df_user.empty:
        return {}

    build_rows_fn = build_rows_fn or default_build_rows

    # Optional pre-window merge — accept either pre-merged df or separate pre df.
    if "pre_gross_revenue" not in df_user.columns and df_user_pre is not None and not df_user_pre.empty:
        df_user = merge_pre_window(df_user, df_user_pre)

    if validate:
        validate_user_df(
            df_user,
            n_days=n_days,
            pre_window_days=pre_window_days,
            control_label=control_label,
            test_label=test_label,
            strict=strict,
        )

    modes: dict[str, dict[str, Any]] = {}

    # ── Raw mode ───────────────────────────────────────────────────────────
    if include_raw:
        cis_raw = bootstrap_ab_cis(
            df_user, control_label=control_label, test_label=test_label, iters=iters,
        )
        c_raw = agg_from_users(df_user[df_user["variant"] == control_label], n_days)
        t_raw = agg_from_users(df_user[df_user["variant"] == test_label],    n_days)
        modes[pop_key] = {
            "label": pop_label,
            "rows": build_rows_fn(c_raw, t_raw, cis=cis_raw),
            "timeseries": {},
        }

    # ── Winsorized mode ────────────────────────────────────────────────────
    rows_w, _, _ = rows_and_ts_winsor(
        df_user, n_days, control_label, test_label, build_rows_fn,
        pct=winsor_pct, iters=iters,
    )
    if rows_w:
        modes[f"{pop_key}_winsor"] = {
            "label": f"{pop_label} · Winsor p{int(winsor_pct)}",
            "rows": rows_w,
            "timeseries": {},
        }

    # ── CUPED mode ─────────────────────────────────────────────────────────
    if "pre_gross_revenue" in df_user.columns:
        rows_c, _, _ = rows_and_ts_cuped(
            df_user, n_days, control_label, test_label, build_rows_fn,
            iters=iters,
        )
        if rows_c:
            modes[f"{pop_key}_cuped"] = {
                "label": f"{pop_label} · CUPED",
                "rows": rows_c,
                "timeseries": {},
            }

    return modes
