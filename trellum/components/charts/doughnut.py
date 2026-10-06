"""DoughnutChart: part-to-whole for a handful of categories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from trellum.assets import load_js
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
    """

    df: pd.DataFrame
    label: str
    value: str
    title: str
    dataset_id: Optional[str] = None
    static: bool = False
    cross_filter: bool = False
    show_labels: bool = False  # Show percentage labels on segments

    _component_type: str = field(default="doughnut_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

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
            ctx.register(cid, cfg_d)
        else:
            theme = ctx.theme
            palette = theme.chart_colors
            n = len(self.df)
            colors = [palette[i % len(palette)] for i in range(n)]
            ctx.register(cid, {
                "type": "doughnut",
                "labels": _clean_list(self.df[self.label].tolist()),
                "data": _clean_list(self.df[self.value].tolist()),
                "colors": colors,
                "bgCard": theme.bg_card,
                "tickColor": theme.tick_color,
                "_themeManaged": True,
            })

        dl_btn = (
            f'<button class="fw-chart-dl" data-csv-chart="{cid}" '
            f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
        )
        return (
            '<div class="fw-chart-container" '
            'style="max-height: 350px; max-width: 500px; margin: 0 auto;">'
            f'<canvas id="{cid}"></canvas>{dl_btn}</div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps()

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css()

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/doughnut.js")
