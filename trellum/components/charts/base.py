"""The shared chart machinery: _ChartBase and the HTML helpers.

Every chart type below is a thin subclass -- the axis handling, the
legend, the download button, the theme wiring and the renderer
registration all live here, and are emitted once per page however
many chart types are on it."""

from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape
from typing import Any, Dict, Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext

#: The glyph on every chart's download button.
_DOWNLOAD_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="14" height="14">'
    '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"></path>'
    '<polyline points="7 10 12 15 17 10"></polyline>'
    '<line x1="12" y1="15" x2="12" y2="3"></line></svg>'
)


def _clean_list(values: list) -> list:
    """Sanitise a list for JSON serialisation: NaN → None, Timestamp → ISO date string.

    Uses a single pass with minimal per-element overhead.
    """
    return [
        None if v is None or (isinstance(v, float) and v != v) else
        v.isoformat()[:10] if hasattr(v, "isoformat") else
        v
        for v in values
    ]

def _validate_category_colors(value: Any) -> dict[str, Any] | None:
    """Validate and copy the opt-in declaration without touching caller mappings."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("category_colors must be a mapping or None")
    if set(value) - {"domain", "column", "slots", "overrides"}:
        raise ValueError("category_colors has unknown fields; use domain, column, slots, overrides")
    for key in ("domain", "column"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"category_colors {key} must be a nonempty string")
    slots = value.get("slots")
    if not isinstance(slots, Mapping) or not slots:
        raise ValueError("category_colors slots must be a nonempty mapping")
    for category, index in slots.items():
        if (not isinstance(category, str) or isinstance(index, bool)
                or not isinstance(index, int) or not 0 <= index <= 2**31 - 1):
            raise ValueError("category_colors slots require string categories and integer indices 0..2147483647")
    overrides = value.get("overrides", {})
    if not isinstance(overrides, Mapping):
        raise ValueError("category_colors overrides must be a mapping")
    for category, color in overrides.items():
        if (category not in slots or not isinstance(color, str)
                or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
            raise ValueError("category_colors overrides require declared categories and #RRGGBB colors")
    result = {"domain": value["domain"], "column": value["column"], "slots": dict(slots)}
    if "overrides" in value:
        result["overrides"] = dict(overrides)
    return result


def _category_colors_config(chart: Component, ctx: RenderContext) -> dict[str, Any]:
    """Check applicability after supplied binding, and report-wide domain identity.

    Previously registered declarations belong to this RenderContext's render;
    using them also makes reset() clear the domain definitions automatically.
    Column names and chart-local overrides are deliberately not domain identity.
    """
    declaration = _validate_category_colors(chart.category_colors)
    if declaration is None:
        return {}
    column = declaration["column"]
    if chart._component_type == "doughnut_chart":
        if column != chart.label:
            raise ValueError("DoughnutChart category_colors column must equal label")
    else:
        if not chart.dataset_id:
            raise ValueError("category_colors requires a dataset-bound category split; static wide-column "
                             "LineChart/StackedBar y columns are series, not category rows")
        splits = [chart.stack_by] + list((chart.stack_by_options or {}).values())
        if column not in [s for s in splits if s]:
            raise ValueError("category_colors column must be a configured stack_by or nonempty stack_by_options value")
    for config in ctx.component_data.values():
        previous = config.get("category_colors")
        if (previous and previous["domain"] == declaration["domain"]
                and previous["slots"] != declaration["slots"]):
            raise ValueError(f"Conflicting category_colors slots for domain {declaration['domain']!r}; "
                             "use identical category/slot definitions within one RenderContext")
    return {"category_colors": declaration}


def _month_context_config(chart: Component) -> dict[str, bool]:
    """Validate the author's daily-label declaration, including after mutation."""
    if type(chart.month_context) is not bool:
        raise ValueError("month_context must be bool")
    if not chart.month_context:
        return {}
    if (getattr(chart, "horizontal", False) or getattr(chart, "sort", None) is not None
            or getattr(chart, "top_n", None) is not None):
        raise ValueError("month_context requires a vertical chronological axis without sort or top_n")
    return {"month_context": True}


