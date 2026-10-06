"""Post-generation report validator.

Walks the component tree after ``generate()`` completes and checks
structural invariants that would otherwise cause silent failures at
runtime (broken filters, orphan dataset_ids, theme incompatibilities,
missing annotations, etc.).

Usage from the runner::

    from trellum.validation import validate_report
    validation = validate_report(ctx, events=events)
    validation.print_summary()
    validation.write(output_dir)
"""

from __future__ import annotations

from typing import Any

from trellum.validation.checks.annotations import _check_annotations
from trellum.validation.checks.columns import _check_columns
from trellum.validation.checks.datasource import (
    _check_dataset_id_integrity,
    _check_datasource_filterbar,
    _check_slider_filter_columns,
)
from trellum.validation.checks.effectiveness import _check_filter_effectiveness
from trellum.validation.checks.live_query import _check_live_queries
from trellum.validation.checks.metrics import _check_metrics
from trellum.validation.checks.rawhtml import _check_rawhtml
from trellum.validation.checks.scopes import _check_scopes
from trellum.validation.checks.structural import _check_structural
from trellum.validation.checks.theme import _check_theme
from trellum.validation.checks.visibility import _check_toggle_visible
from trellum.validation.checks.yaml_schema import _check_yaml_schema
from trellum.validation.result import (
    Check,
    ValidationResult,
    _apply_dataset_suppressions,
    _apply_suppressions,
    _deduplicate,
    _mark_matrix_suppressions,
)
from trellum.validation.walk import (
    _all_components,
    _get_filterable_types,
    _iter_all_sections,
)

# `_get_filterable_types` is re-exported rather than used here: it was
# importable from `trellum.validation` when this module was one file, and
# test_validation.py imports it from there. Splitting the module must not
# move anything anyone already reaches for.
__all__ = ["validate_report", "Check", "ValidationResult", "_get_filterable_types"]


def validate_report(
    ctx: Any,
    *,
    events: list[dict] | None = None,
    details: dict | None = None,
) -> ValidationResult:
    """Run all validation checks on a completed ReportContext.

    Call after ``report.generate(ctx)`` and before ``render_report()``.
    ``details`` (the output of ``trellum.reporting.diagnostics.compute_details``)
    enables filter-coverage checks; pass ``None`` to skip them.
    """
    result = ValidationResult(slug=ctx.slug)
    all_sections = _iter_all_sections(ctx)
    comps = _all_components(ctx)

    # Each group registers only once it has returned, so a group that raises is
    # not counted as coverage. Overstating what was examined would be worse than
    # reporting nothing, because the whole point of the count is to be trusted.
    def _run(group: str, fn, *args):
        out = fn(*args)
        result.ran(group)
        return out

    ds_map = _run("data sources and filter bars",
                  _check_datasource_filterbar, ctx, comps, result)
    if ds_map is None:
        ds_map = {}
    _run("dataset id integrity", _check_dataset_id_integrity, comps, ds_map, result)
    _run("slider filter columns", _check_slider_filter_columns, comps, result)
    _run("live queries", _check_live_queries, ctx, comps, result)
    _run("raw html lifecycle", _check_rawhtml, comps, result)
    _run("theme", _check_theme, ctx, comps, result)
    _run("annotations", _check_annotations, ctx, comps, events, result)
    _run("columns", _check_columns, comps, ds_map, result)
    _run("metrics", _check_metrics, ctx, comps, ds_map, result)
    _run("toggle and visible", _check_toggle_visible, comps, result)
    _run("scopes", _check_scopes, ctx, result)
    _run("report.yaml schema", _check_yaml_schema, ctx, result)
    _run("structure", _check_structural, ctx, comps, all_sections, result)
    if details is not None:
        _run("filter effectiveness", _check_filter_effectiveness, ctx, details, result)

    result.checks = _deduplicate(result.checks)

    for item in getattr(ctx, "extra_validation_checks", None) or getattr(
        ctx, "_extra_validation_checks", []
    ):
        if not isinstance(item, dict):
            continue
        result.add(
            item.get("id", "data-check"),
            item.get("level", "info"),
            item.get("message", ""),
            component=item.get("component", ""),
            section=item.get("section", ""),
            dataset_id=item.get("dataset_id", ""),
        )

    val_cfg = ctx.config.get("validation", {})
    suppress = val_cfg.get("suppress", [])
    suppress_per_dataset = val_cfg.get("suppress_per_dataset", {})
    accept_inactive_filters = val_cfg.get("accept_inactive_filters", {})
    if suppress:
        result.checks = _apply_suppressions(result.checks, suppress)
    if suppress_per_dataset:
        result.checks = _apply_dataset_suppressions(result.checks, suppress_per_dataset)
    if details is not None and (suppress or suppress_per_dataset or accept_inactive_filters):
        _mark_matrix_suppressions(
            details, suppress, suppress_per_dataset, accept_inactive_filters
        )

    return result
