"""Filter labels supply native and plugin accessible names."""
from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult


def _check_filter_labels(comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    from trellum.components.filterable import FilterBar

    for comp, section in comps:
        if not isinstance(comp, FilterBar):
            continue
        for spec in comp.filters:
            label = spec.get("label", spec.get("column", ""))
            if label is None or not str(label).strip():
                result.warn(
                    "filter-label-empty",
                    f"Filter '{spec.get('column', '')}' needs a non-empty label "
                    "to name its native control and plugin focus targets.",
                    component="FilterBar", section=section, dataset_id=comp.dataset_id,
                )
