"""TreemapChart: nested rectangles sized by value."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
)


@dataclass
class TreemapChart(Component):
    """Treemap chart for hierarchical proportional breakdowns.

    Pass one or more ``group_cols`` for nested grouping and a ``value``
    column for rectangle sizing.  Pass ``dataset_id`` for reactive
    filter support.
    """

    df: pd.DataFrame
    group_cols: list[str]
    value: str
    title: str = ""
    color_col: Optional[str] = None
    value_format: str = "number"
    dataset_id: Optional[str] = None
    static: bool = False

    _component_type: str = field(default="treemap_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            ctx.register(cid, {
                "type": "treemap",
                "dataset_id": self.dataset_id,
                "group_cols": self.group_cols,
                "value_col": self.value,
                "color_col": self.color_col,
                "value_format": self.value_format,
                "title": self.title,
            })
        else:
            rows = []
            for _, row in self.df.iterrows():
                r: dict = {self.value: row[self.value]}
                for g in self.group_cols:
                    gv = row[g]
                    r[g] = gv.isoformat()[:10] if hasattr(gv, "isoformat") else str(gv)
                if self.color_col and self.color_col in row:
                    r[self.color_col] = row[self.color_col]
                rows.append(r)
            ctx.register(cid, {
                "type": "treemap",
                "rows": rows,
                "group_cols": self.group_cols,
                "value_col": self.value,
                "color_col": self.color_col,
                "value_format": self.value_format,
            })

        return _chart_container_html(cid, self.title)

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps() + ["chartjs_treemap"]

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css()

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/treemap.js")
