"""HTML builder: assembles component trees into complete HTML documents.

All reports use the same pattern:
  1. Python generates an HTML shell with empty containers (canvas, KPI divs)
  2. Python writes all chart/KPI data to data.json
  3. Browser fetches data.json and populates everything client-side
  4. Loading overlay is shown until data is loaded

This module is fully generic — it never imports or names specific component
types.  Components self-describe via ``render_html()``, ``css()``, and
``client_js()`` on the ``Component`` base class.
"""

from __future__ import annotations

import html as html_lib
import os
import time
from datetime import datetime, timezone
from typing import Any

import orjson

import trellum
from trellum.assets import load_css, load_js
from trellum.components.base import RenderContext, collect_asset_parts
from trellum.licensing import inject_source_notice, source_notice_html
from trellum.project import load_extensions
from trellum.rendering.artifacts import (
    _cron_to_seconds,
    _gc_stale_chunks,
    _write_live_queries,
    _write_manifest,
    _write_meta,
)
from trellum.rendering.cdn import build_cdn_tags
from trellum.rendering.js_runtime import generate_js_runtime, host_flag_scripts
from trellum.rendering.sections import _inject_nav, _render_section_list
from trellum.themes import BUILTIN_THEMES, DEFAULT_THEME_NAME, THEME_REGISTRY, effective_theme_name
from trellum.themes.theme import Theme

#: ``?only=<section id>``: render ONE block -- the section with that id, its
#: ancestors, and the page's shared controls (the sticky FilterBar sits
#: outside ``.fw-section``) -- with the page chrome dropped (``body.fw-only``,
#: styled in static/css/base.css). That is what an ``<iframe>`` onto a single
#: block loads, and what a deep link to one section opens. Emitted inline
#: after the sections and before the data loader, so it runs once the nodes
#: exist and before anything is drawn -- no flash. An id this build does not
#: have leaves the page whole rather than blank: a stale link degrades to the
#: report it came from.
_ONLY_SECTION_SCRIPT = """<script>
(function() {
    var only = new URLSearchParams(location.search).get('only');
    if (!only) return;
    var target = document.getElementById(only);
    if (!target) return;
    document.body.classList.add('fw-only');
    document.querySelectorAll('.fw-section').forEach(function(sec) {
        if (sec !== target && !sec.contains(target)) sec.style.display = 'none';
    });
})();
</script>"""

# ═══════════════════════════════════════════════════════════════
# Base CSS (framework shell only — component CSS comes from
# each Component.css() classmethod)
# ═══════════════════════════════════════════════════════════════

def _generate_base_css(
    default_theme_name: str,
    themes: dict[str, Theme],
) -> str:
    theme_blocks = []
    for name, theme in themes.items():
        selector = f'[data-theme="{name}"]'
        theme_blocks.append(theme.to_css_vars(selector))

    theme_css = "\n".join(theme_blocks)

    # The static half lives in static/css/base.css; only the per-theme
    # custom-property blocks are generated, one per registered theme.
    return theme_css + "\n\n" + load_css("base.css")


# ═══════════════════════════════════════════════════════════════
# Generic data-driven client-side data loader JS
# ═══════════════════════════════════════════════════════════════

def _generate_data_loader_js() -> str:
    """Generate the generic JS that fetches data.json and dispatches to
    component renderers registered on ``window._fwRenderers``.

    Supports scope splitting: when ``_scopeFiles`` is present in data.json,
    non-default scope data is loaded lazily from separate JSON files on
    scope switch, reducing initial payload size for multi-scope reports.
    """
    return f"\n<script>\n{load_js('data_loader.js')}\n</script>"


def _theme_select_html(default_theme_name: str, enabled: bool, themes=None) -> str:
    """The header's theme <select>, empty when switching is off or moot."""
    themes = THEME_REGISTRY if themes is None else themes
    if not enabled or len(themes) < 2:
        return ""
    opts = []
    for tname in themes:
        label = tname.replace("_", " ").title()
        sel_attr = " selected" if tname == default_theme_name else ""
        opts.append(f'<option value="{tname}"{sel_attr}>{label}</option>')
    return (f'<select class="fw-theme-select" id="fwThemeSelect" '
            f'title="Switch theme">{"".join(opts)}</select>')


# ═══════════════════════════════════════════════════════════════
# Top-level render
# ═══════════════════════════════════════════════════════════════

