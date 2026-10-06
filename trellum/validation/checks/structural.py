"""Report structure: sections, ordering and shape."""

from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult
from trellum.validation.walk import (
    _comp_name,
    _df_columns,
    _is_section_scoped_pair,
)


def _check_structural(ctx: Any, comps: list[tuple[Any, str]], sections: list[dict], result: ValidationResult) -> None:
    """Category 10: Structural / convention checks."""
    from trellum.components.filterable import DataSource, FilterBar

    _DATE_COLS = {"event_date", "date", "day", "report_date"}

    for sec in sections:
        title = sec.get("title", "")
        if sec.get("default_collapsed") and not sec.get("collapsible"):
            result.warn(
                "default-collapsed-not-collapsible",
                f"Section '{title}' sets default_collapsed=True but "
                f"collapsible=False, so the flag is ignored. Set "
                f"collapsible=True to make the section foldable.",
                component="Section", section=title,
            )
        if not title:
            continue
        # Sticky-positioning concern only applies to FilterBars (and
        # the DataSources they're paired with). A titled section that
        # contains DataSources WITHOUT a FilterBar has no positioning
        # issue — the DataSource is invisible chart-binding data.
        # Only warn when there's a FilterBar misplaced in a titled
        # section AND it's not in the canonical section-scoped pair.
        section_comps = sec.get("components", [])
        has_fb = any(isinstance(c, FilterBar) for c in section_comps)
        if not has_fb:
            continue
        if _is_section_scoped_pair(section_comps):
            continue  # canonical [DS(s), FB, ...content] layout — fine
        for comp in section_comps:
            if not isinstance(comp, (DataSource, FilterBar)):
                continue
            result.warn(
                "datasource-not-in-untitled-section",
                f"{_comp_name(comp)} is in a section titled '{title}' "
                f"with a FilterBar that isn't in the canonical "
                f"section-scoped pair layout. Either move the DataSource "
                f"+ FilterBar to an untitled top section (main FilterBar "
                f"pattern), or place them as [DataSource(s), FilterBar, "
                f"...content] at the start of this section (section-"
                f"scoped FilterBar pattern).",
                component=_comp_name(comp), section=title,
            )

    # Date-range coverage: skip the warning when the report has an
    # explicit date scope set elsewhere (e.g. `ab_test.start_date` /
    # `end_date` in report.yaml drives the window) OR when ANY FilterBar
    # in the report — main or section — already provides a `date_range`
    # filter (one is enough; it doesn't have to live on every FilterBar).
    config = getattr(ctx, "config", {}) or {}
    # `ab_test` is one experiment's dict, or a list of them for a report
    # carrying several experiments on one page.
    ab_raw = config.get("ab_test") or {}
    ab_entries = ab_raw if isinstance(ab_raw, list) else [ab_raw]
    has_ab_window = any(
        isinstance(e, dict) and e.get("start_date") for e in ab_entries
    )

    fb_comps = [(c, s) for c, s in comps if isinstance(c, FilterBar)]
    any_date_range = any(
        any(f.get("type") == "date_range" for f in fb.filters)
        for fb, _ in fb_comps
    )
    if has_ab_window or any_date_range:
        return  # date scope is covered elsewhere; suppress all warnings

    for fb, sec in fb_comps:
        filter_types = {f.get("type", "dropdown") for f in fb.filters}
        if "date_range" not in filter_types:
            fb_cols = _df_columns(fb.df)
            if fb_cols & _DATE_COLS:
                result.info(
                    "missing-date-range-filter",
                    f"FilterBar for '{fb.dataset_id}' has date columns "
                    f"{sorted(fb_cols & _DATE_COLS)} but no date_range filter "
                    f"and the report has no other date scope.",
                    component="FilterBar", section=sec,
                )
