"""Table components for structured data display."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.data.transforms import format_value

_DOWNLOAD_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="14" height="14">'
    '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"></path>'
    '<polyline points="7 10 12 15 17 10"></polyline>'
    '<line x1="12" y1="15" x2="12" y2="3"></line></svg>'
)

#: The M1 live-query control (DataTable(live=...)) was replaced by
#: LiveDataSource + FilterBar param bindings (trellum.components.filterable)
#: -- a clean break, not a shim: the framework is at v0.1.0 with exactly one
#: first-party consumer of the old shape (the user-event-log demo, since
#: migrated). DataTable.render_html raises this below; the validation check
#: `live-query-legacy-config` (validation/checks/live_query.py) names the
#: section as a build-time FAIL for anyone who still has old config lying
#: around, so the mistake surfaces before the loud crash does.
_LIVE_LEGACY_ERROR = (
    "DataTable(live=...) was replaced by LiveDataSource + FilterBar param "
    "bindings — see docs/COMPATIBILITY.md"
)


@dataclass
class DataTable(Component):
    """Styled HTML data table with optional sorting.

    Pass ``dataset_id`` to make this table reactive to filter changes.
    When reactive, ``columns`` controls which DataFrame columns are shown.

    A live-query-backed table is just a reactive table: declare
    ``LiveDataSource(id, query=..., df=snapshot)`` and pass
    ``dataset_id=id`` here like any other filter-aware component -- see
    ``trellum.components.filterable.LiveDataSource`` and ``FilterBar``. The
    ``live=`` keyword below is the removed M1 form; passing it raises.
    """

    df: pd.DataFrame
    title: str
    max_rows: int = 200
    sortable: bool = False
    bar_column: Optional[int] = None
    dataset_id: Optional[str] = None
    static: bool = False
    columns: Optional[List[str]] = None
    conditional_formats: Optional[dict] = None
    searchable: Optional[bool] = None
    live: Optional[dict] = None

    _component_type: str = field(default="data_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        if self.live:
            # Loud, not silent (owner decision, clean break: v0.1.0, one
            # first-party consumer). The validation check
            # `live-query-legacy-config` also FAILs this at build time, so
            # the mistake is named before this crash is ever reached.
            raise ValueError(_LIVE_LEGACY_ERROR)

        cid = ctx.next_id()

        dl_btn = (
            f'<button class="fw-csv-btn" data-csv-target="{cid}" '
            f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
        )
        show_search = self.searchable if self.searchable is not None else len(self.df) > 20
        search_html = ""
        if show_search:
            search_html = (
                f'<input type="text" class="fw-table-search" '
                f'data-table-search="{cid}" placeholder="Search..." '
                f'autocomplete="off" data-bwignore="true" '
                f'data-1p-ignore="true" data-lpignore="true" />'
            )

        if self.dataset_id:
            show_cols = self.columns or list(self.df.columns)
            cfg: dict[str, Any] = {
                "type": "table",
                "dataset_id": self.dataset_id,
                "columns": show_cols,
                "maxRows": self.max_rows,
                "barColumn": self.bar_column,
                "searchable": show_search,
                "sortable": self.sortable,
            }
            if self.conditional_formats:
                cfg["conditionalFormats"] = self.conditional_formats
            ctx.register(cid, cfg)
            return (
                f'<div class="fw-table-header">{search_html}{dl_btn}</div>'
                f'<div id="{cid}" class="fw-table-scroll"></div>'
            )

        df = self.df.head(self.max_rows)
        total = len(self.df)

        formatted = df.apply(
            lambda col: col.map(format_value), axis=0
        )
        reg: dict[str, Any] = {
            "type": "table",
            "columns": list(df.columns),
            "rows": formatted.values.tolist(),
            "totalRows": total,
            "maxRows": self.max_rows,
            "searchable": show_search,
            "sortable": self.sortable,
        }
        if self.bar_column is not None:
            reg["barColumn"] = self.bar_column
        if self.conditional_formats:
            reg["conditionalFormats"] = self.conditional_formats

        table_html = (
            f'<div class="fw-table-header">{search_html}{dl_btn}</div>'
            f'<div id="{cid}" class="fw-table-scroll"></div>'
        )
        ctx.register(cid, reg)
        return table_html

    @classmethod
    def css(cls) -> str:
        return load_css("components/data_table.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/data_table.js")


@dataclass
class PivotTable(Component):
    """Interactive pivot table with client-side row/column pivoting.

    Users can change row/column dimensions and aggregation via dropdowns.
    Pass ``dataset_id`` for reactive filter support.
    """

    df: pd.DataFrame
    rows: List[str]
    cols: Optional[str] = None
    values: str = ""
    agg: str = "sum"
    title: str = ""
    value_format: str = "number"
    dataset_id: Optional[str] = None
    static: bool = False

    _component_type: str = field(default="pivot_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        all_cols = list(self.df.columns)
        dim_cols = [c for c in all_cols if self.df[c].dtype == "object" or str(self.df[c].dtype).startswith("datetime")]
        num_cols = [c for c in all_cols if c not in dim_cols]

        default_value = self.values or (num_cols[0] if num_cols else "")
        base_cfg: dict = {
            "type": "pivot",
            "dim_cols": dim_cols,
            "num_cols": num_cols,
            "default_rows": self.rows,
            "default_col": self.cols,
            "default_value": default_value,
            "default_agg": self.agg,
            "value_format": self.value_format,
        }

        if self.dataset_id:
            base_cfg["dataset_id"] = self.dataset_id
            ctx.register(cid, base_cfg)
        else:
            records = []
            for _, row in self.df.iterrows():
                r = {}
                for c in all_cols:
                    v = row[c]
                    if hasattr(v, "isoformat"):
                        r[c] = v.isoformat()[:10]
                    elif v is None or (isinstance(v, float) and v != v):
                        r[c] = None
                    else:
                        r[c] = v
                records.append(r)
            base_cfg["data"] = records
            ctx.register(cid, base_cfg)

        dl_btn = (
            f'<button class="fw-csv-btn" data-csv-target="{cid}" '
            f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
        )
        return (
            f'<div class="fw-pivot-controls" id="{cid}_ctrl"></div>'
            f'<div class="fw-table-header">{dl_btn}</div>'
            f'<div id="{cid}" class="fw-table-scroll"></div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return ["slim_select_js", "slim_select_css"]

    @classmethod
    def css(cls) -> str:
        return DataTable.css() + load_css("components/pivot_table.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/pivot_table.js")


@dataclass
class ComparisonTable(Component):
    """Table with period comparison columns and automatic conditional formatting.

    Comparison columns (``compare_cols``) are automatically color-coded:
    positive values in green, negative in red.  Pass explicit
    ``conditional_formats`` to override the auto-generated rules.

    Args:
        df: DataFrame with rows to compare.
        title: Table title.
        value_col: Column containing the primary value.
        compare_cols: List of column names for comparison periods.
        dataset_id: Optional DataSource ID for filter reactivity.
        conditional_formats: Explicit rules (overrides auto-generation).
        columns: Optional list of columns to display.
    """
    df: pd.DataFrame
    title: str
    value_col: str
    compare_cols: List[str]
    dataset_id: Optional[str] = None
    static: bool = False
    conditional_formats: Optional[dict] = None
    columns: Optional[List[str]] = None

    _component_type: str = field(default="comparison_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cf = self.conditional_formats
        if cf is None:
            cf = {}
            for col in self.compare_cols:
                cf[col] = [
                    {"op": ">", "value": 0, "color": "var(--accent-green)"},
                    {"op": "<", "value": 0, "color": "var(--accent-red)"},
                ]
        delegate = DataTable(
            df=self.df,
            title=self.title,
            max_rows=200,
            dataset_id=self.dataset_id,
            conditional_formats=cf,
            columns=self.columns,
        )
        return ctx.render_child(delegate)

    @classmethod
    def css(cls) -> str:
        return DataTable.css()

    @classmethod
    def client_js(cls) -> str:
        return DataTable.client_js()
