"""DoughnutChart: part-to-whole for a handful of categories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts.base import (
    _DOWNLOAD_SVG,
    _ChartBase,
    _clean_list,
)


@dataclass
class DoughnutChart(Component):
    """Proportional doughnut chart.

    Pass ``dataset_id`` to make this chart reactive to filter changes.
    ``center_total`` opts into a labeled total of the complete current result;
    authors are responsible for supplying additive quantities. Legend values
    use explicit ``value_format`` units and optional decimal ``precision``.
    """

    df: pd.DataFrame
    label: str
    value: str
    title: str
    dataset_id: Optional[str] = None
    static: bool = False
    cross_filter: bool = False
    show_labels: bool = False  # Show percentage labels on segments
    center_total: bool = field(default=False, kw_only=True)
    center_label: str = field(default="Total", kw_only=True)
    value_format: str = field(default="number", kw_only=True)
    precision: Optional[int] = field(default=None, kw_only=True)

    _component_type: str = field(default="doughnut_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.value_format not in {"number", "currency", "percent", "ratio", "plain", "chips"}:
            raise ValueError("Unsupported doughnut value_format")
        if self.precision is not None and (
            isinstance(self.precision, bool) or not isinstance(self.precision, int)
            or not 0 <= self.precision <= 12
        ):
            raise ValueError("Doughnut precision must be an integer from 0 to 12")
        if not isinstance(self.center_total, bool) or not isinstance(self.center_label, str):
            raise ValueError("Doughnut center_total must be bool and center_label must be text")

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            cfg_d: dict[str, Any] = {
                "type": "doughnut",
                "dataset_id": self.dataset_id,
                "label_col": self.label,
                "value_col": self.value,
                "title": self.title,
            }
            if self.cross_filter:
                cfg_d["crossFilter"] = self.label
            if self.show_labels:
                cfg_d["show_labels"] = True
        else:
            theme = ctx.theme
            palette = theme.chart_colors
            n = len(self.df)
            colors = [palette[i % len(palette)] for i in range(n)]
            values = self.df[self.value].tolist()
            cfg_d = {
                "type": "doughnut",
                "title": self.title,
                "labels": _clean_list(self.df[self.label].tolist()),
                "data": _clean_list([None if v is pd.NA else v for v in values]),
                "colors": colors,
                "bgCard": theme.bg_card,
                "tickColor": theme.tick_color,
                "_themeManaged": True,
                # JSON normalizes NaN/nonfinite numbers; retain their supplied
                # text for disclosure and eligibility without changing arc/CSV data.
                "supplied_values": [
                    "null" if v is None or v is pd.NA else str(v)
                    for v in values
                ],
            }

        cfg_d.update({
            "show_labels": self.show_labels,
            "center_total": self.center_total,
            "center_label": self.center_label,
            "value_format": self.value_format,
            "precision": self.precision,
        })
        ctx.register(cid, cfg_d)

        dl_btn = (
            f'<button class="fw-chart-dl" data-csv-chart="{cid}" '
            f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
        )
        return (
            '<div class="fw-doughnut">'
            '<div class="fw-doughnut-layout">'
            '<div class="fw-chart-container fw-doughnut-ring">'
            f'<canvas id="{cid}" role="img" aria-label="Doughnut chart; values in category legend"></canvas>'
            '<div class="fw-doughnut-center" hidden></div>'
            f'{dl_btn}</div>'
            '<div class="fw-doughnut-legend" aria-label="Category visibility and values"></div>'
            '</div><div class="fw-doughnut-context" aria-live="polite"></div></div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps()

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css() + "\n" + load_css("components/doughnut.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/doughnut.js")
