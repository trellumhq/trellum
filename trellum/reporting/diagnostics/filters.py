"""Which filters actually reach which charts.

The question a report owner asks about a filter bar -- does moving
this control change that chart -- answered per (filter, chart) pair
rather than assumed from the fact that both exist.
"""

from __future__ import annotations

from typing import Any, Literal

FilterStatus = Literal["active", "propagated", "inactive", "untracked"]

from trellum.reporting.diagnostics.types import (
    ChartFilterRow,
    FilterCell,
)
from trellum.reporting.diagnostics.walk import (
    _chart_value_columns,
    _comp_kind,
    _detect_static_columns,
    _df_columns,
    _is_filterable,
    _walk_view,
)


def _classify_filter(
    fb: Any,
    src_col: str,
    target_ds_id: str | None,
    ds_columns: dict[str, set[str]],
    scoped_parents: dict[str, str] | None = None,
) -> FilterCell:
    """Classify how a single FilterBar filter reaches a chart's DataSource."""
    if not target_ds_id:
        return {
            "column": src_col,
            "target_column": "",
            "status": "inactive",
            "reason": "Chart has no dataset_id.",
        }

    # ScopedDataSource: filters on the parent's FilterBar automatically apply
    # to the child (the JS engine derives child rows from the parent's
    # filtered output). When this FilterBar isn't directly attached to the
    # child, check whether it reaches the child via the parent.
    sp = scoped_parents or {}
    if (
        target_ds_id in sp
        and fb.dataset_id != target_ds_id
        and target_ds_id not in (getattr(fb, "propagate_to", None) or {})
    ):
        parent_id = sp[target_ds_id]
        parent_cell = _classify_filter(fb, src_col, parent_id, ds_columns, sp)
        if parent_cell["status"] in ("active", "propagated"):
            return {
                "column": src_col,
                "target_column": parent_cell.get("target_column") or src_col,
                "status": "active",
                "reason": (
                    f"ScopedDataSource '{target_ds_id}' inherits this filter "
                    f"from parent '{parent_id}'."
                ),
            }
        return parent_cell

    if target_ds_id == fb.dataset_id:
        cols = ds_columns.get(target_ds_id, set())
        if not cols or src_col in cols:
            return {
                "column": src_col,
                "target_column": src_col,
                "status": "active",
                "reason": "Chart reads the FilterBar's primary DataSource directly.",
            }
        return {
            "column": src_col,
            "target_column": src_col,
            "status": "inactive",
            "reason": (
                f"Filter column '{src_col}' is missing from the FilterBar's "
                f"primary DataSource '{target_ds_id}'."
            ),
        }

    propagate = getattr(fb, "propagate_to", None) or {}
    col_map = propagate.get(target_ds_id) or {}
    if src_col in col_map:
        tgt_col = col_map[src_col]
        cols = ds_columns.get(target_ds_id, set())
        if not cols or tgt_col in cols:
            return {
                "column": src_col,
                "target_column": tgt_col,
                "status": "propagated",
                "reason": (
                    f"propagate_to maps '{src_col}' → '{tgt_col}' on "
                    f"'{target_ds_id}'."
                ),
            }
        return {
            "column": src_col,
            "target_column": tgt_col,
            "status": "inactive",
            "reason": (
                f"propagate_to maps '{src_col}' → '{tgt_col}' but column "
                f"'{tgt_col}' is missing from DataSource '{target_ds_id}'."
            ),
        }

    if target_ds_id in propagate:
        return {
            "column": src_col,
            "target_column": "",
            "status": "inactive",
            "reason": (
                f"DataSource '{target_ds_id}' is in propagate_to but the "
                f"mapping does not include '{src_col}'. Filters only flow "
                f"to columns listed in the per-DataSource map."
            ),
        }

    return {
        "column": src_col,
        "target_column": "",
        "status": "inactive",
        "reason": (
            f"DataSource '{target_ds_id}' is not the FilterBar's primary "
            f"DataSource and is not listed in propagate_to. The JS filter "
            f"engine will never push '{src_col}' to it."
        ),
    }

