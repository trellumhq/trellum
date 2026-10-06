"""Text filter plugin -- a labeled free-text input.

Commits on Enter or blur only, never on keystroke. That Enter-to-commit rule
was originally a property of the M1 live-query control's single-purpose
input row; the live-query redesign turns the live inputs into ordinary
FilterBar filters, and this plugin is where that specific commit rule
survives -- as a property of the *input type*, not of live-ness. Nothing
below is live-query-specific: a ``text`` filter works against any dataset,
live or not (though v1 usage is expected to be almost entirely live-bound
params -- see ``trellum.components.filterable.FilterBar``).
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from trellum.assets import load_js
from trellum.components.filters import register_filter
from trellum.components.filters.base import BaseFilter

#: Matches DEFAULT_STR_MAX in apps.reports.livequery -- a UX cap only; the
#: server re-caps strictly regardless of what this ships.
DEFAULT_MAX_LENGTH = 200


@register_filter
class TextFilter(BaseFilter):
    filter_type = "text"

    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        cfg: dict[str, Any] = {
            "id": fid, "column": col, "type": "text",
            "max_length": int(f.get("max_length", DEFAULT_MAX_LENGTH)),
        }
        if f.get("placeholder"):
            cfg["placeholder"] = str(f["placeholder"])
        return cfg

    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        placeholder = str(f.get("placeholder", ""))
        maxlen = int(f.get("max_length", DEFAULT_MAX_LENGTH))
        return (
            f'<div class="fw-filter-item">'
            f'<label class="fw-filter-label">{label}</label>'
            f'<input type="text" class="fw-text-filter" data-filter-id="{fid}" '
            f'data-filter-col="{col}" maxlength="{maxlen}" '
            f'placeholder="{placeholder}" autocomplete="off" '
            f'data-bwignore="true" data-1p-ignore="true" data-lpignore="true">'
            f'</div>'
        )

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/text_filter.js")
