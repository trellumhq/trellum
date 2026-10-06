"""Data sources, filter bars and the dataset ids that tie them together."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trellum.validation.result import ValidationResult
from trellum.validation.walk import (
    _comp_name,
    _df_columns,
    _get_filterable_types,
    _kpi_row_is_fully_static,
    _walk_components,
)


def _register_ds(ds_map: dict, comp: Any, sec: str, kind: str, result: ValidationResult) -> None:
    """Add *comp* to ds_map under its id, FAILing on a collision. Shared by
    DataSource/LiveDataSource/ScopedDataSource -- same rule (data.json only
    has room for one entry per id), three component types."""
    if comp.id in ds_map:
        result.fail(
            "duplicate-datasource-id",
            f"{kind} id '{comp.id}' collides with an existing "
            f"DataSource/LiveDataSource/ScopedDataSource. Pick a different id.",
            component=kind, section=sec,
        )
    ds_map[comp.id] = comp


def _check_datasource_filterbar(ctx: Any, comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Category 1: DataSource / FilterBar wiring."""
    from trellum.components.filterable import (
        DataSource,
        FilterBar,
        LiveDataSource,
        ScopedDataSource,
    )

    ds_map: dict[str, Any] = {}
    fb_list: list[tuple[Any, str]] = []
    scoped_list: list[tuple[Any, str]] = []

    for comp, sec in comps:
        if isinstance(comp, DataSource):
            _register_ds(ds_map, comp, sec, "DataSource", result)
        elif isinstance(comp, LiveDataSource):
            # A live dataset is a full ds_map citizen: charts/tables/KPI
            # rows and FilterBars reference it by dataset_id exactly like
            # any other DataSource, and its snapshot df has real columns
            # for the column-reference checks below to validate against.
            _register_ds(ds_map, comp, sec, "LiveDataSource", result)
        elif isinstance(comp, ScopedDataSource):
            _register_ds(ds_map, comp, sec, "ScopedDataSource", result)
            scoped_list.append((comp, sec))
        elif isinstance(comp, FilterBar):
            fb_list.append((comp, sec))

    # Each ScopedDataSource's parent must exist and must NOT itself be scoped.
    for sds, sec in scoped_list:
        if sds.parent not in ds_map:
            result.fail(
                "scoped-ds-parent-missing",
                f"ScopedDataSource '{sds.id}' references parent '{sds.parent}' "
                f"which is not a declared DataSource. Available: "
                f"{[k for k, v in ds_map.items() if isinstance(v, DataSource)]}",
                component="ScopedDataSource", section=sec,
            )
        elif isinstance(ds_map[sds.parent], ScopedDataSource):
            result.fail(
                "scoped-ds-parent-not-base",
                f"ScopedDataSource '{sds.id}' parent '{sds.parent}' is itself a "
                f"ScopedDataSource. Parents must be base DataSources.",
                component="ScopedDataSource", section=sec,
            )

    fb_ds_ids = {fb.dataset_id for fb, _ in fb_list}
    propagate_targets: set[str] = set()
    for fb, _ in fb_list:
        if fb.propagate_to:
            propagate_targets.update(fb.propagate_to.keys())

    # Hierarchical FilterBar rule: a report can have at most ONE main
    # FilterBar (in the untitled top section) plus at most ONE section
    # FilterBar per titled section. Filters compose — a chart inside a
    # section is subject to BOTH the main FilterBar and the section's
    # FilterBar. Multi-scope reports get this rule applied per scope.
    def _fbs_grouped(sections: list[dict]):
        """Walk sections; for each section yield (section_title, [FilterBar])."""
        flat_fbs: list[Any] = []
        section_fbs: dict[str, list[Any]] = {}
        for i, sec in enumerate(sections):
            title = sec.get("title", "")
            fbs_in_sec: list[Any] = []
            for comp, _sec in _walk_components(sec.get("components", []), section_title=title):
                if isinstance(comp, FilterBar):
                    fbs_in_sec.append(comp)
            if not fbs_in_sec:
                continue
            # The "main" FilterBar lives in the FIRST untitled section
            # (the canonical sticky-top position). Anything in a
            # titled section is a section-scoped FilterBar.
            if i == 0 and title == "":
                flat_fbs.extend(fbs_in_sec)
            else:
                section_fbs.setdefault(title or "(untitled)", []).extend(fbs_in_sec)
        return flat_fbs, section_fbs

    def _check_one_view(scope_label: str, sections: list[dict]) -> None:
        main_fbs, section_fbs = _fbs_grouped(sections)
        scope_prefix = f"Scope '{scope_label}': " if scope_label else ""
        if len(main_fbs) > 1:
            result.fail(
                "single-main-filterbar",
                f"{scope_prefix}{len(main_fbs)} FilterBars in the untitled top "
                f"section. A report can have at most one main FilterBar there; "
                f"move section-specific filters into their titled sections.",
                component="FilterBar", section="",
            )
        for title, fbs in section_fbs.items():
            if len(fbs) > 1:
                result.fail(
                    "single-filterbar-per-section",
                    f"{scope_prefix}Section '{title}' has {len(fbs)} FilterBars. "
                    f"Each section may have at most one section-scoped FilterBar; "
                    f"split or merge them.",
                    component="FilterBar", section=title,
                )

    flat_sections = getattr(ctx, "sections", None) or []
    scopes_map = getattr(ctx, "scopes", None) or {}
    if scopes_map:
        for scope_name, scope_sections in scopes_map.items():
            _check_one_view(scope_name, list(flat_sections) + list(scope_sections))
    else:
        _check_one_view("", list(flat_sections))

    # Coverage note: with the hierarchical (main + section) model, a
    # DataSource is "covered" if ANY FilterBar reaches it — either the
    # main one (primary or propagate_to) OR a section FilterBar that
    # lives in the same section as the DataSource. Only flag DSes
    # covered by zero FilterBars.
    covered = fb_ds_ids | propagate_targets
    consuming_ds_ids: set[str] = set()
    for comp, _ in comps:
        if isinstance(comp, (DataSource, FilterBar)):
            continue
        ds_id = getattr(comp, "dataset_id", None)
        if ds_id:
            consuming_ds_ids.add(ds_id)
    uncovered = sorted(consuming_ds_ids - covered)
    if uncovered and fb_list:
        result.info(
            "filterbar-coverage",
            f"DataSource(s) {uncovered} are not covered by ANY FilterBar (no "
            f"primary, no propagate_to). Components reading from them will not "
            f"react to filters. If that's intentional (pre-aggregated trend "
            f"data, static reference tables, etc.) ignore this note; otherwise "
            f"add a FilterBar in the same section, or list them in the main "
            f"FilterBar's propagate_to.",
            component="FilterBar", section="",
        )

    for ds_id, ds in ds_map.items():
        if ds_id in fb_ds_ids or ds_id in propagate_targets:
            continue
        # ScopedDataSources are transparent: they inherit the parent's filters
        # automatically, so they don't need their own FilterBar coverage check.
        if isinstance(ds, ScopedDataSource):
            continue
        # `ds-no-filterbar` is only a real concern when the DataSource
        # actually feeds something that should be reactive. Skip when no
        # consumer references this DS (then it's just an unused
        # DataSource, harmless), or when consumers in the same section
        # are tiny / static rollups (not currently inferable; user
        # accepts via existing suppress).
        if ds_id not in consuming_ds_ids:
            continue
        result.warn(
            "ds-no-filterbar",
            f"DataSource '{ds_id}' is read by chart components but no FilterBar "
            f"covers it (neither a section-scoped FilterBar nor any main "
            f"FilterBar's propagate_to). Add a FilterBar in the same section, "
            f"or — if the DS is intentionally non-reactive (small rollup, "
            f"reference data) — suppress 'ds-no-filterbar' for that DS.",
            component="DataSource", section="",
            dataset_id=ds_id,
        )

    for fb, sec in fb_list:
        if fb.dataset_id not in ds_map:
            result.fail(
                "filterbar-orphan",
                f"FilterBar dataset_id '{fb.dataset_id}' does not match any DataSource. "
                f"Available: {list(ds_map.keys())}",
                component="FilterBar", section=sec,
            )

        fb_cols = _df_columns(fb.df)
        for f in fb.filters:
            col = f.get("column", "")
            if col and fb_cols and col not in fb_cols and "options" not in f:
                result.fail(
                    "filter-column-missing",
                    f"FilterBar filter column '{col}' not in DataFrame columns.",
                    component="FilterBar", section=sec,
                )
            depends_on = f.get("depends_on")
            if depends_on:
                parents = [depends_on] if isinstance(depends_on, str) else list(depends_on)
                fb_filter_cols = {ff.get("column") for ff in fb.filters}
                for parent in parents:
                    if parent == col:
                        result.fail(
                            "filter-depends-on-self",
                            f"FilterBar filter '{col}' has depends_on='{parent}' "
                            f"which is itself. Cascading needs a different parent.",
                            component="FilterBar", section=sec,
                        )
                    elif parent not in fb_filter_cols:
                        result.fail(
                            "filter-depends-on-missing",
                            f"FilterBar filter '{col}' has depends_on='{parent}' "
                            f"but no filter on column '{parent}' exists in this "
                            f"FilterBar. Cascading only works between filters on "
                            f"the same FilterBar.",
                            component="FilterBar", section=sec,
                        )
                    elif fb_cols and parent not in fb_cols:
                        result.fail(
                            "filter-depends-on-missing",
                            f"FilterBar filter '{col}' has depends_on='{parent}' "
                            f"but column '{parent}' is not in the DataFrame.",
                            component="FilterBar", section=sec,
                        )

        if fb.propagate_to:
            for target_ds_id, col_map in fb.propagate_to.items():
                if target_ds_id not in ds_map:
                    result.fail(
                        "propagate-target-missing",
                        f"FilterBar propagate_to target '{target_ds_id}' "
                        f"does not match any DataSource. Available: {list(ds_map.keys())}",
                        component="FilterBar", section=sec,
                    )
                elif isinstance(ds_map[target_ds_id], ScopedDataSource):
                    # propagate_to a ScopedDataSource is unnecessary — scoped
                    # children inherit their parent's filters automatically.
                    result.warn(
                        "propagate-to-scoped-ds",
                        f"FilterBar propagate_to target '{target_ds_id}' is a "
                        f"ScopedDataSource. Scoped children automatically "
                        f"inherit their parent's filters, so this propagate_to "
                        f"entry is redundant and may cause double-filtering.",
                        component="FilterBar", section=sec,
                    )
                else:
                    target_cols = _df_columns(ds_map[target_ds_id].df)
                    for src_col, tgt_col in col_map.items():
                        if target_cols and tgt_col not in target_cols:
                            result.warn(
                                "propagate-column-missing",
                                f"FilterBar propagate_to column '{tgt_col}' not in "
                                f"DataSource '{target_ds_id}' DataFrame.",
                                component="FilterBar", section=sec,
                            )

    # ── Chunking checks ──────────────────────────────────────────────────────

    # Build a map from DataSource id → FilterBar filters for that DataSource
    fb_filters_map: dict[str, list[dict]] = {
        fb.dataset_id: fb.filters for fb, _ in fb_list
    }

    # DataSources that receive a propagated date_range filter count as having one
    # for chunking purposes — the chunk loader subscribes to that propagated filter.
    propagated_date_range: set[str] = set()
    for fb, _ in fb_list:
        if not any(f.get("type") == "date_range" for f in (fb.filters or [])):
            continue
        for target_ds_id, col_map in (getattr(fb, "propagate_to", {}) or {}).items():
            if "event_date" in col_map:
                propagated_date_range.add(target_ds_id)

    _LARGE_DS_BYTES = 50 * 1024 * 1024  # 50 MB

    for ds_id, ds in ds_map.items():
        if isinstance(ds, ScopedDataSource):
            # No DataFrame and no chunking — derived from parent at runtime.
            continue
        if getattr(ds, "chunk_by", None):
            # chunk_by is set — check that a date_range filter exists (direct or propagated)
            filters = fb_filters_map.get(ds_id, [])
            has_date_range = (
                any(f.get("type") == "date_range" for f in filters)
                or ds_id in propagated_date_range
            )
            if not has_date_range:
                result.fail(
                    "chunk-no-date-filter",
                    f"DataSource '{ds_id}' has chunk_by='{ds.chunk_by}' but neither "
                    f"its FilterBar nor any propagate_to source has a date_range filter. "
                    f"Chunking requires a date column to partition on.",
                    component="DataSource", section="",
                )
        else:
            # No chunking — warn if DataFrame is large
            try:
                mem = ds.df.memory_usage(deep=True).sum()
            except Exception:
                mem = 0
            if mem > _LARGE_DS_BYTES * 2:
                result.warn(
                    "large-ds-no-chunking",
                    f"DataSource '{ds_id}' uses ~{mem // (1024*1024)} MB of memory. "
                    f"Consider adding chunk_by='month' to split the data into lazy-loaded "
                    f"monthly files and reduce initial page load.",
                    component="DataSource", section="",
                )

    return ds_map

