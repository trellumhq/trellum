"""Filterable components for client-side interactive filtering.

Two components provide the data + filter plumbing:
  1. DataSource -- sends raw DataFrame rows to the browser
  2. FilterBar  -- renders controls that update filter state

Standard chart / KPI / table components become filter-aware when
constructed with ``dataset_id`` set (see ``charts.py``, ``kpis.py``,
``tables.py``).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import orjson
import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.components.filters import get_all_filters, get_filter
from trellum.components.live_filterable import BINDABLE_FILTER_TYPES as _BINDABLE_FILTER_TYPES
from trellum.components.live_filterable import LiveDataSource  # noqa: F401 -- re-exported
from trellum.components.live_filterable import bake_disabled as _bake_disabled
from trellum.components.live_filterable import build_binding as _build_binding
from trellum.components.live_filterable import derive_enum_options as _derive_enum_options
from trellum.components.live_filterable import live_meta_html as _live_meta_html


def _serialize_columnar(df: pd.DataFrame) -> dict:
    """Serialize DataFrame in columnar format with optional dictionary encoding.

    Returns ``{"_cols": [...], "_data": [[...], ...]}`` plus an optional
    ``"_dict"`` mapping for columns with high repetition (unique/total < 0.3).
    Dictionary-encoded columns store integer indices in ``_data`` instead of
    repeated strings, typically reducing JSON size by 60-70%.

    The client-side ``data_source`` renderer expands indices back to values
    before passing rows to the filter engine.
    """
    import numpy as np

    cols = list(df.columns)
    n_rows = len(df)
    col_arrays: list[list] = []
    dict_maps: dict[str, list] = {}

    for col in cols:
        series = df[col]
        dtype = series.dtype
        is_numeric = False

        if pd.api.types.is_datetime64_any_dtype(dtype):
            notna = series.dropna()
            has_time = False
            if len(notna) > 0:
                times = notna.dt.time
                has_time = (times != pd.Timestamp("00:00:00").time()).any()
            fmt = "%Y-%m-%d %H:%M" if has_time else "%Y-%m-%d"
            arr = series.dt.strftime(fmt)
            na_mask = series.isna()
            if na_mask.any():
                arr = arr.where(~na_mask, None)
            values = arr.tolist()

        elif pd.api.types.is_float_dtype(dtype):
            is_numeric = True
            raw = series.to_numpy()
            bad = np.isnan(raw) | np.isinf(raw)
            if bad.any():
                values = [None if bad[i] else raw[i] for i in range(n_rows)]
            else:
                values = raw.tolist()

        elif pd.api.types.is_integer_dtype(dtype):
            is_numeric = True
            if series.isna().any():
                values = [
                    None if v != v else v  # NaN != NaN
                    for v in series.astype(object).tolist()
                ]
            else:
                values = series.tolist()

        elif dtype == object or pd.api.types.is_string_dtype(dtype):
            raw = series.to_numpy(dtype=object, na_value=None)
            has_isoformat = False
            for v in raw:
                if v is not None and hasattr(v, "isoformat"):
                    has_isoformat = True
                    break
            if has_isoformat:
                values = [
                    v.isoformat()[:16] if hasattr(v, "isoformat") else (
                        None if v is None or (isinstance(v, float) and v != v)
                        else v
                    )
                    for v in raw
                ]
            else:
                values = [
                    None if v is None or (isinstance(v, float) and v != v)
                    else v
                    for v in raw
                ]
        else:
            values = series.tolist()

        if not is_numeric and n_rows > 0:
            nunique = series.nunique(dropna=False)
            if nunique / n_rows < 0.3:
                vocab = list(dict.fromkeys(values))
                lookup = {v: i for i, v in enumerate(vocab)}
                values = [lookup.get(v, v) for v in values]
                dict_maps[col] = vocab

        col_arrays.append(values)

    data = [list(row) for row in zip(*col_arrays)] if col_arrays else []
    result: dict = {"_cols": cols, "_data": data}
    if dict_maps:
        result["_dict"] = dict_maps
    return result


def _find_date_col(df: pd.DataFrame) -> str | None:
    """Return the first datetime64 column, or fall back to a date-like name."""
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col].dtype):
            return col
    for col in df.columns:
        if col.lower() in ("event_date", "date", "period", "dt"):
            return col
    # Any other *_date column (cohort_date, order_date, assigned_date, ...).
    # Without this, chunk_by silently does nothing for perfectly ordinary
    # column names.
    for col in df.columns:
        if col.lower().endswith("_date"):
            return col
    return None


def _period_key(date_str: str, chunk_by: str) -> str:
    """Return the period bucket key for a date string."""
    if chunk_by == "month":
        return date_str[:7]  # "YYYY-MM"
    if chunk_by == "week":
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return d.strftime("%G-W%V")  # ISO year + week, e.g. "2026-W17"
    return date_str[:7]


def _period_range(key: str, chunk_by: str) -> tuple[str, str]:
    """Return (min_date, max_date) strings for a period key."""
    if chunk_by == "month":
        year, month = int(key[:4]), int(key[5:7])
        last_day = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"
    if chunk_by == "week":
        # Parse ISO week: "%G-W%V" → Monday of that week
        monday = datetime.strptime(key + "-1", "%G-W%V-%u")
        sunday = monday + timedelta(days=6)
        return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")
    return key, key


@dataclass
class DataSource(Component):
    """Sends DataFrame rows to the browser for client-side filtering.

    Rows are stored in ``data.json`` under ``_ds_{id}`` and loaded into
    the filter engine on page load.

    Optional chunking parameters:

    - ``chunk_by``: ``"month"`` | ``"week"`` | ``None`` (default). When set,
      splits the DataFrame by calendar period. Only the most-recent
      ``default_chunks`` periods are embedded inline; older periods are written
      as separate ``data_chunk_{id}_{period}.json`` files and fetched lazily
      when the user expands the date range.
    - ``default_chunks``: number of most-recent periods to embed inline (default 3).
    """

    id: str
    df: pd.DataFrame
    chunk_by: str | None = None
    default_chunks: int = 3

    _component_type: str = field(default="data_source", init=False, repr=False)
    _no_section_wrap: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        if self.chunk_by and len(self.df) > 0:
            self._render_chunked(ctx)
        else:
            columnar = _serialize_columnar(self.df)
            ctx.add_raw_data(f"_ds_{self.id}", columnar)

        cid = ctx.next_id()
        ctx.register(cid, {"type": "data_source", "dataset_id": self.id})
        return f'<div id="{cid}" style="display:none"></div>'

    def _render_chunked(self, ctx: RenderContext) -> None:
        date_col = _find_date_col(self.df)
        if date_col is None:
            # No date column found — fall back to inline (no chunking). Say so:
            # silently ignoring chunk_by looks like the feature is broken.
            print(
                f"  [warn] DataSource '{self.id}': chunk_by={self.chunk_by!r} was "
                f"ignored -- no date column found among {list(self.df.columns)}. "
                f"Chunking needs a datetime column or one named *_date.",
                flush=True,
            )
            ctx.add_raw_data(f"_ds_{self.id}", _serialize_columnar(self.df))
            return

        df = self.df.copy()
        # Produce a plain string date column for period grouping
        if pd.api.types.is_datetime64_any_dtype(df[date_col].dtype):
            date_strs = df[date_col].dt.strftime("%Y-%m-%d")
        else:
            date_strs = df[date_col].astype(str)

        period_col = "__fw_period__"
        df[period_col] = date_strs.apply(lambda v: _period_key(v, self.chunk_by))

        # Sort periods newest-first
        all_periods = sorted(df[period_col].unique(), reverse=True)
        inline_periods = all_periods[: self.default_chunks]
        chunk_periods = all_periods[self.default_chunks :]

        # Inline data: most-recent periods
        inline_df = df[df[period_col].isin(inline_periods)].drop(columns=[period_col])
        ctx.add_raw_data(f"_ds_{self.id}", _serialize_columnar(inline_df))

        # Chunk files: older periods
        chunk_manifest: dict[str, dict] = {}
        for period in chunk_periods:
            period_df = df[df[period_col] == period].drop(columns=[period_col])
            pmin, pmax = _period_range(period, self.chunk_by)
            fname = f"data_chunk_{self.id}_{period}.json"
            payload = {f"_ds_{self.id}": _serialize_columnar(period_df)}
            ctx._pending_chunks[fname] = orjson.dumps(
                payload,
                option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS,
            )
            chunk_manifest[period] = {"file": fname, "min": pmin, "max": pmax}

        ctx._chunk_manifests[self.id] = {
            "chunk_by": self.chunk_by,
            "loaded_periods": list(inline_periods),
            "chunks": chunk_manifest,
        }

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/data_source.js")


# LiveDataSource lives in live_filterable.py (imported above) -- a
# DataSource-shaped registrar too, just for a declared live query instead
# of a plain DataFrame.


@dataclass
class ScopedDataSource(Component):
    """A virtual DataSource that derives its rows from a parent DataSource.

    Use this when a section needs its own filter dimension (e.g. *Price Tier*
    on a CPD chart) that should NOT bleed into the rest of the report. The
    child shares the parent's underlying data — no DataFrame is duplicated
    in Python or in ``data.json`` — and the parent's filters automatically
    apply (no ``propagate_to`` wiring needed).

    Typical pattern::

        # Untitled top section: main DataSource + main FilterBar
        ctx.add_section("", [
            DataSource("cpd", df),
            FilterBar("cpd", df, filters=[
                {"column": "event_date", "type": "date_range"},
                {"column": "package_group"},
            ]),
        ])

        # Section with its OWN filter dimension that only affects this section
        ctx.add_section("CPD by Price Point", [
            ScopedDataSource("cpd_pp", parent="cpd"),
            FilterBar("cpd_pp", df, filters=[
                {"column": "price_tier"},
            ]),
            LineChart(df=df, x="event_date", dataset_id="cpd_pp",
                      stack_by="price_point_display",
                      ratios=[{"numerator": "chips_allocated",
                                "denominator": "revenue", "label": "CPD"}],
                      title="CPD by Price Point"),
        ])

    Client-side, ``getFiltered("cpd_pp")`` returns the parent's currently
    filtered rows further filtered by the section's local filters. Charts
    using ``dataset_id="cpd_pp"`` react to BOTH the main FilterBar (via the
    parent) and the section FilterBar (via the child) automatically.

    Constraints:

    - ``parent`` must reference an existing ``DataSource`` declared earlier
      in the same report. The validator enforces this.
    - A scoped child cannot itself be a chunked DataSource — chunking is a
      property of the parent's columnar storage.
    - A scoped child SHOULD have a section-scoped ``FilterBar`` attached.
      Without one it just mirrors the parent and adds no value.
    """

    id: str
    parent: str

    _component_type: str = field(default="scoped_data_source", init=False, repr=False)
    _no_section_wrap: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        ctx.register(cid, {
            "type": "scoped_data_source",
            "dataset_id": self.id,
            "parent_id": self.parent,
        })
        return f'<div id="{cid}" style="display:none"></div>'

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/scoped_data_source.js")


@dataclass
class FilterBar(Component):
    """Row of filter controls bound to a DataSource.

    Delegates rendering and interaction to filter plugins registered in
    ``trellum.components.filters``.  Each filter spec dict in ``filters``
    is dispatched to the plugin matching its ``type`` key.

    Filter types (built-in):

    - ``"dropdown"`` (default): ``<select>`` with (All) + distinct column values
    - ``"toggle"``: button group with All + distinct values (best for 2-4 options)
    - ``"flag"``: 3-way toggle for All / Exclude {value} / Only {value}
    - ``"date_range"``: date picker with Kibana-style quick-select presets
    - ``"slider"``: noUiSlider control -- ``mode="range"`` (default) for a
      continuous min/max band, ``mode="single"`` to snap to one distinct
      value. Numeric by default; give it an explicit ``"values"`` list (an
      ordered list of category strings) and it becomes an ORDINAL slider
      over those categories instead -- the column need not be numeric

    Pick by shape of the column, not habit: a continuous numeric range (a
    discount %, a price, a quantity) fits ``slider``, where enumerating every
    value as ``dropdown`` options would be absurd; ordered categories with no
    numeric value of their own (a spender tier, a severity level) fit the
    ordinal form of ``slider``; unordered categorical values fit ``dropdown``;
    a handful of distinct values fit ``toggle``; dates always stay on
    ``date_range`` -- never model a date column as a ``slider``.

    ``propagate_to`` syncs filter changes from the primary DataSource to
    secondary DataSources that may have different column names or grains.
    Each entry maps ``{target_ds_id: {source_col: target_col, ...}}``.
    Only listed columns are propagated; unlisted columns are ignored.

    ``"text"`` is a fifth built-in filter type: a labeled free-text input
    that commits on Enter or blur only (never on keystroke). It is intended
    almost entirely for live-query bindings (see below) -- an id or name
    field a viewer types in and commits explicitly.

    ── Binding a FilterBar to a live dataset ──────────────────────────────

    When ``dataset_id`` names a :class:`LiveDataSource` (declared earlier in
    the same section list -- the same DataSource-before-FilterBar ordering
    convention used everywhere else), every filter spec must additionally
    carry the query param it drives:

    - Scalar filters (``dropdown``, ``toggle``, ``flag``, single-mode
      ``slider``, ``text``) take a ``"param"`` key.
    - Range-shaped filters (``date_range``, range-mode ``slider``) take
      ``"min_param"``/``"max_param"`` -- one filter, two params.
    - ``"sentinel"`` overrides the enum value a deselected/"'All'" commit
      maps to (default: ``"all"`` when the declared param's ``values``
      include it).

    A live FilterBar renders server-side disabled with an honest note (a
    share link, an email snapshot, or a plain static file all show it) and
    only the serving host's runtime ever enables it -- see
    ``static/js/runtime/live_query.js`` and docs/COMPATIBILITY.md. Dropdown
    and toggle options for an enum param are derived from the param's own
    declared ``values`` (minus the sentinel) unless the filter spec gives
    explicit ``options`` -- the live domain is usually broader than
    whatever the build-time snapshot happened to contain. Live-bound
    dropdowns are single-select in v1 (multi-select needs a list-param
    server contract this build doesn't have yet) -- pass ``"multi": False``
    or leave it at the live-bound default.
    """

    dataset_id: str
    df: pd.DataFrame
    filters: List[Dict[str, Any]]
    propagate_to: Optional[Dict[str, Dict[str, str]]] = None

    _component_type: str = field(default="filter_bar", init=False, repr=False)
    _no_section_wrap: bool = field(default=True, init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        live_target = ctx.live_datasets.get(self.dataset_id)
        declared_params: dict[str, dict] = {}
        if live_target:
            declared = ctx.live_queries.get(live_target["query_id"]) or {}
            declared_params = {
                str(p.get("name")): p for p in declared.get("params") or []
            }

        filter_configs: list[dict] = []
        html_parts: list[str] = []
        bindings: list[dict] = []

        for i, f in enumerate(self.filters):
            col = f["column"]
            label = f.get("label", col)
            ftype = f.get("type", "dropdown")
            fid = f"{cid}_f{i}"

            f_eff = f
            if live_target:
                derived = _derive_enum_options(ftype, f, declared_params)
                if derived is not None:
                    f_eff = derived

            plugin = get_filter(ftype)
            filter_configs.append(plugin.build_config(fid, col, f_eff, self.df))
            control_html = plugin.render_html(fid, col, label, f_eff, self.df)
            if live_target:
                control_html = _bake_disabled(control_html)
            html_parts.append(control_html)

            if live_target and ftype in _BINDABLE_FILTER_TYPES:
                binding = _build_binding(col, ftype, f, declared_params)
                if binding:
                    bindings.append(binding)

        reg: dict[str, Any] = {
            "type": "filter_bar",
            "dataset_id": self.dataset_id,
            "filters": filter_configs,
        }
        if self.propagate_to:
            reg["propagate_to"] = self.propagate_to
        if live_target:
            reg["live_dataset"] = True
            # Merge this FilterBar's computed bindings into the paired
            # LiveDataSource's already-registered config -- the binder
            # (window._fwLiveQuery) reads bindings off THAT entry, keyed by
            # dataset_id, not off this filter_bar entry.
            live_reg = ctx.component_data.get(live_target["cid"])
            if live_reg is not None:
                live_reg.setdefault("live", {}).setdefault("bindings", [])
                live_reg["live"]["bindings"].extend(bindings)
        ctx.register(cid, reg)

        share_btn = (
            '<button class="fw-share-btn" title="Copy filter link">'
            '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" '
            'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07'
            'l-1.72 1.71"/>'
            '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07'
            'l1.71-1.71"/></svg></button>'
        )

        if not live_target:
            return (
                f'<div class="fw-filter-bar" id="{cid}" '
                f'data-fw-filter-bar="1" data-fw-ds-id="{self.dataset_id}">'
                f'{"".join(html_parts)}{share_btn}</div>'
            )

        # Live posture: server-rendered disabled with the honest note. The
        # runtime only ever enables (see static/js/runtime/live_query.js);
        # a page with no host, an old host, or an anonymous share link
        # never wires this bar and never issues a request.
        return (
            f'<div class="fw-filter-bar fw-live-bar" id="{cid}" '
            f'data-fw-filter-bar="1" data-fw-ds-id="{self.dataset_id}" '
            f'data-live-state="standalone">'
            f'{"".join(html_parts)}{_live_meta_html()}{share_btn}</div>'
        )

    @classmethod
    def cdn_deps(cls) -> list[str]:
        deps: list[str] = []
        for plugin_cls in get_all_filters():
            deps.extend(plugin_cls.cdn_deps())
        return deps

    @classmethod
    def css(cls) -> str:
        from trellum.components.controls import Dropdown, Toggle

        own = load_css("components/filter_bar.css") + "\n" + load_css("components/live_query.css")
        plugin_css = "\n".join(
            pcls.css() for pcls in get_all_filters() if pcls.css()
        )
        return Toggle.css() + "\n" + Dropdown.css() + "\n" + own + "\n" + plugin_css

    @classmethod
    def client_js(cls) -> str:
        plugin_js_parts = [
            pcls.client_js() for pcls in get_all_filters() if pcls.client_js()
        ]
        plugin_js = "\n".join(plugin_js_parts)

        orchestrator = load_js("components/filter_bar.js")
        return plugin_js + "\n" + orchestrator
