"""HeatmapChart: a value per (x, y) cell."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts._heatmap_summary import (
    heatmap_input_reason,
    heatmap_summary_config,
)
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
    _validate_header_context,
)


@dataclass
class HeatmapChart(Component):
    """Color-coded matrix chart using ``chartjs-chart-matrix``.

    Ideal for retention cohort grids and day/hour pattern analysis.
    Pass ``dataset_id`` for reactive filter support. Finite nonnegative cells
    use a single theme hue and an explicit scale key. Keyword-only
    ``show_values=False`` hides the default fitted values; tooltips keep units
    and full aggregate values. Absent coordinates are distinct from measured
    zero; existing null-to-zero aggregation is unchanged.

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
    #: Draw formatted cell values when they fit; exact values remain in tooltips.
    show_values: bool = field(default=True, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)
    header_summary: Optional[dict] = field(default=None, kw_only=True)

    def __post_init__(self):
        heatmap_summary_config(self)
        _validate_header_context(self.header_context)
        if not isinstance(self.show_values, bool):
            raise ValueError("HeatmapChart show_values must be a bool")
        if self.normalize not in (None, "row", "col"):
            raise ValueError(
                f"HeatmapChart normalize must be None, 'row' or 'col', "
                f"got {self.normalize!r}")

    _component_type: str = field(default="heatmap_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        summary = heatmap_summary_config(self)
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
                "heatmap_style": "sequential-v1",
                "show_values": self.show_values,
            }
            if self.log_scale is not None:
                cfg["log_scale"] = self.log_scale
            if self.normalize:
                cfg["normalize"] = self.normalize
            if self.drop_empty:
                cfg["drop_empty"] = True
            cfg.update(summary)
            ctx.register(cid, cfg)
        else:
            # Aggregate per (x, y) cell so multiple rows that share a
            # coordinate sum into one matrix point. Without this the
            # chartjs-chart-matrix renderer would draw overlapping
            # points (last-write-wins for color, tooltip fans out
            # across the hidden rows). One row per cell is the only
            # shape the matrix renderer renders correctly.
            input_reason = (heatmap_input_reason(self.df[self.value].tolist())
                            if summary and summary["header_summary"]["stat"] != "count" else None)
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
                "heatmap_style": "sequential-v1",
                "show_values": self.show_values,
                # JSON sanitization can turn nonfinite numbers into nulls.
                # Keep this presentation fact separate from aggregated values.
                "scale_population": ("nonnegative" if all(
                    c["v"] is not None and math.isfinite(c["v"]) and c["v"] >= 0
                    for c in cells) else "unsupported"),
            }
            if self.log_scale is not None:
                cfg["log_scale"] = self.log_scale
            if self.normalize:
                cfg["normalize"] = self.normalize
            if self.drop_empty:
                cfg["drop_empty"] = True
            cfg.update(summary)
            if input_reason is not None:
                cfg["summary_input_reason"] = input_reason
            ctx.register(cid, cfg)

        return _chart_container_html(
            cid, self.title,
            extra_before=f'<div class="fw-heatmap-key" data-heatmap-key="{cid}"></div>',
            header_context=self.header_context, summary=bool(summary),
        )

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
        return _ChartBase.css() + "\n" + load_css("components/heatmap.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/heatmap.js")
