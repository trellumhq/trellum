"""ComboChart-only declarations and raw static input eligibility."""
from __future__ import annotations

import math
from numbers import Real
from typing import Any

_FORMATS = ("number", "currency", "percent", "chips", "ratio", "plain")
_STATS = {"sum": "Total", "mean": "Mean", "min": "Minimum", "max": "Maximum", "last": "Last shown"}


def combo_summary_config(comp: Any) -> dict:
    """Validate without choosing a future filtered dataset or changing its units."""
    option = getattr(comp, "header_summary", None)
    if option is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError("ComboChart header_summary: " + message)

    if not isinstance(option, dict):
        bad("must be a dict.")
    if set(option) - {"stat", "scope", "axis", "series", "label", "format"}:
        bad("unknown keys; use stat, scope, axis, series, label and format.")
    stat = option.get("stat")
    if not isinstance(stat, str) or stat not in _STATS:
        bad("stat must be sum, mean, min, max or last.")
    if not isinstance(option.get("scope"), str) or option["scope"] != "axis_series":
        bad("scope must be 'axis_series'.")
    axis = option.get("axis")
    if not isinstance(axis, str) or axis not in ("left", "right"):
        bad("axis must be left or right.")
    series = option.get("series")
    if not isinstance(series, str) or not series.strip() or len(series) > 120:
        bad("series must be a nonblank exact final caption of at most 120 characters.")
    if "label" in option and (not isinstance(option["label"], str) or not option["label"].strip() or len(option["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in option and (not isinstance(option["format"], str) or option["format"] not in _FORMATS):
        bad("format must be number, currency, percent, chips, ratio or plain.")
    declared = getattr(comp, "bar_format" if axis == "left" else "line_format")
    # Static Combo historically does not forward these declared formats.
    fallback = "currency" if axis == "left" else "number"
    actual = (declared if isinstance(declared, str) and declared in ("currency", "number", "percent") else fallback) if comp.dataset_id else ("number" if axis == "left" else "currency")
    fmt = option.get("format", actual)
    if stat == "sum" and (fmt in ("percent", "ratio") or (isinstance(declared, str) and declared in ("percent", "ratio"))):
        bad("sum is undefined for declared percent/ratio units or summary formats.")
    return {"combo_summary": {"stat": stat, "scope": "axis_series", "axis": axis,
        "series": series, "label": option.get("label", f"{_STATS[stat]} · {series}"), "format": fmt}}


def combo_summary_option_problems(comp: Any) -> list[tuple[str, str]]:
    """Pure validator dispatch, including declarations mutated after construction."""
    try:
        combo_summary_config(comp)
    except ValueError as exc:
        return [("chart-summary-invalid", str(exc))]
    return []


def combo_summary_input_reason(comp: Any) -> str:
    """Inspect only the uniquely selected raw static column, before cleaning.

    Static zip truncation is intentional. Final identity problems take precedence
    in the runtime; never substitute a source name for a missing final caption.
    """
    option = comp.header_summary
    cols = comp.bar_cols if option["axis"] == "left" else comp.line_cols
    labels = (comp.bar_labels if option["axis"] == "left" else comp.line_labels) or cols
    matches = [col for col, label in zip(cols, labels) if label == option["series"]]
    if len(matches) != 1:
        return ""
    col = matches[0]
    if col not in comp.df or list(comp.df.columns).count(col) != 1:
        return "Original selected column unavailable or ambiguous"
    for value in comp.df[col].tolist():
        if isinstance(value, bool) or not isinstance(value, Real):
            return "Incomplete or nonnumeric original selected values"
        try:
            if not math.isfinite(value) or (value == math.trunc(value) and abs(value) > 9007199254740991):
                return "Nonfinite or unsafe original selected values"
        except (OverflowError, ValueError):
            return "Nonfinite or unsafe original selected values"
    return ""


def combo_summary_html(cid: str, title: str, html: str) -> str:
    """Add an owned header without changing either existing plot container."""
    from trellum.components.charts.base import _title_html

    head = ('<div class="fw-combo-summary-head">' + _title_html(title, margin=False) +
            f'<div class="fw-combo-summary" data-combo-summary="{cid}"></div></div>')
    original = _title_html(title)
    return html.replace(original, head, 1) if original else head + html
