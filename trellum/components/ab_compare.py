"""Reusable A/B test comparison: volumes table, KPI delta bars, optional drill-down."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.data.transforms import format_value

#: Natural precision per format, and the point past which widening stops being
#: readable and starts being noise.
_AB_DEFAULT_DP = {"currency": 2, "percent": 1, "number": 2}
_AB_MAX_DP = 6


def _ab_format_cell(val: Any, fmt: str, dp: int | None = None) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "-"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)
    if dp is None:
        dp = _AB_DEFAULT_DP.get(fmt, 2)
    if fmt == "currency":
        return f"${v:,.{dp}f}"
    if fmt == "percent":
        return f"{v * 100:,.{dp}f}%"
    if fmt == "number":
        if abs(v - int(v)) < 1e-9:
            return f"{int(v):,}"
        return f"{v:,.{dp}f}"
    return format_value(val)


def _ab_format_pair(c: Any, t: Any, fmt: str) -> tuple[str, str]:
    """Format two compared values so they stay distinguishable.

    The KPI rows are normalised rates -- payers per DAU is around 0.02 -- but
    they inherit the format of the total they were derived from, which is set
    for figures in the thousands. At a fixed two decimals a control of 0.0204
    and a test of 0.0219 both render as "0.02", sitting next to a "+7.4%"
    delta. The row then contradicts itself, and the reader's first conclusion
    is that the report is broken rather than that the number is small.

    So widen the precision until the two actually differ, and no further. Values
    that already differ keep their natural format, and values that are genuinely
    equal stay equal -- a delta of zero should look like one.
    """
    base = _AB_DEFAULT_DP.get(fmt, 2)
    c_str, t_str = _ab_format_cell(c, fmt, base), _ab_format_cell(t, fmt, base)
    try:
        if float(c) == float(t):
            return c_str, t_str
    except (TypeError, ValueError):
        return c_str, t_str

    for dp in range(base + 1, _AB_MAX_DP + 1):
        if c_str != t_str:
            return c_str, t_str
        c_str, t_str = _ab_format_cell(c, fmt, dp), _ab_format_cell(t, fmt, dp)

    if c_str == t_str:
        # Still indistinguishable at six decimals, so the difference is not one
        # a reader needs to see. Give back the clean format rather than a wall
        # of zeros that implies precision nobody asked for.
        return (_ab_format_cell(c, fmt, base), _ab_format_cell(t, fmt, base))
    return c_str, t_str


def _ab_process_payload(
    raw_rows: list[dict],
    timeseries: dict,
    exclude_keys: list[str] | None = None,
) -> dict[str, Any]:
    excl = {str(k) for k in (exclude_keys or []) if k is not None}
    vol: list[dict] = []
    kpi: list[dict] = []
    for r in raw_rows:
        row_key = r.get("key") or r.get("metric_key")
        if row_key is not None and str(row_key) in excl:
            continue
        metric = str(r.get("metric", ""))
        ctrl = r.get("control")
        tst = r.get("test")
        fmt = str(r.get("format", "number"))
        hib = bool(r.get("higher_is_better", True))
        key = row_key
        try:
            c = float(ctrl) if ctrl is not None and not (isinstance(ctrl, float) and pd.isna(ctrl)) else 0.0
            t = float(tst) if tst is not None and not (isinstance(tst, float) and pd.isna(tst)) else 0.0
        except (TypeError, ValueError):
            c, t = 0.0, 0.0
        vol.append({
            "metric": metric,
            "control": _ab_format_cell(c, fmt),
            "test": _ab_format_cell(t, fmt),
        })
        if r.get("kpi") is False:
            continue
        dc = r.get("delta_control")
        dt = r.get("delta_test")

        def _finite_num(x: Any) -> bool:
            if x is None:
                return False
            if isinstance(x, float) and pd.isna(x):
                return False
            try:
                float(x)
            except (TypeError, ValueError):
                return False
            return True

        if _finite_num(dc) and _finite_num(dt):
            c_delta, t_delta = float(dc), float(dt)
        else:
            c_delta, t_delta = c, t
        d_pct = (t_delta - c_delta) / c_delta * 100.0 if c_delta else 0.0
        bar_w = min(90.0, max(2.0, 8.0 + abs(d_pct) ** 0.5 * 2.8))
        kpi_label = str(r.get("kpi_metric") or (metric + " vs Control"))
        c_cell, t_cell = _ab_format_pair(c_delta, t_delta, fmt)
        entry = {
            "metric": kpi_label,
            "delta_pct": d_pct,
            "higher_is_better": hib,
            "key": str(key) if key else "",
            "bar_width": bar_w,
            "control_val": c_cell,
            "test_val": t_cell,
        }
        # Optional 95% CI on the delta — surfaced visually in the bar/colour
        # and as a small "CI [lo, hi]" line under the delta in the JS renderer.
        ci_pct = r.get("ci_pct")
        if isinstance(ci_pct, (list, tuple)) and len(ci_pct) == 2:
            try:
                entry["ci_pct"] = [float(ci_pct[0]), float(ci_pct[1])]
            except (TypeError, ValueError):
                pass
        kpi.append(entry)
    ts_out: dict[str, Any] = {}
    for k, v in (timeseries or {}).items():
        sk = str(k)
        if sk in excl:
            continue
        if isinstance(v, dict):
            ts_out[sk] = {
                "labels": [str(x)[:10] for x in (v.get("labels") or [])],
                "control": [float(x) if x is not None else 0.0 for x in (v.get("control") or [])],
                "test": [float(x) if x is not None else 0.0 for x in (v.get("test") or [])],
            }
    return {"volume_rows": vol, "kpi_rows": kpi, "timeseries": ts_out}


@dataclass
class ABCompare(Component):
    """Side-by-side A/B comparison: header, volumes, normalized KPI bars, drill-down.

    Args:
        rows: Each dict has ``metric``, ``control``, ``test`` (numeric),
              ``format`` (``currency`` | ``number`` | ``percent``),
              ``higher_is_better`` (bool), optional ``key`` (drill-down timeseries).
              Optional ``delta_control`` / ``delta_test``: basis for % delta when volumes
              are raw totals but KPIs must be normalized (e.g. per DAU). Optional
              ``kpi_metric`` overrides the KPI row label; ``kpi``: ``False`` skips the KPI
              row (volumes only). Optional ``ci_pct``: ``[lo, hi]`` 95% confidence
              interval on the delta in percentage points (see "Variance reduction"
              below).
        title: Optional section subtitle under the header.
        control_label: Left column label (e.g. "Control" / "No Ads").
        test_label: Right data column label (e.g. "Test" / "Ads").
        description: What the test measures (shown at top).
        split: Text split ratio e.g. ``"90/10"``.
        start_date: Test start date ``YYYY-MM-DD``.
        timeseries: Map ``key`` -> ``{"labels": [...], "control": [...], "test": [...]}``.
        modes: Optional alternative views — typically a raw / winsorized / CUPED-
               adjusted family (see "Variance reduction" below). Mapping of mode
               key to ``{"label": "...", "rows": [...], "timeseries": {...}}``.
        default_mode: Which ``modes`` key opens initially. Falls back to
               ``"exclude_top1"`` if present, then to the first mode in the dict.
        exclude_keys: Row ``key`` values to omit from the volume table, KPI bars,
            and matching drill-down timeseries (leave rows in ``rows`` for reuse).

    Variance reduction (CIs, winsorization, CUPED):
        Heavy-tailed revenue metrics are noisy — a handful of whales can dominate
        each variant's mean and swing the observed delta by tens of percent. The
        ``trellum.stats`` package ships three composable techniques to address
        this; ABCompare renders their output natively:

        - **Bootstrap CIs** — when a row carries a ``ci_pct=[lo, hi]`` field, the
          KPI cell shows ``95% CI [lo, hi]`` under the delta. If the interval
          straddles zero (``lo ≤ 0 ≤ hi``), the bar renders **neutral grey** and
          the CI italicises to signal "not statistically significant". One-sided
          intervals keep the standard green/red colour.

        - **Winsorization** — caps each user's metric at the pooled p99 percentile
          (same cap both arms — never per-variant, which biases the comparison).
          Implement via ``trellum.stats.ab.winsorize_user_df`` and add a
          ``*_winsor`` mode alongside the raw mode.

        - **CUPED** — adjusts each user's metric by their pre-experiment value as
          a covariate. 30–50% variance reduction is typical on revenue metrics.
          Implement via ``trellum.stats.ab.cuped_user_df`` (requires a
          pre-experiment column merged onto the per-user DataFrame) and add a
          ``*_cuped`` mode. **Recommended default** for any A/B report with
          heavy-tailed metrics — set ``default_mode`` to the CUPED mode on
          whichever subpopulation the test actually affects.

        See ``reports/local-currency-ab/generator.py`` for a canonical wiring.
    """

    rows: List[Dict[str, Any]]
    title: str = ""
    control_label: str = "Control"
    test_label: str = "Test"
    description: str = ""
    test_name: str = ""
    split: str = ""
    start_date: str = ""
    timeseries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    modes: Dict[str, Dict[str, Any]] | None = None
    exclude_keys: List[str] = field(default_factory=list)
    default_mode: str = ""
    #: Sample-ratio-mismatch verdict rendered as a header badge -- the dict
    #: from trellum.stats.srm_check. A failed SRM means the readout below
    #: cannot be trusted, and the badge says so where the reader is.
    srm: Dict[str, Any] | None = None

    _component_type: str = field(default="ab_compare", init=False, repr=False)

    # ── The front door (issue #30) ───────────────────────────────────────
    @classmethod
    def from_users(
        cls,
        df: pd.DataFrame,
        *,
        metrics: List[Any],
        variant_col: str = "variant",
        control: str = "Control",
        test: str = "Test",
        control_label: str | None = None,
        test_label: str | None = None,
        user_col: str = "user_id",
        exposure_col: str | None = None,
        n_days: int | None = None,
        expected_split: tuple[float, float] | None = (50, 50),
        modes: tuple[str, ...] = ("raw", "winsor", "cuped"),
        winsor_pct: float = 99.0,
        iters: int = 2000,
        pre_window_days: int | None = None,
        validate: bool = True,
        default_mode: str = "all_users",
        title: str = "",
        test_name: str = "",
        description: str = "",
        split: str = "",
        start_date: str = "",
    ) -> "ABCompare":
        """One per-user frame with a variant column -> the finished component.

        Declared :class:`trellum.stats.Metric` objects carry YOUR column
        names; internally everything delegates to the same (unchanged)
        winsorisation / CUPED / bootstrap machinery every A/B report uses,
        with the same deterministic seed. The SRM verdict renders as a
        header badge, judged against ``expected_split``.
        """
        from trellum.stats.ab import build_modes_from_metrics, srm_check

        c_lbl = control_label or str(control)
        t_lbl = test_label or str(test)
        built, _ = build_modes_from_metrics(
            df, list(metrics),
            variant_col=variant_col, control=control, test=test,
            control_label=c_lbl, test_label=t_lbl,
            user_col=user_col, exposure_col=exposure_col, n_days=n_days,
            winsor_pct=winsor_pct, iters=iters, include=tuple(modes),
            pre_window_days=pre_window_days, validate=validate,
        )
        srm = None
        if expected_split is not None:
            counts = df[variant_col].value_counts()
            n_c, n_t = int(counts.get(control, 0)), int(counts.get(test, 0))
            # No recognisable variants (e.g. a mock-data run): no verdict is
            # honest; a red badge against synthetic data would be noise.
            if n_c + n_t > 0:
                srm = srm_check(n_c, n_t, expected_split)
        first = next(iter(built), "")
        raw_rows = (built.get("all_users") or built.get(first) or {}).get("rows", [])
        return cls(
            rows=raw_rows,
            modes=built,
            default_mode=default_mode if default_mode in built else first,
            title=title,
            test_name=test_name,
            description=description,
            control_label=c_lbl,
            test_label=t_lbl,
            split=split or (f"{expected_split[0]:g}/{expected_split[1]:g}"
                            if expected_split else ""),
            start_date=start_date,
            srm=srm,
        )

    @classmethod
    def from_groups(
        cls,
        groups: Dict[str, pd.DataFrame],
        *,
        metrics: List[Any],
        user_col: str = "user_id",
        **kwargs: Any,
    ) -> "ABCompare":
        """Two pre-separated frames -> the finished component.

        The shape for pipelines where the server event-tags assignment and
        each group is fetched with its own query: ``{"Control": df_control,
        "Test": df_test}`` -- first key is control, second is test, and the
        keys become the display labels. Groups must be mutually exclusive on
        ``user_col``; overlap fails loudly rather than double-counting.
        Accepts every :meth:`from_users` keyword except the variant ones.
        """
        if len(groups) != 2:
            raise ValueError(
                f"from_groups takes exactly two groups (control, test); "
                f"got {len(groups)}: {list(groups)}")
        (c_lbl, df_c), (t_lbl, df_t) = list(groups.items())
        overlap = set(df_c[user_col]) & set(df_t[user_col])
        if overlap:
            sample = list(overlap)[:5]
            raise ValueError(
                f"{len(overlap)} user(s) appear in BOTH groups (e.g. "
                f"{sample}) -- groups must be mutually exclusive.")
        marker = "__ab_group"
        df = pd.concat([
            df_c.assign(**{marker: c_lbl}),
            df_t.assign(**{marker: t_lbl}),
        ], ignore_index=True)
        return cls.from_users(
            df, metrics=metrics, variant_col=marker,
            control=c_lbl, test=t_lbl, user_col=user_col, **kwargs)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        theme = ctx.theme
        header = {
            "test_name": self.test_name or "",
            "description": self.description or "",
            "split": self.split or "",
            "start_date": self.start_date or "",
            "control_label": self.control_label,
            "test_label": self.test_label,
            "srm": self.srm or None,
        }
        body: dict[str, Any] = {
            "volume_rows": [],
            "kpi_rows": [],
            "timeseries": {},
        }
        modes_payload: dict[str, Any] = {}
        if self.modes:
            for mode_id, payload in self.modes.items():
                if not isinstance(payload, dict):
                    continue
                raw = payload.get("rows") or []
                ts = payload.get("timeseries") or {}
                lbl = str(payload.get("label", mode_id.replace("_", " ").title()))
                modes_payload[str(mode_id)] = {
                    "label": lbl,
                    **_ab_process_payload(raw, ts, self.exclude_keys),
                }
            # Caller-specified default (preferred) → "exclude_top1" if present
            # → first mode in the dict (insertion order).
            if self.default_mode and self.default_mode in modes_payload:
                default_mode = self.default_mode
            elif "exclude_top1" in modes_payload:
                default_mode = "exclude_top1"
            else:
                default_mode = next(iter(modes_payload), "")
        else:
            default_mode = ""
            body = _ab_process_payload(self.rows, self.timeseries, self.exclude_keys)

        ctx.register(cid, {
            "type": "ab_compare",
            "header": header,
            "title": self.title or "",
            "default_mode": default_mode,
            "modes": modes_payload if modes_payload else None,
            "body": body if not modes_payload else None,
            "gridColor": theme.grid_color,
            "tickColor": theme.tick_color,
        })

        return f'<div class="fw-ab-compare-root" id="{cid}"></div>'

    @classmethod
    def css(cls) -> str:
        return load_css("components/ab_compare.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/ab_compare.js")
