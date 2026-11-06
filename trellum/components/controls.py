"""Interactive control components: toggles, dropdowns, tabs."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any, Dict, List, Optional

from trellum.assets import load_css, load_js
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
    """Labeled, searchable choice retaining the native select and its events.

    ``id``, ``options`` and ``default`` retain their positional contract.
    ``label`` is keyword-only and defaults to "Choose an option".
    """
    id: str
    options: List[str]
    default: Optional[str] = None
    label: str = field(default="Choose an option", kw_only=True)

    _component_type: str = field(default="dropdown", init=False, repr=False)

    def __post_init__(self):
        if self.default is None and self.options:
            self.default = self.options[0]

    def render_html(self, ctx: RenderContext) -> str:
        opts = []
        for opt in self.options:
            selected = " selected" if opt == self.default else ""
            text = escape(str(opt), quote=True)
            opts.append(f'<option value="{text}"{selected}>{text}</option>')
        disabled = "" if opts else " disabled"
        if not opts:
            opts.append('<option value="">No choices available</option>')
        control_id = escape(str(self.id), quote=True)
        label = escape(str(self.label), quote=True)
        return (
            f'<div class="fw-choice"><label for="{control_id}">{label}</label>'
            f'<select class="fw-dropdown fw-report-select" id="{control_id}" '
            f'aria-label="{label}" data-fw-choice{disabled}>{"".join(opts)}</select></div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return ["slim_select_js", "slim_select_css"]

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/report_dropdown.js")

    @classmethod
    def css(cls) -> str:
        return load_css("components/report_dropdown.css")


@dataclass
class TabGroup(Component):
    """Report-local views: underlined tabs, searchable dropdown or sidebar.

    ``tabs``, ``id`` and ``mode`` retain their positional contract. Unknown
    legacy modes continue to render tabs. ``label`` names the view navigation.
    The opt-in sidebar becomes a selector when its panel is at most 520px wide.
    """
    tabs: List[Dict[str, Any]]
    id: Optional[str] = None
    mode: str = "tabs"
    label: str = field(default="Report view", kw_only=True)

    _component_type: str = field(default="tab_group", init=False, repr=False)

    @classmethod
    def cdn_deps(cls) -> list[str]:
        return Dropdown.cdn_deps()

    @classmethod
    def client_js(cls) -> str:
        return Dropdown.client_js()

    def render_html(self, ctx: RenderContext) -> str:
        tab_id = escape(str(self.id or ctx.next_id()), quote=True)
        label = escape(str(self.label), quote=True)
        mode = self.mode if self.mode in ("dropdown", "sidebar") else "tabs"
        panels, buttons, options = [], [], []
        for i, tab in enumerate(self.tabs):
            panel_id, button_id = ctx.next_id(), ctx.next_id()
            text = escape(str(tab["label"]), quote=True)
            active = " active" if i == 0 else ""
            display = "" if i == 0 else ' style="display:none"'
            content = ctx.render_children(tab.get("content", []))
            panel_label = (f'aria-labelledby="{button_id}"' if mode != "dropdown"
                           else f'aria-label="{text}"')
            panels.append(
                f'<div class="fw-tab-panel" id="{panel_id}" role="tabpanel" '
                f'{panel_label}{display}>{content}</div>'
            )
            buttons.append(
                f'<button type="button" class="fw-tab-btn{active}" id="{button_id}" '
                f'role="tab" aria-controls="{panel_id}" aria-selected="{str(i == 0).lower()}" '
                f'tabindex="{0 if i == 0 else -1}">{text}</button>'
            )
            options.append(f'<option value="{i}">{text}</option>')
        if not self.tabs:
            return (f'<div class="fw-tab-root" data-tab-mode="{mode}">'
                    '<p class="fw-tab-empty">No report views available</p></div>')
        control = ""
        if mode != "dropdown":
            orientation = ' aria-orientation="vertical"' if mode == "sidebar" else ""
            control += (
                f'<div class="fw-tab-group" data-tab-id="{tab_id}" role="tablist" '
                f'aria-label="{label}"{orientation}>{"".join(buttons)}</div>'
            )
        if mode in ("dropdown", "sidebar"):
            select_id = ctx.next_id()
            mirror = ' data-tab-mirror' if mode == "sidebar" else ""
            control += (
                f'<div class="fw-choice fw-tab-choice"><label for="{select_id}">{label}</label>'
                f'<select id="{select_id}" class="fw-tab-dropdown fw-report-select" '
                f'data-tab-id="{tab_id}" data-fw-choice aria-label="{label}"{mirror}>'
                f'{"".join(options)}</select></div>'
            )
        return (
            f'<div class="fw-tab-root" data-tab-mode="{mode}">'
            f'<div class="fw-tab-nav">{control}</div>'
            f'<div class="fw-tab-panels">{"".join(panels)}</div></div>'
        )

    @classmethod
    def css(cls) -> str:
        return Dropdown.css() + "\n" + load_css("components/tab_group.css")


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
