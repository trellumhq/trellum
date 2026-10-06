"""Theme compatibility: hardcoded colours and contrast."""

from __future__ import annotations

import re
from typing import Any

from trellum.validation.result import ValidationResult


def _check_theme(ctx: Any, comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Category 4: Theme compatibility."""
    from trellum.components.layout import RawHTML as RawHTMLComp
    from trellum.themes import THEME_REGISTRY

    theme_name = ctx.config.get("theme")
    if theme_name and theme_name not in THEME_REGISTRY:
        result.fail(
            "theme-invalid",
            f"report.yaml theme '{theme_name}' is not in THEME_REGISTRY. "
            f"Available: {list(THEME_REGISTRY.keys())}",
        )

    _CACHED_THEME = re.compile(
        r'(?:^|;)\s*(?:var|let|const)\s+\w+\s*=\s*getThemeColors\s*\(\s*\)',
    )

    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp) or not comp.js:
            continue

        lines = comp.js.split("\n")
        in_function = 0
        for line in lines:
            stripped = line.strip()
            in_function += stripped.count("{") - stripped.count("}")
            if in_function <= 0 and _CACHED_THEME.search(stripped):
                result.warn(
                    "rawhtml-cached-theme-colors",
                    "RawHTML JS caches getThemeColors() at top level. "
                    "These values go stale on theme switch.",
                    component="RawHTML", section=sec,
                )
                break

    _HEX_BG_CHARTJS = re.compile(
        r"(?:backgroundColor|pointBackgroundColor)\s*:\s*['\"]#[0-9a-fA-F]{6}['\"]"
    )
    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp):
            continue
        if not comp.js:
            continue
        matches = _HEX_BG_CHARTJS.findall(comp.js)
        bg_only = [m for m in matches if "transparent" not in m.lower()]
        if bg_only:
            result.warn(
                "rawhtml-hardcoded-colors",
                "RawHTML JS passes hardcoded hex background colors to Chart.js. "
                "Use getThemeColors() instead.",
                component="RawHTML", section=sec,
            )

    # ── Strict hex-color check (FAIL) ──────────────────────────────────────
    # Any `#RRGGBB` or `#RGB` literal in RawHTML JS or HTML is a fail —
    # hardcoded hex breaks theme switching (the chart/text stays the old
    # palette while the rest of the report repaints). Use:
    #   - `fw.getThemeColors().chart_colors[i]` for chart palette
    #   - CSS custom properties (`var(--accent-red)`, `var(--text-main)`,
    #     `var(--bg-card)` …) for HTML/CSS styling
    #   - `var(--accent-green)` / `var(--accent-red)` for semantic green/red
    # Only pure `#fff` / `#000` (and their 6-digit forms) are accepted —
    # they're used for text-on-color overlays where the color is forced
    # for contrast regardless of theme.
    # Suppress globally for a report with `validation.suppress:
    # [rawhtml-hardcoded-hex]` in report.yaml when a hex is genuinely
    # intentional (e.g. an SVG icon stroke that should never theme).
    _HEX_LITERAL = re.compile(r"#([0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\b")
    _HEX_WHITELIST = {"fff", "ffffff", "000", "000000"}
    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp):
            continue
        offenders: set[str] = set()
        for source in (comp.js or "", comp.html or ""):
            for m in _HEX_LITERAL.findall(source):
                if m.lower() not in _HEX_WHITELIST:
                    offenders.add("#" + m)
        if offenders:
            shown = sorted(offenders)[:8]
            extra = "" if len(offenders) <= 8 else f" (+{len(offenders) - 8} more)"
            result.fail(
                "rawhtml-hardcoded-hex",
                f"RawHTML contains hardcoded hex color(s) {shown}{extra}. "
                f"Hex literals break theme switching. Use "
                f"fw.getThemeColors().chart_colors[i] for chart palettes "
                f"or CSS vars (`var(--accent-red)`, `var(--text-main)`, "
                f"`var(--bg-card)`) for HTML/CSS. Only `#fff` / `#000` are "
                f"accepted (pure white/black text-on-color overlays). To "
                f"silence intentionally, add `rawhtml-hardcoded-hex` to "
                f"`validation.suppress` in report.yaml.",
                component="RawHTML", section=sec,
            )

    # ── CSS variable strings in Chart.js color properties (WARN) ──────────
    # `color: 'var(--...'` passes a literal string to Chart.js, which has no
    # CSS resolver. The value is treated as an unknown/invalid color (renders
    # as black or transparent). Fix:
    #   - `fw.getThemeColors().tick_color` / `.grid_color` for axis colors
    #   - `getComputedStyle(document.documentElement).getPropertyValue('--name').trim()`
    #     to resolve the CSS variable before passing to Chart.js
    _CSS_VAR_IN_JS = re.compile(
        r"""(?:color|borderColor|backgroundColor)\s*:\s*['"]var\s*\(--"""
    )
    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp) or not comp.js:
            continue
        if _CSS_VAR_IN_JS.search(comp.js):
            result.warn(
                "rawhtml-css-var-in-chartjs",
                "RawHTML JS passes a CSS variable string (e.g. `color: 'var(--text-main)'`) "
                "to a Chart.js color property. Chart.js does not resolve CSS custom "
                "properties — the string is used as-is (invalid color). Use "
                "`fw.getThemeColors().tick_color` / `.grid_color` for axis colors, or "
                "`getComputedStyle(document.documentElement).getPropertyValue('--name').trim()` "
                "to resolve the CSS variable before passing it to Chart.js.",
                component="RawHTML", section=sec,
            )

    # ── Hardcoded rgba?() color literals in RawHTML JS (WARN) ─────────────
    # rgba(r,g,b,a) / rgb(r,g,b) literals in RawHTML JS are theme-opaque —
    # they do not change when the user switches themes. Fix:
    #   - `fw.getThemeColors().grid_color` for Chart.js grid line color
    #   - `fw.getThemeColors().tick_color` for axis tick and legend label colors
    #   - `fw.getThemeColors().chart_colors[i]` for dataset border/fill colors
    #   - CSS vars in HTML/CSS style contexts (resolved at paint time)
    # Exception: `rgba(0,0,0,0)` is a universally transparent placeholder and
    # is not flagged. Intentional semantic annotation colors (e.g. a fixed
    # purple campaign marker, a gold holiday line) can be suppressed per-report
    # with `rawhtml-hardcoded-rgba` in `validation.suppress` in report.yaml.
    _RGBA_LITERAL = re.compile(r"\brgba?\s*\(\s*\d")
    _RGBA_TRANSPARENT = re.compile(r"\brgba?\s*\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)")
    for comp, sec in comps:
        if not isinstance(comp, RawHTMLComp) or not comp.js:
            continue
        js_no_transparent = _RGBA_TRANSPARENT.sub("", comp.js)
        matches = _RGBA_LITERAL.findall(js_no_transparent)
        if matches:
            result.warn(
                "rawhtml-hardcoded-rgba",
                f"RawHTML JS contains {len(matches)} hardcoded rgba?()/rgb() color "
                "literal(s) that won't update on theme switch. Use "
                "`fw.getThemeColors().grid_color` for grid lines, "
                "`fw.getThemeColors().tick_color` for axis ticks / legend labels, "
                "`fw.getThemeColors().chart_colors[i]` for dataset colors. "
                "For intentional fixed-color semantic annotations (e.g. campaign "
                "or holiday markers), suppress with `rawhtml-hardcoded-rgba` in "
                "`validation.suppress` in report.yaml.",
                component="RawHTML", section=sec,
            )
