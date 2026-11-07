"""Bar ranking and stacked-bar option diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

import pandas as pd

if TYPE_CHECKING:
    from trellum.components.charts.bar import StackedBar


def apply_static_top_n(chart, df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """Slice a static DataFrame to top-N + 'Other' before rendering.

    Aggregates by ``x`` (in case the caller passed unaggregated rows),
    sorts by the first value column descending, keeps the top N, and
    sums the remaining rows into a single ``Other (k items)`` bar.
    """
    sort_col = cols[0]
    agg = df.groupby(chart.x, observed=True, dropna=False)[cols].sum().reset_index()
    ascending = chart.sort == "asc"
    agg = agg.sort_values(sort_col, ascending=ascending)
    n = int(chart.top_n)
    head = agg.head(n).copy()
    tail = agg.iloc[n:]
    if tail.empty or not chart.top_n_show_other:
        return head
    other = {chart.x: f"Other ({len(tail)} items)"}
    for c in cols:
        other[c] = tail[c].sum() if c in tail.columns else 0
    return pd.concat([head, pd.DataFrame([other])], ignore_index=True)


def stack_option_problems(chart: "StackedBar") -> list[tuple[str, str]]:
    """Strict opt-in controls/sizing diagnostics, also after author mutation."""
    problems = []
    show_controls = getattr(chart, "show_controls", True)
    height = getattr(chart, "height", None)
    if type(show_controls) is not bool:
        problems.append(("stacked-bar-controls-invalid", "StackedBar show_controls must be bool."))
    if height is not None and (type(height) is not int or not 128 <= height <= 1200):
        problems.append(("stacked-bar-height-invalid", "StackedBar height must be None or an integer from 128 to 1200 (bool is not a height)."))
    if show_controls is False and chart.dataset_id:
        for requested, name in ((chart.stack_by_options, "stack_toggle_id"), (chart.normalize_toggle, "normalize_toggle_id")):
            value = getattr(chart, name)
            if requested and (not isinstance(value, str) or not value.strip() or value != value.strip()):
                problems.append(("stacked-bar-control-id-missing", f"StackedBar show_controls=False requires an explicit nonempty {name} for each requested control."))
    return problems
