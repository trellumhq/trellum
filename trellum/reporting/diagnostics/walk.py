"""Reading a rendered component tree: kinds, columns, and what is static."""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd

FilterStatus = Literal["active", "propagated", "inactive", "untracked"]

def _walk_components(components: list, *, section_title: str = "") -> list[tuple[Any, str]]:
    """Recursively flatten a component list into (component, section_title) pairs."""
    from trellum.components.controls import TabGroup
    from trellum.components.layout import Grid, Panel, Section, SplitPane, Visible

    results: list[tuple[Any, str]] = []
    for comp in components:
        results.append((comp, section_title))
        children: list = []
        if isinstance(comp, (Grid, Panel, Section)):
            children = getattr(comp, "children", []) or []
        elif isinstance(comp, SplitPane):
            children = [comp.left, comp.right]
        elif isinstance(comp, Visible):
            children = getattr(comp, "children", []) or []
        elif isinstance(comp, TabGroup):
            for tab in comp.tabs:
                children.extend(tab.get("content", []))
        if children:
            results.extend(_walk_components(children, section_title=section_title))
    return results

def _walk_view(sections: list[dict]) -> list[tuple[Any, str]]:
    pairs: list[tuple[Any, str]] = []
    for sec in sections:
        title = sec.get("title", "")
        pairs.extend(_walk_components(sec.get("components", []), section_title=title))
    return pairs

def _df_columns(df: Any) -> set[str]:
    if isinstance(df, pd.DataFrame):
        return set(df.columns)
    return set()

def _is_filterable(comp: Any) -> bool:
    return bool(getattr(comp, "_supports_dataset_id", False))

def _comp_kind(comp: Any) -> str:
    return type(comp).__name__

def _chart_value_columns(comp: Any) -> list[str]:
    """Return the value-bearing columns a chart aggregates from its DS.

    Best-effort introspection — covers the common chart attributes
    (``y``, ``y_cols``, ``ratios``, ``bar_cols``, ``line_cols``,
    ``value``, ``size``, ``color_col``). Components we can't introspect
    return [] and the grain-mismatch check is skipped for them. KpiRow
    walks each kpi spec.
    """
    cols: list[str] = []

    def _add(name: Any) -> None:
        if isinstance(name, str) and name and name not in cols:
            cols.append(name)

    # Direct y / y_cols
    y = getattr(comp, "y", None)
    if isinstance(y, list):
        for v in y:
            _add(v)
    else:
        _add(y)
    y_cols = getattr(comp, "y_cols", None)
    if isinstance(y_cols, list):
        for v in y_cols:
            _add(v)

    # Ratios (LineChart, KpiRow)
    ratios = getattr(comp, "ratios", None)
    if isinstance(ratios, list):
        for r in ratios:
            if isinstance(r, dict):
                _add(r.get("numerator"))
                _add(r.get("denominator"))

    # ComboChart
    for attr in ("bar_cols", "line_cols"):
        v = getattr(comp, attr, None)
        if isinstance(v, list):
            for x in v:
                _add(x)

    # Doughnut / Funnel / Treemap / Heatmap / Scatter
    for attr in ("value", "size", "color_col", "color_by"):
        _add(getattr(comp, attr, None))

    # KpiRow: walk every kpi spec
    kpis = getattr(comp, "kpis", None)
    if isinstance(kpis, list):
        for k in kpis:
            if not isinstance(k, dict):
                continue
            _add(k.get("column"))
            _add(k.get("numerator"))
            _add(k.get("denominator"))
            kc = k.get("columns")
            if isinstance(kc, list):
                for c in kc:
                    _add(c)

    return cols

def _detect_static_columns(df: Any, x_col: str | None, value_cols: list[str]) -> list[str]:
    """Return the subset of ``value_cols`` whose values are constant
    within each value of ``x_col`` *despite the DataSource having
    multiple rows per x*.

    A column is "static per x" when ``df.groupby(x_col)[col].nunique()``
    never exceeds 1 — but only when there ARE multiple rows per x in
    the first place. If the DataSource is itself at x-grain (one row
    per x), every column is trivially nunique=1 per group; that's the
    natural shape of the data, not a grain mismatch.

    The actual mismatch we want to flag is "the DS has finer grain than
    the column — values were duplicated across sub-grain rows". So we
    require both:
      1. ``df.groupby(x_col).size().max() > 1`` (DS is finer than x), AND
      2. the column's nunique never exceeds 1 within any group.
    """
    if not isinstance(df, pd.DataFrame) or not x_col or x_col not in df.columns:
        return []
    if len(df) == 0:
        return []
    try:
        sizes = df.groupby(x_col, dropna=False).size()
    except Exception:
        return []
    if len(sizes) == 0 or int(sizes.max()) <= 1:
        # DS is at x-grain (or coarser) — no sub-grain, no mismatch.
        return []
    static: list[str] = []
    for col in value_cols:
        if col not in df.columns:
            continue
        # A literal unit column -- 1 on every row -- is a row counter, put
        # there so `sum` means "count" and ratio aggs can divide by it. It is
        # constant per x BY CONSTRUCTION, not because a coarser table was
        # merged in, and the harm the check describes cannot occur: the
        # column re-sums from row grain like any other measure. Three demo
        # reports hit this false positive before the exemption was added.
        try:
            if (df[col] == 1).all():
                continue
        except Exception:
            pass
        try:
            nu = df.groupby(x_col, dropna=False)[col].nunique(dropna=False)
        except Exception:
            continue
        if len(nu) == 0:
            continue
        # 100% strictness — even a single x with >1 distinct value
        # disproves static. Real columns vary in nearly every group.
        if int(nu.max()) <= 1:
            static.append(col)
    return static
