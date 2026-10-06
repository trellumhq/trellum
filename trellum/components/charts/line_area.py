"""Continuous series over an x axis: LineChart and AreaChart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from trellum.components.base import RenderContext
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
    _clean_list,
    _stack_toggle_html,
)


@dataclass
class LineChart(_ChartBase):
    """Time-series line chart with one or more series.

    Pass ``dataset_id`` to make this chart reactive to filter changes.

    Ratio mode: set ``ratios`` to compute values as numerator/denominator
    instead of summing.  Each entry is a dict with ``numerator``,
    ``denominator``, and ``label`` keys.  When ``ratios`` is provided,
    ``y`` is ignored.

    ``stack_by``: name of a column in the underlying DataSource to pivot
    rows by, producing one line per distinct value. Works with both
    ``y`` and ``ratios``. Long-format friendly — prefer this over
    hand-listing one ``y`` column per category.
    """

    df: pd.DataFrame
    x: str
    y: str | list[str] = ""
    title: str = ""
    y_labels: Optional[List[str]] = None
    colors: Optional[List[str]] = None
    show_legend: bool = True
    toggle_target: Optional[str] = None
    dataset_id: Optional[str] = None
    static: bool = False
    ratios: Optional[List[Dict[str, str]]] = None
    y_format: Optional[str] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    stack_by: Optional[str] = None
    # When set, renders a button group above the chart that lets the user
    # switch the ``stack_by`` column on the fly. ``{"Label": "column"}``.
    stack_by_options: Optional[Dict[str, str]] = None
    stack_toggle_id: Optional[str] = None
    # Ordering of pivoted lines when ``stack_by`` is set.
    # "volume_desc" (default) | "volume_asc" | "label_asc" | "label_desc".
    # Use "label_asc" when stack values carry an intrinsic order in their
    # label ("01. Bucket A", "02. Bucket B", ...) and the visual order
    # should follow that, not the volume ranking.
    stack_sort: Optional[str] = None
    # Cap on how many lines to render before bundling the long tail into
    # an "Other" bucket. Default 20 matches the historical hardcoded limit;
    # raise this when the long tail itself is the point of the chart.
    max_stacks: Optional[int] = None
    show_labels: bool = False  # Show data values on line points

    _component_type: str = field(default="line_chart", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.ratios and self.dataset_id:
            all_cols: list[str] = []
            for r in self.ratios:
                for k in ("numerator", "denominator"):
                    if r[k] not in all_cols:
                        all_cols.append(r[k])
            cfg_r: dict[str, Any] = {
                "type": "chart",
                "dataset_id": self.dataset_id,
                "chartType": "line",
                "x": self.x,
                "value_cols": all_cols,
                "ratios": self.ratios,
                "y_format": self.y_format or "number",
                "title": self.title,
            }
            if self.y_min is not None:
                cfg_r["y_min"] = self.y_min
            if self.y_max is not None:
                cfg_r["y_max"] = self.y_max
            if self.stack_by:
                cfg_r["stack_by"] = self.stack_by
            if self.stack_by_options:
                cfg_r["stack_by_options"] = self.stack_by_options
                if self.stack_toggle_id:
                    cfg_r["stack_toggle_id"] = self.stack_toggle_id
            if self.stack_sort:
                cfg_r["stack_sort"] = self.stack_sort
            if self.max_stacks is not None:
                cfg_r["max_stacks"] = self.max_stacks
            if self.show_labels:
                cfg_r["show_labels"] = True
            ctx.register(cid, cfg_r)
            toggle_html_r = ""
            if self.stack_by_options:
                toggle_html_r = _stack_toggle_html(
                    cid, self.stack_by_options, self.stack_toggle_id,
                )
            return _chart_container_html(cid, self.title, extra_before=toggle_html_r)

        y_cols = self.y if isinstance(self.y, list) else [self.y]
        y_labels = self.y_labels or y_cols

        if self.dataset_id:
            cfg: dict[str, Any] = {
                "type": "chart",
                "dataset_id": self.dataset_id,
                "chartType": "line",
                "x": self.x,
                "value_cols": y_cols,
                "y_labels": y_labels,
                "title": self.title,
            }
            if self.y_format:
                cfg["y_format"] = self.y_format
            if self.y_min is not None:
                cfg["y_min"] = self.y_min
            if self.y_max is not None:
                cfg["y_max"] = self.y_max
            if self.stack_by:
                cfg["stack_by"] = self.stack_by
            if self.stack_by_options:
                cfg["stack_by_options"] = self.stack_by_options
                if self.stack_toggle_id:
                    cfg["stack_toggle_id"] = self.stack_toggle_id
            if self.stack_sort:
                cfg["stack_sort"] = self.stack_sort
            if self.max_stacks is not None:
                cfg["max_stacks"] = self.max_stacks
            if self.show_labels:
                cfg["show_labels"] = True
            ctx.register(cid, cfg)
            toggle_html_l = ""
            if self.stack_by_options:
                toggle_html_l = _stack_toggle_html(
                    cid, self.stack_by_options, self.stack_toggle_id,
                )
            if toggle_html_l:
                return _chart_container_html(cid, self.title, extra_before=toggle_html_l)
        else:
            theme = ctx.theme
            datasets = []
            for i, (col, label) in enumerate(zip(y_cols, y_labels)):
                has_custom_color = self.colors and i < len(self.colors)
                color = (self.colors[i] if has_custom_color
                         else theme.chart_colors[i % len(theme.chart_colors)])
                datasets.append({
                    "label": label,
                    "data": _clean_list(self.df[col].tolist()),
                    "borderColor": color,
                    "backgroundColor": f"{color}22",
                    "borderWidth": 2.5,
                    "pointRadius": 2,
                    "tension": 0,
                    "spanGaps": False,
                    "_themeManaged": not has_custom_color,
                })
            ctx.register(cid, {
                "type": "chart",
                "chartType": "line",
                "labels": _clean_list(self.df[self.x].tolist()),
                "datasets": datasets,
                "showLegend": self.show_legend,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
                "textSecondary": theme.text_secondary,
            })

        return _chart_container_html(cid, self.title)

@dataclass
class AreaChart(_ChartBase):
    """Filled area chart -- a ``LineChart`` variant with ``fill`` enabled.

    Use ``stacked=True`` for stacked area (composition over time).
    Pass ``dataset_id`` for reactive filter support.
    """

    df: pd.DataFrame
    x: str
    y: str | list[str] = ""
    title: str = ""
    y_labels: Optional[List[str]] = None
    stacked: bool = False
    dataset_id: Optional[str] = None
    static: bool = False
    y_format: Optional[str] = None
    show_labels: bool = False  # Show data values on points

    _component_type: str = field(default="area_chart", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        y_cols = self.y if isinstance(self.y, list) else [self.y]
        y_labels = self.y_labels or y_cols

        if self.dataset_id:
            cfg: dict[str, Any] = {
                "type": "chart",
                "dataset_id": self.dataset_id,
                "chartType": "line",
                "x": self.x,
                "value_cols": y_cols,
                "y_labels": y_labels,
                "stacked": self.stacked,
                "area_fill": True,
                "title": self.title,
            }
            if self.y_format:
                cfg["y_format"] = self.y_format
            if self.show_labels:
                cfg["show_labels"] = True
            ctx.register(cid, cfg)
        else:
            theme = ctx.theme
            datasets = []
            for i, (col, label) in enumerate(zip(y_cols, y_labels)):
                color = theme.chart_colors[i % len(theme.chart_colors)]
                datasets.append({
                    "label": label,
                    "data": _clean_list(self.df[col].tolist()),
                    "borderColor": color,
                    "backgroundColor": color if self.stacked else f"{color}33",
                    "borderWidth": 1 if self.stacked else 2,
                    "pointRadius": 0,
                    "tension": 0,
                    "fill": True,
                    "spanGaps": False,
                    "_themeManaged": True,
                    "_fillOpacity": "" if self.stacked else "33",
                })
            ctx.register(cid, {
                "type": "chart",
                "chartType": "line",
                "labels": _clean_list(self.df[self.x].tolist()),
                "datasets": datasets,
                "stacked": self.stacked,
                "showLegend": len(y_cols) > 1,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        return _chart_container_html(cid, self.title)
