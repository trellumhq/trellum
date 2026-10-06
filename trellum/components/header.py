"""Report header component.

Encapsulates the sticky header bar with the host nav slot, report title,
subtitle, and optional scope toggle.  Subclass and override ``render_html()``
/ ``css()`` for project-specific branding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext

# Kept for hosts building their own `extensions.nav_html`: a back-chevron that
# matches the header's own iconography. Nothing in the framework renders it --
# the framework has nowhere to navigate back to.
_CHEVRON_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6">'
    '</polyline></svg>'
)

_REFRESH_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="23 4 23 10 17 10"></polyline>'
    '<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>'
)

_EXPORT_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"></path>'
    '<polyline points="7 10 12 15 17 10"></polyline>'
    '<line x1="12" y1="15" x2="12" y2="3"></line></svg>'
)


@dataclass
class ReportHeader(Component):
    """Sticky header bar rendered at the top of every report."""

    name: str = ""
    subtitle: str = ""
    slug: str = ""
    scope_toggle_html: str = ""
    theme_select_html: str = ""
    #: Host extension slot, from the project's ``extensions.nav_html``. The
    #: framework itself has nowhere to navigate to, so an empty slot renders
    #: no nav group at all rather than a link to a page that doesn't exist.
    nav_html: str = ""

    _component_type: str = field(default="header", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        nav_html = (
            f'<div class="fw-nav-group">{self.nav_html}</div>'
            if self.nav_html else ''
        )
        export_html = (
            f'<div class="fw-export-wrap">'
            f'<button class="fw-export-btn" id="fwExportBtn" title="Export report">'
            f'{_EXPORT_SVG}<span>Export</span></button>'
            f'<div class="fw-export-menu" id="fwExportMenu">'
            f'<button data-export="png">PNG image</button>'
            f'<button data-export="pdf">PDF document</button>'
            f'</div>'
            f'</div>'
        )
        help_html = (
            '<div class="fw-help-wrap">'
            '<button class="fw-help-btn" id="fwHelpBtn" '
            'title="Page feels slow? Click for help" '
            'aria-label="Help">Slow?</button>'
            '<div class="fw-help-menu" id="fwHelpMenu">'
            '<div class="fw-help-title">Page feels slow?</div>'
            '<div class="fw-help-body">'
            'Browser extensions that scan input fields '
            '(password managers like <b>Bitwarden</b>, <b>1Password</b>, '
            '<b>LastPass</b>, or autofill tools) can add significant '
            'overhead on dense reports — <em>especially</em> ones with '
            'many filters.'
            '<div class="fw-help-detected" id="fwHelpDetected"></div>'
            '<div class="fw-help-tip">'
            'Fix: open the extension&rsquo;s site settings and disable '
            'autofill for this domain. Or try an Incognito / Private '
            'window to confirm.'
            '</div>'
            '</div>'
            '</div>'
            '</div>'
        )
        right_controls = (
            f'<div class="fw-header-right">'
            f'{self.scope_toggle_html}{export_html}{help_html}{self.theme_select_html}'
            f'</div>'
        )
        # Title + subtitle + freshness collapse onto a single line on desktop
        # (fw-titleblock is a row) and stack into two lines on narrow
        # viewports (the header.css mobile query flips it to a column, with
        # subtitle+freshness sharing the second row via fw-meta).
        return (
            f'<div class="fw-header">\n'
            f'    <div class="fw-header-left">\n'
            f'        {nav_html}\n'
            f'        <div class="fw-titleblock">\n'
            f'            <h1>{self.name}</h1>\n'
            f'            <div class="fw-meta">\n'
            f'                <span class="fw-subtitle">{self.subtitle}</span>\n'
            f'                <span class="fw-freshness" id="fwFreshness"></span>\n'
            f'            </div>\n'
            f'        </div>\n'
            f'    </div>\n'
            f'    {right_controls}\n'
            f'</div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/header.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/header.js")
