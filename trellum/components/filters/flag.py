"""Flag filter plugin -- 3-way toggle: All / Exclude {value} / Only {value}."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from trellum.assets import load_js
from trellum.components.filters import register_filter
from trellum.components.filters.base import BaseFilter


@register_filter
class FlagFilter(BaseFilter):
    filter_type = "flag"

    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        return {
            "id": fid, "column": col, "type": "flag",
            "flag_value": str(f["flag_value"]),
        }

    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        fv = str(f["flag_value"])
        return (
            f'<div class="fw-filter-item">'
            f'<label class="fw-filter-label">{label}</label>'
            f'<div class="fw-toggle-group" data-filter-id="{fid}">'
            f'<button class="fw-toggle-btn active">All</button>'
            f'<button class="fw-toggle-btn">Exclude {fv}</button>'
            f'<button class="fw-toggle-btn">Only {fv}</button>'
            f"</div></div>"
        )

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/flag_filter.js")
