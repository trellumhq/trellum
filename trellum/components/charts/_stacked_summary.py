"""StackedBar-only summaries of one final series in original units."""
from __future__ import annotations

import math
from numbers import Real
from typing import Any

_FORMATS = ("number", "currency", "percent", "chips", "ratio", "plain")
_STATS = {"sum": "Total", "mean": "Mean", "min": "Minimum", "max": "Maximum", "last": "Last shown"}


def stacked_summary_config(comp: Any) -> dict:
    """Validate the declaration without guessing a future filtered caption."""
    option = getattr(comp, "header_summary", None)
    if option is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError("StackedBar header_summary: " + message)

    if not isinstance(option, dict):
        bad("must be a dict.")
    if set(option) - {"stat", "scope", "series", "label", "format"}:
        bad("unknown keys; use stat, scope, series, label and format.")
    stat = option.get("stat")
    if not isinstance(stat, str) or stat not in _STATS:
        bad("stat must be sum, mean, min, max or last.")
    scope = option.get("scope")
    if not isinstance(scope, str) or scope != "absolute_series":
        bad("scope must be 'absolute_series'.")
    series = option.get("series")
    if not isinstance(series, str) or not series.strip() or len(series) > 120:
        bad("series must be a nonblank exact final bar-series caption of at most 120 characters.")
    if "label" in option and (not isinstance(option["label"], str) or
                              not option["label"].strip() or len(option["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in option and (not isinstance(option["format"], str) or option["format"] not in _FORMATS):
        bad("format must be number, currency, percent, chips, ratio or plain.")
    declared = getattr(comp, "value_format", None)
    fmt = option.get("format", declared if isinstance(declared, str) and declared in _FORMATS else "number")
    if stat == "sum" and (fmt in ("percent", "ratio") or
                          (isinstance(declared, str) and declared in ("percent", "ratio"))):
        bad("sum is undefined for declared percent/ratio units or summary formats.")
    return {"stacked_summary": {"stat": stat, "scope": scope, "series": series,
        "label": option.get("label", f"{_STATS[stat]} · {series}"), "format": fmt}}


def stacked_summary_option_problems(comp: Any) -> list[tuple[str, str]]:
    """Pure shared-validator dispatch hook, including post-construction mutation."""
    try:
        stacked_summary_config(comp)
    except ValueError as exc:
        return [("chart-summary-invalid", str(exc))]
    return []


def stacked_summary_input_reason(comp: Any) -> str:
    """Compact static eligibility only; never copy or clean the plotted rows."""
    selected = comp.header_summary["series"]
    # Missing or repeated final captions are diagnosed by the rendered-series
    # selector. Unselected static columns do not contribute to this statistic.
    for col in [selected] if comp.y_cols.count(selected) == 1 else []:
        if col not in comp.df:
            return "Missing original bar values"
        for value in comp.df[col].tolist():
            if isinstance(value, bool) or not isinstance(value, Real):
                return "Incomplete or nonnumeric original bar values"
            try:
                if not math.isfinite(value) or (value == math.trunc(value) and abs(value) > 9007199254740991):
                    return "Nonfinite or unsafe original bar values"
            except (OverflowError, ValueError):
                return "Nonfinite or unsafe original bar values"
    return ""
