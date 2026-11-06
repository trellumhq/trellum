"""Explicit summaries of retained aggregate cells; no plot coercion changes."""
from __future__ import annotations

import math
from typing import Any

_LABELS = {"count": "Cell entries", "sum": "Cell total", "min": "Minimum cell",
           "max": "Maximum cell", "mean": "Mean cell"}
_FORMATS = ("number", "currency", "percent", "chips", "ratio", "plain")


def heatmap_summary_config(chart: Any) -> dict:
    """Validate and copy an author declaration, including after mutation."""
    cfg = chart.header_summary
    if cfg is None:
        return {}

    def bad(message):
        raise ValueError(f"HeatmapChart header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict with stat and scope='current_cells'.")
    if set(cfg) - {"stat", "scope", "label", "format"}:
        bad("unknown keys; use stat, scope, label and format only.")
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in _LABELS:
        bad("stat must be count, sum, min, max or mean.")
    if not isinstance(cfg.get("scope"), str) or cfg["scope"] != "current_cells":
        bad("scope must be 'current_cells'.")
    if "label" in cfg and (not isinstance(cfg["label"], str) or not cfg["label"].strip()
                           or len(cfg["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in cfg and (not isinstance(cfg["format"], str) or cfg["format"] not in _FORMATS):
        bad("format must be number, currency, percent, chips, ratio or plain.")
    # Match the actual heatmap cell formatter, whose other names fall back to number.
    value_format = chart.value_format if isinstance(chart.value_format, str) else ""
    default = value_format if value_format in ("number", "currency", "percent") else "number"
    fmt = cfg.get("format", "number" if stat == "count" else default)
    if stat == "count" and fmt not in ("number", "plain"):
        bad("count format must be number or plain; count describes aggregate entries.")
    if stat == "sum" and (value_format in ("percent", "ratio") or fmt in ("percent", "ratio")):
        bad("sum is undefined for declared percent/ratio values or summary formats.")
    return {"header_summary": {"stat": stat, "scope": "current_cells",
                               "label": cfg.get("label", _LABELS[stat]), "format": fmt}}


def heatmap_option_problems(chart: Any) -> list[tuple[str, str]]:
    """Pure shared-validator dispatch hook for mutated declarations."""
    try:
        heatmap_summary_config(chart)
    except ValueError as exc:
        return [("chart-summary-invalid", str(exc))]
    return []


def heatmap_input_reason(values) -> str:
    """Inspect original values before pandas coercion, without retaining source rows."""
    for value in values:
        if (type(value) not in (int, float) or (type(value) is int and abs(value) > 2**53 - 1)
                or not math.isfinite(value)
                or (float(value).is_integer() and abs(value) > 2**53 - 1)):
            return "Supplied raw values include nonnumeric, missing, nonfinite or unsafe integer input; numeric summary withheld before drop_empty."
    return ""