def _flash_script(themes=None) -> str:
    """Flash-prevention: apply a saved 'fw-theme' before first paint.

    Guarded: localStorage throws in sandboxed iframes, and this script runs
    before the runtime's safe helpers exist.

    The saved value must also be checked against the names this page actually
    has CSS for: 'fw-theme' is shared across every report on the origin, so
    it can hold a name written by a build with a different registry. Applying
    it unchecked leaves every var(--...) unresolved -- an unstyled page -- and
    the runtime's own sync ignores unknown saved names rather than correcting
    the attribute.

    No else branch: with nothing (valid) saved, the <html> tag's own
    data-theme stands -- the baked default, or a host's serve-time override
    of it, which a fallback stamp here used to clobber.
    """
    known_themes_js = orjson.dumps(list(THEME_REGISTRY if themes is None else themes)).decode()
    return f"""<script>
(function(){{var v={known_themes_js};var t=null;try{{t=localStorage.getItem('fw-theme');}}catch(e){{}}
if(v.indexOf(t)>=0)document.documentElement.setAttribute('data-theme',t);}})();
</script>"""


def render_report(
    ctx,
    output_dir: str | None = None,
    data: dict | None = None,
    auto_refresh: bool = True,
    validation=None,
    production: bool = False,
    details: dict | None = None,
    gc_stale_chunks: bool = True,
) -> tuple[str, str]:
    """Render a complete report from a ReportContext.

    All reports produce:
      - index.html (HTML shell with empty containers + loading overlay)
      - data.json (component data, fetched client-side)
      - _meta.json (build metadata)

    Args:
        auto_refresh: When False, the JS runtime will not embed the
            ``setInterval`` loop that polls data.json for updates.
        validation: Optional ValidationResult to merge into _meta.json.
        production: When True, pre-generates ``data.json.gz`` for CDN
            deployment.  Skipped in local-dev mode since the dev server
            compresses on the fly.
    """
    out = output_dir or ctx.output_dir
    os.makedirs(out, exist_ok=True)

    theme = ctx.theme
    config = ctx.config
    themes = BUILTIN_THEMES if config.get("kind") == "analysis" else THEME_REGISTRY
    name = config.get("name", "Report")
    if config.get("kind") == "analysis":
        name = html_lib.escape(name, quote=True)
    slug = ctx.slug

    # Host-supplied markup slots. Empty unless something is serving these
    # reports behind its own control plane -- see trellum.project.
    extensions = load_extensions()
    live_registry = getattr(ctx, "live_queries", None) or {}

    # ── Custom HTML mode (for complex dashboards like Pulse) ──
    if ctx.custom_html is not None:
        custom = inject_source_notice(
            _inject_nav(ctx.custom_html, extensions["nav_html"])
        )
        html_path = os.path.join(out, ctx.custom_html_filename)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(custom)

        data_json = ctx.custom_data or data or {}
        json_path = os.path.join(out, ctx.custom_data_filename)
        with open(json_path, "wb") as f:
            f.write(orjson.dumps(
                data_json,
                option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS,
            ))

        _write_meta(out, slug, name, config, validation=validation, details=details)
        _write_live_queries(out, live_registry)
        return html_path, json_path

    # ── Standard component rendering ──
    refresh_seconds = (
        _cron_to_seconds(config.get("schedule", {}).get("cron", ""))
        if auto_refresh else 0
    )

    # ── Scope vs flat rendering ──────────────────────────────
    scope_toggle_html = ""
    raw_js_collected: list[str] = []
    all_render_ctxs: list[RenderContext] = []

    if ctx.has_scopes:
        scope_data_map: dict[str, dict] = {scope: {} for scope in ctx.scopes}
        default_scope = ctx.default_scope
        raw_data_collected: dict[str, Any] = {}

        # Render only the default scope's sections into HTML.
        # Non-default scopes render to collect component data only
        # (HTML discarded). All scopes use a fresh RenderContext
        # starting at fw_c1, so every scope maps to the same DOM
        # element IDs that the default scope rendered. _renderComps
        # on scope switch then overwrites those slots in-place.
        # This contract requires every scope to produce the same
        # section/component count and order.
        t0_scopes = time.time()
        default_render_ctx = RenderContext(theme, live_queries=live_registry)
        all_render_ctxs.append(default_render_ctx)
        sections_html = _render_section_list(
            ctx.scopes[default_scope], default_render_ctx
        )
        scope_data_map[default_scope] = dict(default_render_ctx.component_data)
        raw_js_collected = list(default_render_ctx.raw_js_blocks)
        for rk, rv in default_render_ctx.raw_data_blocks.items():
            raw_data_collected[rk] = rv

        for scope_name in ctx.scopes:
            if scope_name == default_scope:
                continue
            scope_render_ctx = RenderContext(theme, live_queries=live_registry)
            all_render_ctxs.append(scope_render_ctx)
            _render_section_list(ctx.scopes[scope_name], scope_render_ctx)
            scope_data_map[scope_name] = dict(scope_render_ctx.component_data)
            for rk, rv in scope_render_ctx.raw_data_blocks.items():
                raw_data_collected[rk] = rv

        ctx.mark("render_sections", time.time() - t0_scopes)

        scope_btns = []
        for i, (key, label) in enumerate(ctx.scope_labels.items()):
            active = " active" if i == 0 else ""
            scope_btns.append(
                f'<button class="fw-toggle-btn{active}" data-scope-key="{key}">{label}</button>'
            )
        scope_toggle_html = (
            f'<div class="fw-toggle-group fw-scope-toggle" '
            f'data-toggle-id="__scope__">{"".join(scope_btns)}</div>'
        )

        # Write per-scope JSON files for non-default scopes
        scope_files: dict[str, str] = {}
        for scope_name, scope_components in scope_data_map.items():
            if scope_name == default_scope:
                continue
            fname = f"data_{scope_name}.json"
            scope_files[scope_name] = fname
            scope_path = os.path.join(out, fname)
            with open(scope_path, "wb") as f:
                f.write(orjson.dumps(
                    {"components": scope_components},
                    option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS,
                ))

        # Main data.json carries the default scope's components
        report_data = data or {}
        report_data["components"] = scope_data_map.get(default_scope, {})
        report_data["defaultScope"] = default_scope
        report_data["_scopeFiles"] = scope_files
        for rk, rv in raw_data_collected.items():
            report_data[rk] = rv
    else:
        render_ctx = RenderContext(theme, live_queries=live_registry)
        all_render_ctxs.append(render_ctx)
        t0 = time.time()
        sections_html = _render_section_list(ctx.sections, render_ctx)
        ctx.mark("render_sections", time.time() - t0)
        raw_js_collected = list(render_ctx.raw_js_blocks)

        report_data = data or {}
        report_data["components"] = render_ctx.component_data
        for rk, rv in render_ctx.raw_data_blocks.items():
            report_data[rk] = rv

    # ── Collect CSS and JS assets from all rendered component types ──
    all_seen: set[type] = set()
    for rctx in all_render_ctxs:
        all_seen.update(rctx._seen_types)

    # Shared with RenderContext.collect_assets. This was a second copy of that
    # loop, and the copy was the one that actually ran -- so a fix applied to
    # the method (making emission order deterministic) changed nothing here.
    seen_css: set[str] = set()
    seen_js: set[str] = set()
    component_css_parts, component_js_parts = collect_asset_parts(
        all_seen, seen_css, seen_js,
    )

    all_cdn_keys: set[str] = set()
    for rctx in all_render_ctxs:
        all_cdn_keys.update(rctx.collect_cdn_deps())
    all_cdn_keys.update(config.get("extra_cdn", {}).keys())

    # ── Assemble page ────────────────────────────────────────
    subtitle = ctx.header_meta.get("subtitle", "")

    # Determine default theme name and whether the switcher is enabled.
    # report.yaml's own `theme:` wins; otherwise the project's config.yaml
    # `theme:` (effective_theme_name); otherwise the trellum look
    # ("light"/"dark" are the pre-trellum classics, kept for projects that
    # chose them). This must stay in step with trellum.themes.resolve_theme,
    # which is what the Python side hands to components -- a mismatch
    # renders charts for one palette inside a page painted for the other.
    default_theme_name = effective_theme_name(config.get("theme"))
    # An unknown name (a typo like "trellum-light") must not reach the page:
    # the server-side render already fell back to the default palette in the
    # runner, but stamping the raw string into data-theme would leave every
    # var(--...) unresolved -- an unstyled page -- while the build log claims
    # the fallback worked. Validation's `theme-invalid` check reports the typo.
    if default_theme_name not in themes:
        default_theme_name = DEFAULT_THEME_NAME
    theme_switcher_enabled = config.get("theme_switcher", True)

    theme_select_html = _theme_select_html(default_theme_name,
                                           theme_switcher_enabled, themes)

    # Build the header component (use custom if set, else default)
    from trellum.components.header import ReportHeader
    header_component = getattr(ctx, "_header_component", None)
    if header_component is None:
        header_component = ReportHeader(
            name=name,
            subtitle=subtitle,
            slug=slug,
            scope_toggle_html=scope_toggle_html,
            theme_select_html=theme_select_html,
            nav_html=extensions["nav_html"],
        )

    header_render_ctx = RenderContext(theme)
    header_html = header_component.render_html(header_render_ctx)
    header_render_ctx._seen_types.add(type(header_component))

    # Merge header assets into all_seen
    all_seen.update(header_render_ctx._seen_types)

    # Re-collect CSS/JS including header. The seen_* sets carry over, so the
    # header's assets are skipped if some other component already emitted them.
    header_css_parts, header_js_parts = collect_asset_parts(
        [type(header_component)], seen_css, seen_js,
    )
    component_css_parts.extend(header_css_parts)
    component_js_parts.extend(header_js_parts)

    # Re-collect CDN deps including header
    all_cdn_keys.update(header_render_ctx.collect_cdn_deps())
    cdn_tags = build_cdn_tags(all_cdn_keys)

    component_css = "\n".join(component_css_parts)
    component_js_html = "\n".join(
        f"<script>\n{js}\n</script>" for js in component_js_parts
    )

    t0 = time.time()
    base_css = _generate_base_css(default_theme_name, themes)
    js_runtime = generate_js_runtime(
        default_theme_name, themes, refresh_seconds,
    )
    data_loader = _generate_data_loader_js()
    raw_js_html = "\n".join(f"<script>{js}</script>" for js in raw_js_collected)

    flash_script = _flash_script(themes)

    # Host extension slots: `_fwHasHost`, `_fwLiveQueryUrl`, script tags.
    # See host_flag_scripts — no host, no request; `{slug}` resolves here.
    host_flag_script, extension_scripts_html = host_flag_scripts(extensions, slug)

    html = f"""<!DOCTYPE html>
<html lang="en" data-theme="{default_theme_name}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>{name}</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' rx='8' fill='white'/><g stroke='%23c7cbe0' stroke-width='3'><line x1='8' y1='8' x2='8' y2='32'/><line x1='20' y1='8' x2='20' y2='32'/><line x1='32' y1='8' x2='32' y2='32'/></g><circle cx='8' cy='32' r='5' fill='%230D9488'/><circle cx='20' cy='20' r='5' fill='%237C6FE0'/><circle cx='32' cy='8' r='5' fill='%230D9488'/></svg>">
    <!-- PWA: installable as a fullscreen mobile app via "Add to Home Screen". -->
    <!-- Per-report manifest.json (written alongside index.html) sets start_url -->
    <!-- to this report so the home-screen icon always opens this dashboard.   -->
    <meta name="theme-color" content="#0F766E">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="{name}">
    <link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' rx='8' fill='white'/><g stroke='%23c7cbe0' stroke-width='3'><line x1='8' y1='8' x2='8' y2='32'/><line x1='20' y1='8' x2='20' y2='32'/><line x1='32' y1='8' x2='32' y2='32'/></g><circle cx='8' cy='32' r='5' fill='%230D9488'/><circle cx='20' cy='20' r='5' fill='%237C6FE0'/><circle cx='32' cy='8' r='5' fill='%230D9488'/></svg>">
    <!-- use-credentials: a host may serve the report (and this manifest) behind
         auth, and the browser fetches the manifest WITHOUT cookies unless told
         to, which would bounce to an HTML login/error page and fail to parse. -->
    <link rel="manifest" href="manifest.json" crossorigin="use-credentials">
{flash_script}
{host_flag_script}
{cdn_tags}
    <style>{base_css}
{component_css}</style>
</head>
<body data-report-slug="{slug}" data-content-kind="{config.get('kind', 'report')}">
<div class="fw-loading" id="fwLoading">
    <div class="fw-spinner"></div>
    <div class="fw-loading-title">{name}</div>
    <div class="fw-loading-text">Loading report...</div>
</div>
<div id="fwChunkBanner">
    <div class="fw-chunk-spinner"></div>
    <span id="fwChunkBannerText">Loading older data...</span>
</div>
{js_runtime}
<script>window._chartInstances=window._chartInstances||{{}};window._fwRenderers=window._fwRenderers||{{}};</script>
{component_js_html}
{header_html}
<div class="fw-container{' fw-full-width' if config.get('layout', {}).get('full_width') else ''}">
    {"".join(sections_html)}
</div>
{_ONLY_SECTION_SCRIPT}{data_loader}
{raw_js_html}
{'''<script>
setTimeout(function(){
    var hasRaw=document.querySelector('.fw-raw');
    if(!hasRaw)return;
    var inst=window._chartInstances||{};
    var hasCharts=Object.keys(inst).length>0;
    if(hasCharts&&typeof window.renderAll==='function'){
        var src=(window.renderAll.toString()||'');
        if(src.indexOf('_renderComps')!==-1)
            console.warn('[fw] RawHTML sections with charts detected but window.renderAll '
                +'was not overridden by custom JS. Auto-refresh and theme switching may not '
                +'update custom charts. Assign window.renderAll = yourRenderFunction.');
    }
},3000);
</script>''' if raw_js_collected else ''}
{source_notice_html()}
{extension_scripts_html}
</body>
</html>"""

    ctx.mark("assemble_html", time.time() - t0)

    html_path = os.path.join(out, "index.html")

    cron_interval = _cron_to_seconds(config.get("schedule", {}).get("cron", ""))
    report_data["_freshness"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "refresh_seconds": cron_interval,
    }
    report_data["_framework_version"] = trellum.__version__
    report_data["_content_kind"] = config.get("kind", "report")

    # `_metrics`: claimed metrics.yaml definitions; see COMPATIBILITY.md.
    from trellum.metrics import claimed_metrics_block, metrics_used_entries
    metrics_block = claimed_metrics_block(
        rctx.component_data for rctx in all_render_ctxs)
    if metrics_block:
        report_data["_metrics"] = metrics_block

    json_path = os.path.join(out, "data.json")
    # ── Merge chunk manifests from all render contexts ───────
    all_chunk_manifests: dict[str, dict] = {}
    all_pending_chunks: dict[str, bytes] = {}
    for rctx in all_render_ctxs:
        all_chunk_manifests.update(rctx._chunk_manifests)
        all_pending_chunks.update(rctx._pending_chunks)
    if all_chunk_manifests:
        report_data["_chunk_manifests"] = all_chunk_manifests

    t0 = time.time()
    json_bytes = orjson.dumps(
        report_data,
        option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS,
    )
    ctx.mark("serialize_json", time.time() - t0)

    t0 = time.time()
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(json_path, "wb") as f:
        f.write(json_bytes)

    # ── Write chunk files ────────────────────────────────────
    for fname, chunk_bytes in all_pending_chunks.items():
        with open(os.path.join(out, fname), "wb") as f:
            f.write(chunk_bytes)

    # ── GC stale chunk files from prior runs ─────────────────
    # The framework rewrites chunks for whatever periods this run
    # produced; periods (or DataSources) that no longer exist linger
    # forever. Clean them up so the output dir matches reality and
    # `total_size_bytes` reflects only live data.
    #
    # Trigger condition: any chunked DataSource ran (manifests
    # populated) OR this run wrote chunk files. This catches the case
    # where the report uses chunking but mock/short-range data fits
    # entirely inline — `all_chunk_manifests` is non-empty, leftovers
    # from prior runs should still be GC'd against the empty canonical
    # set.
    if gc_stale_chunks and (all_chunk_manifests or all_pending_chunks):
        _gc_stale_chunks(out, set(all_pending_chunks.keys()))

    if production:
        import gzip as _gzip
        gz_bytes = _gzip.compress(json_bytes, compresslevel=6)
        gz_path = os.path.join(out, "data.json.gz")
        with open(gz_path, "wb") as f:
            f.write(gz_bytes)
        ctx.mark("gzip_compress", time.time() - t0)
    ctx.mark("write_files", time.time() - t0)

    # PWA manifest -- one per report. Adding this report's URL to a phone's
    # home screen yields a standalone-mode app icon labelled with the report
    # name that always opens this specific dashboard (start_url + scope are
    # both per-report, so the app stays inside this report's URL space).
    _write_manifest(out, slug, config.get("name", name))

    _write_meta(out, slug, name, config, validation=validation,
                timings=ctx._timings, details=details,
                metrics_used=metrics_used_entries(metrics_block))
    _write_live_queries(out, live_registry)
    return html_path, json_path


