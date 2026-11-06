"""Toggle and Visible: controls that hide things."""

from __future__ import annotations

import re
from typing import Any

from trellum.validation.result import ValidationResult
from trellum.validation.walk import (
    _walk_components,
)


def _check_toggle_visible(comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Category 7: Toggle / Visible wiring."""
    toggle_map, toggle_default, visible_list, rawhtml_list = _collect_toggle_components(comps, result)
    visible_targets_used = _check_visible_components(
        visible_list, toggle_map, toggle_default, result,
    )
    _check_rawhtml_toggle_references(
        comps, rawhtml_list, toggle_map, visible_targets_used, result,
    )
    visible_targets_used.update(_check_shared_stacked_controls(comps, result))
    for tid in toggle_map:
        if tid not in visible_targets_used:
            result.info(
                "toggle-unused",
                f"Toggle(id='{tid}') exists but no Visible or custom JS references it.",
                component="Toggle",
            )


def _collect_toggle_components(comps, result):
    from trellum.components.charts.bar import StackedBar
    from trellum.components.charts.line_area import LineChart
    from trellum.components.controls import Toggle
    from trellum.components.layout import RawHTML as RawHTMLComp
    from trellum.components.layout import Visible

    toggle_map: dict[str, list[str]] = {}
    toggle_default: dict[str, str] = {}
    visible_list: list[tuple[Any, str]] = []
    rawhtml_list: list[tuple[Any, str]] = []

    for comp, sec in comps:
        if (
            isinstance(comp, (LineChart, StackedBar))
            and comp.dataset_id
            and comp.stack_by_options
            and not comp.stack_toggle_id
        ):
            # Chart lookup and URL state use trimmed button text as the key.
            # The first option also supplies the default selection.
            invalid_labels = [
                label for label in comp.stack_by_options
                if not isinstance(label, str) or not label or label != label.strip()
            ]
            if invalid_labels:
                result.fail(
                    "chart-stack-label-invalid",
                    f"Chart '{comp.title}' has automatic breakdown labels "
                    f"{invalid_labels!r} that cannot restore the intended selection "
                    "from a saved URL. Use nonempty string labels without leading "
                    "or trailing whitespace in stack_by_options, including the "
                    "first (default) option.",
                    component=type(comp).__name__, section=sec,
                )
        if isinstance(comp, Toggle):
            toggle_map[comp.id] = list(comp.options)
            toggle_default[comp.id] = comp.default or (comp.options[0] if comp.options else "")
        elif isinstance(comp, Visible):
            visible_list.append((comp, sec))
        elif isinstance(comp, RawHTMLComp) and comp.js:
            rawhtml_list.append((comp, sec))
    return toggle_map, toggle_default, visible_list, rawhtml_list


def _check_visible_components(visible_list, toggle_map, toggle_default, result):
    visible_targets_used: set[str] = set()

    for comp, sec in visible_list:
        target = comp.toggle_target
        value = comp.toggle_value
        visible_targets_used.add(target)

        if target not in toggle_map:
            result.fail(
                "visible-target-missing",
                f"Visible toggle_target '{target}' does not match any Toggle id. "
                f"Available: {list(toggle_map.keys())}",
                component="Visible", section=sec,
            )
        elif value not in toggle_map[target]:
            result.warn(
                "visible-value-missing",
                f"Visible toggle_value '{value}' is not in Toggle '{target}' options "
                f"{toggle_map[target]}. Section will never be shown.",
                component="Visible", section=sec,
            )
        else:
            # Warn when a live chart starts hidden (non-default Visible).
            # Charts rendered while hidden get a 0×0 canvas from Chart.js; the
            # framework calls chart.resize() when the toggle shows them, so they
            # will display correctly — but only after the user first switches the
            # toggle. Suppress if intentional.
            default_val = toggle_default.get(target, "")
            if value != default_val:
                live_children = [
                    c for c, _ in _walk_components(comp.children)
                    if getattr(c, "dataset_id", None)
                ]
                if live_children:
                    names = ", ".join(sorted({type(c).__name__ for c in live_children}))
                    result.warn(
                        "live-chart-in-non-default-visible",
                        f"Visible(toggle_target='{target}', toggle_value='{value}') "
                        f"contains live components ({names}) that start hidden. "
                        f"The chart canvas is 0×0 until the user switches the toggle; "
                        f"the framework resizes it on show. Suppress if intentional.",
                        component="Visible", section=sec,
                    )

    return visible_targets_used


def _check_rawhtml_toggle_references(comps, rawhtml_list, toggle_map,
                                     visible_targets_used, result):
    _TOGGLE_ID_HTML = re.compile(r'data-toggle-id\s*=\s*["\']([^"\']+)["\']')
    all_html_toggle_ids: set[str] = set()
    for comp, _ in comps:
        html = getattr(comp, "html", None)
        if html:
            all_html_toggle_ids.update(_TOGGLE_ID_HTML.findall(html))

    _TOGGLE_COMPLETE = re.compile(
        r"""data-toggle-id\s*=\s*(?:"""
        r"""\\?["']([^"']+)\\?["']"""   # complete ID in one string literal
        r""")"""
    )

    for comp, sec in rawhtml_list:
        complete_ids: set[str] = set()
        if comp.html:
            complete_ids.update(_TOGGLE_ID_HTML.findall(comp.html))

        for m in _TOGGLE_COMPLETE.finditer(comp.js):
            ref_id = m.group(1)
            end_pos = m.end()
            rest = comp.js[end_pos:end_pos + 5].lstrip()
            if rest.startswith("+"):
                continue
            complete_ids.add(ref_id)

        for ref_id in complete_ids:
            visible_targets_used.add(ref_id)
            if ref_id not in toggle_map and ref_id not in all_html_toggle_ids:
                result.warn(
                    "rawhtml-toggle-id-mismatch",
                    f"RawHTML JS references data-toggle-id='{ref_id}' but no "
                    f"Toggle(id='{ref_id}') exists.",
                    component="RawHTML", section=sec,
                )


def _check_shared_stacked_controls(comps, result):
    """Opt-in shared IDs must be report-unique, including inactive report scopes.

    The existing visibility walker flattens report scopes. Until it supplies
    scope identity, this narrow composition deliberately requires report-wide
    unique native owners; legacy local controls retain their diagnostics.
    """
    from trellum.components.charts.bar import StackedBar, stack_option_problems
    from trellum.components.charts.line_area import LineChart
    from trellum.components.controls import Toggle

    used = set()
    for chart, section in comps:
        if not isinstance(chart, StackedBar):
            continue
        # Sizing problems are dispatched by chart_options; control diagnostics
        # belong here so they also run without the optional shared hook.
        for check_id, message in stack_option_problems(chart):
            if check_id != "stacked-bar-height-invalid":
                result.fail(check_id, message, component="StackedBar", section=section)
        if getattr(chart, "show_controls", True) is not False or not chart.dataset_id:
            continue
        requests = []
        if chart.stack_by_options:
            requests.append((chart.stack_toggle_id, list(chart.stack_by_options)))
        if chart.normalize_toggle:
            requests.append((chart.normalize_toggle_id, ["Absolute", "Percentage"]))
        for tid, expected in requests:
            if not isinstance(tid, str) or not tid.strip():
                continue
            owners = [c for c, _ in comps if isinstance(c, Toggle) and c.id == tid]
            local_duplicates = [c for c, _ in comps if isinstance(c, (StackedBar, LineChart))
                and c.dataset_id and getattr(c, "show_controls", True) is not False and
                ((c.stack_by_options and c.stack_toggle_id == tid) or
                 (getattr(c, "normalize_toggle", False) and c.normalize_toggle_id == tid))]
            if len(owners) != 1 or local_duplicates:
                result.fail("stacked-bar-control-owner-invalid",
                    f"Shared StackedBar control '{tid}' requires exactly one native Toggle and no chart-local copy. "
                    "This opt-in composition requires report-wide unique IDs, including inactive report scopes; "
                    "use a distinct shared ID for each independent scope/pair.", component="StackedBar", section=section)
            elif (list(owners[0].options) != expected or not expected or
                  any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in expected) or
                  owners[0].default not in expected):
                result.fail("stacked-bar-control-options-invalid",
                    f"Shared StackedBar control '{tid}' requires exactly {expected!r} in order and a selected default from those choices.",
                    component="StackedBar", section=section)
            else:
                used.add(tid)
    return used
