"""Toggle filter plugin -- button group with All + distinct values."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from trellum.assets import load_js
from trellum.components.filters import register_filter
from trellum.components.filters.base import BaseFilter


@register_filter
class ToggleFilter(BaseFilter):
    filter_type = "toggle"

    @staticmethod
    def _options(col: str, f: Dict[str, Any], df: pd.DataFrame) -> list[str]:
        # Explicit options (e.g. derived from a live-bound enum param's
        # declared `values` -- see FilterBar.render_html) win over the
        # snapshot DataFrame's own distinct values, which for a live
        # dataset may be narrower than the live domain actually is.
        if "options" in f:
            return [str(v) for v in f["options"] if v is not None]
        return sorted([str(v) for v in df[col].dropna().unique()], key=str)

    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        options = self._options(col, f, df)
        default = f.get("default")
        no_all = f.get("no_all", False)
        if default is None and not no_all:
            default = "All"
        elif default is None and no_all and options:
            default = options[0]
        return {
            "id": fid, "column": col, "type": "toggle",
            "options": options, "default": str(default) if default else None,
        }

    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        options = self._options(col, f, df)
        default = f.get("default")
        no_all = f.get("no_all", False)
        if default is None and not no_all:
            default = "All"
        elif default is None and no_all and options:
            default = options[0]

        btns: list[str] = []
        if not no_all:
            active = " active" if default == "All" else ""
            btns.append(f'<button class="fw-toggle-btn{active}">All</button>')
        for opt in options:
            active = " active" if str(default) == opt else ""
            btns.append(f'<button class="fw-toggle-btn{active}">{opt}</button>')
        return (
            f'<div class="fw-filter-item">'
            f'<label class="fw-filter-label">{label}</label>'
            f'<div class="fw-toggle-group" data-filter-id="{fid}">'
            f'{"".join(btns)}</div></div>'
        )

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/toggle_filter.js")
