"""Slider filter plugin -- numeric range (or single-value snap), or an
ordinal range/single-snap over an explicit, ORDERED list of category
values -- via noUiSlider.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.filters import register_filter
from trellum.components.filters.base import BaseFilter


def _fmt(value: float, fmt: str) -> str:
    """Match the client formatters in ``static/js/runtime/formatters.js``."""
    if fmt == "percent":
        return f"{value * 100:.0f}%"
    if fmt == "currency":
        return f"${value:,.2f}"
    return f"{value:g}"


def _ordinal_label(value: Any, labels: Dict[str, str]) -> str:
    """Display label for a category value -- falls back to the raw value."""
    return str(labels.get(value, value))


@register_filter
class SliderFilter(BaseFilter):
    filter_type = "slider"

    # ── Numeric bounds (no "values" in the spec) ────────────────────────

    def _compute_bounds(self, col: str, df: pd.DataFrame):
        """Return (min, max, is_int). Guards empty/all-NaN like DateRangeFilter."""
        vals = df[col].dropna()
        if len(vals) == 0:
            return 0, 0, False
        is_int = pd.api.types.is_integer_dtype(df[col].dtype)
        lo, hi = vals.min(), vals.max()
        return (int(lo), int(hi), True) if is_int else (float(lo), float(hi), False)

    def _step(self, f: Dict[str, Any], lo, hi, is_int) -> float:
        if "step" in f:
            return f["step"]
        if is_int:
            return 1
        return (hi - lo) / 100 if hi > lo else 1

    # ── Ordinal helpers ("values" present in the spec) ──────────────────
    #
    # An ordinal slider maps its explicit, ordered category list onto
    # integer positions 0..N-1 -- that is what noUiSlider actually drags
    # across. The column itself never needs to be numeric; bounds come
    # from len(values), not df[col].min()/.max().

    @staticmethod
    def _ordinal_values(f: Dict[str, Any]) -> List[str]:
        values = f.get("values") or []
        return [str(v) for v in values]

    def _ordinal_default_min_max(self, f: Dict[str, Any], values: List[str]):
        """Resolve default_min/default_max to category values, guarding
        against a value missing from ``values`` the same way empty/NaN
        columns are guarded for the numeric case -- render must not crash
        even when the validator would fail this config."""
        default_min = f.get("default_min")
        default_max = f.get("default_max")
        if default_min not in values:
            default_min = values[0] if values else ""
        if default_max not in values:
            default_max = values[-1] if values else ""
        return default_min, default_max

    def _ordinal_default_single(self, f: Dict[str, Any], values: List[str]) -> str:
        default = f.get("default")
        if default not in values:
            default = values[0] if values else ""
        return default

    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        if "values" in f:
            return self._build_ordinal_config(fid, col, f)

        lo, hi, is_int = self._compute_bounds(col, df)
        mode = f.get("mode", "range")
        cfg = {
            "id": fid, "column": col, "type": "slider", "mode": mode,
            "min": lo, "max": hi, "step": self._step(f, lo, hi, is_int),
            "default_min": f.get("default_min", lo),
            "default_max": f.get("default_max", hi),
            "format": f.get("format", "number"),
        }
        if mode == "single":
            cfg["values"] = sorted(df[col].dropna().unique().tolist())
        return cfg

    def _build_ordinal_config(self, fid: str, col: str, f: Dict[str, Any]) -> dict:
        values = self._ordinal_values(f)
        mode = f.get("mode", "range")
        cfg = {
            "id": fid, "column": col, "type": "slider", "mode": mode,
            "ordinal": True,
            "values": values,
            "labels": {str(k): str(v) for k, v in (f.get("labels") or {}).items()},
            "min": 0, "max": max(len(values) - 1, 0), "step": 1,
        }
        if mode == "single":
            default = self._ordinal_default_single(f, values)
            cfg["default"] = values.index(default) if default in values else 0
        else:
            default_min, default_max = self._ordinal_default_min_max(f, values)
            cfg["default_min"] = values.index(default_min) if default_min in values else 0
            cfg["default_max"] = (
                values.index(default_max) if default_max in values else max(len(values) - 1, 0)
            )
        return cfg

    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        mode = f.get("mode", "range")

        if "values" in f:
            values = self._ordinal_values(f)
            labels = {str(k): str(v) for k, v in (f.get("labels") or {}).items()}
            if mode == "single":
                default = self._ordinal_default_single(f, values)
                readout = _ordinal_label(default, labels)
            else:
                default_min, default_max = self._ordinal_default_min_max(f, values)
                readout = f"{_ordinal_label(default_min, labels)} – {_ordinal_label(default_max, labels)}"
            item_class = "fw-filter-item fw-filter-slider-item fw-filter-slider-item--ordinal"
        else:
            lo, hi, _is_int = self._compute_bounds(col, df)
            fmt = f.get("format", "number")
            if mode == "single":
                vals = sorted(df[col].dropna().unique().tolist())
                default = vals[0] if vals else lo
                readout = _fmt(default, fmt)
            else:
                default_min = f.get("default_min", lo)
                default_max = f.get("default_max", hi)
                readout = f"{_fmt(default_min, fmt)} – {_fmt(default_max, fmt)}"
            item_class = "fw-filter-item fw-filter-slider-item"

        return (
            f'<div class="{item_class}">'
            f'<div class="fw-filter-label-row">'
            f'<label class="fw-filter-label">{label}</label>'
            f'<span class="fw-slider-readout" data-filter-id="{fid}" '
            f'aria-live="polite">{readout}</span>'
            f'</div>'
            f'<div class="fw-slider" data-filter-id="{fid}" data-mode="{mode}"></div>'
            f'</div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/slider_filter.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/slider_filter.js")

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return ["nouislider_js", "nouislider_css"]
