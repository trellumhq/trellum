"""ScatterChart: two measures against each other, optionally sized."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
    _scatter_radius,
    _scatter_val,
)


@dataclass
class ScatterChart(Component):
    """Scatter or bubble chart using native Chart.js types.

    Pass ``size`` column name to enable bubble mode (point radius scales
    with the value).  Use ``color_by`` to split data into separate series
    by a categorical column.
    """

    df: pd.DataFrame
    x: str
    y: str
    size: Optional[str] = None
    color_by: Optional[str] = None
    title: str = ""
    x_format: str = "number"
    y_format: str = "number"
    dataset_id: Optional[str] = None
    static: bool = False

    _component_type: str = field(default="scatter_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            ctx.register(cid, {
                "type": "scatter",
                "dataset_id": self.dataset_id,
                "x_col": self.x,
                "y_col": self.y,
                "size_col": self.size,
                "color_by": self.color_by,
                "x_format": self.x_format,
                "y_format": self.y_format,
                "title": self.title,
            })
        else:
            theme = ctx.theme

            if self.color_by and self.color_by in self.df.columns:
                groups = self.df.groupby(self.color_by)
                datasets = []
                for i, (name, grp) in enumerate(groups):
                    color = theme.chart_colors[i % len(theme.chart_colors)]
                    pts = []
                    for _, row in grp.iterrows():
                        pt: dict = {"x": _scatter_val(row[self.x]), "y": _scatter_val(row[self.y])}
                        if self.size and self.size in grp.columns:
                            pt["r"] = _scatter_radius(row[self.size], self.df[self.size])
                        pts.append(pt)
                    datasets.append({
                        "label": str(name),
                        "data": pts,
                        "backgroundColor": f"{color}99",
                        "borderColor": color,
                        "borderWidth": 1,
                        "_fillOpacity": "99",
                        "_themeManaged": True,
                    })
            else:
                color = theme.chart_colors[0]
                pts = []
                for _, row in self.df.iterrows():
                    pt: dict = {"x": _scatter_val(row[self.x]), "y": _scatter_val(row[self.y])}
                    if self.size and self.size in self.df.columns:
                        pt["r"] = _scatter_radius(row[self.size], self.df[self.size])
                    pts.append(pt)
                datasets = [{
                    "label": self.title or "Data",
                    "data": pts,
                    "backgroundColor": f"{color}99",
                    "borderColor": color,
                    "borderWidth": 1,
                    "_fillOpacity": "99",
                    "_themeManaged": True,
                }]

            ctx.register(cid, {
                "type": "scatter",
                "chartType": "bubble" if self.size else "scatter",
                "datasets": datasets,
                "x_format": self.x_format,
                "y_format": self.y_format,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        return _chart_container_html(cid, self.title)

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps()

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css()

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/scatter.js")
