"""Chart annotations and the events that feed them."""

from __future__ import annotations

import os
import re
from typing import Any

import pandas as pd

from trellum.validation.result import ValidationResult
from trellum.validation.walk import (
    _comp_name,
)


def _check_annotations(
    ctx: Any,
    comps: list[tuple[Any, str]],
    events: list[dict] | None,
    result: ValidationResult,
) -> None:
    """Category 5: Annotation support."""
    from trellum.components.layout import RawHTML as RawHTMLComp

    anno_cfg = ctx.config.get("annotations", True)
    if anno_cfg is False or ctx.config.get("kind") == "analysis":
        return

    # ── Weekday highlight config sanity ────────────────────
    # A recurring weekday marker (annotations.weekday_highlight) only
    # renders on date x-axes; validate the config parses and that at
    # least one chart can actually display it.
    wh_cfg = None
    if isinstance(anno_cfg, dict) and anno_cfg.get("weekday_highlight") is not None:
        from trellum.runner import _weekday_highlight_config
        wh_cfg = _weekday_highlight_config(ctx.config)
        if wh_cfg is None:
            result.warn(
                "weekday-highlight-invalid",
                "annotations.weekday_highlight is set but no valid days were "
                "parsed. Use day names (e.g. [Wed]) or integers 0-6 (Sun=0). "
                "Weekday markers will not render.",
            )

    from trellum.project import get_project_root
    project_root = get_project_root()
    events_path = os.path.join(project_root, "events.yaml")

    if not os.path.exists(events_path):
        result.info(
            "annotations-enabled-no-events",
            "Annotations are not disabled but no events.yaml exists. "
            "Chart annotations are silently skipped.",
        )
        return

    if events is not None and len(events) == 0:
        studio = ctx.config.get("studio", "shared")
        result.info(
            "annotations-studio-mismatch",
            f"All events in events.yaml were filtered out for studio '{studio}'. "
            f"No annotations will appear.",
        )

    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
    _PARTIAL_DATE_RE = re.compile(r"^\d{4}-\d{2}$")
    chart_types = {"LineChart", "BarChart", "StackedBar", "AreaChart", "ComboChart"}
    has_date_chart = False
    for comp, sec in comps:
        name = _comp_name(comp)
        if name not in chart_types:
            continue
        df = getattr(comp, "df", None)
        x_col = getattr(comp, "x", None)
        if df is None or x_col is None:
            continue
        if isinstance(df, pd.DataFrame) and x_col in df.columns and len(df) > 0:
            first_val = str(df[x_col].iloc[0])
            if _DATE_RE.match(first_val):
                has_date_chart = True
                continue
            title = getattr(comp, "title", "") or name
            if _PARTIAL_DATE_RE.match(first_val):
                result.info(
                    "annotations-non-date-x-axis",
                    f"{name} '{title}' x-axis uses YYYY-MM format "
                    f"(first value: '{first_val}'). Daily annotations skipped.",
                    component=name, section=sec,
                )
            # Purely categorical x-axes (hours, buckets, counts) are
            # expected to lack annotations -- no diagnostic needed.

    if wh_cfg is not None and not has_date_chart:
        result.info(
            "weekday-highlight-no-date-chart",
            "annotations.weekday_highlight is configured but no chart has a "
            "date (YYYY-MM-DD) x-axis. Weekday markers only render on daily "
            "charts.",
        )

    _BUILD_ANNO = re.compile(r"(?:_buildAnnotations|fw\.buildAnnotations)")
    _NEW_CHART = re.compile(r"new\s+Chart\s*\(")
    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp) or not comp.js:
            continue
        if _NEW_CHART.search(comp.js) and not _BUILD_ANNO.search(comp.js):
            result.info(
                "rawhtml-charts-no-annotations",
                "RawHTML JS creates charts but does not call _buildAnnotations / "
                "fw.buildAnnotations. Custom charts will not show event markers.",
                component="RawHTML", section=sec,
            )
