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
    from trellum.components.controls import Toggle
    from trellum.components.layout import RawHTML as RawHTMLComp
    from trellum.components.layout import Visible

    toggle_map: dict[str, list[str]] = {}
    toggle_default: dict[str, str] = {}
    visible_list: list[tuple[Any, str]] = []
    rawhtml_list: list[tuple[Any, str]] = []

    for comp, sec in comps:
        if isinstance(comp, Toggle):
            toggle_map[comp.id] = list(comp.options)
            toggle_default[comp.id] = comp.default or (comp.options[0] if comp.options else "")
        elif isinstance(comp, Visible):
            visible_list.append((comp, sec))
        elif isinstance(comp, RawHTMLComp) and comp.js:
            rawhtml_list.append((comp, sec))

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

    for tid in toggle_map:
        if tid not in visible_targets_used:
            result.info(
                "toggle-unused",
                f"Toggle(id='{tid}') exists but no Visible or custom JS references it.",
                component="Toggle",
            )
