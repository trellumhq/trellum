"""FunnelChart: ordered stages and the drop between them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
    _clean_list,
)


@dataclass
class FunnelChart(Component):
    """Funnel chart for FTUE or purchase conversion funnels.

    Each row represents a stage. ``label`` is the stage name column,
    ``value`` is the count column.  Stages are rendered in DataFrame order.
    Pass ``dataset_id`` for reactive filter support.
    """

    df: pd.DataFrame
    label: str
    value: str
    title: str = ""
    show_percentages: bool = True
    dataset_id: Optional[str] = None
    static: bool = False

    _component_type: str = field(default="funnel_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            ctx.register(cid, {
                "type": "funnel",
                "dataset_id": self.dataset_id,
                "label_col": self.label,
                "value_col": self.value,
                "show_pct": self.show_percentages,
                "title": self.title,
            })
        else:
            labels = _clean_list(self.df[self.label].tolist())
            values = _clean_list(self.df[self.value].tolist())
            ctx.register(cid, {
                "type": "funnel",
                "labels": labels,
                "values": values,
                "show_pct": self.show_percentages,
            })

        return _chart_container_html(cid, self.title)

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps() + ["chartjs_funnel"]

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css()

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/funnel.js")
