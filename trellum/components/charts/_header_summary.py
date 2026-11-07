"""Explicit chart-family summary declarations and population scopes."""

from __future__ import annotations

from typing import Any

_LABELS = {"mean": "Shown average", "sum": "Shown total", "last": "Last shown",
           "min": "Shown min", "max": "Shown max"}
_FORMATS = ("number", "currency", "percent", "chips", "ratio", "plain")
_KEYS = {"stat", "scope", "series", "label", "format"}


def _plotted_labels(chart: Any) -> list:
    """Mirror renderer precedence, including static zip and live fallbacks."""
    bar = hasattr(chart, "y_cols")
    cols = (chart.y_cols or [chart.y]) if bar else (
        chart.y if isinstance(chart.y, list) else [chart.y])
    labels = chart.y_labels or cols
    if chart.dataset_id:
        if bar and chart.net_color:
            if chart.ratios:
                raise ValueError("net_color with ratios is unsupported for header_summary; "
                                 "choose net_color or ratios explicitly.")
            return [chart.title]
        if bar and chart.ratios:
            return [ratio.get("label") for ratio in chart.ratios]
        return [labels[i] if i < len(labels) and labels[i] else col
                for i, col in enumerate(cols)]
    return [label for _, label in zip(cols, labels)]


def header_summary_config(chart: Any) -> dict:
    """Validate author options against actual plotted identity on every render."""
    cfg = getattr(chart, "header_summary", None)
    if cfg is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError(f"{type(chart).__name__} header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict with stat and scope='plotted_series'.")
    if set(cfg) - _KEYS:
        bad(f"unknown keys {sorted(set(cfg) - _KEYS, key=str)}.")
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in _LABELS:
        bad(f"stat must be one of {list(_LABELS)}.")
    if cfg.get("scope") != "plotted_series":
        bad("scope must be 'plotted_series'.")
    if "label" in cfg and (not isinstance(cfg["label"], str)
                           or not cfg["label"].strip() or len(cfg["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in cfg and cfg["format"] not in _FORMATS:
        bad(f"format must be one of {list(_FORMATS)}.")
    fmt = cfg.get("format", chart.y_format if chart.y_format in _FORMATS else "number")
    labels = _plotted_labels(chart)
    series = cfg.get("series", 0)
    if "series" not in cfg and len(labels) != 1:
        bad(f"series is required for {len(labels)} plotted series.")
    if isinstance(series, bool) or not isinstance(series, (int, str)):
        bad("series must be a nonnegative integer index or a unique plotted series label.")
    if isinstance(series, int):
        if not 0 <= series < len(labels):
            bad(f"series={series} is outside the {len(labels)} plotted series.")
    elif labels.count(series) != 1:
        bad(f"series={series!r} must match one unique plotted label; labels are {labels}.")
    if stat == "sum" and (chart.y_format in ("percent", "ratio") or fmt in ("percent", "ratio") or (
            hasattr(chart, "ratios") and chart.dataset_id and chart.ratios)):
        bad("sum is undefined for declared ratio series or percent/ratio formats; "
            "choose mean (unweighted), last, min or max.")
    return {"header_summary": {"stat": stat, "scope": "plotted_series", "series": series,
                               "label": cfg.get("label", _LABELS[stat]), "format": fmt}}


def doughnut_summary_config(chart: Any) -> dict:
    """Validate a category-entry summary independently of plotted series."""
    cfg = getattr(chart, "header_summary", None)
    if cfg is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError(f"DoughnutChart header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict with stat and scope='all_categories'.")
    if set(cfg) - {"stat", "scope", "label", "format"}:
        bad("unknown keys in declaration.")
    labels = {"sum": "Total", "max": "Largest category", "count": "Category entries"}
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in labels:
        bad("stat must be sum, max or count.")
    if not isinstance(cfg.get("scope"), str) or cfg["scope"] != "all_categories":
        bad("scope must be 'all_categories'.")
    if "label" in cfg and (not isinstance(cfg["label"], str)
                           or not cfg["label"].strip() or len(cfg["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in cfg and (not isinstance(cfg["format"], str) or cfg["format"] not in _FORMATS):
        bad(f"format must be one of {list(_FORMATS)}.")
    fmt = cfg.get("format", "number" if stat == "count" else chart.value_format)
    if stat == "count" and fmt not in ("number", "plain"):
        bad("count format must be number or plain; it counts category entries.")
    if stat == "sum" and (chart.value_format in ("percent", "ratio")
                          or fmt in ("percent", "ratio")):
        bad("sum is undefined for percent/ratio amounts or summary formats.")
    return {"header_summary": {"stat": stat, "scope": "all_categories",
                               "label": cfg.get("label", labels[stat]), "format": fmt}}
