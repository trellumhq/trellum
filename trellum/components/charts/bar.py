"""Bars, and the combo chart built on them: BarChart, StackedBar,
ComboChart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from trellum.components.base import RenderContext
from trellum.components.charts.base import (
    _DOWNLOAD_SVG,
    _chart_container_html,
    _ChartBase,
    _clean_list,
    _normalize_toggle_html,
    _stack_toggle_html,
    _title_html,
)


@dataclass
class BarChart(_ChartBase):
    """Vertical or horizontal bar chart.

    Pass ``dataset_id`` to make this chart reactive to filter changes.
    Set ``show_labels=True`` to display data values on each bar.

    Ratio mode: set ``ratios=[{numerator, denominator, label}]`` to plot
    sum(numerator) / sum(denominator) per x-value. Works in horizontal
    mode too (typical use: CPD/ARPU/conversion ratios across categorical
    rows, with ``sort='desc'`` to rank). ``y`` is ignored in ratio mode.

    Top-N mode: set ``top_n=20`` to render only the top (or bottom) N
    x-values after aggregation, collapsing the long tail into a single
    ``Other (N items)`` bar. Implies ``sort='desc'`` if no explicit
    sort is given. Works in both reactive (``dataset_id``) and static
    modes — in reactive mode the top-N is recomputed on every filter
    change, so filtering remains correct.

    Set ``top_n_show_other=False`` to drop the "Other" bar entirely when
    a clean top-N ranking is preferred (the long tail's contribution
    can otherwise dwarf each top entry visually).
    """

    df: pd.DataFrame
    x: str
    y: str
    title: str
    y_cols: Optional[List[str]] = None
    y_labels: Optional[List[str]] = None
    horizontal: bool = False
    stacked: bool = False
    net_color: bool = False
    dataset_id: Optional[str] = None
    static: bool = False
    y_format: Optional[str] = None
    cross_filter: bool = False
    sort: Optional[str] = None  # "desc" or "asc" — sort bars by aggregated value
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    show_labels: bool = False  # Show data values on bars
    ratios: Optional[List[Dict[str, str]]] = None
    top_n: Optional[int] = None  # Cap to top/bottom N x-values + "Other" bucket
    top_n_show_other: bool = True  # If False, drop the "Other (N items)" bar

    _component_type: str = field(default="bar_chart", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        cols = self.y_cols or [self.y]
        col_labels = self.y_labels or cols

        if self.dataset_id:
            if self.ratios:
                for r in self.ratios:
                    for k in ("numerator", "denominator"):
                        if k in r and r[k] not in cols:
                            cols = list(cols) + [r[k]]
            cfg: dict[str, Any] = {
                "type": "chart",
                "dataset_id": self.dataset_id,
                "chartType": "bar",
                "x": self.x,
                "value_cols": cols,
                "y_labels": col_labels,
                "horizontal": self.horizontal,
                "stacked": self.stacked,
                "net_color": self.net_color,
                "title": self.title,
            }
            if self.ratios:
                cfg["ratios"] = self.ratios
            if self.y_format:
                cfg["y_format"] = self.y_format
            if self.cross_filter:
                cfg["crossFilter"] = self.x
            # top_n implies sort=desc unless caller picked an explicit
            # direction. Without a sort the "top" is undefined.
            effective_sort = self.sort or ("desc" if self.top_n else None)
            if effective_sort:
                cfg["sort"] = effective_sort
            if self.top_n is not None and self.top_n > 0:
                cfg["top_n"] = int(self.top_n)
                if not self.top_n_show_other:
                    cfg["top_n_show_other"] = False
            if self.y_min is not None:
                cfg["y_min"] = self.y_min
            if self.y_max is not None:
                cfg["y_max"] = self.y_max
            if self.show_labels:
                cfg["show_labels"] = True
            ctx.register(cid, cfg)
        else:
            theme = ctx.theme
            df_to_render = self.df
            if self.top_n is not None and self.top_n > 0 and not df_to_render.empty:
                df_to_render = self._apply_static_top_n(df_to_render, cols)
            datasets = []
            for i, (col, label) in enumerate(zip(cols, col_labels)):
                color = theme.chart_colors[i % len(theme.chart_colors)]
                datasets.append({
                    "label": label,
                    "data": _clean_list(df_to_render[col].tolist()),
                    "backgroundColor": f"{color}cc",
                    "borderColor": color,
                    "borderWidth": 1,
                    "_themeManaged": True,
                })
            ctx.register(cid, {
                "type": "chart",
                "chartType": "bar",
                "labels": _clean_list(df_to_render[self.x].tolist()),
                "datasets": datasets,
                "stacked": self.stacked,
                "horizontal": self.horizontal,
                "showLegend": len(cols) > 1,
                "showLabels": self.show_labels,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        return _chart_container_html(cid, self.title)

    def _apply_static_top_n(self, df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
        """Slice a static DataFrame to top-N + 'Other' before rendering.

        Aggregates by ``x`` (in case the caller passed unaggregated rows),
        sorts by the first value column descending, keeps the top N, and
        sums the remaining rows into a single ``Other (k items)`` bar.
        """
        sort_col = cols[0]
        agg = df.groupby(self.x, observed=True, dropna=False)[cols].sum().reset_index()
        ascending = self.sort == "asc"
        agg = agg.sort_values(sort_col, ascending=ascending)
        n = int(self.top_n)
        head = agg.head(n).copy()
        tail = agg.iloc[n:]
        if tail.empty or not self.top_n_show_other:
            return head
        other = {self.x: f"Other ({len(tail)} items)"}
        for c in cols:
            other[c] = tail[c].sum() if c in tail.columns else 0
        return pd.concat([head, pd.DataFrame([other])], ignore_index=True)

@dataclass
class StackedBar(_ChartBase):
    """Stacked bar chart, optionally diverging around zero.

    In live mode (``dataset_id`` set), use ``stack_by`` to specify which column
    to pivot on.  ``stack_by_options`` renders a toggle to switch dimensions.
    """

    df: pd.DataFrame
    x: str
    y_cols: List[str]
    title: str
    diverging: bool = False
    toggle_target: Optional[str] = None
    stack_tooltip_breakdown: bool = True
    value_format: str = "currency"
    dataset_id: Optional[str] = None
    static: bool = False
    stack_by: Optional[str] = None
    stack_by_options: Optional[Dict[str, str]] = None
    line_cols: Optional[List[str]] = None
    line_labels: Optional[List[str]] = None
    normalize_toggle: bool = False
    stack_toggle_id: Optional[str] = None
    normalize_toggle_id: Optional[str] = None
    cross_filter: bool = False
    # Ordering of pivoted stack segments when ``stack_by`` is set.
    # "volume_desc" (default) | "volume_asc" | "label_asc" | "label_desc".
    # Use "label_asc" for tier buckets that carry an intrinsic order in
    # their label so the rendering matches the conceptual ordering.
    stack_sort: Optional[str] = None
    # Cap on how many stacks to render before bundling the tail into an
    # "Other" segment. Default 20 matches the historical hardcoded limit;
    # raise this on charts where the long tail itself is the point.
    max_stacks: Optional[int] = None
    # Toggle the fwLegend (HTML legend buttons) above the chart. Useful
    # for stacked charts with very high cardinality (hundreds of stacks)
    # where the legend would dominate the screen and the tooltip + filter
    # UI is enough to identify individual series.
    show_legend: bool = True

    _component_type: str = field(default="stacked_bar", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()

        if self.dataset_id:
            cfg = {
                "type": "chart",
                "dataset_id": self.dataset_id,
                "chartType": "bar",
                "x": self.x,
                "value_cols": self.y_cols,
                "stacked": True,
                "stack_by": self.stack_by,
                "stack_by_options": self.stack_by_options,
                "title": self.title,
                "value_format": self.value_format,
            }
            if self.diverging:
                cfg["diverging"] = True
            if self.line_cols:
                cfg["line_cols"] = self.line_cols
                cfg["line_labels"] = self.line_labels or self.line_cols
            if self.normalize_toggle:
                cfg["normalize_toggle"] = True
            if self.stack_by_options and self.stack_toggle_id:
                cfg["stack_toggle_id"] = self.stack_toggle_id
            if self.normalize_toggle and self.normalize_toggle_id:
                cfg["normalize_toggle_id"] = self.normalize_toggle_id
            if self.cross_filter and self.stack_by:
                cfg["crossFilter"] = self.stack_by
                cfg["crossFilterMode"] = "dataset"
            elif self.cross_filter:
                cfg["crossFilter"] = self.x
            if self.stack_sort:
                cfg["stack_sort"] = self.stack_sort
            if self.max_stacks is not None:
                cfg["max_stacks"] = self.max_stacks
            if not self.show_legend:
                cfg["show_legend"] = False
            ctx.register(cid, cfg)
            toggle_html = ""
            if self.stack_by_options:
                toggle_html = _stack_toggle_html(
                    cid, self.stack_by_options, self.stack_toggle_id,
                )
            normalize_html = ""
            if self.normalize_toggle:
                normalize_html = _normalize_toggle_html(
                    cid, self.normalize_toggle_id,
                )
            controls_html = toggle_html + normalize_html
            return _chart_container_html(cid, self.title, extra_before=controls_html)

        theme = ctx.theme
        datasets = []
        for i, col in enumerate(self.y_cols):
            color = theme.chart_colors[i % len(theme.chart_colors)]
            datasets.append({
                "label": col,
                "data": _clean_list(self.df[col].tolist()),
                "backgroundColor": f"{color}cc",
                "borderColor": color,
                "borderWidth": 1,
                "_themeManaged": True,
            })

        ctx.register(cid, {
            "type": "chart",
            "chartType": "bar",
            "labels": _clean_list(self.df[self.x].tolist()),
            "datasets": datasets,
            "stacked": True,
            "horizontal": False,
            "showLegend": True,
            "gridColor": theme.grid_color,
            "tickColor": theme.tick_color,
            "textSecondary": theme.text_secondary,
            "stackTooltipBreakdown": self.stack_tooltip_breakdown,
            "valueFormat": self.value_format,
            "yTickFormat": self.value_format,
        })

        return _chart_container_html(cid)

@dataclass
class ComboChart(_ChartBase):
    """Dual-axis combo chart: bars on the left y-axis, lines on the right y-axis.

    Replaces the most common RawHTML pattern. Uses the same _createChart
    factory that already supports y1 axis detection.
    """

    df: pd.DataFrame
    x: str
    bar_cols: List[str]
    line_cols: List[str]
    title: str = ""
    bar_labels: Optional[List[str]] = None
    line_labels: Optional[List[str]] = None
    bar_format: str = "currency"
    line_format: str = "number"
    stacked_bars: bool = False
    dataset_id: Optional[str] = None
    static: bool = False
    line_colors: Optional[List[str]] = None  # Custom colors for line series
    show_labels: bool = False  # Show data labels on bars
    height: Optional[int] = None  # Custom chart height in pixels
    y_max: Optional[float] = None  # Max value for left y-axis
    y1_max: Optional[float] = None  # Max value for right y-axis (line)

    _component_type: str = field(default="combo_chart", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        b_labels = self.bar_labels or self.bar_cols
        l_labels = self.line_labels or self.line_cols

        if self.dataset_id:
            cfg = {
                "type": "chart",
                "dataset_id": self.dataset_id,
                "chartType": "bar",
                "x": self.x,
                "value_cols": self.bar_cols,
                "y_labels": b_labels,
                "stacked": self.stacked_bars,
                "combo": True,
                "line_cols": self.line_cols,
                "line_labels": l_labels,
                "bar_format": self.bar_format,
                "line_format": self.line_format,
                "title": self.title,
            }
            if self.line_colors:
                cfg["line_colors"] = self.line_colors
            if self.show_labels:
                cfg["show_labels"] = True
            if self.y_max is not None:
                cfg["y_max"] = self.y_max
            if self.y1_max is not None:
                cfg["y1_max"] = self.y1_max
            ctx.register(cid, cfg)
        else:
            theme = ctx.theme
            datasets = []
            for i, (col, label) in enumerate(zip(self.bar_cols, b_labels)):
                color = theme.chart_colors[i % len(theme.chart_colors)]
                datasets.append({
                    "label": label,
                    "data": _clean_list(self.df[col].tolist()),
                    "backgroundColor": f"{color}cc",
                    "borderColor": color,
                    "borderWidth": 1,
                    "yAxisID": "y",
                    "_themeManaged": True,
                })
            for i, (col, label) in enumerate(zip(self.line_cols, l_labels)):
                if self.line_colors and i < len(self.line_colors):
                    color = self.line_colors[i]
                    theme_managed = False
                else:
                    ci = len(self.bar_cols) + i
                    color = theme.chart_colors[ci % len(theme.chart_colors)]
                    theme_managed = True
                datasets.append({
                    "label": label,
                    "data": _clean_list(self.df[col].tolist()),
                    "type": "line",
                    "yAxisID": "y1",
                    "borderColor": color,
                    "backgroundColor": "transparent",
                    "borderWidth": 2.5,
                    "pointRadius": 2,
                    "tension": 0,
                    "order": -1,
                    "_themeManaged": theme_managed,
                })
            ctx.register(cid, {
                "type": "chart",
                "chartType": "bar",
                "labels": _clean_list(self.df[self.x].tolist()),
                "datasets": datasets,
                "stacked": self.stacked_bars,
                "showLegend": True,
                "showLabels": self.show_labels,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        # Use custom height if provided
        if self.height:
            dl_btn = (
                f'<button class="fw-chart-dl" data-csv-chart="{cid}" '
                f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
            )
            return (
                f'{_title_html(self.title)}'
                f'<div class="fw-chart-container" style="height: {self.height}px;">'
                f'<canvas id="{cid}"></canvas>{dl_btn}</div>'
            )
        return (
            _chart_container_html(cid, self.title)
        )
