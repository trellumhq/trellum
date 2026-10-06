"""Assembles the client-side JS runtime that every report carries.

The JavaScript lives in ``static/js/runtime/*.js`` -- real files, read at build
time and inlined into ``index.html``. It used to be one 2,313-line Python
f-string in this module, for the sake of three interpolated values; those three
now travel as a small generated ``_fwConfig`` object and the rest is plain JS.

Provides:
- Number formatting (fmtCompact, fmtCompact$, fmtChips, fmtPercent, fmt$)
- Auto-refresh with data.json fetching
- UI state preservation across refreshes
- Chart.js global defaults
"""

from __future__ import annotations

import json

from trellum.assets import load_js
from trellum.themes.theme import Theme

#: Concatenation order IS execution order. Every module below lands in one
#: shared IIFE scope, and several of them do work at load -- registering
#: observers, binding document listeners, defining globals a later module
#: reads. So this tuple is a dependency contract, not an alphabetical
#: convenience: ``fw_namespace`` must be last because it references everything,
#: ``storage`` first because the theme module reads localStorage through it.
#:
#: ``test_js_runtime.py`` asserts this list and the directory agree, so a new
#: file cannot sit there silently never loaded.
RUNTIME_MODULES: tuple[str, ...] = (
    "storage",
    "formatters",
    "theme",
    "autofill",
    "chart_defaults",
    "annotations",
    "filter_engine",
    "cross_filter",
    "url_sync",
    "color_registry",
    "aggregate",
    "live_wrap",
    "live_query",
    "state",
    "auto_refresh",
    "ui_handlers",
    "chunk_loader",
    "csv_download",
    "export",
    "fw_namespace",
)


def host_flag_scripts(extensions: dict, slug: str = "") -> tuple[str, str]:
    """The host-extension script fragments of a report page.

    ``window._fwHasHost`` tells client-side code whether a control plane
    fronts this report at all; ``window._fwLiveQueryUrl`` is where declared
    live queries may be POSTed (see docs/COMPATIBILITY.md). Both land at
    build time, exactly like ``nav_html``: a host that serves reports builds
    them with ``FW_EXTENSIONS_JSON``, and a standalone build stamps
    false/absent — so a page without a host never issues a request to a
    server that isn't there.

    ``live_query_url`` is a URL *template*: extensions are configured per
    project/studio while the endpoint is per-report, so the host writes a
    literal ``{slug}`` placeholder and the build — the moment the report
    knows its own slug — substitutes it. Plain text replacement, no URL
    encoding of the braces.

    Returns ``(head_flag_script, body_script_tags)``.
    """
    body_tags = "\n".join(
        f'<script src="{src}" defer></script>' for src in extensions["scripts"]
    )
    live_url = str(extensions.get("live_query_url") or "")
    if live_url:
        live_url = live_url.replace("{slug}", slug)
    has_host = bool(extensions["nav_html"] or extensions["scripts"] or live_url)
    flag = f"window._fwHasHost={'true' if has_host else 'false'};"
    if live_url:
        flag += f"window._fwLiveQueryUrl={json.dumps(live_url)};"
    return f"<script>{flag}</script>", body_tags


def generate_js_runtime(
    default_theme_name: str,
    themes: dict[str, Theme],
    refresh_seconds: int = 0,
) -> str:
    """Generate the shared JS runtime code.

    Args:
        default_theme_name: Name of the report's default theme.
        themes: Full theme registry mapping name -> Theme instance.
        refresh_seconds: Auto-refresh interval. 0 = no auto-refresh.
    """
    themes_js_obj = {}
    for tname, tobj in themes.items():
        themes_js_obj[tname] = {
            "chart_colors": tobj.chart_colors,
            "grid_color": tobj.grid_color,
            "tick_color": tobj.tick_color,
        }

    # The only build-time knowledge the runtime has. Everything else in the
    # page is data (data.json) or static JS, which is what lets those modules
    # be plain files.
    config = {
        "defaultTheme": default_theme_name,
        "themes": themes_js_obj,
        "refreshMs": refresh_seconds * 1000 if refresh_seconds > 0 else 0,
    }

    parts = [f"    var _fwConfig = {json.dumps(config)};"]
    parts += [load_js(f"runtime/{name}.js") for name in RUNTIME_MODULES]
    body = "\n".join(parts)

    return f"""
<script>
(function() {{
    'use strict';

{body}
}})();
</script>
"""
