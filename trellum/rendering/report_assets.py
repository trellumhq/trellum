"""Collect shared component assets across every rendered report scope."""

from trellum.components.base import collect_asset_parts


def collect_report_assets(render_contexts, config):
    """Return component assets and deduplication sets to extend with header assets."""
    # ── Collect CSS and JS assets from all rendered component types ──
    all_seen: set[type] = set()
    for rctx in render_contexts:
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
    for rctx in render_contexts:
        all_cdn_keys.update(rctx.collect_cdn_deps())
    all_cdn_keys.update(config.get("extra_cdn", {}).keys())

    return component_css_parts, component_js_parts, seen_css, seen_js, all_cdn_keys
