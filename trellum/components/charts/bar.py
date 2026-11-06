"""Bars, and the combo chart built on them: BarChart, StackedBar,
ComboChart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from trellum.components.base import RenderContext
from trellum.components.charts._header_summary import header_summary_config
from trellum.components.charts._stacked_summary import (
    stacked_summary_config, stacked_summary_input_reason,
)
from trellum.components.charts.base import (
    _DOWNLOAD_SVG,
    _category_colors_config,
    _chart_container_html,
    _ChartBase,
    _clean_list,
    _context_description_attr,
    _header_context_html,
    _month_context_config,
    _monthly_average_config,
    _normalize_toggle_html,
    _stack_toggle_html,
    _title_html,
    _validate_category_colors,
    _validate_header_context,
)


@dataclass
class BarChart(_ChartBase):
    """Vertical or horizontal bar chart.

    Pass ``dataset_id`` to make this chart reactive to filter changes.
    Set ``show_labels=True`` to display data values on each bar in live mode.
    Keyword-only ``show_values=None`` adds a right value column automatically
    for horizontal nonstacked categories; False hides it, True includes dates.
    See ``docs/report-bar-labels.md`` for fitting limits and static differences.

    Keyword-only ``header_summary=None`` optionally declares a statistic over
    one final plotted series with ``scope='plotted_series'``. See
    ``docs/report-chart-summaries.md`` for selection and validity requirements.

    ``monthly_average=None`` optionally declares one additive daily quantity,
    its inclusive reporting interval and observed-day monthly guides/coverage.
    See ``docs/report-monthly-averages.md`` for input and scope requirements.

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

    show_values: Optional[bool] = field(default=None, kw_only=True)
    # Author-declared daily reporting dates; see docs/report-month-context.md.
    month_context: bool = field(default=False, kw_only=True)
    monthly_average: Optional[dict] = field(default=None, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)

    header_summary: Optional[Dict[str, Any]] = field(default=None, kw_only=True)

    _component_type: str = field(default="bar_chart", init=False, repr=False)

    def __post_init__(self) -> None:
        _month_context_config(self)
        _monthly_average_config(self)
        if self.monthly_average is not None:
            self.monthly_average = dict(self.monthly_average)
        _validate_header_context(self.header_context)
        self._validate_values()
        header_summary_config(self)

    def _validate_values(self) -> None:
        if self.show_values is not None and type(self.show_values) is not bool:
            raise ValueError("show_values must be bool or None")

    def render_html(self, ctx: RenderContext) -> str:
        summary_config = header_summary_config(self)
        month_config = {**_month_context_config(self), **_monthly_average_config(self)}
        self._validate_values()
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
                **month_config,
                **summary_config,
                "dataset_id": self.dataset_id,
                "chartType": "bar",
                "bar_values": {"show": self.show_values},
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
                **month_config,
                **summary_config,
                "chartType": "bar",
                "bar_values": {"show": self.show_values},
                "labels": _clean_list(df_to_render[self.x].tolist()),
                "datasets": datasets,
                "stacked": self.stacked,
                "horizontal": self.horizontal,
                "showLegend": len(cols) > 1,
                "showLabels": self.show_labels,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        return _chart_container_html(cid, self.title, summary=self.header_summary is not None,
                                     header_context=self.header_context)

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
    to pivot on. ``stack_by_options`` renders a toggle to switch dimensions.
    Keyword-only ``show_controls=False`` binds explicit external native Toggles
    without local copies. ``height=None`` keeps responsive sizing; an integer
    128..1200 sizes only the plot at every viewport. See
    docs/report-traffic-composition.md.
    Keyword-only ``show_totals=False`` enables visible absolute bar totals;
    ``show_percentages=False`` enables fitting labels in live pivot Percentage
    mode. Both require complete finite nonnegative additive quantities. See
    ``docs/report-bar-labels.md`` for eligibility and preserved path differences.
    Keyword-only ``header_summary=None`` optionally describes one exact final
    bar series in original units with ``scope="absolute_series"``. See
    ``docs/report-stacked-summaries.md`` for eligibility and scope.
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

    show_controls: bool = field(default=True, kw_only=True)
    height: Optional[int] = field(default=None, kw_only=True)

    show_totals: bool = field(default=False, kw_only=True)
    show_percentages: bool = field(default=False, kw_only=True)
    # Author-declared daily reporting dates; see docs/report-month-context.md.
    month_context: bool = field(default=False, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)
    category_colors: Optional[dict] = field(default=None, kw_only=True)

    header_summary: Optional[dict] = field(default=None, kw_only=True)

    _component_type: str = field(default="stacked_bar", init=False, repr=False)

    def __post_init__(self) -> None:
        stacked_summary_config(self)
        _month_context_config(self)
        _validate_header_context(self.header_context)
        _validate_category_colors(self.category_colors)
        self._validate_stack_labels()
        problems = stack_option_problems(self)
        if problems:
            raise ValueError(problems[0][1])

    def _validate_stack_labels(self) -> None:
        for name in ("show_totals", "show_percentages"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be bool")

    @classmethod
    def css(cls) -> str:
        from trellum.assets import load_css
        return super().css() + "\n" + load_css("components/stacked_bar.css")

    def _plot_container(self) -> dict:
        if self.height is None:
            return {}
        return {"container_class": "fw-stacked-height", "container_style": f"--fw-stacked-height:{self.height}px"}

    def render_html(self, ctx: RenderContext) -> str:
        summary_config = stacked_summary_config(self)
        month_config = _month_context_config(self)
        category_config = _category_colors_config(self, ctx)
        self._validate_stack_labels()
        problems = stack_option_problems(self)
        if problems:
            raise ValueError(problems[0][1])
        cid = ctx.next_id()
        stack_labels = {"totals": self.show_totals, "percentages": self.show_percentages,
                        "format": self.value_format}

        if self.dataset_id:
            cfg = {
                "type": "chart",
                **month_config,
                **summary_config,
                "dataset_id": self.dataset_id,
                "chartType": "bar",
                "stack_labels": stack_labels,
                "x": self.x,
                "value_cols": self.y_cols,
                "stacked": True,
                "stack_by": self.stack_by,
                "stack_by_options": self.stack_by_options,
                "title": self.title,
                "value_format": self.value_format,
            }
            if not self.show_controls:
                cfg["stacked_bar_controls"] = True
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
            cfg.update(category_config)
            ctx.register(cid, cfg)
            toggle_html = ""
            if self.show_controls and self.stack_by_options:
                toggle_html = _stack_toggle_html(
                    cid, self.stack_by_options, self.stack_toggle_id,
                )
            normalize_html = ""
            if self.show_controls and self.normalize_toggle:
                normalize_html = _normalize_toggle_html(
                    cid, self.normalize_toggle_id,
                )
            controls_html = toggle_html + normalize_html
            return _chart_container_html(cid, self.title, extra_before=controls_html,
                                         summary=self.header_summary is not None,
                                         header_context=self.header_context, **self._plot_container())

        if self.show_totals or self.show_percentages:
            stack_labels["duplicate_columns"] = len(set(self.y_cols)) != len(self.y_cols)
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

        if summary_config:
            summary_config["stacked_summary_input_reason"] = stacked_summary_input_reason(self)
        ctx.register(cid, {
            "type": "chart",
            **summary_config,
            **month_config,
            "chartType": "bar",
            "stack_labels": stack_labels,
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

        return _chart_container_html(cid, summary=self.header_summary is not None,
                                     header_context=self.header_context, **self._plot_container())

def stack_option_problems(chart: StackedBar) -> list[tuple[str, str]]:
    """Strict opt-in controls/sizing diagnostics, also after author mutation."""
    problems = []
    show_controls = getattr(chart, "show_controls", True)
    height = getattr(chart, "height", None)
    if type(show_controls) is not bool:
        problems.append(("stacked-bar-controls-invalid", "StackedBar show_controls must be bool."))
    if height is not None and (type(height) is not int or not 128 <= height <= 1200):
        problems.append(("stacked-bar-height-invalid", "StackedBar height must be None or an integer from 128 to 1200 (bool is not a height)."))
    if show_controls is False and chart.dataset_id:
        for requested, name in ((chart.stack_by_options, "stack_toggle_id"), (chart.normalize_toggle, "normalize_toggle_id")):
            value = getattr(chart, name)
            if requested and (not isinstance(value, str) or not value.strip() or value != value.strip()):
                problems.append(("stacked-bar-control-id-missing", f"StackedBar show_controls=False requires an explicit nonempty {name} for each requested control."))
    return problems

@dataclass
class ComboChart(_ChartBase):
    """Dual-axis combo chart: bars on the left y-axis, lines on the right y-axis.

    Keyword-only ``left_axis_label`` / ``right_axis_label`` describe supplied
    metric units. Defaults list actual series captions. Context and legend
    controls sit outside the plot, preserving its existing height and scales.
    See ``docs/report-combos.md`` for static/live formatting differences.
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
    left_axis_label: Optional[str] = field(default=None, kw_only=True)
    right_axis_label: Optional[str] = field(default=None, kw_only=True)
    # Author-declared daily reporting dates; see docs/report-month-context.md.
    month_context: bool = field(default=False, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)

    header_summary: Optional[Dict[str, Any]] = field(default=None, kw_only=True)

    _component_type: str = field(default="combo_chart", init=False, repr=False)

    def __post_init__(self) -> None:
        from trellum.components.charts._combo_summary import combo_summary_config
        combo_summary_config(self)
        _month_context_config(self)
        _validate_header_context(self.header_context)
        self._validate_axis_labels()

    def _validate_axis_labels(self) -> None:
        for name in ("left_axis_label", "right_axis_label"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty string or None")

    @classmethod
    def css(cls) -> str:
        from trellum.assets import load_css

        return super().css() + load_css("components/combo_chart.css")

    def render_html(self, ctx: RenderContext) -> str:
        from trellum.components.charts._combo_summary import (
            combo_summary_config, combo_summary_html, combo_summary_input_reason,
        )
        summary_config = combo_summary_config(self)
        month_config = _month_context_config(self)
        self._validate_axis_labels()
        cid = ctx.next_id()
        b_labels = self.bar_labels or self.bar_cols
        l_labels = self.line_labels or self.line_cols
        combo_axes = {"left_label": self.left_axis_label,
                      "right_label": self.right_axis_label}

        if self.dataset_id:
            cfg = {
                "type": "chart",
                **month_config,
                **summary_config,
                "dataset_id": self.dataset_id,
                "chartType": "bar",
                "x": self.x,
                "value_cols": self.bar_cols,
                "y_labels": b_labels,
                "stacked": self.stacked_bars,
                "combo": True,
                "combo_axes": combo_axes,
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
            if summary_config:
                summary_config["combo_summary_input_reason"] = combo_summary_input_reason(self)
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
                **month_config,
                **summary_config,
                "chartType": "bar",
                "combo_axes": combo_axes,
                "labels": _clean_list(self.df[self.x].tolist()),
                "datasets": datasets,
                "stacked": self.stacked_bars,
                "showLegend": True,
                "showLabels": self.show_labels,
                "gridColor": theme.grid_color,
                "tickColor": theme.tick_color,
            })

        def with_summary(html):
            return combo_summary_html(cid, self.title, html) if summary_config else html

        # Use custom height if provided
        if self.height:
            dl_btn = (
                f'<button class="fw-chart-dl" data-csv-chart="{cid}" '
                f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
            )
            return with_summary(
                f'{_title_html(self.title)}'
                f'{_header_context_html(cid, self.header_context)}'
                f'<div class="fw-chart-container" style="height: {self.height}px;">'
                f'<canvas id="{cid}"{_context_description_attr(cid, self.header_context)}></canvas>{dl_btn}</div>'
            )
        return with_summary(
            _chart_container_html(cid, self.title, header_context=self.header_context)
        )