def _check_dataset_id_integrity(comps: list[tuple[Any, str]], ds_map: dict[str, Any], result: ValidationResult) -> None:
    """Category 2: Component dataset_id integrity."""
    from trellum.components.filterable import DataSource, FilterBar

    has_ds = len(ds_map) > 0

    for comp, sec in comps:
        if isinstance(comp, (DataSource, FilterBar)):
            continue
        name = _comp_name(comp)
        if name not in _get_filterable_types():
            continue

        ds_id = getattr(comp, "dataset_id", None)
        is_static = getattr(comp, "static", False)
        is_static_kpi_row = (
            name == "KpiRow"
            and _kpi_row_is_fully_static(getattr(comp, "kpis", None))
        )
        if ds_id is None and has_ds and not is_static and not is_static_kpi_row:
            title = getattr(comp, "title", "") or name
            result.fail(
                "component-missing-dataset-id",
                f"{name} '{title}' has no dataset_id but DataSource(s) "
                f"{list(ds_map.keys())} exist. It will not react to filters.",
                component=name, section=sec,
            )
        elif ds_id is not None and ds_id not in ds_map:
            result.fail(
                "component-orphan-dataset-id",
                f"{name} dataset_id '{ds_id}' does not match any DataSource. "
                f"Available: {list(ds_map.keys())}",
                component=name, section=sec,
            )
        elif ds_id is not None and is_static_kpi_row:
            # All kpis use static `value` → dataset_id is unused. Not an
            # error (the row renders fine), just noise — nudge the author
            # to drop the attribute.
            title = getattr(comp, "title", "") or name
            result.info(
                "kpi-static-with-dataset-id",
                f"KpiRow '{title}' has dataset_id='{ds_id}' but all kpis "
                f"use static 'value' (no agg). The dataset_id is unused; "
                f"consider removing it for clarity.",
                component=name, section=sec,
            )


