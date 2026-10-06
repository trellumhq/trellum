"""HeatmapChart: a value per (x, y) cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
)


@dataclass
class HeatmapChart(Component):
    """Color-coded matrix chart using ``chartjs-chart-matrix``.

    Ideal for retention cohort grids and day/hour pattern analysis.
    Pass ``dataset_id`` for reactive filter support.

    ``normalize``: ``None`` (default) colours cells on one global scale;
    ``"row"`` / ``"col"`` stretch each row or column across its own min..max.
    Use it whenever the rows are structurally different sizes -- feature
    scales, spender tiers, cohort day-offsets. On a global scale such a
    matrix renders every row flat (the biggest row owns the whole palette),
    and the within-row pattern -- the thing a heatmap exists to show -- is
    invisible. Tooltips always show the raw value; only the colour is
    relative.
    """

    df: pd.DataFrame
    x: str
    y: str
    value: str
    title: str = ""
    color_scale: tuple[str, str] | str = "auto"
    log_scale: Optional[bool] = None
    normalize: Optional[str] = None
    #: Drop rows/columns whose every cell is empty or zero. For matrices where
    #: a category structurally never carries the value (non-spenders never
    #: have IAP revenue), the all-zero row is dead space; recomputed live, so
    #: a row that becomes non-zero under a filter reappears.
    drop_empty: bool = False
    value_format: str = "number"
    dataset_id: Optional[str] = None
    static: bool = False

    def __post_init__(self):
        if self.normalize not in (None, "row", "col"):
            raise ValueError(
                f"HeatmapChart normalize must be None, 'row' or 'col', "
                f"got {self.normalize!r}")

    _component_type: str = field(default="heatmap_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            cfg = {
                "type": "heatmap",
                "dataset_id": self.dataset_id,
                "x_col": self.x,
                "y_col": self.y,
                "value_col": self.value,
                "color_scale": self.color_scale,
                "value_format": self.value_format,
                "title": self.title,
            }
            if self.log_scale is not None:
                cfg["log_scale"] = self.log_scale
            if self.normalize:
                cfg["normalize"] = self.normalize
            if self.drop_empty:
                cfg["drop_empty"] = True
            ctx.register(cid, cfg)
        else:
            # Aggregate per (x, y) cell so multiple rows that share a
            # coordinate sum into one matrix point. Without this the
            # chartjs-chart-matrix renderer would draw overlapping
            # points (last-write-wins for color, tooltip fans out
            # across the hidden rows). One row per cell is the only
            # shape the matrix renderer renders correctly.
            cells = self._aggregate_cells(self.df)
            x_labels = list(dict.fromkeys(c["x"] for c in cells))
            y_labels = list(dict.fromkeys(c["y"] for c in cells))
            cfg = {
                "type": "heatmap",
                "cells": cells,
                "x_labels": x_labels,
                "y_labels": y_labels,
                "color_scale": self.color_scale,
                "value_format": self.value_format,
            }
            if self.log_scale is not None:
                cfg["log_scale"] = self.log_scale
            if self.normalize:
                cfg["normalize"] = self.normalize
            if self.drop_empty:
                cfg["drop_empty"] = True
            ctx.register(cid, cfg)

        return _chart_container_html(cid, self.title)

    def _aggregate_cells(self, df: pd.DataFrame) -> list[dict]:
        """Collapse to one row per (x, y) coordinate, summing the value."""
        if df.empty:
            return []
        def _stringify(v: Any) -> str:
            if hasattr(v, "isoformat"):
                return v.isoformat()[:10]
            return str(v) if v is not None else ""
        x_str = df[self.x].map(_stringify)
        y_str = df[self.y].map(_stringify)
        v_num = pd.to_numeric(df[self.value], errors="coerce").fillna(0)
        tmp = pd.DataFrame({"x": x_str, "y": y_str, "v": v_num})
        agg = tmp.groupby(["x", "y"], sort=False, observed=True)["v"].sum().reset_index()
        return [
            {"x": row["x"], "y": row["y"],
             "v": None if row["v"] is None else float(row["v"])}
            for _, row in agg.iterrows()
        ]

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps() + ["chartjs_matrix"]

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css()

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/heatmap.js")
