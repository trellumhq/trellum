"""Continuous series over an x axis: LineChart and AreaChart."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Dict, List, Optional

import pandas as pd

from trellum.assets import load_css
from trellum.components.base import RenderContext
from trellum.components.charts._header_summary import header_summary_config
from trellum.components.charts._point_context import _LinePointContextAssets, point_context_config
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

#: Plotting-area heights a LineChart can ask for, in CSS pixels. The class a
#: height maps to sets the same value at every viewport width.
LINE_CHART_HEIGHTS = {"balanced": 220, "compact": 160, "roomy": 320}

#: Statistics a header summary can compute over one plotted series.
SUMMARY_STATS = ("mean", "sum", "last", "min", "max")
#: Formatter names the runtime's ``getFormatter`` knows.
SUMMARY_FORMATS = ("number", "currency", "percent", "chips", "ratio", "plain")
_SUMMARY_KEYS = {"stat", "series", "label", "format"}
_SUMMARY_LABEL_MAX = 60
_DEFAULT_SUMMARY_LABELS = {
    "mean": "Average", "sum": "Total", "last": "Latest", "min": "Min", "max": "Max",
}


def _line_series_labels(chart: "LineChart") -> list[str]:
    """Labels of the series the chart plots, in plotting order."""
    if chart.ratios and chart.dataset_id:
        return [str(r.get("label", "")) for r in chart.ratios]
    y_cols = chart.y if isinstance(chart.y, list) else [chart.y]
    labels = chart.y_labels or y_cols
    return [str(lb) for lb, _ in zip(labels, y_cols)]


def _normalize_forecast_series(value: Any) -> Optional[list[int | str]]:
    """Copy and shape-check author-selected forecast series references."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError("LineChart forecast_series must be a list or tuple of series positions or labels.")
    result: list[int | str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            raise ValueError("LineChart forecast_series entries must be non-bool integer positions or exact string labels.")
        result.append(item)
    return result


def _actual_line_captions(chart: "LineChart") -> list[str]:
    """Actual ordinary renderer/legend captions, including legacy fallbacks."""
    # Match the captions actually emitted by each existing renderer. Bound
    # wide series fall back to column names when y_labels is short or blank;
    # static series retain their existing zip behavior. The HTML legend also
    # supplies a caption for a blank dataset label.
    if chart.ratios and chart.dataset_id:
        captions = [ratio.get("label") for ratio in chart.ratios]
    else:
        columns = chart.y if isinstance(chart.y, list) else [chart.y]
        authored = chart.y_labels or columns
        captions = ([authored[i] if i < len(authored) and authored[i] else column
                     for i, column in enumerate(columns)] if chart.dataset_id
                    else [label for label, _ in zip(authored, columns)])
    return [str(label) if label else f"Series {i + 1}"
            for i, label in enumerate(captions)]


def _forecast_series_indices(chart: "LineChart") -> list[int]:
    """Resolve declarations against the chart's ordinary wide series."""
    refs = _normalize_forecast_series(chart.forecast_series) or []
    if not refs:
        return []
    if chart.stack_by or chart.stack_by_options:
        raise ValueError("LineChart forecast_series is not supported with stack_by or stack_by_options because split series are dynamic.")
    labels = _actual_line_captions(chart)
    resolved: list[int] = []
    for ref in refs:
        if isinstance(ref, int):
            if not 0 <= ref < len(labels):
                raise ValueError(f"LineChart forecast_series position {ref} is outside the {len(labels)} plotted series.")
            index = ref
        else:
            matches = [i for i, label in enumerate(labels) if label == ref]
            if len(matches) != 1:
                raise ValueError(f"LineChart forecast_series label {ref!r} matches {'no series' if not matches else 'more than one series'}; labels are {labels}.")
            index = matches[0]
        if index in resolved:
            raise ValueError(f"LineChart forecast_series selects series {index} more than once.")
        if labels.count(labels[index]) != 1:
            raise ValueError(f"LineChart forecast_series selects {labels[index]!r}, whose caption is shared by another series and grouped by the existing legend.")
        resolved.append(index)
    return resolved


def _normalize_series_availability(value: Any) -> Optional[list[dict]]:
    """Copy declarations and reject unsupported shapes before resolving series."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError("LineChart series_availability must be a list or tuple of rule dicts.")
    result = []
    for rule in value:
        if not isinstance(rule, dict):
            raise ValueError("LineChart series_availability entries must be rule dicts.")
        unknown = set(rule) - {"series", "required_columns", "state_column", "reason"}
        if unknown:
            raise ValueError(f"LineChart series_availability has unknown keys {sorted(unknown, key=str)}.")
        ref = rule.get("series")
        if isinstance(ref, bool) or not isinstance(ref, (int, str)):
            raise ValueError("LineChart series_availability series must be a non-bool integer position or exact displayed caption.")
        cols = rule.get("required_columns")
        if (not isinstance(cols, (list, tuple)) or not cols
                or any(not isinstance(c, str) or not c.strip() for c in cols)
                or len(set(cols)) != len(cols)):
            raise ValueError("LineChart series_availability required_columns must be nonempty distinct nonempty strings.")
        for key in ("state_column", "reason"):
            if key in rule and (not isinstance(rule[key], str) or not rule[key].strip()):
                raise ValueError(f"LineChart series_availability {key} must be nonempty plain text.")
        if "reason" in rule and (len(rule["reason"]) > 240 or any(ord(c) < 32 and c not in "\n\t\r" for c in rule["reason"])):
            raise ValueError("LineChart series_availability reason must be plain text of at most 240 characters.")
        result.append({**rule, "required_columns": list(cols)})
    return result


def _finite_line_input(value: Any) -> bool:
    """Numeric scalars supported by the chart, excluding missing/boolean values."""
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _series_availability_config(chart: "LineChart") -> dict:
    rules = _normalize_series_availability(chart.series_availability) or []
    if not rules:
        return {}
    if chart.stack_by or chart.stack_by_options:
        raise ValueError("LineChart series_availability is not supported with stack_by or stack_by_options because split series are dynamic.")
    captions = _actual_line_captions(chart)
    columns = chart.y if isinstance(chart.y, list) else [chart.y]
    selected = set()
    resolved = []
    for rule in rules:
        ref = rule["series"]
        matches = ([ref] if isinstance(ref, int) and 0 <= ref < len(captions)
                   else [i for i, caption in enumerate(captions) if caption == ref] if isinstance(ref, str) else [])
        if len(matches) != 1:
            raise ValueError(f"LineChart series_availability series {ref!r} must match exactly one plotted series; captions are {captions}.")
        index = matches[0]
        if index in selected or captions.count(captions[index]) != 1:
            raise ValueError(f"LineChart series_availability duplicate target or shared displayed caption {captions[index]!r} is unsupported by the grouped legend.")
        selected.add(index)
        underlying = ([chart.ratios[index][k] for k in ("numerator", "denominator")]
                      if chart.ratios and chart.dataset_id else [columns[index]])
        if not set(underlying) <= set(rule["required_columns"]):
            raise ValueError(f"LineChart series_availability required_columns must include underlying columns {underlying} for {captions[index]!r}.")
        item = {**rule, "series": index}
        if not chart.dataset_id:
            # Static points retain their source row positions, including duplicate x labels.
            item["unavailable"] = [i for i, row in enumerate(chart.df.to_dict("records"))
                if any(not _finite_line_input(row.get(col)) for col in rule["required_columns"])
                or ("state_column" in rule and (not isinstance(row.get(rule["state_column"]), str)
                    or row[rule["state_column"]] != "available"))]
        resolved.append(item)
    return {"series_availability": resolved}


def line_option_problems(chart: "LineChart") -> list[tuple[str, str]]:
    """Everything wrong with a LineChart's size, summary and interpolation.

    Returns ``(check id, message)`` pairs; empty when the options are usable.
    The component raises on the first one at construction, and the report
    validator reports them all for a chart changed afterwards.
    """
    problems: list[tuple[str, str]] = []
    try:
        point_context_config(chart)
    except ValueError as exc:
        problems.append(("line-chart-point-context-invalid", str(exc)))
    if type(getattr(chart, "result_presentation", True)) is not bool:
        problems.append(("line-chart-result-presentation-invalid", "LineChart result_presentation must be bool."))
    try:
        _series_availability_config(chart)
    except ValueError as exc:
        problems.append(("line-chart-availability-invalid", str(exc)))
    if not isinstance(chart.height, str) or chart.height not in LINE_CHART_HEIGHTS:
        problems.append((
            "line-chart-height-invalid",
            f"LineChart height={chart.height!r} must be one of "
            f"{sorted(LINE_CHART_HEIGHTS)}.",
        ))
    if (isinstance(chart.tension, bool)
            or not isinstance(chart.tension, (int, float))
            or not 0 <= chart.tension <= 1 or not math.isfinite(chart.tension)):
        problems.append((
            "line-chart-tension-invalid",
            f"LineChart tension={chart.tension!r} must be a finite number from 0 to 1.",
        ))
    cfg = chart.header_summary
    if cfg is None:
        return problems

    def bad(message: str) -> None:
        problems.append(("line-chart-summary-invalid", f"LineChart header_summary: {message}"))

    if not isinstance(cfg, dict):
        bad("must be a dict such as {'stat': 'mean', 'series': 0, 'label': 'Daily avg'}.")
        return problems
    unknown = sorted(set(cfg) - _SUMMARY_KEYS, key=str)
    if unknown:
        bad(f"unknown keys {unknown}; allowed keys are {sorted(_SUMMARY_KEYS)}.")
    stat = cfg.get("stat")
    if stat not in SUMMARY_STATS:
        bad(f"stat={stat!r} must be one of {list(SUMMARY_STATS)}.")
    fmt = cfg.get("format")
    if fmt is not None and fmt not in SUMMARY_FORMATS:
        bad(f"format={fmt!r} must be one of {list(SUMMARY_FORMATS)}.")
    label = cfg.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > _SUMMARY_LABEL_MAX):
        bad(f"label must be text of at most {_SUMMARY_LABEL_MAX} characters.")
    if chart.stack_by or chart.stack_by_options:
        bad("is not supported with stack_by or stack_by_options: the lines come "
            "from the data and change with the breakdown, so no series can be "
            "named in advance.")
        return problems

    labels = _line_series_labels(chart)
    series = cfg.get("series")
    if series is None:
        if len(labels) != 1:
            bad(f"series is required when the chart plots {len(labels)} series; "
                "give a position (0 is the first) or the series label.")
    elif isinstance(series, bool) or not isinstance(series, (int, str)):
        bad("series must be a position (int) or a series label (str).")
    elif isinstance(series, int):
        if not 0 <= series < len(labels):
            bad(f"series={series} is outside the {len(labels)} plotted series.")
    elif labels.count(series) != 1:
        found = "no series" if series not in labels else "more than one series"
        bad(f"series={series!r} matches {found} labelled that way; labels are {labels}.")
    if stat == "sum" and chart.ratios and chart.dataset_id:
        bad("stat='sum' is not meaningful for ratio series; use mean, last, min or max.")
    return problems


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

    ``event_labels``: keyword-only ``"adaptive"`` (default) shows dated buttons
    above the plot, with local exact event details. ``"grouped"`` retains
    numbered markers; ``"full"`` retains legacy in-plot labels.

    ``monthly_average=None`` optionally declares one additive daily quantity,
    its inclusive reporting interval and observed-day monthly guides/coverage.
    See ``docs/report-monthly-averages.md`` for input and scope requirements.

    ``series_availability``: keyword-only rules requiring complete finite raw
    supplied inputs per point. Invalid supplied observations become null gaps
    with selected-data context below the plot. See docs/report-line-availability.md.

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

    def render_html(self, ctx: RenderContext) -> str:
        month_config = {**_month_context_config(self), **_monthly_average_config(self)}
        self._validate_event_labels()
        forecast_indices = _forecast_series_indices(self)
        availability_config = {**_series_availability_config(self), **point_context_config(self, ctx)}
        if self.point_context is not None:
            ctx.render_child(_LinePointContextAssets())
        category_config = _category_colors_config(self, ctx)
        cid = ctx.next_id()

        if self.ratios and self.dataset_id:
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
            elif ctx.grid_color(self):
                cfg["grid_color"] = ctx.grid_color(self)
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
            if ctx.grid_color(self):
                cfg["grid_color"] = ctx.grid_color(self)
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
