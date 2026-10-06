"""The shared chart machinery: _ChartBase and the HTML helpers.

Every chart type below is a thin subclass -- the axis handling, the
legend, the download button, the theme wiring and the renderer
registration all live here, and are emitted once per page however
many chart types are on it."""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component

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

def _chart_container_html(cid: str, title: str = "", extra_before: str = "") -> str:
    dl_btn = (
        f'<button class="fw-chart-dl" data-csv-chart="{cid}" '
        f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
    )
    return (
        f'{_title_html(title)}{extra_before}'
        f'<div class="fw-chart-container">'
        f'<canvas id="{cid}"></canvas>{dl_btn}</div>'
    )

def _title_html(title: str) -> str:
    if not title:
        return ""
    return (
        f'<div class="fw-chart-title" style="font-size:13px;'
        f'color:var(--text-secondary);margin-bottom:4px;">{title}</div>'
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
