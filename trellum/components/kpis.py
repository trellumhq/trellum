"""KPI card and row components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext


@dataclass
class KpiCard(Component):
    """Single KPI metric card.

    Args:
        label: Metric name (e.g. "Gross Revenue").
        value: Numeric value (formatted using ``format``).
        format: One of: number, currency, chips, percent, ratio, plain.
        delta: Optional delta string (e.g. "+5.2%").
        delta_direction: "up" or "down" for color styling.
        forecast: Optional forecast value to display.
        sub: Optional subtitle text (e.g. "5 min lag").
        clickable: If True, card can expand to show an hourly detail chart.
        metric: Optional metrics.yaml id this card claims. Fills label and
            format from the definition at build time (explicit values win);
            the card still needs its own ``value`` -- a static card cannot
            aggregate.
        availability: Optional authored availability or bound-input completeness
            declaration. See ``docs/report-kpi-states.md`` for the public shape.
        maturity: Optional supplied cohort state, age, observed value and forecast.
        comparison: Optional explicit relative-percent or percentage-point change.
    """
    label: str = ""
    value: Any = None
    format: str = "number"
    delta: Optional[str] = None
    delta_direction: Optional[str] = None
    forecast: Optional[Any] = None
    sub: Optional[str] = None
    clickable: bool = False
    card_id: Optional[str] = None
    metric: Optional[str] = None
    alignment: str = field(default="left", kw_only=True)
    full_number: bool = field(default=False, kw_only=True)
    precision: Optional[int] = field(default=None, kw_only=True)
    availability: Optional[Dict[str, Any]] = field(default=None, kw_only=True)
    maturity: Optional[Dict[str, Any]] = field(default=None, kw_only=True)
    comparison: Optional[Dict[str, Any]] = field(default=None, kw_only=True)

    _component_type: str = field(default="kpi_card", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        _validate_number_options(self.format, self.full_number, self.precision)
        if self.alignment not in {"left", "center"}:
            raise ValueError("alignment must be 'left' or 'center'")

        label, fmt = self.label, self.format
        if self.metric:
            from trellum.metrics import expand_claim

            # Only fields the author left at their defaults are filled from
            # the definition -- an explicit label or format on the card wins.
            claim: dict[str, Any] = {"metric": self.metric}
            if self.label:
                claim["label"] = self.label
            if self.format != "number":
                claim["format"] = self.format
            claim["value"] = self.value
            expanded = expand_claim(claim)
            label = expanded.get("label", label)
            fmt = expanded.get("format", fmt)
        _validate_number_options(fmt, self.full_number, self.precision)
        _validate_states(self.__dict__, bound=False)

        data: dict[str, Any] = {
            "type": "kpi",
            "value": self.value,
            "format": fmt,
            "label": label,
            "alignment": self.alignment,
            "full_number": self.full_number,
            "precision": self.precision,
        }
        if self.metric:
            data["metric"] = self.metric
        if self.delta:
            data["delta"] = self.delta
            data["deltaDirection"] = self.delta_direction or ""
        if self.forecast is not None:
            data["forecast"] = self.forecast
        if self.sub:
            data["sub"] = self.sub
        for key in ("availability", "maturity", "comparison"):
            if getattr(self, key) is not None:
                data[key] = getattr(self, key)
        if self.comparison is not None and self.delta_direction is not None:
            data["delta_direction"] = self.delta_direction

        ctx.register(cid, data)

        attrs = f'class="fw-kpi-card" id="{cid}"'
        if self.card_id:
            attrs += f' data-kpi-id="{self.card_id}"'
        if self.clickable:
            attrs += ' style="cursor:pointer"'
        return f"<div {attrs}></div>"

    @classmethod
    def css(cls) -> str:
        return load_css("components/kpi_card.css") + "\n" + load_css("components/kpi_state.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/kpi_state.js") + "\n" + load_js("components/kpi_card.js")


@dataclass
class KpiRow(Component):
    """Row of KPI metric cards.

    In static mode each kpi dict has ``label``, ``value``, ``format`` etc.
    (same fields as :class:`KpiCard`).

    In live mode (``dataset_id`` provided) each kpi dict describes an
    aggregation: ``label``, ``format``, ``agg`` (sum / abssum / ratio /
    count / purchase_pct), ``column`` / ``columns`` / ``numerator`` /
    ``denominator``.

    In either mode a kpi dict may instead **claim** a metrics.yaml metric:
    ``{"metric": "gross_revenue"}``. The claim expands at build time into the
    label / format / aggregation spec of the definition, so every claimant
    computes the same number by construction. Explicit keys on the dict win
    over the definition, and the registered config keeps ``"metric": id``.

    Live mode also supports optional delta display via:
    ``delta_col`` (column to aggregate for the delta value),
    ``delta_format`` (``percent`` | ``currency`` | ``number``),
    ``delta_direction`` (``auto`` | ``up`` | ``down``).
    """

    kpis: List[Dict[str, Any] | KpiCard]
    mobile_collapse: int = 0
    dataset_id: Optional[str] = None
    static: bool = False

    _component_type: str = field(default="kpi_row", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            from trellum.metrics import expand_claim

            kpis = [expand_claim(k) if isinstance(k, dict) else _card_spec(k)
                    for k in self.kpis]
            for kpi in kpis:
                _validate_states(kpi, bound=True)
                _validate_number_options(kpi.get("format", "number"),
                                         kpi.get("full_number", False),
                                         kpi.get("precision"))
                if kpi.get("alignment", "left") not in {"left", "center"}:
                    raise ValueError("alignment must be 'left' or 'center'")
            ctx.register(cid, {
                "type": "kpi_row_live",
                "dataset_id": self.dataset_id,
                "kpis": kpis,
            })
            cards = [
                f'<div class="fw-kpi-card" id="{cid}_k{i}"></div>'
                for i in range(len(self.kpis))
            ]
            mc = self.mobile_collapse
            if mc and 0 < mc < len(cards):
                for i in range(mc, len(cards)):
                    cards[i] = cards[i].replace('class="fw-kpi-card"',
                                                'class="fw-kpi-card fw-kpi-extra"')
            grid = f'<div class="fw-kpi-grid" id="{cid}">{"".join(cards)}</div>'
            if mc and 0 < mc < len(cards):
                grid += (f'<button class="fw-kpi-more-btn" aria-controls="{cid}" aria-expanded="false">'
                         f'+ {len(cards) - mc} more</button>')
            return grid

        mc = self.mobile_collapse
        cards = []
        for i, kpi in enumerate(self.kpis):
            if isinstance(kpi, dict):
                kpi = KpiCard(**kpi)
            html = ctx.render_child(kpi)
            if mc and 0 < mc <= i:
                html = html.replace(
                    'class="fw-kpi-card"',
                    'class="fw-kpi-card fw-kpi-extra"',
                    1,
                )
            cards.append(html)

        grid = f'<div class="fw-kpi-grid" id="{cid}">{"".join(cards)}</div>'

        if mc and 0 < mc < len(cards):
            rest_count = len(cards) - mc
            grid += (
                f'<button class="fw-kpi-more-btn" aria-controls="{cid}" aria-expanded="false">'
                f'+ {rest_count} more</button>'
            )

        return grid

    @classmethod
    def css(cls) -> str:
        return KpiCard.css()

    @classmethod
    def client_js(cls) -> str:
        static_js = KpiCard.client_js()
        live_js = load_js("components/kpi_row.js")
        return static_js + "\n" + live_js


@dataclass
class MiniKpi(Component):
    """Compact inline KPI for secondary metrics.

    Args:
        label: Metric name.
        value: Numeric value.
        format: Display format (same options as KpiCard).
        metric: Optional metrics.yaml id this KPI claims -- fills label and
            format from the definition (explicit values win); the value must
            still be supplied.
    """
    label: str = ""
    value: Any = None
    format: str = "number"
    metric: Optional[str] = None
    stacked: bool = field(default=False, kw_only=True)
    full_number: bool = field(default=False, kw_only=True)
    precision: Optional[int] = field(default=None, kw_only=True)

    _component_type: str = field(default="mini_kpi", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        _validate_number_options(self.format, self.full_number, self.precision)

        label, fmt = self.label, self.format
        if self.metric:
            from trellum.metrics import expand_claim

            claim: dict[str, Any] = {"metric": self.metric, "value": self.value}
            if self.label:
                claim["label"] = self.label
            if self.format != "number":
                claim["format"] = self.format
            expanded = expand_claim(claim)
            label = expanded.get("label", label)
            fmt = expanded.get("format", fmt)
        _validate_number_options(fmt, self.full_number, self.precision)

        data: dict[str, Any] = {
            "type": "mini_kpi",
            "label": label,
            "value": self.value,
            "format": fmt,
            "stacked": self.stacked,
            "full_number": self.full_number,
            "precision": self.precision,
        }
        if self.metric:
            data["metric"] = self.metric
        ctx.register(cid, data)
        return f'<span class="fw-mini-kpi" id="{cid}"></span>'

    @classmethod
    def css(cls) -> str:
        return """\
