"""Funnel-local declarations for summaries of current stage entries."""

from __future__ import annotations

from typing import Any


def funnel_summary_config(chart: Any) -> dict:
    """Validate again at render/validation time without mutating author options."""
    cfg = getattr(chart, "header_summary", None)
    if cfg is None:
        return {}

    def bad(message: str) -> None:
        raise ValueError(f"FunnelChart header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict with stat and scope='current_stages'.")
    if set(cfg) - {"stat", "scope", "label", "format", "stage"}:
        bad("unknown keys in declaration.")
    defaults = {"stage": "Selected stage", "max": "Largest stage", "count": "Stage entries"}
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in defaults:
        bad("stat must be stage, max or count.")
    if not isinstance(cfg.get("scope"), str) or cfg["scope"] != "current_stages":
        bad("scope must be 'current_stages'.")
    for key, limit in (("label", 60), ("stage", 120)):
        if key in cfg and (not isinstance(cfg[key], str) or not cfg[key].strip()
                           or len(cfg[key]) > limit):
            bad(f"{key} must be nonblank text of at most {limit} characters.")
    if stat == "stage" and "stage" not in cfg:
        bad("stage is required for stat='stage'.")
    if stat != "stage" and "stage" in cfg:
        bad("stage is only allowed for stat='stage'.")
    if "format" in cfg and (not isinstance(cfg["format"], str)
                            or cfg["format"] not in ("number", "plain")):
        bad("format must be number or plain; summaries describe counts.")
    summary = {"stat": stat, "scope": "current_stages",
               "label": cfg.get("label", defaults[stat]), "format": cfg.get("format", "number")}
    if stat == "stage":
        summary["stage"] = cfg["stage"]
    return {"header_summary": summary}


def funnel_option_problems(chart: Any) -> list[tuple[str, str]]:
    """Validator dispatch hook for mutated FunnelChart declarations."""
    try:
        funnel_summary_config(chart)
    except ValueError as exc:
        return [("chart-summary-invalid", str(exc))]
    return []
