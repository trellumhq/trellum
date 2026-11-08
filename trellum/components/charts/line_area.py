"""Continuous series over an x axis: LineChart and AreaChart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from trellum.assets import load_css
from trellum.components.base import RenderContext
from trellum.components.charts._header_summary import header_summary_config
from trellum.components.charts._line_options import (
    _DEFAULT_SUMMARY_LABELS as _DEFAULT_SUMMARY_LABELS,
)
from trellum.components.charts._line_options import _SUMMARY_KEYS as _SUMMARY_KEYS
from trellum.components.charts._line_options import _SUMMARY_LABEL_MAX as _SUMMARY_LABEL_MAX

# Preserve helper imports used by existing report integrations.
from trellum.components.charts._line_options import LINE_CHART_HEIGHTS as LINE_CHART_HEIGHTS
from trellum.components.charts._line_options import SUMMARY_FORMATS as SUMMARY_FORMATS
from trellum.components.charts._line_options import SUMMARY_STATS as SUMMARY_STATS
from trellum.components.charts._line_options import _actual_line_captions as _actual_line_captions
from trellum.components.charts._line_options import _finite_line_input as _finite_line_input
from trellum.components.charts._line_options import (
    _forecast_series_indices as _forecast_series_indices,
)
from trellum.components.charts._line_options import _line_series_labels as _line_series_labels
from trellum.components.charts._line_options import (
    _normalize_forecast_series as _normalize_forecast_series,
)
from trellum.components.charts._line_options import (
    _normalize_series_availability as _normalize_series_availability,
)
from trellum.components.charts._line_options import (
    _series_availability_config as _series_availability_config,
)
from trellum.components.charts._line_options import line_option_problems as line_option_problems
from trellum.components.charts._point_context import _LinePointContextAssets, point_context_config
from trellum.components.charts._trends import trend_config
from trellum.components.charts.base import (
    _category_colors_config,
    _chart_container_html,
    _ChartBase,
    _clean_list,
    _month_context_config,
    _monthly_average_config,
    _stack_toggle_html,
    _validate_category_colors,
    _validate_header_context,
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

    ``tension``: keyword-only cubic interpolation, finite 0..1 (default 0,
    straight). Curves infer movement between observations and can overshoot;
    they are not averages, forecasts or monotone estimates. Persistent dots
    adapt to current plot spacing; isolated finite observations keep a dot.

    ``height``: size of the plotting area, the same at every viewport width.
    ``"balanced"`` (default) is 220px, ``"compact"`` 160px and ``"roomy"``
    320px. The title stays visible at every size, including in tile grids.

    ``header_summary``: optional statistic shown right-aligned in the chart
    header, for example ``{"stat": "mean", "series": 0, "label": "Daily avg",
    "format": "number"}``. It is computed in the browser from the series the
    chart plots after filters and aggregation, so it always matches the line.
    See the LineChart section of the README.

    ``event_labels``: keyword-only ``"adaptive"`` (default) draws a small
    marker per event in the plot and shows each name beside its marker where it
    fits, with exact names and original dates on selection and in an "All N
    events" list. ``"grouped"`` retains numbered markers; ``"full"`` retains
    legacy in-plot labels.

    ``monthly_average=None`` optionally declares one additive daily quantity,
    its inclusive reporting interval and observed-day monthly guides/coverage.
    See ``docs/report-monthly-averages.md`` for input and scope requirements.

    ``series_availability``: keyword-only rules requiring complete finite raw
    supplied inputs per point. Invalid supplied observations become null gaps
    with selected-data context below the plot. See docs/report-line-availability.md.

    ``trend_line``: optional keyword-only linear OLS declaration. Fits eligible
    observed daily values without altering source data; see docs/report-chart-trends.md.

    ``result_presentation``: keyword-only bool, default True. Show precise
    no-result messages and requested/displayed live result context without
    changing plotted values. See docs/report-line-results.md; False opts out.
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
    tension: float = field(default=0.0, kw_only=True)
    height: str = "balanced"
    header_summary: Optional[Dict[str, Any]] = None
    # Author-declared daily reporting dates; see docs/report-month-context.md.
    month_context: bool = field(default=False, kw_only=True)
    monthly_average: Optional[dict] = field(default=None, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)
    category_colors: Optional[dict] = field(default=None, kw_only=True)
    forecast_series: Optional[List[int | str] | tuple[int | str, ...]] = field(default=None, kw_only=True)
    event_labels: str = field(default="adaptive", kw_only=True)
    series_availability: Optional[list[dict] | tuple[dict, ...]] = field(default=None, kw_only=True)
    result_presentation: bool = field(default=True, kw_only=True)
    point_context: Optional[list[dict]] = field(default=None, kw_only=True)
    trend_line: Optional[dict] = field(default=None, kw_only=True)

    _component_type: str = field(default="line_chart", init=False, repr=False)

    @classmethod
    def css(cls) -> str:
        return super().css() + load_css("components/line_results.css") + load_css("components/adaptive_events.css")

    def __post_init__(self):
        _month_context_config(self)
        _monthly_average_config(self)
        if self.monthly_average is not None:
            self.monthly_average = dict(self.monthly_average)
        self._validate_event_labels()
        _validate_header_context(self.header_context)
        _validate_category_colors(self.category_colors)
        self.forecast_series = _normalize_forecast_series(self.forecast_series)
        self.series_availability = _normalize_series_availability(self.series_availability)
        problems = line_option_problems(self)
        if problems:
            raise ValueError(problems[0][1])

    def _container(self, cid: str, extra_before: str = "") -> str:
        return _chart_container_html(
            cid, self.title, extra_before=extra_before,
            container_class=f"fw-line-chart fw-line-{self.height}",
            title_class="fw-line-chart-title",
            summary=self.header_summary is not None,
            header_context=self.header_context,
        )

    def _summary_config(self) -> dict[str, Any]:
        """The ``header_summary`` entry for the chart's registered config."""
        cfg = self.header_summary
        if cfg is None:
            return {}
        out: dict[str, Any] = {
            "stat": cfg["stat"],
            "series": 0 if cfg.get("series") is None else cfg["series"],
            "label": cfg["label"] if cfg.get("label") is not None
            else _DEFAULT_SUMMARY_LABELS[cfg["stat"]],
        }
        if cfg.get("format"):
            out["format"] = cfg["format"]
        return {"header_summary": out}

    def _style_config(self) -> dict[str, Any]:
        if type(self.result_presentation) is not bool:
            raise ValueError("LineChart result_presentation must be bool.")
        return {"line_style": "adaptive", "tension": self.tension,
                "event_labels": self.event_labels,
                **({"line_result_presentation": True} if self.result_presentation else {})}

    def _validate_event_labels(self) -> None:
        if not isinstance(self.event_labels, str) or self.event_labels not in ("adaptive", "grouped", "full"):
            raise ValueError("LineChart event_labels must be 'adaptive', 'grouped' or 'full'.")

    def _render_ratios(self, ctx: RenderContext, cid: str, month_config: dict,
                       availability_config: dict, forecast_indices: list[int],
                       category_config: dict) -> str:
        all_cols: list[str] = []
        for r in self.ratios:
            for k in ("numerator", "denominator"):
                if r[k] not in all_cols:
                    all_cols.append(r[k])
        cfg_r: dict[str, Any] = {
            "type": "chart",
            **month_config,
            **availability_config,
            "dataset_id": self.dataset_id,
            "chartType": "line",
            "x": self.x,
            "value_cols": all_cols,
            "ratios": self.ratios,
            "y_format": self.y_format or "number",
            "title": self.title,
        }
        if forecast_indices:
            cfg_r["forecast_series"] = forecast_indices
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
        if self.colors:
            cfg_r["colors"] = list(self.colors)
        elif ctx.grid_color_slot(self) is not None:
            cfg_r["grid_color_slot"] = ctx.grid_color_slot(self)
        cfg_r.update(self._summary_config())
        cfg_r.update(self._style_config())
        cfg_r.update(category_config)
        ctx.register(cid, cfg_r)
        toggle_html_r = ""
        if self.stack_by_options:
            toggle_html_r = _stack_toggle_html(
                cid, self.stack_by_options, self.stack_toggle_id,
            )
        return self._container(cid, toggle_html_r)

    def render_html(self, ctx: RenderContext) -> str:
        month_config = {**_month_context_config(self), **_monthly_average_config(self)}
        self._validate_event_labels()
        forecast_indices = _forecast_series_indices(self)
        availability_config = {**_series_availability_config(self), **point_context_config(self, ctx),
                               **trend_config(self, ctx)}
        if self.point_context is not None:
            ctx.render_child(_LinePointContextAssets())
        category_config = _category_colors_config(self, ctx)
        cid = ctx.next_id()

        if self.ratios and self.dataset_id:
            return self._render_ratios(ctx, cid, month_config, availability_config,
                                       forecast_indices, category_config)

        y_cols = self.y if isinstance(self.y, list) else [self.y]
        y_labels = self.y_labels or y_cols

        if self.dataset_id:
            cfg: dict[str, Any] = {
                "type": "chart",
                **month_config,
                **availability_config,
                "dataset_id": self.dataset_id,
                "chartType": "line",
                "x": self.x,
                "value_cols": y_cols,
                "y_labels": y_labels,
                "title": self.title,
            }
            if forecast_indices:
                cfg["forecast_series"] = forecast_indices
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
            if self.colors:
                cfg["colors"] = list(self.colors)
            elif ctx.grid_color_slot(self) is not None:
                cfg["grid_color_slot"] = ctx.grid_color_slot(self)
            cfg.update(self._summary_config())
            cfg.update(self._style_config())
            cfg.update(category_config)
            ctx.register(cid, cfg)
            toggle_html_l = ""
            if self.stack_by_options:
                toggle_html_l = _stack_toggle_html(
                    cid, self.stack_by_options, self.stack_toggle_id,
                )
            if toggle_html_l:
                return self._container(cid, toggle_html_l)
        else:
            theme = ctx.theme
            datasets = []
            declared = {r["series"] for r in availability_config.get("series_availability", [])}
            for i, (col, label) in enumerate(zip(y_cols, y_labels)):
                has_custom_color = self.colors and i < len(self.colors)
                color = (self.colors[i] if has_custom_color
                         else (ctx.grid_color(self) or theme.chart_colors[i % len(theme.chart_colors)]))
                values = ([None] * len(self.df) if col not in self.df and i in declared
                          else _clean_list(self.df[col].tolist()))
                if i in declared:
                    # Nullable pandas scalars must reach the browser as JSON null.
                    # Keep finite values so state/extra-input rules still mask only
                    # the final runtime spec, without mutating the source frame.
                    values = [v if _finite_line_input(v) else None for v in values]
                datasets.append({
                    "label": label,
                    "data": values,
                    "borderColor": color,
                    "backgroundColor": f"{color}22",
                    "borderWidth": 2.5,
                    "pointRadius": 2,
                    "tension": self.tension,
                    "spanGaps": False,
                    "_themeManaged": not has_custom_color,
                    **({"_fwPaletteSlot": ctx.grid_color_slot(self)}
                       if not has_custom_color and ctx.grid_color_slot(self) is not None else {}),
                    **({"_fwForecast": True} if i in forecast_indices else {}),
                    **({"borderDash": [7, 4]} if i in forecast_indices else {}),
                })
            ctx.register(cid, {
                "type": "chart",
                **month_config,
                **availability_config,
                "chartType": "line",
                "labels": _clean_list(self.df[self.x].tolist()),
                "datasets": datasets,
                "showLegend": self.show_legend,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
                "textSecondary": theme.text_secondary,
                **self._summary_config(),
                **self._style_config(),
            })

        return self._container(cid)

