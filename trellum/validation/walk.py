"""Walking a finished report: the component tree, ids and columns.

Every check starts by asking the same questions of the same tree, so
the asking lives here and the checks stay about their own rule."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


def _get_filterable_types() -> set[str]:
    """Derive filterable component names from _supports_dataset_id metadata."""
    from trellum.components.base import Component

    result: set[str] = set()

    def _scan(cls: type) -> None:
        for sub in cls.__subclasses__():
            if getattr(sub, "_supports_dataset_id", False) and not sub.__name__.startswith("_"):
                result.add(sub.__name__)
            _scan(sub)

    _scan(Component)
    return result

def _iter_all_sections(ctx: Any) -> list[dict]:
    """Return flat list of {title, components} dicts across sections and scopes."""
    sections = list(ctx.sections)
    for scope_sections in ctx.scopes.values():
        sections.extend(scope_sections)
    return sections

def _walk_components(components: list, *, section_title: str = "") -> list[tuple[Any, str]]:
    """Recursively flatten a component list into (component, section_title) pairs."""
    from trellum.components.controls import TabGroup
    from trellum.components.layout import Grid, Panel, Section, SplitPane, Visible

    results: list[tuple[Any, str]] = []
    for comp in components:
        results.append((comp, section_title))
        children = []
        if isinstance(comp, (Grid, Panel, Section)):
            children = getattr(comp, "children", [])
        elif isinstance(comp, SplitPane):
            children = [comp.left, comp.right]
        elif isinstance(comp, Visible):
            children = getattr(comp, "children", [])
        elif isinstance(comp, TabGroup):
            for tab in comp.tabs:
                children.extend(tab.get("content", []))
        if children:
            results.extend(_walk_components(children, section_title=section_title))
    return results

def _all_components(ctx: Any) -> list[tuple[Any, str]]:
    """Walk all sections (flat + scoped) and return (component, section_title) pairs."""
    pairs: list[tuple[Any, str]] = []
    for sec in _iter_all_sections(ctx):
        title = sec.get("title", "")
        pairs.extend(_walk_components(sec.get("components", []), section_title=title))
    return pairs

def _comp_name(comp: Any) -> str:
    return type(comp).__name__

def _df_columns(df: Any) -> set[str]:
    if isinstance(df, pd.DataFrame):
        return set(df.columns)
    return set()

def _resolve_ds_columns(ds_id: str, ds_map: dict[str, Any]) -> set[str]:
    """Walk past ScopedDataSource to its base DataSource and read its df cols.

    Shared by the columns check and the metrics check: both answer "which
    column names can a component on this dataset legally reference".
    """
    from trellum.components.filterable import ScopedDataSource

    seen: set[str] = set()
    while ds_id in ds_map and ds_id not in seen:
        seen.add(ds_id)
        ds = ds_map[ds_id]
        if isinstance(ds, ScopedDataSource):
            ds_id = ds.parent
            continue
        return _df_columns(getattr(ds, "df", None))
    return set()

def _kpi_row_is_fully_static(kpis: Any) -> bool:
    """True when every entry in a KpiRow is a Python-precomputed value.

    Static KPIs have a concrete ``value`` and none of the live-aggregation
    fields (``agg``/``column``/``columns``/``numerator``). Such rows don't
    need a ``dataset_id`` — they render Python-time constants and don't
    react to filters. Non-dict entries (e.g. KpiCard instances) return
    False so we fall back to the normal missing-dataset-id behaviour.
    """
    if not kpis:
        return False
    for k in kpis:
        if not isinstance(k, dict):
            return False
        if k.get("agg") or k.get("column") or k.get("columns") or k.get("numerator"):
            return False
        if "value" not in k:
            return False
    return True

def _collect_all_html_ids(comps: list[tuple[Any, str]]) -> set[str]:
    """Gather DOM ids from ALL RawHTML components (across sections/scopes).

    Scans both static HTML templates and JS strings that create elements
    via innerHTML (e.g. ``id="efXRayClose"`` inside a JS string literal).
    """
    from trellum.components.layout import RawHTML as RawHTMLComp

    _ID_IN_HTML = re.compile(r'id\s*=\s*["\']([^"\']+)["\']')
    _ID_IN_JS = re.compile(r"""id\s*=\s*(?:\\?['"]|&quot;)([A-Za-z_][\w-]*)(?:\\?['"]|&quot;)""")
    ids: set[str] = set()
    for comp, _ in comps:
        if not isinstance(comp, RawHTMLComp):
            continue
        if comp.html:
            ids.update(_ID_IN_HTML.findall(comp.html))
        if comp.js:
            ids.update(_ID_IN_JS.findall(comp.js))
    return ids

def _is_section_scoped_pair(section_comps: list) -> bool:
    """A section is "section-scoped" when it opens with one or more
    DataSource(s) or ScopedDataSource(s) followed by exactly one FilterBar,
    before any chart / KPI / table component. That's the canonical layout
    for a drill-down section in the main + section FilterBar model.
    """
    from trellum.components.filterable import DataSource, FilterBar, ScopedDataSource

    saw_filterbar = False
    saw_datasource = False
    for c in section_comps:
        if isinstance(c, (DataSource, ScopedDataSource)):
            if saw_filterbar:
                return False  # DataSource after FilterBar — irregular
            saw_datasource = True
            continue
        if isinstance(c, FilterBar):
            if saw_filterbar:
                return False  # >1 FilterBar — separate validator catches this
            saw_filterbar = True
            continue
        # First non-DS, non-FB component — must come AFTER the pair
        return saw_filterbar and saw_datasource
    # Section was only DS + FB (no charts) — also valid as the pair pattern
    return saw_filterbar and saw_datasource