def _compute_view_matrix(
    sections: list[dict],
    scope: str | None,
    chart_counter: list[int],
) -> list[ChartFilterRow]:
    """Build filter-coverage rows for one view (flat or one scope's sections)."""
    from trellum.components.filterable import DataSource, FilterBar, ScopedDataSource
    from trellum.components.layout import RawHTML

    pairs = _walk_view(sections)

    ds_columns: dict[str, set[str]] = {}
    ds_dfs: dict[str, Any] = {}
    scoped_parents: dict[str, str] = {}   # child_id → parent_id
    fbs: list[Any] = []
    for comp, _sec in pairs:
        if isinstance(comp, DataSource):
            ds_columns[comp.id] = _df_columns(getattr(comp, "df", None))
            ds_dfs[comp.id] = getattr(comp, "df", None)
        elif isinstance(comp, ScopedDataSource):
            scoped_parents[comp.id] = comp.parent
        elif isinstance(comp, FilterBar):
            fbs.append(comp)

    # ScopedDataSources inherit their parent's columns and df. Resolve
    # them now so charts reading a scoped DS report the parent's grain
    # in the coverage matrix.
    for child_id, parent_id in scoped_parents.items():
        parent_id_resolved = parent_id
        # walk up if the user accidentally chained (validator already
        # forbids this, but be defensive)
        while parent_id_resolved in scoped_parents:
            parent_id_resolved = scoped_parents[parent_id_resolved]
        ds_columns[child_id] = ds_columns.get(parent_id_resolved, set())
        ds_dfs[child_id] = ds_dfs.get(parent_id_resolved)

    # Build dataset_id → list[FilterBar]. A DataSource is "covered"
    # by a FilterBar if it is the FilterBar's primary DS or appears in
    # its propagate_to. A chart may be covered by MULTIPLE FilterBars:
    # a main (top-level, report-wide) FilterBar plus a section
    # FilterBar that adds drill-down dimensions. Both compose — the
    # JS engine ANDs all active filters per DataSource — so the
    # diagnostic must union their filter columns.
    fb_by_ds: dict[str, list[Any]] = {}
    for fb in fbs:
        fb_by_ds.setdefault(fb.dataset_id, []).append(fb)
        for tgt_ds in (getattr(fb, "propagate_to", None) or {}):
            fb_by_ds.setdefault(tgt_ds, []).append(fb)
    # Inheritance: every FilterBar that covers a parent also implicitly
    # covers each ScopedDataSource child (the JS engine derives child
    # rows from parent's filtered output).
    for child_id, parent_id in scoped_parents.items():
        if parent_id in fb_by_ds:
            fb_by_ds.setdefault(child_id, []).extend(fb_by_ds[parent_id])

    rows: list[ChartFilterRow] = []
    for comp, sec_title in pairs:
        if isinstance(comp, (DataSource, FilterBar)):
            continue

        kind = _comp_kind(comp)
        title = getattr(comp, "title", "") or kind
        chart_counter[0] += 1
        chart_id = f"{scope or 'flat'}:{sec_title}:{kind}:{chart_counter[0]}"

        # RawHTML / ABCompare: pick the first FilterBar in the view
        # purely for the column list to display. We can't classify
        # statically anyway — every cell is "untracked".
        any_fb = fbs[0] if fbs else None
        any_fb_cols = [
            f.get("column") for f in (getattr(any_fb, "filters", None) or [])
            if f.get("column")
        ] if any_fb else []

        if isinstance(comp, RawHTML) or kind == "ABCompare":
            untracked_reason = (
                "Custom JS — framework can't statically check filter wiring. "
                "Inspect the JS to confirm it subscribes to the filter engine."
                if isinstance(comp, RawHTML)
                else "ABCompare manages its own filtering internally."
            )
            cells: list[FilterCell] = [
                {
                    "column": col,
                    "target_column": "",
                    "status": "untracked",
                    "reason": untracked_reason,
                }
                for col in any_fb_cols
            ]
            rows.append(
                {
                    "chart_id": chart_id,
                    "chart_title": title,
                    "chart_kind": kind,
                    "section": sec_title,
                    "scope": scope,
                    "dataset_id": getattr(comp, "data_key", None) or None,
                    "filters": cells,
                    "static_value_columns": [],
                }
            )
            continue

        if not _is_filterable(comp):
            continue

        ds_id = getattr(comp, "dataset_id", None)

        # Components with no dataset_id are either intentionally static
        # (e.g. KpiRow with precomputed values) or missing a wiring step
        # — the existing `component-missing-dataset-id` validator
        # handles both. Skip them here so the matrix only contains
        # genuine "this chart is supposed to be live" rows.
        if ds_id is None:
            continue

        # Grain-mismatch detection: walk the chart's value columns and
        # check whether each one is constant within every x-axis value.
        # If so, the chart can't change its plotted numbers when
        # filtered, even if the FilterBar reaches the DS structurally.
        x_col = getattr(comp, "x", None)
        value_cols = _chart_value_columns(comp)
        static_cols = _detect_static_columns(ds_dfs.get(ds_id), x_col, value_cols)

        # Find ALL FilterBars whose primary or propagate_to includes
        # this chart's DataSource. Compose their filter columns so a
        # main FilterBar's date_range AND a section FilterBar's
        # chest_level both show on a chest chart.
        covering_fbs = fb_by_ds.get(ds_id, [])

        # Collect column → list of (fb, status-cell). Preserve first-seen
        # column order across all FBs.
        col_order: list[str] = []
        col_to_candidates: dict[str, list[FilterCell]] = {}
        for fb in covering_fbs:
            for f in (getattr(fb, "filters", None) or []):
                col = f.get("column")
                if not col:
                    continue
                if col not in col_to_candidates:
                    col_order.append(col)
                    col_to_candidates[col] = []
                col_to_candidates[col].append(
                    _classify_filter(fb, col, ds_id, ds_columns, scoped_parents)
                )

        if not col_order:
            # No FilterBar covers this chart's DataSource — section may
            # be intentionally non-filterable (tiny rollup chart) or a
            # propagation gap. Emit the row so totals stay accurate;
            # empty filters means "no FilterBar applies to this chart".
            rows.append(
                {
                    "chart_id": chart_id,
                    "chart_title": title,
                    "chart_kind": kind,
                    "section": sec_title,
                    "scope": scope,
                    "dataset_id": ds_id,
                    "filters": [],
                    "static_value_columns": static_cols,
                }
            )
            continue

        # When multiple FilterBars define the same column for this DS
        # (e.g. main has date_range, a section FilterBar also has
        # date_range), pick the most permissive status per column —
        # active beats propagated beats inactive beats untracked.
        status_priority = {"active": 0, "propagated": 1, "inactive": 2, "untracked": 3}
        cells: list[FilterCell] = []
        for col in col_order:
            cands = col_to_candidates[col]
            best = min(cands, key=lambda c: status_priority.get(c.get("status", ""), 99))
            cells.append(best)

        rows.append(
            {
                "chart_id": chart_id,
                "chart_title": title,
                "chart_kind": kind,
                "section": sec_title,
                "scope": scope,
                "dataset_id": ds_id,
                "filters": cells,
                "static_value_columns": static_cols,
            }
        )

    return rows

def compute_filter_effectiveness(ctx: Any) -> list[ChartFilterRow]:
    """Walk every (chart, scope-view) pair and classify filter coverage.

    For multi-scope reports, the FilterBar of each scope-view (flat
    sections + that scope's sections) is what charts under that view
    react to.
    """
    rows: list[ChartFilterRow] = []
    chart_counter = [0]
    flat_sections = list(getattr(ctx, "sections", []) or [])
    scopes_map = getattr(ctx, "scopes", {}) or {}

    if not scopes_map:
        rows.extend(_compute_view_matrix(flat_sections, None, chart_counter))
        return rows

    # Multi-scope: each scope-view gets its own analysis. Charts in the
    # flat sections appear once per scope (the FilterBar may differ per
    # scope), so we record them for each scope to reflect the user's
    # actual view.
    for scope_name, scope_sections in scopes_map.items():
        rows.extend(
            _compute_view_matrix(
                flat_sections + list(scope_sections), scope_name, chart_counter
            )
        )
    return rows
