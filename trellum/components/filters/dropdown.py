"""Dropdown filter plugin -- multi-select with Slim Select."""

from __future__ import annotations

from html import escape
from typing import Any, Dict

import pandas as pd

from trellum.assets import load_js
from trellum.components.filters import register_filter
from trellum.components.filters.base import BaseFilter


def validate_parent_clear(f):
    if "clear_on_parent_change" not in f:
        return
    parents = f.get("depends_on")
    valid_parents = ((isinstance(parents, str) and bool(parents.strip()))
                     or (isinstance(parents, list) and bool(parents)
                         and all(isinstance(p, str) and p.strip() for p in parents)))
    if (type(f["clear_on_parent_change"]) is not bool
            or f.get("type", "dropdown") != "dropdown" or not valid_parents):
        raise ValueError("clear_on_parent_change requires a boolean on a dropdown with nonempty depends_on")


@register_filter
class DropdownFilter(BaseFilter):
    filter_type = "dropdown"

    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        validate_parent_clear(f)
        if "options" in f:
            options = [str(v) for v in f["options"] if v is not None]
        else:
            options = sorted([str(v) for v in df[col].dropna().unique()], key=str)
        multi = bool(f.get("multi", True))
        cfg = {"id": fid, "column": col, "type": "dropdown", "options": options,
               "multi": multi}
        if f.get("default_empty"):
            cfg["default_empty"] = True
        if f.get("placeholder"):
            cfg["placeholder"] = str(f["placeholder"])
        # ``depends_on`` makes this dropdown cascading: when the named parent
        # filter changes, the child's options are re-derived at runtime from
        # the parent-filtered rows of this DataSource. Single-column form
        # ("package_group") or list form (["region", "country"]) are both
        # accepted. The JS reads the engine's filtered rows — no extra data
        # is shipped to the browser.
        depends_on = f.get("depends_on")
        if depends_on:
            cfg["depends_on"] = (
                [depends_on] if isinstance(depends_on, str) else list(depends_on)
            )
        if f.get("clear_on_parent_change"):
            cfg["clear_on_parent_change"] = True
        return cfg

    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        multi = bool(f.get("multi", True))
        # Single-select (live-bound dropdowns in v1, or any caller that asks
        # for it directly): no "Select all"/"Clear" actions -- they read as
        # nonsense against a control that can only ever hold one value, and
        # the (All) placeholder already covers "no filter" the same way.
        actions = ""
        if multi:
            actions = (
                f'<div class="fw-filter-actions">'
                f'<a href="#" class="fw-filter-action" '
                f'data-action="select-all" data-filter-id="{fid}">Select all</a>'
                f'<a href="#" class="fw-filter-action" '
                f'data-action="clear-all" data-filter-id="{fid}">Clear</a>'
                f'</div>'
            )
        multiple_attr = " multiple" if multi else ""
        return (
            f'<div class="fw-filter-item">'
            f'<div class="fw-filter-label-row">'
            f'<label class="fw-filter-label" id="{escape(fid, quote=True)}_label" for="{escape(fid, quote=True)}_input">{escape(str(label))}</label>'
            f'{actions}</div>'
            f'<select class="fw-dropdown" id="{escape(fid, quote=True)}_input" name="{escape(fid, quote=True)}" '
            f'aria-labelledby="{escape(fid, quote=True)}_label" data-filter-id="{escape(fid, quote=True)}" '
            f'data-filter-col="{escape(col, quote=True)}"{multiple_attr} placeholder="(All)"></select>'
            f'</div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return ["slim_select_js", "slim_select_css"]

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/dropdown_filter.js")
