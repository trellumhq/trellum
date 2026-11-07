"""Sankey-local declarations; the admitted graph remains the only population."""

from __future__ import annotations


def sankey_summary_config(chart) -> dict:
    """Normalize an opt-in declaration after config validates the ordered stages."""
    cfg = getattr(chart, "header_summary", None)
    if cfg is None:
        return {}

    def bad(message):
        raise ValueError(f"SankeyChart header_summary: {message}")

    if not isinstance(cfg, dict):
        bad("must be a dict declaring stat and scope")
    if set(cfg) - {"stat", "scope", "label", "format", "from_stage", "to_stage"}:
        bad("unknown declaration fields")
    stat = cfg.get("stat")
    if not isinstance(stat, str) or stat not in ("sum", "max", "count"):
        bad("stat must be sum, max or count")
    scope = "complete_paths" if chart.mode == "paths" else "adjacent_boundary"
    if not isinstance(cfg.get("scope"), str) or cfg["scope"] != scope:
        bad(f"scope must be {scope}")
    if chart.mode == "paths":
        if "from_stage" in cfg or "to_stage" in cfg:
            bad("complete_paths forbids from_stage and to_stage")
    else:
        keys = [s["key"] for s in chart.stages]
        for name in ("from_stage", "to_stage"):
            value = cfg.get(name)
            if not isinstance(value, str) or value not in keys:
                bad(f"{name} must identify a declared stage key")
        if keys.index(cfg["to_stage"]) != keys.index(cfg["from_stage"]) + 1:
            bad("boundary stages must be adjacent in declared order")
    if "label" in cfg and (not isinstance(cfg["label"], str)
                           or not cfg["label"].strip() or len(cfg["label"]) > 60):
        bad("label must be nonblank text of at most 60 characters")
    fmt = cfg.get("format", "number")
    if not isinstance(fmt, str) or fmt not in ("number", "currency", "chips", "plain"):
        bad("format must be number, currency, chips or plain")
    if stat == "count" and fmt not in ("number", "plain"):
        bad("count format must be number or plain")
    labels = ({"sum": "Path total", "max": "Largest path", "count": "Path entries"}
              if chart.mode == "paths" else
              {"sum": "Boundary total", "max": "Largest edge", "count": "Edge entries"})
    result = {"stat": stat, "scope": scope, "label": cfg.get("label", labels[stat]), "format": fmt}
    if chart.mode == "edges":
        result.update(from_stage=cfg["from_stage"], to_stage=cfg["to_stage"])
    return {"header_summary": result}
