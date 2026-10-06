"""Structural layout components: sections, panels, grids, and passthrough HTML."""

from __future__ import annotations

import re as _re
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trellum.assets import load_css
from trellum.components.base import Component, RenderContext


@dataclass
class Section(Component):
    """Titled content block that groups child components.

    Args:
        title: Section heading text.
        children: List of component instances.
        collapsible: If True, section can be collapsed/expanded.
        default_collapsed: If True (and ``collapsible`` is True), the
            section starts collapsed on initial load.
        anchor: DOM id for this block. A stable deep-link target -- the
            page's ``?only=<id>`` filter shows one anchored block and hides
            the rest, and ``#<id>`` scrolls to it. Give one when the block
            is generated and its title is not a name you want in a URL.
    """
    title: str
    children: List[Any]
    collapsible: bool = False
    default_collapsed: bool = False
    anchor: str = ""

    _component_type: str = field(default="section", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        inner = ctx.render_children(self.children)
        id_attr = f' id="{self.anchor}"' if self.anchor else ""
        if self.collapsible and self.title:
            collapsed_cls = " fw-collapsed" if self.default_collapsed else ""
            return (
                f'<div class="fw-section fw-collapsible{collapsed_cls}"{id_attr}>'
                f'<h2 class="fw-collapsible-toggle">'
                f'{self.title}'
                f'<span class="fw-chevron"></span></h2>'
                f'<div class="fw-collapsible-body">{inner}</div></div>'
            )
        title_html = f"<h2>{self.title}</h2>" if self.title else ""
        return f'<div class="fw-section"{id_attr}>{title_html}{inner}</div>'

    @classmethod
    def css(cls) -> str:
        return ""


@dataclass
class Panel(Component):
    """Card-style container without a required title.

    Args:
        children: List of component instances.
        title: Optional heading.
    """
    children: List[Any]
    title: Optional[str] = None

    _component_type: str = field(default="panel", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        inner = ctx.render_children(self.children)
        title_html = (
            f'<h3 style="margin-bottom: 12px;">{self.title}</h3>'
        ) if self.title else ""
        return f'<div class="fw-panel">{title_html}{inner}</div>'

    @classmethod
    def css(cls) -> str:
        return """\
.fw-panel {
    background: var(--bg-card-hover);
    border-radius: 8px;
    padding: 14px;
    border: 1px solid var(--border-color);
}
@media (max-width: 640px) {
    .fw-panel { padding: 10px; }
}"""


@dataclass
class Grid(Component):
    """CSS grid layout for arranging children in columns.

    Each child is wrapped in its own ``<div>`` so multi-element
    components (e.g. title + canvas) occupy exactly one grid cell.

    Args:
        children: List of component instances.
        columns: Number of grid columns.
        card: If True, each cell gets a card-panel wrapper (background,
              border, border-radius, padding).
        min_width: Tile mode. When set, the columns are
            ``repeat(auto-fill, minmax(<min_width>px, 1fr))`` and ``columns``
            is ignored: the row fits as many tiles as the width allows and
            reflows on its own, with no breakpoints to keep in sync. Each
            cell is then a TILE -- a glance, not a page -- so the charts
            inside it are shortened and their titles dropped (grid.css).
            A chart stops being readable much under 320px.
    """
    children: List[Any]
    columns: int = 2
    card: bool = False
    min_width: int = 0

    _component_type: str = field(default="grid", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cls = "fw-grid-card" if self.card else "fw-grid-item"
        items = []
        for child in self.children:
            child_html = ctx.render_child(child)
            items.append(f'<div class="{cls}">{child_html}</div>')
        inner = "\n".join(items)
        if self.min_width:
            grid_cls = "fw-grid fw-grid-auto"
            cols = f"repeat(auto-fill, minmax({self.min_width}px, 1fr))"
        else:
            grid_cls = "fw-grid"
            cols = f"repeat({self.columns}, 1fr)"
        return (
            f'<div class="{grid_cls}" '
            f'style="grid-template-columns: {cols};">'
            f'{inner}</div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/grid.css")


@dataclass
class SplitPane(Component):
    """Side-by-side two-panel layout.

    Args:
        left: Component (usually Section or Panel) for the left side.
        right: Component for the right side.
        ratio: CSS ratio string like "1:1" or "2:1".
    """
    left: Any
    right: Any
    ratio: str = "1:1"

    _component_type: str = field(default="split_pane", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        parts = self.ratio.split(":")
        fr = " ".join(f"{p}fr" for p in parts)
        left_html = ctx.render_child(self.left)
        right_html = ctx.render_child(self.right)
        return (
            f'<div class="fw-split" '
            f'style="grid-template-columns: {fr};">'
            f'{left_html}{right_html}</div>'
        )

    @classmethod
    def css(cls) -> str:
        return """\
.fw-split {
    display: grid; gap: var(--spacing-lg);
}
@media (max-width: 900px) {
    .fw-split { grid-template-columns: 1fr !important; }
}"""


@dataclass
class RawHTML(Component):
    """Passthrough for custom HTML + JS within the component tree.

    Use this to embed complex, hand-written sections that the framework's
    standard components cannot express (e.g. Economy Monitor in Pulse).

    Args:
        html: Raw HTML markup to embed verbatim.
        js: JavaScript code to append (no ``<script>`` tags needed).
        data_key: If set, ``data`` is stored under this key in data.json
                  so the custom JS can read it via ``window._reportData``.
        data: Arbitrary dict to include when ``data_key`` is set.
    """
    html: str
    js: str = ""
    data_key: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    _component_type: str = field(default="raw_html", init=False, repr=False)

    _REIMPL_PATTERNS = [
        (_re.compile(r"function\s+fmt\$"), "fmt$ (use fmtCompact$ or fw['fmt$'] instead)"),
        (_re.compile(r"function\s+fmtN\b"), "fmtN (use fmtCompact instead)"),
        (_re.compile(r"function\s+fmtC\b"), "fmtC (use fmtCompact instead)"),
        (_re.compile(r"function\s+fmtC\$"), "fmtC$ (use fmtCompact$ instead)"),
        (_re.compile(r"function\s+buildAnnotations\b"), "buildAnnotations (use fw.buildAnnotations instead)"),
    ]

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        if self.data_key and self.data:
            ctx.add_raw_data(self.data_key, self.data)
        if self.js:
            ctx.add_raw_js(self.js)
            self._warn_reimplemented_utils()
        return f'<div id="{cid}" class="fw-raw">{self.html}</div>'

    def _warn_reimplemented_utils(self) -> None:
        """Warn at generation time if custom JS re-implements framework utilities."""
        found = [msg for pat, msg in self._REIMPL_PATTERNS if pat.search(self.js)]
        if found:
            _warnings.warn(
                f"[RawHTML] Custom JS re-implements framework utilities: "
                f"{', '.join(found)}. These will diverge from trellum behavior.",
                stacklevel=4,
            )


@dataclass
class Visible(Component):
    """Conditional-visibility wrapper controlled by a Toggle.

    All children are rendered into the DOM but wrapped in a ``<div>``
    whose ``display`` is managed by the toggle click handler.  Nesting
    ``Visible`` wrappers enables compound toggle conditions.

    Args:
        children: Components to wrap.
        toggle_target: ``id`` of the Toggle that controls visibility.
        toggle_value: The toggle option label that makes this block visible.
    """
    children: List[Any]
    toggle_target: str
    toggle_value: str

    _component_type: str = field(default="visible", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        inner = ctx.render_children(self.children)
        return (
            f'<div class="fw-toggle-vis" '
            f'data-toggle-target="{self.toggle_target}" '
            f'data-toggle-value="{self.toggle_value}">'
            f'{inner}</div>'
        )
