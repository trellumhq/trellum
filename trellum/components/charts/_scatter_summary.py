"""Scatter-local declarations for summaries of final supplied point entries."""

from __future__ import annotations

from typing import Any


def scatter_summary_config(chart: Any) -> dict:
    """Validate without mutation, including after authors change chart options."""
    cfg = getattr(chart, "header_summary", None)
    if cfg is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError(f"ScatterChart header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict with stat and scope='point_entries'.")
    if set(cfg) - {"stat", "scope", "axis", "label", "format"}:
        bad("unknown keys in declaration.")
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in ("count", "min", "max", "mean"):
        bad("stat must be count, min, max or mean.")
    if not isinstance(cfg.get("scope"), str) or cfg["scope"] != "point_entries":
        bad("scope must be 'point_entries'.")
    axis = cfg.get("axis")
    if stat == "count":
        if "axis" in cfg:
            bad("count forbids axis; it counts point entries.")
    elif not isinstance(axis, str) or axis not in ("x", "y"):
        bad("min, max and mean require axis='x' or axis='y'.")
    if "label" in cfg and (not isinstance(cfg["label"], str)
                           or not cfg["label"].strip() or len(cfg["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in cfg and (not isinstance(cfg["format"], str)
                            or cfg["format"] not in ("number", "currency", "percent", "chips", "ratio", "plain")):
        bad("format must be number, currency, percent, chips, ratio or plain.")
    # Scatter's legacy axes support only these three formats. In particular,
    # ratio/chips/plain axis declarations have always fallen back to number.
    legacy = getattr(chart, f"{axis}_format", "number") if stat != "count" else "number"
    fmt = cfg.get("format", legacy if isinstance(legacy, str) and legacy in ("number", "currency", "percent") else "number")
    if stat == "count" and fmt not in ("number", "plain"):
        bad("count format must be number or plain; it always displays an integer.")
    label = "Point entries" if stat == "count" else f"{dict(min='Minimum', max='Maximum', mean='Mean')[stat]} {axis}"
    result = {"stat": stat, "scope": "point_entries", "label": cfg.get("label", label), "format": fmt}
    if stat != "count":
        result["axis"] = axis
    return {"header_summary": result}


def scatter_option_problems(chart: Any) -> list[tuple[str, str]]:
    """Pure validator-dispatch hook for mutated ScatterChart declarations."""
    try:
        scatter_summary_config(chart)
    except ValueError as exc:
        return [("chart-summary-invalid", str(exc))]
    return []