def _check_slider_filter_columns(comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Category: a slider filter's column must be numeric -- unless it's an
    ordinal slider, which trades numeric bounds for an explicit ``values``
    list and gets its own checks instead.

    A numeric slider derives its min/max bounds from ``df[col].min()``/
    ``.max()``. On a non-numeric column those bounds are meaningless (string
    min/max) or raise outright -- catching it here turns that into a named
    validator message instead of a confusing render-time failure. Kept as
    its own function rather than folded into ``_check_datasource_filterbar``,
    which is already at its size budget (see test_module_size.py).
    """
    from trellum.components.filterable import FilterBar

    for comp, sec in comps:
        if not isinstance(comp, FilterBar):
            continue
        fb_cols = _df_columns(comp.df)
        for f in comp.filters:
            if f.get("type") != "slider":
                continue
            col = f.get("column", "")
            if "values" in f:
                _check_ordinal_slider_values(f, col, comp.df, fb_cols, result, sec)
                continue
            if not col or col not in fb_cols:
                continue  # reported separately by filter-column-missing
            if not pd.api.types.is_numeric_dtype(comp.df[col]):
                result.fail(
                    "filter-slider-not-numeric",
                    f"Slider filter column '{col}' is not numeric "
                    f"(dtype {comp.df[col].dtype}). Sliders need a numeric "
                    f"range -- use 'dropdown' or 'toggle' for categorical "
                    f"columns instead.",
                    component="FilterBar", section=sec,
                )


def _check_ordinal_slider_values(
    f: dict, col: str, df: pd.DataFrame, fb_cols: "set[str]", result: ValidationResult, sec: str
) -> None:
    """Category: an ordinal slider's ``values`` spec must be well-formed.

    ``values`` replaces the numeric min/max an ordinary slider derives from
    the column -- so it gets its own shape checks rather than the numeric
    dtype check above. FAIL on a malformed list or a default outside it
    (both are render-breaking, same severity as ``filter-slider-not-
    numeric``); WARN -- not fail -- when a listed category never occurs in
    the DataFrame, since a tier legitimately going empty under a narrow
    date filter is normal, not a broken config.
    """
    values = f.get("values")
    if not isinstance(values, list) or not values or not all(
        isinstance(v, str) for v in values
    ):
        result.fail(
            "filter-slider-not-numeric",
            f"Slider filter 'values' must be a non-empty list of strings, "
            f"got {values!r}.",
            component="FilterBar", section=sec,
        )
        return

    mode = f.get("mode", "range")
    if mode == "single":
        default = f.get("default")
        if default is not None and default not in values:
            result.fail(
                "filter-slider-not-numeric",
                f"Slider default {default!r} is not one of values {values}.",
                component="FilterBar", section=sec,
            )
    else:
        for key in ("default_min", "default_max"):
            default = f.get(key)
            if default is not None and default not in values:
                result.fail(
                    "filter-slider-not-numeric",
                    f"Slider {key} {default!r} is not one of values {values}.",
                    component="FilterBar", section=sec,
                )

    if not col or col not in fb_cols:
        return  # reported separately by filter-column-missing
    seen = set(df[col].dropna().astype(str).unique().tolist())
    for v in values:
        if v not in seen:
            result.warn(
                "filter-slider-value-unused",
                f"Slider value '{v}' for column '{col}' never occurs in "
                f"the DataFrame -- check for a typo, or a stale fixture, "
                f"if it's expected to.",
                component="FilterBar", section=sec,
            )
