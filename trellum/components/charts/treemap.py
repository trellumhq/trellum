"""TreemapChart: nested rectangles sized by value."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts._treemap_summary import (
    treemap_static_summary_guard,
    treemap_summary_config,
)
from trellum.components.charts.base import (
    _chart_container_html,
    _ChartBase,
    _validate_header_context,
)


def _validate_percentage_option(value: bool) -> None:
    if not isinstance(value, bool):
        raise ValueError("Treemap show_percentages must be bool")


@dataclass
class TreemapChart(Component):
    """Treemap chart for hierarchical proportional breakdowns.

    Pass one or more ``group_cols`` for nested grouping and a ``value``
    column for rectangle sizing.  Pass ``dataset_id`` for reactive
    filter support. Keyword-only ``show_percentages`` adds a share line to
    fitting leaf labels; authors must supply complete nonnegative additive
    values. Full-path details retain values and eligible shares independently.
    Keyword-only ``header_summary`` optionally reduces final plugin leaf entries;
    numeric reducers conservatively require safe nonnegative supplied values.
    """

    df: pd.DataFrame
    group_cols: list[str]
    value: str
    title: str = ""
    color_col: Optional[str] = None
    value_format: str = "number"
    dataset_id: Optional[str] = None
    static: bool = False
    show_percentages: bool = field(default=False, kw_only=True)
    header_context: Optional[str] = field(default=None, kw_only=True)
    header_summary: Optional[dict] = field(default=None, kw_only=True)

    _component_type: str = field(default="treemap_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_header_context(self.header_context)
        _validate_percentage_option(self.show_percentages)
        treemap_summary_config(self)

    def render_html(self, ctx: RenderContext) -> str:
        # Also reject invalid mutations after construction.
        _validate_percentage_option(self.show_percentages)
        summary = treemap_summary_config(self)
        if summary and not self.dataset_id:
            summary["summary_guard"] = treemap_static_summary_guard(self)
        cid = ctx.next_id()

        if self.dataset_id:
            ctx.register(cid, {
                "type": "treemap",
                "dataset_id": self.dataset_id,
                "group_cols": self.group_cols,
                "value_col": self.value,
                "color_col": self.color_col,
                "value_format": self.value_format,
                "title": self.title,
                "show_percentages": self.show_percentages,
                **summary,
            })
        else:
            rows = []
            for _, row in self.df.iterrows():
                r: dict = {self.value: None if row[self.value] is pd.NA else row[self.value]}
                for g in self.group_cols:
                    gv = row[g]
                    r[g] = gv.isoformat()[:10] if hasattr(gv, "isoformat") else str(gv)
                if self.color_col and self.color_col in row:
                    r[self.color_col] = row[self.color_col]
                rows.append(r)
            ctx.register(cid, {
                "type": "treemap",
                "rows": rows,
                "title": self.title,
                "group_cols": self.group_cols,
                "value_col": self.value,
                "color_col": self.color_col,
                "value_format": self.value_format,
                "show_percentages": self.show_percentages,
                "supplied_values": [
                    "null" if v is None or v is pd.NA else str(v)
                    for v in self.df[self.value].tolist()
                ],
                **summary,
            })

        return (
            '<div class="fw-treemap">'
            + _chart_container_html(cid, self.title, header_context=self.header_context,
                                    summary=bool(summary))
            + f'<div class="fw-treemap-context" data-treemap-context="{cid}"></div>'
            + '</div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return _ChartBase.cdn_deps() + ["chartjs_treemap"]

    @classmethod
    def css(cls) -> str:
        return _ChartBase.css() + load_css("components/treemap.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/treemap.js")
