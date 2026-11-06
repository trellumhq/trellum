"""Table components for structured data display."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import math
from typing import Any, List, Optional

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.data.transforms import df_to_json_records, format_value

_DOWNLOAD_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="14" height="14">'
    '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"></path>'
    '<polyline points="7 10 12 15 17 10"></polyline>'
    '<line x1="12" y1="15" x2="12" y2="3"></line></svg>'
)

#: DataTable(live=...) is replaced by LiveDataSource + FilterBar parameter
#: bindings. The `live-query-legacy-config` validation check reports the
#: unsupported configuration before render_html raises this error.
_LIVE_LEGACY_ERROR = (
    "DataTable(live=...) was replaced by LiveDataSource + FilterBar param "
    "bindings — see docs/COMPATIBILITY.md"
)


@dataclass
class DataTable(Component):
    """Styled HTML data table with optional sorting.

    Pass ``dataset_id`` to make this table reactive to filter changes.
    When reactive, ``columns`` controls which DataFrame columns are shown.
    ``columns`` also selects static display/export columns. ``max_rows`` only
    limits display: search, stable sort and CSV use every supplied row (or
    every row delivered by the dataset wrapper). CSV uses raw numeric values.

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

    # ComparisonTable's presentation metadata travels with the same population.
    _comparison: Optional[dict] = field(default=None, init=False, repr=False)

    _component_type: str = field(default="data_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        if self.live:
            raise ValueError(_LIVE_LEGACY_ERROR)

        cid = ctx.next_id()

        dl_btn = (
            f'<button class="fw-csv-btn" data-csv-target="{cid}" '
            f'title="Download CSV">{_DOWNLOAD_SVG}</button>'
        )
        show_search = self.searchable if self.searchable is not None else len(self.df) > 20
        show_cols = self.columns or list(self.df.columns)
        if self._comparison is not None and self.columns is not None:
            show_cols = self.columns
        numeric_cols = [c for c in show_cols
                        if c in self.df.columns
                        and (pd.api.types.is_numeric_dtype(self.df[c].dtype)
                            or pd.api.types.infer_dtype(self.df[c], skipna=True)
                            in {"integer", "floating", "mixed-integer-float", "decimal"})
                        and not pd.api.types.is_bool_dtype(self.df[c].dtype)]
        search_html = ""
        if show_search:
            search_html = (
                f'<input type="text" class="fw-table-search" '
                f'data-table-search="{cid}" placeholder="Search..." '
                f'autocomplete="off" data-bwignore="true" '
                f'data-1p-ignore="true" data-lpignore="true" />'
            )

        if self.dataset_id:
            cfg: dict[str, Any] = {
                "type": "table",
                "dataset_id": self.dataset_id,
                "columns": show_cols,
                "maxRows": self.max_rows,
                "barColumn": self.bar_column,
                "searchable": show_search,
                "sortable": self.sortable,
                "numericColumns": numeric_cols,
            }
            if self.conditional_formats:
                cfg["conditionalFormats"] = self.conditional_formats
            if self._comparison is not None:
                cfg["comparison"] = self._comparison
            ctx.register(cid, cfg)
            return (
                f'<div class="fw-table-header fw-data-table-header">{search_html}{dl_btn}</div>'
                f'<div id="{cid}" class="fw-table-scroll"></div>'
            )

        # Retain the supplied population. max_rows is a display limit applied
        # after local search and sort, never a search or export limit.
        df = self.df[show_cols]
        total = len(self.df)

        formatted = df.apply(
            lambda col: col.map(format_value), axis=0
        )
        reg: dict[str, Any] = {
            "type": "table",
            "columns": list(df.columns),
            "rows": formatted.values.tolist(),
            "numericColumns": numeric_cols,
            "totalRows": total,
            "maxRows": self.max_rows,
            "searchable": show_search,
            "sortable": self.sortable,
        }
        if numeric_cols or (self._comparison and self._comparison["context"] is not None):
            # Keep established text/date/boolean CSV representation; only
            # numeric cells need a separate unrounded value. Text-only catalog
            # tables need no duplicate row payload (workspace pages fill those
            # rows from their catalog).
            raw_rows = [row.copy() for row in reg["rows"]]
            raw_cols = show_cols if self._comparison and self._comparison["context"] is not None else numeric_cols
            numeric_indices = [(show_cols.index(c), c) for c in raw_cols]
            for row, record in zip(raw_rows, df_to_json_records(df[raw_cols])):
                for index, column in numeric_indices:
                    value = record[column]
                    # JSON numbers cannot carry arbitrary decimal precision or
                    # integers beyond JavaScript's exact range. Keep their
                    # numeric text for lossless table sort and CSV.
                    if isinstance(value, Decimal):
                        value = str(value) if value.is_finite() else None
                    elif pd.isna(value) or (self._comparison is not None
                                           and isinstance(value, float) and not math.isfinite(value)):
                        value = None
                    elif isinstance(value, int) and abs(value) > 2**53 - 1:
                        value = str(value)
                    row[index] = value
            reg["rawRows"] = raw_rows
        if self.bar_column is not None:
            reg["barColumn"] = self.bar_column
        if self.conditional_formats:
            reg["conditionalFormats"] = self.conditional_formats
        if self._comparison is not None:
            reg["comparison"] = self._comparison
            context = self._comparison["context"]
            if context is not None:
                metadata_cols = list(dict.fromkeys([
                    context["metric_column"], *context.get("reason_columns", {}).values(),
                ]))
                reg["comparisonRows"] = df_to_json_records(self.df[metadata_cols])

        table_html = (
            f'<div class="fw-table-header fw-data-table-header">{search_html}{dl_btn}</div>'
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


def _is_dimension(dtype) -> bool:
    """Text and dates are the pivot's dimensions; everything else is a value.

    Text is ``object`` under pandas 2, ``str`` under pandas 3 and ``category``
    from query_df, so a check for ``object`` alone would turn every text
    column into a value column.
    """
    return (dtype == "object" or pd.api.types.is_string_dtype(dtype)
            or isinstance(dtype, pd.CategoricalDtype)
            or str(dtype).startswith("datetime"))


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
    freeze: bool = field(default=False, kw_only=True)
    freeze_height: int = field(default=320, kw_only=True)

    _component_type: str = field(default="pivot_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        if not isinstance(self.freeze, bool):
            raise ValueError("freeze must be a bool")
        if (isinstance(self.freeze_height, bool) or not isinstance(self.freeze_height, int)
                or self.freeze_height <= 0):
            raise ValueError("freeze_height must be a positive integer")
        cid = ctx.next_id()
        all_cols = list(self.df.columns)
        dim_cols = [c for c in all_cols if _is_dimension(self.df[c].dtype)]
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
            f'<div id="{cid}" class="fw-table-scroll fw-pivot-scroll"'
            f' data-freeze="{str(self.freeze).lower()}"'
            f' data-freeze-height="{self.freeze_height}"></div>'
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
    """Supplied comparisons with optional explicit context and outcome text.

    Comparison columns (``compare_cols``) are automatically color-coded:
    positive values in green, negative in red.  Pass explicit
    ``conditional_formats`` to override the auto-generated rules. Keyword-only
    ``comparison_context`` opts into metric-aware supplied-change semantics.

    Args:
        df: DataFrame with rows to compare.
        title: Table title.
        value_col: Column containing the primary value.
        compare_cols: List of column names for comparison periods.
        dataset_id: Optional DataSource ID for filter reactivity.
        conditional_formats: Explicit rules (overrides auto-generation).
        columns: Optional list of columns to display.
        comparison_context: Keyword-only supplied period labels, metric units,
            directions and missing-value reasons. No comparison is calculated.
            See ``trellum/docs/report-comparison-tables.md`` for raw units.
        show_divider: Keyword-only opt-out for the comparison group border.
    """
    df: pd.DataFrame
    title: str
    value_col: str
    compare_cols: List[str]
    dataset_id: Optional[str] = None
    static: bool = False
    conditional_formats: Optional[dict] = None
    columns: Optional[List[str]] = None
    comparison_context: Optional[dict] = field(default=None, kw_only=True)
    show_divider: bool = field(default=True, kw_only=True)

    _component_type: str = field(default="comparison_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        context = self.comparison_context
        if context is not None:
            self._validate_context(context)
        cf = self.conditional_formats
        if cf is None and context is None:
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
            columns=(self.columns if self.columns is not None else
                     [c for c in self.df.columns
                      if context is None or c not in context.get("reason_columns", {}).values()]),
        )
        delegate._comparison = {
            "context": context, "compareColumns": self.compare_cols,
            "valueColumn": self.value_col, "showDivider": self.show_divider,
            "autoColors": self.conditional_formats is None,
        }
        return ctx.render_child(delegate)

    def _validate_context(self, context: dict) -> None:
        """Validate new declarations without tightening the legacy call contract."""
        def invalid(message):
            raise ValueError("ComparisonTable comparison_context: " + message)

        if not isinstance(context, dict):
            invalid("must be a dict")
        if not isinstance(context.get("metric_column"), str) or context["metric_column"] not in self.df.columns:
            invalid("metric_column must name a supplied column")
        for name in ("period_labels", "change_labels", "reason_columns", "metrics"):
            mapping = context.get(name, {})
            if not isinstance(mapping, dict) or not all(isinstance(k, str) for k in mapping):
                invalid(name + " must be a dict with string keys")
            if name != "metrics" and not all(isinstance(v, str) for v in mapping.values()):
                invalid(name + " must contain string values")
        for column, reason in context.get("reason_columns", {}).items():
            if column not in self.df.columns or reason not in self.df.columns:
                invalid("reason_columns must name supplied value and reason columns")
        for name in ("period_labels", "change_labels"):
            if any(c not in self.df.columns for c in context.get(name, {})):
                invalid(name + " must name supplied columns")
        for metric, spec in context.get("metrics", {}).items():
            if not isinstance(spec, dict):
                invalid("metrics entries must be dicts")
            for name in ("direction", "format", "change_measure"):
                if name in spec and not isinstance(spec[name], str):
                    invalid(metric + "." + name + " must be a string")
            if "format" in spec and spec["format"] not in {"number", "currency", "percent", "ratio"}:
                invalid(metric + ".format must be number, currency, percent or ratio")
            for name in ("precision", "change_precision"):
                if name in spec and (type(spec[name]) is not int or not 0 <= spec[name] <= 12):
                    invalid(metric + "." + name + " must be an integer from 0 to 12")

    @classmethod
    def css(cls) -> str:
        return DataTable.css()

    @classmethod
    def client_js(cls) -> str:
        return DataTable.client_js()