.fw-mini-kpi {
    display: inline-flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 4px;
    max-width: 100%;
    box-sizing: border-box;
    padding: 4px var(--spacing-sm);
    font-size: var(--font-size-small);
    color: var(--text-secondary);
}
.fw-mini-kpi .fw-mini-label { min-width: 0; overflow-wrap: anywhere; }
.fw-mini-kpi .fw-mini-val {
    font-size: 14px; font-weight: 600; color: var(--text-main);
    font-variant-numeric: tabular-nums; white-space: nowrap;
    max-width: 100%; overflow-x: auto;
}
.fw-mini-kpi.fw-mini-stacked { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.fw-mini-kpi.fw-mini-stacked .fw-mini-val { font-size: 16px; }"""

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/kpi_card.js") + "\n" + """\
window._fwRenderers['mini_kpi'] = function renderMiniKpi(id, cfg) {
    var el = document.getElementById(id);
    var label = document.createElement('span');
    label.className = 'fw-mini-label';
    label.textContent = cfg.label + (cfg.stacked ? '' : ':');
    var value = document.createElement('span');
    value.className = 'fw-mini-val';
    value.textContent = window._fwKpiFormat(cfg.value, cfg.format, cfg.full_number, cfg.precision);
    el.replaceChildren(label, value);
    el.classList.toggle('fw-mini-stacked', !!cfg.stacked);
};"""


def _validate_number_options(fmt: str, full_number: bool, precision: Optional[int]) -> None:
    if precision is not None and (isinstance(precision, bool) or not isinstance(precision, int)
                                  or not 0 <= precision <= 12):
        raise ValueError("precision must be an integer from 0 to 12")
    if (full_number or precision is not None) and fmt not in {"number", "currency", "percent", "ratio"}:
        raise ValueError("full_number and precision support number, currency, percent, and ratio formats")


def _card_spec(card: KpiCard) -> dict[str, Any]:
    from trellum.metrics import expand_claim

    spec = {key: getattr(card, key) for key in (
        "label", "format", "value", "alignment", "full_number", "precision",
        "delta", "delta_direction", "forecast", "sub", "metric",
        "availability", "maturity", "comparison") if getattr(card, key) is not None}
    spec["value"] = card.value  # Explicit None is a constant too.
    if card.metric:
        if not card.label:
            spec.pop("label", None)
        if card.format == "number":
            spec.pop("format", None)
    return expand_claim(spec)


def _validate_states(kpi: dict[str, Any], *, bound: bool) -> None:
    """Validate only the opt-in declarations, including post-init mutations."""
    import math

    def mapping(value, keys, name):
        if not isinstance(value, dict) or set(value) - set(keys):
            raise ValueError(f"{name}: expected a mapping with keys {', '.join(keys)}")

    def text(value, name):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")

    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def numeric(spec, name):
        mapping(spec, ("value", "unit", "agg", "column", "numerator", "denominator"), name)
        if spec.get("unit") not in ("number", "fraction", "percent"):
            raise ValueError(f"{name}.unit must be number, fraction, or percent")
        if "value" in spec:
            if set(spec) != {"value", "unit"} or (spec["value"] is not None and not finite(spec["value"])):
                raise ValueError(f"{name}: value must be finite or None, without reducer fields")
        else:
            if not bound:
                raise ValueError(f"{name}: reducers require a dataset-bound row")
            agg = spec.get("agg")
            if not isinstance(agg, str):
                raise ValueError(f"{name}: agg must be a supported reducer name")
            required = {"sum": {"column"}, "abssum": {"column"}, "count": set(),
                        "ratio": {"numerator", "denominator"}}.get(agg)
            if required is None or set(spec) != required | {"agg", "unit"}:
                raise ValueError(f"{name}: invalid reducer fields")
            for key in required:
                text(spec[key], f"{name}.{key}")
            if agg == "ratio" and spec["unit"] != "fraction":
                raise ValueError(f"{name}: ratio reducers return fraction units")
            if agg != "ratio" and spec["unit"] != "number":
                raise ValueError(f"{name}: rate reductions require numerator/denominator, not summed percentages")

    availability = kpi.get("availability")
    if availability is not None:
        mapping(availability, ("state", "reason", "required_columns", "empty", "ratio_denominator"), "availability")
        if "reason" in availability:
            text(availability["reason"], "availability.reason")
        if "state" in availability:
            if availability["state"] not in ("available", "unavailable") or set(availability) - {"state", "reason"}:
                raise ValueError("availability.state must be available or unavailable, without completeness fields")
        else:
            if not bound:
                raise ValueError("availability completeness requires a dataset-bound row")
            cols = availability.get("required_columns")
            if not isinstance(cols, list) or not cols or any(not isinstance(c, str) or not c.strip() for c in cols):
                raise ValueError("availability.required_columns must be a nonempty list of column names")
            if availability.get("empty") not in ("zero", "unavailable"):
                raise ValueError("availability.empty must explicitly be zero or unavailable")
            if "ratio_denominator" in availability:
                if availability["ratio_denominator"] != "nonzero" or kpi.get("agg") != "ratio":
                    raise ValueError("availability.ratio_denominator requires agg=ratio and nonzero")
                for key in ("numerator", "denominator"):
                    text(kpi.get(key), f"availability ratio {key}")
                if not {kpi["numerator"], kpi["denominator"]} <= set(cols):
                    raise ValueError("availability ratio completeness must include numerator and denominator")

    maturity = kpi.get("maturity")
    if maturity is not None:
        mapping(maturity, ("state", "state_column", "age", "age_column", "horizon", "cohort_column",
                           "observed", "forecast", "reason"), "maturity")
        horizon = maturity.get("horizon")
        if not finite(horizon) or horizon <= 0:
            raise ValueError("maturity.horizon must be positive finite days")
        if ("state" in maturity) == ("state_column" in maturity) or ("age" in maturity) == ("age_column" in maturity):
            raise ValueError("maturity requires exactly one state/state_column and age/age_column")
        if "state" in maturity and maturity["state"] not in ("mature", "immature"):
            raise ValueError("maturity.state must be mature or immature")
        if "age" in maturity and (not finite(maturity["age"]) or maturity["age"] < 0):
            raise ValueError("maturity.age must be nonnegative finite days")
        if "state" in maturity and "age" in maturity:
            if (maturity["state"] == "mature") != (maturity["age"] >= horizon):
                raise ValueError("maturity.state must agree with age and horizon")
        for key in ("state_column", "age_column", "cohort_column", "reason"):
            if key in maturity:
                text(maturity[key], f"maturity.{key}")
        if bound and "cohort_column" not in maturity:
            raise ValueError("bound maturity requires cohort_column to identify one selected cohort")
        if not bound and any(key in maturity for key in ("state_column", "age_column", "cohort_column")):
            raise ValueError("maturity source fields require a dataset-bound row")
        if "observed" not in maturity:
            raise ValueError("maturity.observed is required")
        numeric(maturity["observed"], "maturity.observed")
        if "forecast" in maturity:
            numeric(maturity["forecast"], "maturity.forecast")
        if kpi.get("forecast") is not None or kpi.get("sub"):
            raise ValueError("maturity owns the two detail lines; omit legacy forecast/sub")

    comparison = kpi.get("comparison")
    if comparison is not None:
        mapping(comparison, ("measure", "current", "baseline", "value", "precision", "reason"), "comparison")
        measure = comparison.get("measure")
        if measure not in ("relative_percent", "percentage_points"):
            raise ValueError("comparison.measure must be relative_percent or percentage_points")
        precision = comparison.get("precision", 1)
        if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 12:
            raise ValueError("comparison.precision must be an integer from 0 to 12")
        if "reason" in comparison:
            text(comparison["reason"], "comparison.reason")
        if "value" in comparison:
            if not finite(comparison["value"]) or "current" in comparison or "baseline" in comparison:
                raise ValueError("comparison.value must be a finite delta in the selected measure, without periods")
        else:
            for key in ("current", "baseline"):
                numeric(comparison.get(key), f"comparison.{key}")
            units = [comparison[key]["unit"] for key in ("current", "baseline")]
            if ("number" in units and units != ["number", "number"]) or (measure == "percentage_points" and "number" in units):
                raise ValueError("comparison units must be compatible; percentage_points requires rate units")
        if kpi.get("delta_direction") not in (None, "auto", "up", "down"):
            raise ValueError("comparison delta_direction must be auto, up, or down")
