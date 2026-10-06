"""Live-query-specific pieces of the filter system: ``LiveDataSource`` and
the helpers ``FilterBar`` (in ``filterable.py``) uses to bind its filters to
a live dataset's declared query params.

Split out of ``filterable.py`` -- a live FilterBar is still an ordinary
FilterBar, but everything IT needs to know about "this dataset is backed by
a declared live query" lives here, so filterable.py stays about the
generic DataSource/FilterBar plumbing.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext

#: The standalone degradation label. Exact wording is part of the
#: live-query contract: it is what a share link, email snapshot, or old
#: host shows (moved here from components/tables.py -- the honest note is
#: now FilterBar markup, not per-table markup; see docs/COMPATIBILITY.md
#: and testing/test_live_query.py).
LIVE_STANDALONE_NOTE = (
    "Live lookup — available when served by a host; "
    "showing data from the last build"
)

#: Filter types the live binder knows how to map to query params, and the
#: engine mode each commits under (documented here so this module is the
#: one place that has to agree with the JS binder's switch in
#: static/js/runtime/live_query.js on "what a binding means").
BINDABLE_FILTER_TYPES = frozenset({"dropdown", "toggle", "flag", "slider", "date_range", "text"})

#: Enum-typed filters whose options should be DERIVED from the declared
#: param's ``values`` (minus the sentinel) rather than from the snapshot
#: DataFrame's unique values -- the snapshot is one build-time query's worth
#: of rows and may not contain every value the live domain actually has
#: (e.g. a one-user snapshot might show only "Android", but "iOS"/"Web"
#: must still be selectable).
ENUM_DERIVED_TYPES = frozenset({"dropdown", "toggle"})

_DISABLE_TAG_RE = _re.compile(r"<(select|input|button)\b")


def bake_disabled(html: str) -> str:
    """*html* with ``disabled`` injected onto every native form control tag.

    Belt-and-suspenders for a live FilterBar's standalone posture: the real
    guarantee that a disabled bar issues zero requests is that
    ``filter_bar.js`` never wires it (no SlimSelect/noUiSlider instance, no
    event listener attached -- see ``window._fwLiveQuery``), but baking the
    HTML ``disabled`` attribute in at build time means the page reads as
    honestly non-interactive even a frame before any JS runs, exactly like
    the M1 control did. A blanket tag-name regex (rather than touching each
    filter plugin) is deliberate -- it works uniformly across whatever
    plugin types a live FilterBar mixes, with zero coupling from this
    module into dropdown.py/toggle.py/text.py's own render_html.
    """
    return _DISABLE_TAG_RE.sub(lambda m: m.group(0) + " disabled", html)


def resolve_sentinel(f: Dict[str, Any], pdef: dict) -> str:
    """The engine's '__all__'/deselected-state value maps to this declared
    enum member -- an explicit ``"sentinel"`` on the filter spec wins, else
    ``"all"`` when the declared param's ``values`` include it, else the
    param's first declared value."""
    if f.get("sentinel") is not None:
        return str(f["sentinel"])
    values = [str(v) for v in pdef.get("values") or []]
    return "all" if "all" in values else (values[0] if values else "")


def derive_enum_options(ftype: str, f: Dict[str, Any], declared_params: dict) -> Dict[str, Any] | None:
    """A copy of *f* with ``options`` derived from the declared enum param's
    ``values`` (minus the sentinel), or None when no derivation applies (an
    explicit ``options`` on *f* always wins, and only dropdown/toggle
    support derivation at all)."""
    if ftype not in ENUM_DERIVED_TYPES or "options" in f:
        return None
    param_name = f.get("param")
    pdef = declared_params.get(param_name) if param_name else None
    if not pdef or pdef.get("type") != "enum":
        return None
    sentinel = resolve_sentinel(f, pdef)
    derived = [str(v) for v in pdef.get("values") or [] if str(v) != sentinel]
    if not derived:
        return None
    f_eff = dict(f)
    f_eff["options"] = derived
    return f_eff


