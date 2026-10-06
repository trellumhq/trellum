"""Base component class and rendering context for the framework plugin system.

Component subclasses are self-contained: they carry their own HTML renderer,
CSS, client JS, and data serializer.  The framework dispatcher calls
render_html() generically — no hardcoded dispatch dict needed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trellum.themes.theme import Theme


class Component:
    """Base class for all framework components.

    Subclasses are ``@dataclass`` classes that set ``_component_type`` and
    implement ``render_html()``.  The framework never needs to know concrete
    types — it just calls the methods on this interface.
    """

    _component_type: str = ""
    _no_section_wrap: bool = False
    _supports_dataset_id: bool = False

    def render_html(self, ctx: RenderContext) -> str:
        """Return HTML shell (divs/canvases with IDs).

        Call ``ctx.register(id, data)`` to push data into ``data.json``.
        """
        raise NotImplementedError

    @classmethod
    def client_js(cls) -> str:
        """JS that registers a renderer on ``window._fwRenderers[type]``.

        Emitted once per unique return value in the page.
        """
        return ""

    @classmethod
    def css(cls) -> str:
        """CSS rules for this component type."""
        return ""

    @classmethod
    def cdn_deps(cls) -> list[str]:
        """CDN keys this component needs (matched against CDN registry)."""
        return []


def collect_asset_parts(
    types: Iterable[type],
    seen_css: set[str] | None = None,
    seen_js: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Unique CSS and JS blocks for *types*, in a stable order.

    Deduplicated by string content, so a shared base class like ``_ChartBase``
    emits its assets once however many chart types are on the page. Pass the
    ``seen_*`` sets in to keep deduplicating across several calls.

    The sort is the point of this function existing. ``_seen_types`` is a set
    of classes, and a class hashes by identity, so iterating it emitted the
    blocks in whatever order the interpreter's addresses happened to fall that
    run: two consecutive builds of one report, from identical code, differed by
    thousands of lines of reshuffled CSS. That churns every published artifact
    on every rebuild, and it makes "did my change alter the output?"
    unanswerable -- which is exactly the question a refactor needs to answer.
    Block order carries no meaning (they are independent), so any stable order
    will do.
    """
    seen_css = set() if seen_css is None else seen_css
    seen_js = set() if seen_js is None else seen_js
    css_parts: list[str] = []
    js_parts: list[str] = []
    for cls in sorted(types, key=_asset_order):
        c = cls.css()
        if c and c not in seen_css:
            seen_css.add(c)
            css_parts.append(c)
        j = cls.client_js()
        if j and j not in seen_js:
            seen_js.add(j)
            js_parts.append(j)
    return css_parts, js_parts


def _asset_order(cls: type) -> tuple[str, str]:
    """Sort key for component classes: module then qualified name.

    Stable across processes, unlike the identity hash a set iterates by.
    """
    return (cls.__module__, cls.__qualname__)


class RenderContext:
    """Passed to ``Component.render_html()``.

    Provides ID generation, data registration, theme access,
    and child rendering.  One ``RenderContext`` is created per
    ``render_report()`` call (or per scope when scoped).
    """

    def __init__(self, theme: Theme, live_queries: dict | None = None):
        self.theme = theme
        #: Declared live queries (``ReportContext.live_queries``), read by
        #: components whose ``live=`` config references a query id so the
        #: client-visible entry can carry the param schema — never the SQL.
        self.live_queries: dict[str, dict] = live_queries or {}
        self._counter = 0
        self._component_data: dict[str, dict] = {}
        self._raw_js_blocks: list[str] = []
        self._raw_data_blocks: dict[str, Any] = {}
        self._seen_types: set[type] = set()
        self._pending_chunks: dict[str, bytes] = {}
        self._chunk_manifests: dict[str, dict] = {}
        #: dataset_id -> {"cid", "query_id"} for every LiveDataSource
        #: rendered so far. Populated by LiveDataSource.render_html, read by
        #: FilterBar.render_html (which must run after its LiveDataSource,
        #: same convention as DataSource before FilterBar) to merge computed
        #: param bindings into the LiveDataSource's registered config and to
        #: know whether it must render its live/standalone posture at all.
        self.live_datasets: dict[str, dict] = {}

    def next_id(self) -> str:
        self._counter += 1
        return f"fw_c{self._counter}"

    def register(self, cid: str, data: dict) -> None:
        """Register component data to be written to ``data.json``."""
        self._component_data[cid] = data

    def render_children(self, children: list) -> str:
        return "\n".join(self.render_child(c) for c in children)

    def render_child(self, child: Any) -> str:
        if isinstance(child, str):
            return child
        if isinstance(child, Component):
            self._seen_types.add(type(child))
            before = self._counter
            rendered = child.render_html(self)
            return self._stamp_identity(child, rendered, before)
        return f"<!-- Unknown component: {type(child).__name__} -->"

    def _stamp_identity(self, child: "Component", rendered: str, before: int) -> str:
        """Stamp data-fw-* identity attributes onto a component's root element.

        The root is identified as the first id the component allocated
        (``fw_c{before+1}``); a component whose markup doesn't carry that id
        on any element -- or that allocated none -- is left untouched. The
        kind matches diagnostics' ``_comp_kind`` naming (the class name), so
        DOM identity and ``_details.json`` identity agree.
        """
        if self._counter == before:
            return rendered
        root_marker = f'id="fw_c{before + 1}"'
        if root_marker not in rendered:
            return rendered
        import html as _html

        attrs = f'{root_marker} data-fw-kind="{type(child).__name__}"'
        title = getattr(child, "title", "")
        if title:
            attrs += f' data-fw-title="{_html.escape(str(title), quote=True)}"'
        return rendered.replace(root_marker, attrs, 1)

    def add_raw_js(self, js: str) -> None:
        self._raw_js_blocks.append(js)

    def add_raw_data(self, key: str, data: Any) -> None:
        self._raw_data_blocks[key] = data

    def reset(self) -> None:
        self._counter = 0
        self._component_data = {}
        self._raw_js_blocks = []
        self._raw_data_blocks = {}
        self._seen_types = set()
        self._pending_chunks = {}
        self._chunk_manifests = {}
        self.live_datasets = {}

    @property
    def component_data(self) -> dict[str, dict]:
        return self._component_data

    @property
    def raw_js_blocks(self) -> list[str]:
        return self._raw_js_blocks

    @property
    def raw_data_blocks(self) -> dict[str, Any]:
        return self._raw_data_blocks

    def collect_assets(self) -> tuple[str, str]:
        """Collect unique CSS and JS from all rendered component types.

        Returns ``(css_string, js_string)``; see :func:`collect_asset_parts`
        for the deduplication and ordering rules.
        """
        css_parts, js_parts = collect_asset_parts(self._seen_types)
        return "\n".join(css_parts), "\n".join(js_parts)

    def collect_cdn_deps(self) -> set[str]:
        """Collect CDN dependency keys from all rendered component types."""
        deps: set[str] = set()
        for cls in sorted(self._seen_types, key=_asset_order):
            deps.update(cls.cdn_deps())
        return deps