class _ChartBase(Component):
    """Shared base for line/bar/stacked-bar charts that use ``renderChart``."""

    _supports_dataset_id = True

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return ["chartjs", "hammerjs", "chartjs_zoom", "chartjs_annotation", "chartjs_datalabels"]

    @classmethod
    def css(cls) -> str:
        return load_css("components/chart_base.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/chart_base.js")

def _scatter_val(v):
    """Sanitise a scatter point coordinate."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return v

def _scatter_radius(v, series: pd.Series, max_r: float = 20.0, min_r: float = 3.0) -> float:
    """Scale a bubble radius using sqrt normalisation."""
    if v is None or (isinstance(v, float) and v != v):
        return min_r
    mx = series.max()
    if mx is None or mx == 0 or (isinstance(mx, float) and mx != mx):
        return min_r
    return max(min_r, (float(v) / float(mx)) ** 0.5 * max_r)

def _chart_container_html(
    cid: str,
    title: str = "",
    extra_before: str = "",
    *,
    container_class: str = "",
    title_class: str = "",
    summary: bool = False,
    header_context: Optional[str] = None,
) -> str:
    """The chart's title, optional controls and canvas box.

    The keyword-only arguments are for a chart that sizes its own box
    (``container_class``), keeps its title where tiles hide it
    (``title_class``) or carries a header summary the runtime fills in
    (``summary``). Called without them the markup is the original.
    """
    dl_btn = (
        f'<button class="fw-chart-dl" data-csv-chart="{cid}" '
        f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
    )
    box = f"fw-chart-container {container_class}".strip()
    context = _header_context_html(cid, header_context)
    if summary:
        head = (
            '<div class="fw-chart-head">'
            f'{_title_html(title, cls=title_class, margin=False)}'
            f'<div class="fw-chart-summary" data-chart-summary="{cid}">'
            '<span class="fw-chart-summary-label"></span>'
            '<span class="fw-chart-summary-value"></span></div></div>'
        )
    else:
        head = _title_html(title, cls=title_class)
    return (
        f'{head}{context}{extra_before}'
        f'<div class="{box}">'
        f'<canvas id="{cid}"{_context_description_attr(cid, header_context)}></canvas>{dl_btn}</div>'
    )

def _validate_header_context(value: Optional[str]) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError("header_context must be text or None")

def _header_context_html(cid: str, value: Optional[str]) -> str:
    _validate_header_context(value)
    if value is None or not value.strip():
        return ""
    return (f'<div class="fw-chart-header-context" id="{cid}-header-context">'
            f'{escape(value, quote=True)}</div>')

def _context_description_attr(cid: str, value: Optional[str]) -> str:
    _validate_header_context(value)
    return f' aria-describedby="{cid}-header-context"' if value is not None and value.strip() else ""

def _title_html(title: str, *, cls: str = "", margin: bool = True) -> str:
    if not title:
        return ""
    name = f"fw-chart-title {cls}".strip()
    bottom = "margin-bottom:4px;" if margin else ""
    return (
        f'<div class="{name}" style="font-size:13px;'
        f'color:var(--text-secondary);{bottom}">{title}</div>'
    )

def _stack_toggle_html(
    cid: str, options: Dict[str, str], toggle_id: Optional[str] = None,
) -> str:
    btns: list[str] = []
    for i, label in enumerate(options.keys()):
        active = " active" if i == 0 else ""
        btns.append(f'<button class="fw-toggle-btn{active}">{label}</button>')
    if toggle_id:
        attr = f'data-toggle-id="{toggle_id}"'
    else:
        attr = f'data-stack-toggle="{cid}"'
    return (
        f'<div class="fw-toggle-group" {attr} '
        f'style="margin-bottom:6px;">{"".join(btns)}</div>'
    )

def _normalize_toggle_html(
    cid: str, toggle_id: Optional[str] = None,
) -> str:
    if toggle_id:
        attr = f'data-toggle-id="{toggle_id}"'
    else:
        attr = f'data-normalize-toggle="{cid}"'
    return (
        f'<div class="fw-toggle-group" {attr} '
        f'style="margin-bottom:6px;">'
        f'<button class="fw-toggle-btn active">Absolute</button>'
        f'<button class="fw-toggle-btn">Percentage</button></div>'
    )
