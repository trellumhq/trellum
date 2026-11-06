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

        ctx.register(cid, data)

        attrs = f'class="fw-kpi-card" id="{cid}"'
        if self.card_id:
            attrs += f' data-kpi-id="{self.card_id}"'
        if self.clickable:
            attrs += ' style="cursor:pointer"'
        return f"<div {attrs}></div>"

    @classmethod
    def css(cls) -> str:
        return load_css("components/kpi_card.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/kpi_card.js")


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

            kpis = [expand_claim(k) if isinstance(k, dict)
                    else {"label": k.label, "format": k.format,
                          "value": k.value, "alignment": k.alignment,
                          "full_number": k.full_number, "precision": k.precision}
                    for k in self.kpis]
            for kpi in kpis:
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
