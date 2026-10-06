"""Filter effectiveness: whether a filter actually reaches the charts."""

from __future__ import annotations

from typing import Any

from trellum.validation.result import Check, ValidationResult


def _check_filter_effectiveness(ctx: Any, details: dict, result: ValidationResult) -> None:
    """Flag charts whose dataset_id is not reached by one or more filters.

    Reads the pre-computed ``details.filter_matrix``. The structural rule
    is encoded by ``trellum.reporting.diagnostics`` — this function only converts
    the matrix into validator checks.

    - ``chart-filter-coverage`` (warn): chart has at least one ``inactive``
      filter cell. The most common cause is forgetting to add the chart's
      DataSource to ``FilterBar.propagate_to`` with the right column map.
    - ``filterbar-incomplete-propagation`` (info): a DataSource is in
      ``propagate_to`` but the column-map for at least one filter is
      missing. Less severe — the chart is partially wired.

    Granular acceptance: ``report.yaml``'s
    ``validation.accept_inactive_filters: {ds_id: [col, ...]}`` lets a
    report formally accept that specific (dataset_id, filter_column)
    pairs are dead — the SQL doesn't carry that dim, the rollup is
    semantically agnostic, etc. Cells in this map render as
    suppressed in the matrix and don't fire the warning. Any *other*
    inactive filter on the same DataSource still fires (so a future
    new dead filter isn't accidentally swallowed).
    """
    val_cfg = (getattr(ctx, "config", {}) or {}).get("validation", {}) or {}
    accept_map_raw = val_cfg.get("accept_inactive_filters", {}) or {}
    accept_map: dict[str, set[str]] = {
        ds: set(cols or []) for ds, cols in accept_map_raw.items()
    }

    matrix = details.get("filter_matrix") or []

    seen_partial: set[tuple[str, str]] = set()  # (dataset_id, src_col)
    seen_static: set[tuple[str, str, str]] = set()  # (chart_id, ds_id, col)

    for row in matrix:
        # Grain-mismatch: chart's value column is constant per x. The
        # filter may reach the DS structurally, but no filter changes
        # the plotted numbers because the column was attached at
        # coarser-than-row grain (typical with date-only merges).
        for col in row.get("static_value_columns") or []:
            ds_id = row.get("dataset_id") or ""
            key = (row.get("chart_id", ""), ds_id, col)
            if key in seen_static:
                continue
            seen_static.add(key)
            title = row.get("chart_title") or row.get("chart_kind") or "chart"
            scope = row.get("scope")
            scope_suffix = f" (scope '{scope}')" if scope else ""
            result.warn(
                "chart-value-grain-mismatch",
                f"{row.get('chart_kind', 'Component')} '{title}'{scope_suffix} "
                f"reads '{col}' from DataSource '{ds_id}', but '{col}' is "
                f"constant within each x-axis value — the column was attached "
                f"at coarser grain than the DataSource itself (typical when a "
                f"daily total is merged in on date only). Filters may "
                f"structurally reach the DS, but ratios involving '{col}' "
                f"stay constant under any filter and absolute aggregates "
                f"inflate by the count of duplicated rows. Fix by joining "
                f"the data at the DS's full grain or by using a separate "
                f"DataSource at the right grain.",
                component=row.get("chart_kind", ""),
                section=row.get("section", ""),
                dataset_id=ds_id,
            )

    for row in matrix:
        filters = row.get("filters") or []
        if not filters:
            continue

        inactive_cols = [
            f["column"] for f in filters if f.get("status") == "inactive"
        ]
        if inactive_cols:
            ds_id = row.get("dataset_id") or "(none)"
            accepted = accept_map.get(ds_id, set())
            unaccepted = [c for c in inactive_cols if c not in accepted]
            accepted_here = [c for c in inactive_cols if c in accepted]
            title = row.get("chart_title") or row.get("chart_kind") or "chart"
            scope = row.get("scope")
            scope_suffix = f" (scope '{scope}')" if scope else ""
            kind = row.get("chart_kind", "Component")
            section = row.get("section", "")
            ds_arg = ds_id if ds_id != "(none)" else ""

            # Active warning for filter columns NOT yet accepted. These
            # are the bugs the dev should still act on.
            if unaccepted:
                cols_str = ", ".join(f"'{c}'" for c in unaccepted)
                result.warn(
                    "chart-filter-coverage",
                    f"{kind} '{title}'{scope_suffix} reads dataset_id='{ds_id}' "
                    f"which is NOT reached by filter(s) {cols_str}. Either add "
                    f"the DataSource to FilterBar.propagate_to with a column "
                    f"mapping, or — if the chart is intentionally filter-"
                    f"independent for those columns — list them under "
                    f"`validation.accept_inactive_filters.{ds_id}` in "
                    f"report.yaml.",
                    component=kind, section=section, dataset_id=ds_arg,
                )

            # Suppressed warning for filter columns the report has
            # already accepted as inactive. Surfaces them in the drawer's
            # Suppressed group so a reviewer sees what's deliberately
            # tolerated (and can challenge the choice if circumstances
            # change). Marked suppressed=True directly so the user does
            # NOT need to also list this in `validation.suppress`.
            if accepted_here:
                cols_str = ", ".join(f"'{c}'" for c in accepted_here)
                c = Check(
                    id="chart-filter-coverage",
                    level="warn",
                    message=(
                        f"{kind} '{title}'{scope_suffix} reads "
                        f"dataset_id='{ds_id}'; filter(s) {cols_str} are "
                        f"deliberately accepted as inactive via "
                        f"`validation.accept_inactive_filters.{ds_id}` in "
                        f"report.yaml."
                    ),
                    component=kind,
                    section=section,
                    dataset_id=ds_arg,
                    suppressed=True,
                )
                result.checks.append(c)

        # Detect partial propagation: DS *is* in propagate_to (some
        # filter is `propagated`) but at least one other filter is
        # `inactive`. That is almost always a missing column-map entry.
        statuses = {f.get("status") for f in filters}
        if "propagated" in statuses and "inactive" in statuses:
            ds_id = row.get("dataset_id") or ""
            for f in filters:
                if f.get("status") != "inactive":
                    continue
                key = (ds_id, f["column"])
                if key in seen_partial:
                    continue
                seen_partial.add(key)
                result.info(
                    "filterbar-incomplete-propagation",
                    f"DataSource '{ds_id}' is partially propagated — filter "
                    f"'{f['column']}' is missing from FilterBar.propagate_to"
                    f"['{ds_id}']. Add a mapping or accept that this filter "
                    f"won't apply to charts on '{ds_id}'.",
                    component="FilterBar",
                    section=row.get("section", ""),
                )
