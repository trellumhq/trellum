"""Interactive control components: toggles, dropdowns, tabs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trellum.assets import load_css
from trellum.components.base import Component, RenderContext


@dataclass
class Toggle(Component):
    """Button-group toggle control.

    Args:
        id: Unique identifier (used for state preservation and JS targeting).
        options: List of option labels.
        default: Initially selected option (defaults to first).
    """
    id: str
    options: List[str]
    default: Optional[str] = None

    _component_type: str = field(default="toggle", init=False, repr=False)

    def __post_init__(self):
        if self.default is None and self.options:
            self.default = self.options[0]

    def render_html(self, ctx: RenderContext) -> str:
        btns = []
        for opt in self.options:
            active = " active" if opt == self.default else ""
            btns.append(f'<button class="fw-toggle-btn{active}">{opt}</button>')
        return (
            f'<div class="fw-toggle-group" data-toggle-id="{self.id}">'
            f'{"".join(btns)}</div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/toggle.css")


@dataclass
class Dropdown(Component):
    """Select dropdown control.

    Args:
        id: Unique identifier.
        options: List of option labels.
        default: Initially selected option (defaults to first).
    """
    id: str
    options: List[str]
    default: Optional[str] = None

    _component_type: str = field(default="dropdown", init=False, repr=False)

    def __post_init__(self):
        if self.default is None and self.options:
            self.default = self.options[0]

    def render_html(self, ctx: RenderContext) -> str:
        opts = []
        for opt in self.options:
            selected = " selected" if opt == self.default else ""
            opts.append(f'<option value="{opt}"{selected}>{opt}</option>')
        return f'<select class="fw-dropdown" id="{self.id}">{"".join(opts)}</select>'

    @classmethod
    def css(cls) -> str:
        return """\
.fw-dropdown {
    padding: 6px 12px;
    font-size: var(--font-size-small);
    background: var(--bg-card);
    color: var(--text-main);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    margin-bottom: var(--spacing-md);
}"""


@dataclass
class TabGroup(Component):
    """Tabbed content navigation.

    Args:
        tabs: List of dicts with ``label`` (str) and ``content`` (list of components).
        id: Optional unique identifier for state preservation.
        mode: ``"tabs"`` (default) renders button tabs; ``"dropdown"`` renders a
              ``<select>`` element with the same show/hide panel behaviour.
    """
    tabs: List[Dict[str, Any]]
    id: Optional[str] = None
    mode: str = "tabs"

    _component_type: str = field(default="tab_group", init=False, repr=False)

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return ["slim_select_js", "slim_select_css"]

    def render_html(self, ctx: RenderContext) -> str:
        tab_id = self.id or ctx.next_id()
        panels = []
        for i, tab in enumerate(self.tabs):
            display = "" if i == 0 else ' style="display:none"'
            content = ctx.render_children(tab.get("content", []))
            panels.append(f'<div class="fw-tab-panel"{display}>{content}</div>')

        if self.mode == "dropdown":
            opts = []
            for i, tab in enumerate(self.tabs):
                opts.append(f'<option value="{i}">{tab["label"]}</option>')
            control = (
                f'<select class="fw-dropdown fw-tab-dropdown" '
                f'data-tab-id="{tab_id}">{"".join(opts)}</select>'
            )
        else:
            btns = []
            for i, tab in enumerate(self.tabs):
                active = " active" if i == 0 else ""
                btns.append(
                    f'<button class="fw-tab-btn{active}">{tab["label"]}</button>'
                )
            control = (
                f'<div class="fw-tab-group" data-tab-id="{tab_id}">'
                f'{"".join(btns)}</div>'
            )

        return f'{control}\n{"".join(panels)}'

    @classmethod
    def css(cls) -> str:
        return load_css("components/tab_group.css")


@dataclass
class DateSelector(Component):
    """Day selector for switching between Today, Yesterday, 7D Ago.

    Args:
        id: Unique identifier.
        options: List of day labels.
    """
    id: str
    options: List[str] = field(default_factory=lambda: ["Today", "Yesterday", "7D Ago"])

    _component_type: str = field(default="date_selector", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        delegate = Toggle(id=self.id, options=self.options, default=self.options[0])
        return ctx.render_child(delegate)

    @classmethod
    def css(cls) -> str:
        return Toggle.css()
