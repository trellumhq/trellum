"""Strict declarations and opt-in assets for ordinary native linear fits."""
from __future__ import annotations

import re
from datetime import date, datetime

from trellum.assets import load_css, load_js
from trellum.components.base import Component


class _ChartTrendAssets(Component):
    def render_html(self, ctx):
        return ""

    @classmethod
    def client_js(cls):
        return load_js("runtime/chart_trends.js")

    @classmethod
    def css(cls):
        return load_css("components/chart_trends.css")


def trend_config(chart, ctx=None):
    """Validate exact source identities, never mutable positions or captions.

    Shape validation runs at construction and rendering. Population eligibility
    belongs to the runtime: an unavailable fit must leave its source visible.
    """
    value = chart.trend_line
    if value is None:
        return {}
    def bad(message):
        raise ValueError("trend_line " + message)
    if not isinstance(value, dict) or set(value) - {"method", "series", "details"}:
        bad("requires method, series and optional details only")
    if value.get("method") != "linear":
        bad("method must be 'linear'")
    if "details" in value and type(value["details"]) is not bool:
        bad("details must be bool")
    line = chart._component_type == "line_chart"
    split = ((chart.stack_by or bool(chart.stack_by_options)) if chart.dataset_id else None) if line else chart.color_by
    ratios = chart.ratios if line and chart.dataset_id else None
    columns = (chart.y if isinstance(chart.y, list) else [chart.y]) if line else [chart.y]
    refs = value.get("series")
    if refs != "each":
        if not isinstance(refs, list) or not refs:
            bad("series must be 'each' or a nonempty list of exact identity mappings")
        copied, seen = [], set()
        for ref in refs:
            kind = "ratio" if ratios else "column"
            group_sum = line and split and not ratios
            keys = {"group"} if group_sum else {kind} | ({"group"} if split else set())
            # A single-column split accepts its explicit column as an alias.
            # Multi-column split lines are the renderer's existing pooled sum;
            # their identity is the group, never one contributing column.
            if group_sum and isinstance(ref, dict) and set(ref) == {"column", "group"} and len(columns) == 1:
                keys = {"column", "group"}
            if not isinstance(ref, dict) or set(ref) != keys or any(
                    not isinstance(ref[k], str) or (k != "group" and not ref[k].strip()) for k in keys):
                bad(f"series identities require exactly {sorted(keys)} with string group values and nonempty metric names")
            if kind in keys:
                domain = [r["label"] for r in ratios] if ratios else columns
                if domain.count(ref[kind]) != 1:
                    bad(f"{kind} {ref[kind]!r} must identify exactly one source series")
            item = {"group": ref["group"]} if group_sum else dict(ref)
            key = tuple(sorted(item.items()))
            if key in seen:
                bad("series identities must be distinct")
            seen.add(key)
            copied.append(item)
        refs = copied
    result = {"method": "linear", "series": refs, "details": value.get("details", True)}
    result["columns"] = list(columns)
    if not line and chart.color_by:
        result["group_column"] = chart.color_by
    if line:
        if chart.y_format:
            result["y_format"] = chart.y_format
        # The normal serializer truncates timestamps to dates. Do not infer a
        # UTC daily contract from labels that have already lost their time zone.
        if chart.x in chart.df:
            def canonical(value):
                if isinstance(value, str):
                    try:
                        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) and date.fromisoformat(value).isoformat() == value
                    except ValueError:
                        return False
                if isinstance(value, datetime):
                    return (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0) and (value.utcoffset() is None or value.utcoffset().total_seconds() == 0)
                return isinstance(value, date)
            if any(not canonical(v) for v in chart.df[chart.x].drop_duplicates()):
                result["unavailable"] = "Linear trends require canonical daily UTC dates; timestamps with nonmidnight times or non-UTC offsets are unsupported."
        if ratios:
            names = [r["label"] for r in ratios]
            if len(set(names)) != len(names):
                bad("ratio labels must be distinct stable identities")
        if chart.stack_by_options:
            # Identity includes the breakdown column at runtime; each remains safe.
            if refs != "each":
                bad("exact identities require a fixed stack_by; use series='each' with stack_by_options")
    if ctx is not None:
        ctx.render_child(_ChartTrendAssets())
        if chart.dataset_id:
            source_id, seen_ids = chart.dataset_id, set()
            configs = list(ctx.component_data.values())
            while source_id and source_id not in seen_ids:
                seen_ids.add(source_id)
                source = next((c for c in configs if c.get("dataset_id") == source_id), None)
                if source and source.get("type") == "scoped_data_source":
                    source_id = source["parent_id"]
                    continue
                if (not source or source.get("type") != "data_source"
                        or source_id in getattr(ctx, "_chunk_manifests", {})
                        or source_id in ctx.live_datasets
                        or any(c.get("live_dataset") and seen_ids & set(c.get("propagate_to", {})) for c in configs)):
                    result["unavailable"] = "Linear trends require an ordinary complete snapshot; live, derived and chunked sources are unsupported."
                break
            else:
                result["unavailable"] = "Linear trends require an acyclic ordinary snapshot source."
    return {"trend_line": result}


def trend_option_problems(chart):
    """Pure dispatcher hook for declaration validation after author mutation."""
    try:
        trend_config(chart)
    except ValueError as exc:
        return [("chart-trend-invalid", str(exc))]
    return []
