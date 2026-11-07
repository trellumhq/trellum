"""Structural layout components: sections, panels, grids, and passthrough HTML."""

from __future__ import annotations

import re as _re
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trellum.assets import load_css
from trellum.components.base import Component, RenderContext


def grid_color_option_problems(grid: Any) -> list[tuple[str, str]]:
    """Shared constructor, render and report-validation contract."""
    value = grid.color_cycle
    if value is not None and (not isinstance(value, str) or value != "theme"):
        return [("grid-color-cycle-invalid", "Grid color_cycle must be None or 'theme'")]
    return []


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
    """Panel-width grid with at most ``columns`` equal-width tracks.

    Each child's full output occupies one cell. ``min_width`` is the preferred
    minimum cell width in pixels (0 selects 320px); a narrower panel uses one
    cell that fits. ``columns``, ``card`` and ``min_width`` keep their positional
    arguments. Titles and local chart breakdown/normalization controls stay visible.
    Keyword-only ``show_titles=False`` or ``show_controls=False`` omits redundant
    chart context within this grid, stopping at each nested Grid boundary.
    Keyword-only ``color_cycle="theme"`` assigns stable authored palette slots
    to direct single-metric Line/Area/ordinary Bar tiles as a color fallback.
    The default ``None`` preserves existing colors; nested Grids own their option.
    Chart heights and explicit author sizing are unchanged by this layout.
    """
    children: List[Any]
    columns: int = 2
    card: bool = False
    min_width: int = 0
    show_titles: bool = field(default=True, kw_only=True)
    show_controls: bool = field(default=True, kw_only=True)
    color_cycle: Optional[str] = field(default=None, kw_only=True)

    _component_type: str = field(default="grid", init=False, repr=False)

    def __post_init__(self) -> None:
        problems = grid_color_option_problems(self)
        if problems:
            raise ValueError(problems[0][1])

    def render_html(self, ctx: RenderContext) -> str:
        problems = grid_color_option_problems(self)
        if problems:
            raise ValueError(problems[0][1])
        cls = "fw-grid-card" if self.card else "fw-grid-item"
        items = []
        for index, child in enumerate(self.children):
            # Every authored tile keeps its position, even an override or a
            # nested Grid. Descendant charts never inherit an outer assignment.
            slot = (index if self.color_cycle == "theme"
                    and self._eligible_color_cycle_child(child) else None)
            with ctx.grid_color_scope(child, slot):
                child_html = ctx.render_child(child)
            items.append(f'<div class="{cls}">{child_html}</div>')
        inner = "\n".join(items)
        grid_cls = "fw-grid fw-grid-auto" if self.min_width else "fw-grid"
        # The percentage floor limits the number of auto-filled tracks, while
        # min(100%, ...) lets even a panel narrower than min_width fit one cell.
        minimum = self.min_width or 320
        floor = f"calc((100% - {self.columns - 1} * var(--spacing-md)) / {self.columns})"
        cols = f"repeat(auto-fill, minmax(min(100%, max({minimum}px, {floor})), 1fr))"
        return (
            f'<div class="{grid_cls}" '
            f'data-show-titles="{str(self.show_titles).lower()}" '
            f'data-show-controls="{str(self.show_controls).lower()}" '
            f'style="grid-template-columns: {cols};">'
            f'{inner}</div>'
        )

    @staticmethod
    def _eligible_color_cycle_child(child: Any) -> bool:
        if getattr(child, "_component_type", None) not in {"line_chart", "area_chart", "bar_chart"}:
            return False
        if getattr(child, "category_colors", None):
            return False
        if getattr(child, "dataset_id", None):
            # Reactive category splits retain their authored meaning even if
            # current filters happen to leave only one visible series.
            if getattr(child, "stack_by", None) or getattr(child, "stack_by_options", None):
                return False
            if getattr(child, "ratios", None):
                return (len(child.ratios) == 1 and not getattr(child, "stacked", False)
                        and not getattr(child, "net_color", False))
        if getattr(child, "_component_type", None) == "bar_chart":
            return (not getattr(child, "stacked", False)
                    and not getattr(child, "ratios", None)
                    and not getattr(child, "net_color", False)
                    and len(getattr(child, "y_cols", None) or [getattr(child, "y", "")]) == 1)
        if getattr(child, "_component_type", None) == "line_chart":
            ys = getattr(child, "y", "")
            return (not getattr(child, "stack_by", None)
                    and not getattr(child, "stack_by_options", None)
                    and not getattr(child, "ratios", None)
                    and len(ys if isinstance(ys, list) else [ys]) == 1)
        ys = getattr(child, "y", "")
        return (not getattr(child, "stacked", False)
                and len(ys if isinstance(ys, list) else [ys]) == 1)

    @classmethod
    def css(cls) -> str:
        return load_css("components/grid.css")


@dataclass
class SplitPane(Component):
    """Two panes that keep ``ratio`` while both preferred minimums fit.

    ``ratio`` retains its third positional argument (e.g. "2:1"). Keyword-only
    ``left_min_width`` and ``right_min_width`` default to 320 CSS pixels each,
    including any child Panel framing. Minimums are soft when stacked: even a
    narrower container fits without overflowing. Left always precedes right.
    Each side owns its full rendered output, including chart context/legends.
    """
    left: Any
    right: Any
    ratio: str = "1:1"
    left_min_width: float = field(default=320, kw_only=True)
    right_min_width: float = field(default=320, kw_only=True)

    _component_type: str = field(default="split_pane", init=False, repr=False)

    def __post_init__(self) -> None:
        self._pane_sizes()

    def _pane_sizes(self) -> tuple[float, float, float, float]:
        import math

        try:
            left, right = (float(p) for p in self.ratio.split(":"))
            left_min, right_min = float(self.left_min_width), float(self.right_min_width)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("SplitPane needs a two-part positive ratio and positive pane minimums") from exc
        values = (left, right, left_min, right_min)
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("SplitPane ratio and pane minimums must be finite and positive")
        # Each grow factor is at least one, so a pane fills its own line after
        # wrapping (flex factors below one can leave unused space on that line).
        weight = min(left, right)
        left, right = left / weight, right / weight
        if not math.isfinite(left) or not math.isfinite(right):
            raise ValueError("SplitPane ratio exceeds the supported size range")
        # Proportional bases ensure flex growth preserves the ratio exactly;
        # merely growing two unrelated minimum widths would distort the panes.
        scale = max(left_min / left, right_min / right)
        left_basis, right_basis = left * scale, right * scale
        if any(not math.isfinite(v) or v <= 0 for v in (scale, left_basis, right_basis)):
            raise ValueError("SplitPane ratio and pane minimums exceed the supported size range")
        return left, right, left_basis, right_basis

    def render_html(self, ctx: RenderContext) -> str:
        left, right, left_basis, right_basis = self._pane_sizes()
        left_html = ctx.render_child(self.left)
        right_html = ctx.render_child(self.right)
        return (
            '<div class="fw-split">'
            f'<div class="fw-split-pane" style="--fw-pane-grow:{left:g};'
            f'--fw-pane-basis:{left_basis:g}px;">{left_html}</div>'
            f'<div class="fw-split-pane" style="--fw-pane-grow:{right:g};'
            f'--fw-pane-basis:{right_basis:g}px;">{right_html}</div></div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/split_pane.css")


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