def build_binding(
    col: str, ftype: str, f: Dict[str, Any], declared_params: dict,
) -> dict | None:
    """One ``{param|min_param/max_param, column, filter_type, sentinel}``
    entry for the live binder, or None when *f* names no param at all
    (validation -- ``live-query-filter-missing-param`` -- is what catches
    that; rendering degrades to "this filter never queries" rather than
    raising, matching every other checkable-not-raisable invariant in the
    filter system)."""
    param = f.get("param")
    min_param = f.get("min_param")
    max_param = f.get("max_param")
    if not param and not (min_param and max_param):
        return None
    binding: dict[str, Any] = {"column": col, "filter_type": ftype}
    if param:
        binding["param"] = str(param)
        pdef = declared_params.get(str(param))
        if pdef is not None and pdef.get("type") == "enum":
            binding["sentinel"] = resolve_sentinel(f, pdef)
        elif f.get("sentinel") is not None:
            binding["sentinel"] = str(f["sentinel"])
    if min_param:
        binding["min_param"] = str(min_param)
    if max_param:
        binding["max_param"] = str(max_param)
    return binding


def live_meta_html() -> str:
    """The chip/freshness/note/status block a live FilterBar appends after
    its filter controls -- one status surface for the whole bar, replacing
    the M1 per-table freshness line."""
    return (
        f'<div class="fw-live-meta">'
        f'<span class="fw-live-chip"><span class="fw-live-dot"></span>live</span>'
        f'<span class="fw-live-freshness"></span>'
        f'<span class="fw-live-note">{LIVE_STANDALONE_NOTE}</span>'
        f'<span class="fw-live-status" hidden></span>'
        f'</div>'
    )


@dataclass
class LiveDataSource(Component):
    """Registers a declared live query as a filter-engine dataset.

    The live query becomes a DATASET, not a bespoke control: ``df`` (the
    build-time snapshot -- normally the return value of
    ``ctx.declare_live_query``) is serialized into ``data.json`` under
    ``_ds_{id}`` exactly like :class:`~trellum.components.filterable.DataSource`,
    and the client registers it with the filter engine as *live*. An
    ordinary :class:`~trellum.components.filterable.FilterBar` bound to
    this ``id`` drives it -- each filter spec names the declared query
    param it feeds via a ``param`` (or ``min_param``/``max_param``) key.
    Every chart/table/KPI row built with ``dataset_id=id`` reacts exactly
    as it would to a normal dataset; it never knows the rows came from a
    POST rather than a local filter.

    Declare this BEFORE the FilterBar that binds it (the same convention as
    DataSource before its FilterBar everywhere else in the framework) --
    the FilterBar looks up this component's registration to merge its
    computed param bindings in.

    Args:
        id: Dataset id other components reference via ``dataset_id``.
        query: A query id previously declared via ``ctx.declare_live_query``.
        df: The snapshot DataFrame (``declare_live_query``'s return value).
        bindings: Optional explicit column→param bindings, for a dataset fed
            only via a FilterBar's ``propagate_to`` rather than a FilterBar
            of its own -- e.g. a second live query ("top actors") that
            shares its filters with the primary one and renders no controls
            of its own. Each entry: ``{"column", "param"}`` or
            ``{"column", "min_param", "max_param"}`` for a range-shaped
            propagated filter, plus optional ``"filter_type"``/``"sentinel"``
            (defaulted the same way a FilterBar-declared binding is). Not
            needed when a FilterBar with ``dataset_id=id`` exists -- that
            FilterBar's own filters populate bindings automatically.
    """

    id: str
    query: str
    df: pd.DataFrame
    bindings: Optional[List[Dict[str, Any]]] = None

    _component_type: str = field(default="live_data_source", init=False, repr=False)
    _no_section_wrap: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        from trellum.components.filterable import _serialize_columnar

        declared = ctx.live_queries.get(self.query) or {}
        columnar = _serialize_columnar(self.df)
        ctx.add_raw_data(f"_ds_{self.id}", columnar)

        cid = ctx.next_id()
        live_cfg: dict[str, Any] = {
            "query_id": self.query,
            "params": declared.get("params") or [],
            "defaults": declared.get("snapshot_params") or {},
            "bindings": [dict(b) for b in (self.bindings or [])],
        }
        ctx.register(cid, {
            "type": "live_data_source",
            "dataset_id": self.id,
            "live": live_cfg,
        })
        # Recorded so a FilterBar declared afterward with dataset_id=self.id
        # can find and merge its own bindings into this component's config,
        # and so the FilterBar knows to render its live/standalone posture
        # at all. See FilterBar.render_html in filterable.py.
        ctx.live_datasets[self.id] = {"cid": cid, "query_id": self.query}
        return f'<div id="{cid}" style="display:none"></div>'

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/live_data_source.js")