@dataclass
class AreaChart(_ChartBase):
    """Filled area chart -- a ``LineChart`` variant with ``fill`` enabled.

    Use ``stacked=True`` for stacked area (composition over time).
    Pass ``dataset_id`` for reactive filter support.
    Keyword-only ``header_summary=None`` optionally summarizes one final raw
    band with ``scope='plotted_series'``; see ``docs/report-chart-summaries.md``.
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
    # Author-declared daily reporting dates; see docs/report-month-context.md.
    month_context: bool = field(default=False, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)

    header_summary: Optional[Dict[str, Any]] = field(default=None, kw_only=True)

    _component_type: str = field(default="area_chart", init=False, repr=False)

    def __post_init__(self) -> None:
        header_summary_config(self)
        _month_context_config(self)
        _validate_header_context(self.header_context)

    def render_html(self, ctx: RenderContext) -> str:
        summary_config = header_summary_config(self)
        month_config = _month_context_config(self)
        cid = ctx.next_id()
        y_cols = self.y if isinstance(self.y, list) else [self.y]
        y_labels = self.y_labels or y_cols

        if self.dataset_id:
            cfg: dict[str, Any] = {
                "type": "chart",
                **month_config,
                **summary_config,
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
            if ctx.grid_color_slot(self) is not None:
                cfg["grid_color_slot"] = ctx.grid_color_slot(self)
            ctx.register(cid, cfg)
        else:
            theme = ctx.theme
            datasets = []
            for i, (col, label) in enumerate(zip(y_cols, y_labels)):
                color = ctx.grid_color(self) or theme.chart_colors[i % len(theme.chart_colors)]
                datasets.append({
                    "label": label,
                    "data": _clean_list(self.df[col].tolist()),
                    "borderColor": color,
                    "backgroundColor": f"{color}{'a6' if self.stacked else '1f'}",
                    "borderWidth": 1.5 if self.stacked else 2,
                    "pointRadius": 0,
                    "tension": 0,
                    "fill": "origin" if not self.stacked or i == 0 else "-1",
                    "spanGaps": False,
                    "_themeManaged": True,
                    "_fillOpacity": "a6" if self.stacked else "1f",
                    "_areaFill": True,
                    **({"_fwPaletteSlot": ctx.grid_color_slot(self)}
                       if ctx.grid_color_slot(self) is not None else {}),
                })
            ctx.register(cid, {
                "type": "chart",
                **month_config,
                **summary_config,
                "chartType": "line",
                "labels": _clean_list(self.df[self.x].tolist()),
                "datasets": datasets,
                "stacked": self.stacked,
                "showLegend": len(y_cols) > 1,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        return _chart_container_html(cid, self.title, summary=self.header_summary is not None,
                                     header_context=self.header_context)
