"""Line-series declarations, normalization and option diagnostics."""

from __future__ import annotations

import math
from numbers import Real
from typing import TYPE_CHECKING, Any, Optional

from trellum.components.charts._point_context import point_context_config
from trellum.components.charts._trends import trend_config

if TYPE_CHECKING:
    from trellum.components.charts.line_area import LineChart

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
    if chart.dataset_id:
        return [str(labels[i] if i < len(labels) and labels[i] else col)
                for i, col in enumerate(y_cols)]
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
        trend_config(chart)
    except ValueError as exc:
        problems.append(("line-chart-trend-invalid", str(exc)))
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
