"""Turning a section list into HTML, and splicing in a host's nav."""

from __future__ import annotations

import html as _html

from trellum.components.base import Component, RenderContext

# ═══════════════════════════════════════════════════════════════
# Base CSS (framework shell only — component CSS comes from
# each Component.css() classmethod)
# ═══════════════════════════════════════════════════════════════

def _render_section_list(
    sections: list[dict],
    render_ctx: RenderContext,
) -> list[str]:
    """Render a list of section dicts into HTML strings.

    Sections where ALL components have ``_no_section_wrap = True``
    (e.g. DataSource + FilterBar) render without the ``.fw-section``
    wrapper so that ``position: sticky`` works against the viewport.
    """
    parts: list[str] = []
    for sec in sections:
        title = sec["title"]
        components = sec["components"]
        components_html = render_ctx.render_children(components)

        skip_wrap = (
            not title
            and components
            and all(
                getattr(c, "_no_section_wrap", False)
                for c in components
                if isinstance(c, Component)
            )
        )

        if skip_wrap:
            parts.append(components_html)
        elif title:
            sec_id = title.lower().replace(" ", "-")
            # data-fw-section-title carries the exact generator-side title so
            # review feedback (and other tooling) can name the section without
            # reversing the lossy id slug.
            title_attr = _html.escape(title, quote=True)
            if sec.get("collapsible"):
                collapsed_cls = " fw-collapsed" if sec.get("default_collapsed") else ""
                parts.append(
                    f'<div class="fw-section fw-collapsible{collapsed_cls}" '
                    f'id="sec-{sec_id}" data-fw-section-title="{title_attr}">'
                    f'<h2 class="fw-collapsible-toggle">{title}'
                    f'<span class="fw-chevron"></span></h2>'
                    f'<div class="fw-collapsible-body">{components_html}</div></div>'
                )
            else:
                parts.append(
                    f'<div class="fw-section" id="sec-{sec_id}" '
                    f'data-fw-section-title="{title_attr}">'
                    f"<h2>{title}</h2>{components_html}</div>"
                )
        else:
            parts.append(
                f'<div class="fw-section">{components_html}</div>'
            )
    return parts

def _inject_nav(html: str, nav_html: str = "") -> str:
    """Inject the nav group (host extension slot + reload) into custom dashboards.

    ``nav_html`` comes from the project's ``extensions`` block -- see
    ``trellum.project.load_extensions``. The framework contributes only the
    reload button, which needs no server; when the slot is empty the nav group
    holds nothing else, and the page links nowhere the framework can't serve.
    """
    from trellum.components.header import _REFRESH_SVG

    nav_html = (
        f'<div class="fw-nav-group">'
        f'{nav_html}'
        f'<button class="fw-refresh-btn" id="fwReloadBtn" title="Reload latest data">'
        f'{_REFRESH_SVG}<span class="fw-refresh-label">Reload</span></button>'
        f'</div>'
    )
    nav_css = (
        '<style>'
        '.fw-nav-group{display:inline-flex;align-items:center;gap:4px;margin-right:12px;flex-shrink:0}'
        '.fw-back-link,.fw-refresh-btn{display:inline-flex;align-items:center;gap:4px;'
        'color:rgba(255,255,255,0.7);text-decoration:none;font-size:13px;font-weight:500;'
        'padding:5px 12px;border-radius:6px;background:rgba(0,0,0,0.2);'
        'border:1px solid rgba(255,255,255,0.15);transition:all 0.2s;white-space:nowrap;cursor:pointer}'
        '.fw-back-link:hover,.fw-refresh-btn:hover{background:rgba(0,0,0,0.35);color:#fff}'
        '.fw-back-link svg,.fw-refresh-btn svg{width:14px;height:14px}'
        '.fw-refresh-btn.running svg{animation:fw-spin 1s linear infinite}'
        '</style>'
    )
    reload_script = """
<script>
(function(){
  var btn = document.getElementById('fwReloadBtn');
  if (!btn) return;
  btn.addEventListener('click', function() { location.reload(); });
})();
</script>
"""

    html = html.replace("</head>", nav_css + "</head>", 1)
    html = html.replace("</body>", reload_script + "</body>", 1)

    for marker in ['<div class="header-left">', '<div id="header">']:
        if marker in html:
            html = html.replace(marker, marker + "\n  " + nav_html, 1)
            return html

    return html
