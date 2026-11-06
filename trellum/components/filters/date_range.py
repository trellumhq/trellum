"""Date range filter plugin -- popover with presets and optional hourly support."""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import Any, Dict

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.filters import register_filter
from trellum.components.filters.base import BaseFilter

DEFAULT_PRESETS = [
    {"label": "7D", "days": 7},
    {"label": "14D", "days": 14},
    {"label": "30D", "days": 30},
    {"label": "60D", "days": 60},
    {"label": "90D", "days": 90},
    {"label": "YTD", "days": "ytd"},
    {"label": "All", "days": "all"},
]

HOURLY_PRESETS = [
    {"label": "1H", "hours": 1},
    {"label": "6H", "hours": 6},
    {"label": "12H", "hours": 12},
    {"label": "24H", "hours": 24},
]


def _detect_hourly(df: pd.DataFrame, col: str) -> bool:
    """Return True if the column contains timestamps with non-midnight times."""
    dtype = df[col].dtype
    if pd.api.types.is_datetime64_any_dtype(dtype):
        notna = df[col].dropna()
        if len(notna) == 0:
            return False
        times = notna.dt.time
        return bool((times != pd.Timestamp("00:00:00").time()).any())
    return False


@register_filter
class DateRangeFilter(BaseFilter):
    filter_type = "date_range"

    def _compute_bounds(self, col: str, f: Dict[str, Any], df: pd.DataFrame, hourly: bool):
        vals = df[col].dropna()
        if isinstance(vals.dtype, pd.CategoricalDtype):
            # min/max need an ordered category; the values this frame
            # actually holds compare the way the plain column did.
            vals = pd.Series(vals.cat.categories[pd.unique(vals.cat.codes)])
        if hourly:
            min_d = str(vals.min())[:16] if len(vals) else ""
            max_d = str(vals.max())[:16] if len(vals) else ""
            if min_d and "T" in min_d:
                min_d = min_d.replace("T", " ")
            if max_d and "T" in max_d:
                max_d = max_d.replace("T", " ")
        else:
            min_d = str(vals.min())[:10] if len(vals) else ""
            max_d = str(vals.max())[:10] if len(vals) else ""

        default_min_d = min_d
        default_days = f.get("default_days")
        if default_days and max_d:
            max_date_str = max_d[:10]
            max_dt = datetime.strptime(max_date_str, "%Y-%m-%d")
            computed = (max_dt - timedelta(days=default_days)).strftime("%Y-%m-%d")
            if hourly:
                computed += " 00:00"
            default_min_d = max(computed, min_d)

        return min_d, max_d, default_min_d

    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        hourly = _detect_hourly(df, col)
        min_d, max_d, default_min_d = self._compute_bounds(col, f, df, hourly)

        presets = f.get("presets", DEFAULT_PRESETS)
        preset_configs = [{"label": p["label"], "days": p.get("days"), "hours": p.get("hours")}
                          for p in presets]
        if hourly:
            extra = f.get("hourly_presets", HOURLY_PRESETS)
            for p in extra:
                preset_configs.insert(0, {"label": p["label"], "days": None, "hours": p["hours"]})

        return {
            "id": fid, "column": col, "type": "date_range",
            "min_date": min_d, "max_date": max_d,
            "default_min_date": default_min_d,
            "presets": preset_configs,
            "hourly": hourly,
        }

    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        hourly = _detect_hourly(df, col)
        min_d, max_d, default_min_d = self._compute_bounds(col, f, df, hourly)

        presets = f.get("presets", DEFAULT_PRESETS)
        if hourly:
            extra = f.get("hourly_presets", HOURLY_PRESETS)
            presets = list(extra) + list(presets)

        default_days = f.get("default_days")

        # Build trigger display text
        trigger_text = f"{default_min_d} \u2013 {max_d}" if min_d and max_d else "No date bounds available"

        # Build preset buttons
        preset_btns = []
        for p in presets:
            active = ""
            if default_days and p.get("days") == default_days:
                active = " active"
            elif not default_days and p.get("days") == "all":
                active = " active"
            days_attr = p.get("days", "")
            hours_attr = p.get("hours", "")
            disabled = " disabled" if not min_d or not max_d else ""
            preset_btns.append(
                f'<button type="button" class="fw-date-preset{active}"{disabled} '
                f'data-days="{days_attr}" data-hours="{hours_attr}">'
                f'{p["label"]}</button>'
            )

        unavailable = not min_d or not max_d
        disabled = " disabled" if unavailable else ""
        safe_fid = escape(fid, quote=True)
        popover_id = f"{safe_fid}-popover"
        reference_id = f"{safe_fid}-reference"
        unavailable_id = f"{safe_fid}-unavailable"

        def hour_field(edge: str, selected: str, name: str) -> str:
            if not hourly:
                return ""
            opts = []
            for h in range(24):
                for m in (0, 30):
                    val = f"{h:02d}:{m:02d}"
                    sel = " selected" if val == selected else ""
                    opts.append(f'<option value="{val}"{sel}>{val}</option>')
            return (
                f'<div class="fw-date-hour-row fw-date-field">'
                f'<label id="{safe_fid}-{edge}-hour-label" class="fw-date-field-label" for="{safe_fid}-{edge}-hour">{name} time</label>'
                f'<select id="{safe_fid}-{edge}-hour" name="{safe_fid}-{edge}-hour" class="fw-date-hour" '
                f'aria-labelledby="{safe_fid}-label {safe_fid}-{edge}-hour-label" '
                f'data-edge="{edge}-hour"{disabled}>{"".join(opts)}</select></div>'
            )

        min_time = default_min_d[11:16] if len(default_min_d) > 10 else "00:00"
        max_time = max_d[11:16] if len(max_d) > 10 else "23:30"

        min_date_only = default_min_d[:10]
        max_date_only = max_d[:10]
        data_min = min_d[:10]
        data_max = max_d[:10]

        return (
            f'<div class="fw-filter-item fw-filter-date-item">'
            f'<label class="fw-filter-label" id="{safe_fid}-label" for="{safe_fid}-trigger">{escape(label)}</label>'
            f'<div class="fw-date-range" role="group" aria-labelledby="{safe_fid}-label" data-filter-id="{safe_fid}" '
            f'data-hourly="{str(hourly).lower()}">'
            # Trigger button
            f'<button id="{safe_fid}-trigger" class="fw-date-trigger" type="button"{disabled} '
            f'aria-labelledby="{safe_fid}-label {safe_fid}-value" '
            f'aria-expanded="false" aria-controls="{popover_id}" aria-haspopup="dialog" '
            f'aria-describedby="{unavailable_id if unavailable else reference_id}">'
            f'<span class="fw-date-trigger-text" id="{safe_fid}-value">{trigger_text}</span>'
            f'<svg class="fw-date-trigger-icon" viewBox="0 0 20 20" fill="currentColor">'
            f'<path d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 '
            f'002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 '
            f'5h8a1 1 0 010 2H6a1 1 0 010-2z"/></svg>'
            f'</button>'
            # Popover
            f'<div id="{popover_id}" class="fw-date-popover" role="dialog" '
            f'aria-label="{escape(label, quote=True)}">'
            f'<div class="fw-date-popover-row">'
            f'<div class="fw-date-endpoint">'
            f'<div class="fw-date-field">'
            f'<label class="fw-date-field-label" id="{safe_fid}-min-label" for="{safe_fid}-min">Start date</label>'
            f'<input id="{safe_fid}-min" name="{safe_fid}-min" aria-labelledby="{safe_fid}-label {safe_fid}-min-label" '
            f'type="date" class="fw-date-input" data-edge="min"{disabled} '
            f'value="{min_date_only}" min="{data_min}" max="{data_max}" '
            f'autocomplete="off" data-bwignore="true" '
            f'data-1p-ignore="true" data-lpignore="true">'
            f'</div>'
            f'{hour_field("min", min_time, "Start")}</div>'
            f'<div class="fw-date-endpoint">'
            f'<div class="fw-date-field">'
            f'<label class="fw-date-field-label" id="{safe_fid}-max-label" for="{safe_fid}-max">End date</label>'
            f'<input id="{safe_fid}-max" name="{safe_fid}-max" aria-labelledby="{safe_fid}-label {safe_fid}-max-label" '
            f'type="date" class="fw-date-input" data-edge="max"{disabled} '
            f'value="{max_date_only}" min="{data_min}" max="{data_max}" '
            f'autocomplete="off" data-bwignore="true" '
            f'data-1p-ignore="true" data-lpignore="true">'
            f'</div>'
            f'{hour_field("max", max_time, "End")}</div>'
            f'</div>'
            f'<p id="{reference_id}" class="fw-date-reference">Presets use the latest recorded '
            f'{"date/time" if hourly else "date"}: '
            f'<span class="fw-date-reference-value">{max_d}</span>. '
            f'Records may have gaps.</p>'
            f'<div class="fw-date-presets">{"".join(preset_btns)}</div>'
            f'</div>'
            f'<p id="{unavailable_id}" class="fw-date-unavailable"'
            f'{"" if unavailable else " hidden"}>Date selection is unavailable because '
            f'no valid recorded date bounds are available.</p>'
            f'</div></div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/date_range_filter.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/date_range_filter.js")
