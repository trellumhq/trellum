"""Column references: every name a component reads must exist."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from trellum.validation.result import ValidationResult
from trellum.validation.walk import (
    _comp_name,
    _resolve_ds_columns,
)


def _check_columns(comps: list[tuple[Any, str]], ds_map: dict[str, Any], result: ValidationResult) -> None:
    """Category 6: Component column / parameter validation."""
    _VALID_AGGS = {"sum", "abssum", "ratio", "count", "purchase_pct", "avg_by_date"}
    _VALID_FORMATS = {"number", "currency", "chips", "percent", "ratio", "plain"}

    for comp, sec in comps:
        name = _comp_name(comp)
        df = getattr(comp, "df", None)
        ds_id = getattr(comp, "dataset_id", None)

        if ds_id and ds_id in ds_map:
            cols = _resolve_ds_columns(ds_id, ds_map)
        elif isinstance(df, pd.DataFrame):
            cols = set(df.columns)
        else:
            cols = set()

        if not cols:
            continue

        if name == "LineChart":
            ratios = getattr(comp, "ratios", None)
            if ratios and not ds_id:
                result.warn(
                    "ratios-without-dataset-id",
                    f"LineChart '{getattr(comp, 'title', '')}' has ratios but no "
                    f"dataset_id. Ratios are silently ignored.",
                    component="LineChart", section=sec,
                )

            # Detect pre-computed ratio columns used as y on a live chart.
            # A LineChart with dataset_id aggregates multiple rows per x-value
            # by summing — pre-computed ratio columns (e.g. rtp, ctr, retention)
            # inflate wildly when summed. Use ratios=[{numerator, denominator}]
            # so the framework divides the correct sums.
            _RATIO_COL_RE = re.compile(
                r"(?:^|_)(?:rtp|rate|ratio|pct|percent|share|retention|ctr|arppu|arpdau)(?:_|$)",
                re.IGNORECASE,
            )
            if ds_id:
                y_val = getattr(comp, "y", None)
                y_cols_check = y_val if isinstance(y_val, list) else ([y_val] if y_val else [])
                for yc in y_cols_check:
                    if yc and _RATIO_COL_RE.search(yc):
                        result.warn(
                            "linechart-ratio-column-as-y",
                            f"LineChart '{getattr(comp, 'title', '')}' uses y='{yc}' "
                            f"(a pre-computed ratio column) with dataset_id='{ds_id}'. "
                            f"Filtering aggregates rows by summing, so ratio values "
                            f"inflate. Use ratios=[{{numerator, denominator}}] instead.",
                            component="LineChart", section=sec,
                        )

            if ratios:
                for r in ratios:
                    for key in ("numerator", "denominator"):
                        if key not in r:
                            result.fail(
                                "ratios-missing-keys",
                                f"LineChart ratio is missing '{key}' key.",
                                component="LineChart", section=sec,
                            )
                        elif r[key] not in cols:
                            result.warn(
                                "ratios-column-missing",
                                f"LineChart ratio {key} '{r[key]}' not in DataFrame columns.",
                                component="LineChart", section=sec,
                            )

        x_col = getattr(comp, "x", None)
        if x_col and name in ("LineChart", "BarChart", "StackedBar", "DoughnutChart"):
            if x_col not in cols:
                result.warn(
                    "chart-column-missing",
                    f"{name} x column '{x_col}' not in DataFrame columns.",
                    component=name, section=sec,
                )

        y = getattr(comp, "y", None)
        if y and name in ("LineChart", "BarChart"):
            y_cols = y if isinstance(y, list) else [y]
            for yc in y_cols:
                if yc and yc not in cols:
                    result.warn(
                        "chart-column-missing",
                        f"{name} y column '{yc}' not in DataFrame columns.",
                        component=name, section=sec,
                    )

        y_cols_attr = getattr(comp, "y_cols", None)
        if y_cols_attr and name in ("BarChart", "StackedBar"):
            for yc in y_cols_attr:
                if yc not in cols:
                    result.warn(
                        "chart-column-missing",
                        f"{name} y_cols column '{yc}' not in DataFrame columns.",
                        component=name, section=sec,
                    )

        if name == "StackedBar":
            sbo = getattr(comp, "stack_by_options", None)
            if sbo:
                for label, col_name in sbo.items():
                    if col_name not in cols:
                        result.warn(
                            "stackedbar-option-column-missing",
                            f"StackedBar stack_by_options['{label}'] = '{col_name}' "
                            f"not in DataFrame columns.",
                            component="StackedBar", section=sec,
                        )
            lc = getattr(comp, "line_cols", None)
            if lc:
                for lc_col in lc:
                    if lc_col not in cols:
                        result.warn(
                            "stackedbar-line-cols-missing",
                            f"StackedBar line_cols column '{lc_col}' not in DataFrame.",
                            component="StackedBar", section=sec,
                        )

        # stack_sort enum + max_stacks sanity for both LineChart and StackedBar.
        # Catches typos like stack_sort="alpha" which would silently fall back
        # to volume_desc in the runtime; better to fail at build time.
        if name in ("LineChart", "StackedBar"):
            _VALID_STACK_SORT = {"volume_desc", "volume_asc", "label_asc", "label_desc"}
            ss = getattr(comp, "stack_sort", None)
            if ss is not None and ss not in _VALID_STACK_SORT:
                result.fail(
                    "stack-sort-invalid-value",
                    f"{name} stack_sort='{ss}' is not one of "
                    f"{sorted(_VALID_STACK_SORT)}.",
                    component=name, section=sec,
                )
            sb = getattr(comp, "stack_by", None)
            if ss is not None and not sb and not getattr(comp, "stack_by_options", None):
                result.warn(
                    "stack-sort-without-stack-by",
                    f"{name} sets stack_sort='{ss}' but has neither stack_by "
                    f"nor stack_by_options — the setting has no effect.",
                    component=name, section=sec,
                )
            ms = getattr(comp, "max_stacks", None)
            if ms is not None and (not isinstance(ms, int) or ms < 1):
                result.fail(
                    "max-stacks-invalid-value",
                    f"{name} max_stacks={ms!r} must be a positive integer.",
                    component=name, section=sec,
                )
            if ms is not None and not sb and not getattr(comp, "stack_by_options", None):
                result.warn(
                    "max-stacks-without-stack-by",
                    f"{name} sets max_stacks={ms} but has neither stack_by "
                    f"nor stack_by_options — the setting has no effect.",
                    component=name, section=sec,
                )

        if name == "ComboChart":
            for bc in (getattr(comp, "bar_cols", None) or []):
                if bc not in cols:
                    result.warn(
                        "chart-column-missing",
                        f"ComboChart bar_cols column '{bc}' not in DataFrame.",
                        component="ComboChart", section=sec,
                    )
            for lc_col in (getattr(comp, "line_cols", None) or []):
                if lc_col not in cols:
                    result.warn(
                        "chart-column-missing",
                        f"ComboChart line_cols column '{lc_col}' not in DataFrame.",
                        component="ComboChart", section=sec,
                    )

        if name == "DoughnutChart":
            label_col = getattr(comp, "label", None)
            value_col = getattr(comp, "value", None)
            if label_col and label_col not in cols:
                result.warn(
                    "chart-column-missing",
                    f"DoughnutChart label column '{label_col}' not in DataFrame.",
                    component="DoughnutChart", section=sec,
                )
            if value_col and value_col not in cols:
                result.warn(
                    "chart-column-missing",
                    f"DoughnutChart value column '{value_col}' not in DataFrame.",
                    component="DoughnutChart", section=sec,
                )

        if name == "BarChart":
            tn = getattr(comp, "top_n", None)
            if tn is not None and (not isinstance(tn, int) or tn < 1):
                result.fail(
                    "barchart-top-n-invalid-value",
                    f"BarChart top_n={tn!r} must be a positive integer.",
                    component="BarChart", section=sec,
                )
            # High-cardinality without top_n is the canonical "100+ bars
            # squashed into the chart" mistake the parameter exists to
            # prevent. Only flag on horizontal bars (where it matters
            # most) and only when we can read distinct values from the
            # bound DataFrame.
            if (tn is None and getattr(comp, "horizontal", False)
                    and getattr(comp, "x", None) and isinstance(df, pd.DataFrame)
                    and not df.empty and comp.x in df.columns):
                try:
                    cardinality = int(df[comp.x].nunique(dropna=False))
                except Exception:
                    cardinality = 0
                if cardinality > 50:
                    result.warn(
                        "barchart-horizontal-high-cardinality",
                        f"BarChart '{getattr(comp, 'title', '')}' has {cardinality} "
                        f"distinct values on x='{comp.x}' and no top_n cap. The "
                        f"label wall will be unreadable. Set top_n=20 (or pre-aggregate) "
                        f"to collapse the tail into 'Other'.",
                        component="BarChart", section=sec,
                    )

        if name == "HeatmapChart":
            for attr_name in ("x", "y", "value"):
                col_val = getattr(comp, attr_name, None)
                if col_val and col_val not in cols:
                    result.warn(
                        "chart-column-missing",
                        f"HeatmapChart {attr_name} column '{col_val}' not in DataFrame.",
                        component="HeatmapChart", section=sec,
                    )

        if name == "ScatterChart":
            for attr_name in ("x", "y", "size", "color_by"):
                col_val = getattr(comp, attr_name, None)
                if col_val and col_val not in cols:
                    result.warn(
                        "chart-column-missing",
                        f"ScatterChart {attr_name} column '{col_val}' not in DataFrame.",
                        component="ScatterChart", section=sec,
                    )

        if name == "DataTable":
            cond_fmt = getattr(comp, "conditional_formats", None)
            if cond_fmt and cols:
                for cf_col in cond_fmt:
                    if cf_col not in cols:
                        result.warn(
                            "conditional-format-column-missing",
                            f"DataTable conditional_formats column '{cf_col}' "
                            f"not in DataFrame columns.",
                            component="DataTable", section=sec,
                        )

            # `columns` must be a list of STRINGS (DataFrame column names
            # to display). Passing dicts causes "[object Object]" in the
            # rendered UI. For custom labels/formats, rename the DataFrame
            # columns in Python before constructing the DataTable.
            table_cols = getattr(comp, "columns", None)
            if table_cols:
                bad = [c for c in table_cols if not isinstance(c, str)]
                if bad:
                    example_type = type(bad[0]).__name__
                    result.fail(
                        "datatable-columns-not-strings",
                        f"DataTable columns must be a list of column-name "
                        f"strings, got {len(bad)} non-string entries "
                        f"(first is {example_type}). Rendering will show "
                        f"'[object Object]'. For custom labels or formats, "
                        f"rename the DataFrame columns in Python before "
                        f"passing to DataTable — e.g. "
                        f"df.rename(columns={{'old': 'New Label'}}).",
                        component="DataTable", section=sec,
                    )
                if cols:
                    missing = [c for c in table_cols
                               if isinstance(c, str) and c not in cols]
                    if missing:
                        result.warn(
                            "datatable-columns-missing",
                            f"DataTable columns not in DataFrame: "
                            f"{', '.join(missing)}.",
                            component="DataTable", section=sec,
                        )

            # `columns` must be a list of STRINGS (DataFrame column names
            # to display). Passing dicts causes "[object Object]" in the
            # rendered UI. For custom labels/formats, rename the DataFrame
            # columns in Python before constructing the DataTable.
            table_cols = getattr(comp, "columns", None)
            if table_cols:
                bad = [c for c in table_cols if not isinstance(c, str)]
                if bad:
                    example_type = type(bad[0]).__name__
                    result.fail(
                        "datatable-columns-not-strings",
                        f"DataTable columns must be a list of column-name "
                        f"strings, got {len(bad)} non-string entries "
                        f"(first is {example_type}). Rendering will show "
                        f"'[object Object]'. For custom labels or formats, "
                        f"rename the DataFrame columns in Python before "
                        f"passing to DataTable — e.g. "
                        f"df.rename(columns={{'old': 'New Label'}}).",
                        component="DataTable", section=sec,
                    )
                if cols:
                    missing = [c for c in table_cols
                               if isinstance(c, str) and c not in cols]
                    if missing:
                        result.warn(
                            "datatable-columns-missing",
                            f"DataTable columns not in DataFrame: "
                            f"{', '.join(missing)}.",
                            component="DataTable", section=sec,
                        )

        if name == "KpiRow" and ds_id:
            kpis = getattr(comp, "kpis", [])
            for kpi in kpis:
                if not isinstance(kpi, dict):
                    continue
                agg = kpi.get("agg", "sum")
                if agg not in _VALID_AGGS:
                    result.warn(
                        "kpi-agg-invalid",
                        f"KpiRow agg '{agg}' is not valid. "
                        f"Expected one of: {sorted(_VALID_AGGS)}",
                        component="KpiRow", section=sec,
                    )
                if agg == "ratio":
                    for key in ("numerator", "denominator"):
                        if key not in kpi:
                            result.warn(
                                "kpi-agg-missing-fields",
                                f"KpiRow agg='ratio' is missing '{key}'.",
                                component="KpiRow", section=sec,
                            )
                elif agg == "purchase_pct":
                    for key in ("source_col", "type_col", "match_values"):
                        if key not in kpi:
                            result.warn(
                                "kpi-agg-missing-fields",
                                f"KpiRow agg='purchase_pct' is missing '{key}'.",
                                component="KpiRow", section=sec,
                            )
                fmt = kpi.get("format", "number")
                if fmt not in _VALID_FORMATS:
                    result.info(
                        "kpi-format-unknown",
                        f"KpiRow format '{fmt}' is not standard. "
                        f"Framework falls back to fmtCompact.",
                        component="KpiRow", section=sec,
                    )
                delta_col = kpi.get("delta_col")
                if delta_col and cols and delta_col not in cols:
                    result.warn(
                        "kpirow-delta-col-missing",
                        f"KpiRow delta_col '{delta_col}' not in "
                        f"DataFrame columns.",
                        component="KpiRow", section=sec,
                    )
