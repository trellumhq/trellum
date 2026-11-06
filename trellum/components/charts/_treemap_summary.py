"""Treemap-local declarations and pre-serialization numeric eligibility."""

from __future__ import annotations

import math
from typing import Any


def treemap_summary_config(comp: Any) -> dict:
    """Validate without changing the declaration or existing plugin inputs."""
    cfg = getattr(comp, "header_summary", None)
    if cfg is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError(f"TreemapChart header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict with stat and scope='leaf_entries'.")
    if set(cfg) - {"stat", "scope", "label", "format"}:
        bad("unknown keys in declaration.")
    labels = {"count": "Leaf entries", "sum": "Leaf total", "min": "Minimum leaf",
              "max": "Maximum leaf", "mean": "Mean leaf"}
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in labels:
        bad("stat must be count, sum, min, max or mean.")
    if not isinstance(cfg.get("scope"), str) or cfg["scope"] != "leaf_entries":
        bad("scope must be 'leaf_entries'.")
    if "label" in cfg and (not isinstance(cfg["label"], str)
                           or not cfg["label"].strip() or len(cfg["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters.")
    if "format" in cfg and (not isinstance(cfg["format"], str) or cfg["format"] not in (
            "number", "currency", "percent", "chips", "ratio", "plain")):
        bad("format must be number, currency, percent, chips, ratio or plain.")
    legacy = comp.value_format
    fmt = cfg.get("format", legacy if stat != "count" and isinstance(legacy, str)
                  and legacy in ("number", "currency", "percent") else "number")
    if stat == "count" and fmt not in ("number", "plain"):
        bad("count format must be number or plain; it always displays an integer.")
    if stat == "sum" and (fmt in ("percent", "ratio") or (
            isinstance(legacy, str) and legacy in ("percent", "ratio"))):
        bad("sum requires additive units, never declared percent/ratio values or formats.")
    return {"header_summary": {"stat": stat, "scope": "leaf_entries",
                               "label": cfg.get("label", labels[stat]), "format": fmt}}


def treemap_summary_option_problems(comp: Any) -> list[tuple[str, str]]:
    """Pure shared-validator dispatch hook, including mutated declarations."""
    try:
        treemap_summary_config(comp)
    except ValueError as exc:
        return [("chart-summary-invalid", str(exc))]
    return []


def treemap_static_summary_guard(comp: Any) -> dict:
    """One eligibility bit before iterrows/JSON can coerce supplied values.

    Column extraction preserves native scalar identity for object columns.
    In particular Decimal, bool and numeric text must not become certified
    numbers simply because a serializer or the legacy renderer accepts them.
    """
    return {"values_valid": all(
        type(value) in (int, float) and 0 <= value <= 9007199254740991
        and math.isfinite(value)
        for value in comp.df[comp.value].tolist()
    )}
