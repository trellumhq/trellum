"""The RawHTML lifecycle."""

from __future__ import annotations

import re
from typing import Any

from trellum.validation.result import ValidationResult
from trellum.validation.walk import (
    _collect_all_html_ids,
)


def _check_rawhtml(comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Category 3: RawHTML DOM consistency."""
    from trellum.components.layout import RawHTML as RawHTMLComp
    from trellum.components.layout import Visible as VisibleComp

    _GET_BY_ID = re.compile(r'getElementById\s*\(\s*["\']([^"\']+)["\']\s*\)')
    _QS_ID = re.compile(r'querySelector\s*\(\s*["\']#([^"\']+)["\']\s*\)')
    _NEW_CHART = re.compile(r'new\s+Chart\s*\(')
    _DESTROY = re.compile(r'\.destroy\s*\(')
    _RENDER_ALL = re.compile(r'window\.renderAll\s*=')
    _INIT_TOGGLE_VIS = re.compile(r'window\._initToggleVis\s*=')
    _RAF = re.compile(r'\brequestAnimationFrame\b')
    _THEME_LISTENER = re.compile(r'fw-theme-change')
    _DATA_KEY_REF = re.compile(
        # Accept references like `_reportData.foo`, `D.foo`, `fw.data.foo`,
        # `(fw.data || {}).foo`, `fw['data'].foo`. The closing-paren form is
        # the common defensive idiom — match it explicitly so the
        # rawhtml-data-key-unused check doesn't fire false positives.
        r'(?:_reportData|fw\s*\.\s*data|fw\s*\[\s*["\']data["\']\s*\]|D)'
        r'\s*(?:\|\|\s*\{\s*\})?\s*\)?\s*\.\s*(\w+)'
    )
    _NEW_SLIMSELECT = re.compile(r'new\s+SlimSelect\s*\(')
    _FW_FILTERENGINE_DIRECT = re.compile(r'window\._fwFilterEngine\b')
    # Polling pattern: a setTimeout used to retry a function while
    # waiting for a framework variable to appear. We require:
    #   1. An `if (!x)` / `if (typeof x === 'undefined')` guard
    #      immediately followed (no statement or block boundaries in
    #      between) by setTimeout, AND
    #   2. setTimeout's first argument is a *bare identifier* (the
    #      polling function recurring on itself) — not a function
    #      expression. Debounce / animation timers use the latter form
    #      and shouldn't trip this check.
    _POLLING_INIT = re.compile(
        r"(?:if\s*\(\s*!\s*\w+\s*\)|if\s*\(\s*typeof\s+\w+\s*===\s*['\"]undefined['\"]\s*\))"
        r"\s*\{?\s*[^{};]*?setTimeout\s*\(\s*\w+\s*,",
        re.DOTALL,
    )
    _KPI_LIKE_HTML = re.compile(r'class\s*=\s*["\'][^"\']*\bkpi-(?:value|label|card)\b', re.IGNORECASE)
    _REIMPL = [
        (re.compile(r"function\s+fmt\$"), "fmt$"),
        (re.compile(r"function\s+fmtCompact\b"), "fmtCompact"),
        (re.compile(r"function\s+fmtCompact\$"), "fmtCompact$"),
        (re.compile(r"function\s+buildAnnotations\b"), "buildAnnotations"),
    ]

    global_html_ids = _collect_all_html_ids(comps)
    has_visible_in_report = any(isinstance(c, VisibleComp) for c, _ in comps)

    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp):
            continue
        if not comp.js:
            continue

        js_ids = set(_GET_BY_ID.findall(comp.js)) | set(_QS_ID.findall(comp.js))

        orphan_ids = js_ids - global_html_ids
        if orphan_ids:
            result.warn(
                "rawhtml-canvas-mismatch",
                f"RawHTML JS references DOM id(s) {sorted(orphan_ids)} "
                f"not found in any RawHTML HTML block.",
                component="RawHTML", section=sec,
            )

        if comp.data_key:
            refs = set(_DATA_KEY_REF.findall(comp.js))
            if comp.data_key not in refs:
                result.warn(
                    "rawhtml-data-key-unused",
                    f"RawHTML data_key '{comp.data_key}' is set but JS does not "
                    f"appear to reference it.",
                    component="RawHTML", section=sec,
                )

        _FILTER_SUBSCRIBE = re.compile(
            r"(?:_fwFilterEngine|fw\.filterEngine)\s*\.\s*subscribe"
        )
        has_chart = bool(_NEW_CHART.search(comp.js))
        if has_chart:
            uses_filter_engine = bool(_FILTER_SUBSCRIBE.search(comp.js))
            if not _RENDER_ALL.search(comp.js) and not uses_filter_engine:
                result.warn(
                    "rawhtml-missing-renderall",
                    "RawHTML JS creates Chart.js instances but does not assign "
                    "window.renderAll. Theme switch and auto-refresh will skip custom charts.",
                    component="RawHTML", section=sec,
                )
            if not _THEME_LISTENER.search(comp.js):
                result.warn(
                    "rawhtml-no-theme-change-handler",
                    "RawHTML JS creates Chart.js instances but does not listen for "
                    "'fw-theme-change'. Charts will not update on theme switch.",
                    component="RawHTML", section=sec,
                )
            if not _DESTROY.search(comp.js):
                result.warn(
                    "rawhtml-no-chart-destroy",
                    "RawHTML JS has 'new Chart(' but no '.destroy()' call. "
                    "Charts may leak on theme switch or auto-refresh.",
                    component="RawHTML", section=sec,
                )
            # When the report contains any Visible (toggle-driven container),
            # the chart is likely inside one. The framework calls _renderComps()
            # directly on initial load (NOT renderAll) — so a renderAll-only
            # patch produces a chart that's blank on first load and on URL
            # preload. _updateToggleVis flips display synchronously, so a
            # synchronous render measures canvas at 0×0; rAF defers past the
            # layout pass. See trellum/AGENTS.md "RawHTML chart lifecycle".
            if has_visible_in_report:
                if not _INIT_TOGGLE_VIS.search(comp.js):
                    result.warn(
                        "rawhtml-chart-missing-init-toggle-vis",
                        "RawHTML JS creates Chart.js instances and the report contains a "
                        "Visible component, but the JS does not patch window._initToggleVis. "
                        "Charts inside Visibles render BLANK on initial page load and URL "
                        "preload (renderAll is not called on first load — _renderComps is). "
                        "Patch BOTH window._initToggleVis and window.renderAll.",
                        component="RawHTML", section=sec,
                    )
                if not _RAF.search(comp.js):
                    result.warn(
                        "rawhtml-chart-missing-raf-defer",
                        "RawHTML JS creates Chart.js instances and the report contains a "
                        "Visible component, but the JS does not use requestAnimationFrame. "
                        "_updateToggleVis flips display synchronously; a render running in "
                        "the same tick measures the canvas at 0x0 and the chart is blank. "
                        "Defer renders triggered from _initToggleVis / renderAll with rAF.",
                        component="RawHTML", section=sec,
                    )

        reimpl = [msg for pat, msg in _REIMPL if pat.search(comp.js)]
        if reimpl:
            result.warn(
                "rawhtml-reimpl-utils",
                f"RawHTML JS re-implements framework utilities: {', '.join(reimpl)}.",
                component="RawHTML", section=sec,
            )

        # ── New opinionation checks ─────────────────────────────────────────
        # 1. Custom SlimSelect dropdowns belong in a FilterBar, not RawHTML.
        if _NEW_SLIMSELECT.search(comp.js):
            result.warn(
                "rawhtml-custom-slimselect",
                "RawHTML JS instantiates SlimSelect directly. Use FilterBar "
                "(section-scoped if it should only affect this section) plus "
                "ScopedDataSource for chart-local filters. For cascading "
                "dropdowns add `depends_on` to a dropdown filter spec. "
                "Custom SlimSelect duplicates engine state, breaks URL sync, "
                "and skips theme/clear-filters behaviour.",
                component="RawHTML", section=sec,
            )

        # 2. Direct `window._fwFilterEngine` reference (instead of `fw.filterEngine`)
        #    + setTimeout polling pattern is the legacy hack people copy to wait
        #    for the engine. fw.filterEngine.onReady(dsId, fn) replaces it.
        uses_private_engine = bool(_FW_FILTERENGINE_DIRECT.search(comp.js))
        has_polling = bool(_POLLING_INIT.search(comp.js))
        if uses_private_engine or has_polling:
            reasons = []
            if uses_private_engine:
                reasons.append("references window._fwFilterEngine (private API)")
            if has_polling:
                reasons.append("polls with setTimeout while waiting for the engine")
            result.warn(
                "rawhtml-filterengine-polling",
                "RawHTML JS " + " and ".join(reasons) + ". Replace with "
                "fw.filterEngine.onReady('<dsId>', function() { ... }) and use "
                "fw.filterEngine.* (the stable public API) for subscribe/getFiltered.",
                component="RawHTML", section=sec,
            )

        # 3. Custom KPI cards in RawHTML — `KpiRow(dataset_id=...)` covers it.
        if comp.html and _KPI_LIKE_HTML.search(comp.html):
            result.warn(
                "rawhtml-custom-kpi",
                "RawHTML HTML defines .kpi-value / .kpi-label / .kpi-card classes "
                "that mimic a KPI card. Use KpiRow(dataset_id=...) — it supports "
                "agg='sum'/'ratio'/'count'/'abssum' with format='currency'/"
                "'chips'/'percent'/'number' and reacts to filters natively.",
                component="RawHTML", section=sec,
            )
