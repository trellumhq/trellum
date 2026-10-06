"""The metrics report: every bound metric in ``metrics.yaml``, at a glance.

``python -m trellum metrics --report`` scaffolds ``reports/metrics/`` with a
one-line generator that imports :class:`MetricsReport`. The report is built
entirely from the registry: one DataSource per dataset, then one anchored
block per metric -- a KPI (a bare claim, so the definition expands through
the normal path) and a trend carrying its own breakdown control -- grouped
by the metric's first tag, and a closing table of the metrics that are
defined but bound to no dataset.

The blocks in a tag sit **side by side**, in a ``Grid(min_width=...)``: the
page is an overview, and an overview is many metrics read against each other,
not one chart per screenful scrolled past a dozen times. The grid is
``auto-fill``, so the number across follows the width instead of a
breakpoint, and a tile shortens its chart and drops its title and breakdown
toggle (grid.css) because a tile is a glance. All of that reverses under
``?only=``, which is showing one block and gives it the whole frame.

Two controls, two scopes, on purpose. The **date range** is shared: one
FilterBar on the first time dataset, propagated onto every other time
dataset's own time column, because "the last 30 days" means the same thing
to every grain. The **breakdown** is per metric: each chart carries its own
``stack_by_options`` toggle over its dataset's dimensions, so a metric is
split by something its own rows actually have. There is deliberately no
report-wide dimension filter -- datasets share a time axis but not their
dimensions, and a bar that reads as global while reaching half the page is
worse than no bar. It also makes every block self-contained, which is what
lets ``?only=metric-<name>`` embed one of them on its own.
"""

from __future__ import annotations

import os

import pandas as pd

from trellum.components import (
    BarChart,
    DataSource,
    DataTable,
    FilterBar,
    Grid,
    KpiRow,
    LineChart,
    Section,
)
from trellum.metrics import Dataset, Metric, load_metrics_result
from trellum.report import BaseReport

#: Chart axis formats the runtime knows; other metric formats fall back to
#: the chart's default.
_CHART_FORMATS = {"currency", "percent", "number"}

#: Narrowest a metric tile may get before the grid drops a column. A trend
#: under roughly this is a squiggle with no readable date axis left; at 1400px
#: (the container's cap) it lands on three across, two around 900px, one on a
#: phone -- without a breakpoint per width.
_TILE_MIN_PX = 340


class MetricsReport(BaseReport):

    def generate(self, ctx):
        reg = load_metrics_result()
        bound = [m for m in reg.metrics.values() if m.executable and m.dataset]
        by_ds: dict[str, list[Metric]] = {}
        for m in bound:
            by_ds.setdefault(m.dataset, []).append(m)
        frames = {
            ds: ctx.metrics([m.name for m in ms], by=list(reg.datasets[ds].dimensions))
            for ds, ms in by_ds.items()
        }
        # A count metric has no column to chart; a literal 1 sums to rows.
        for m in bound:
            if m.agg == "count":
                frames[m.dataset][f"{m.name}_rows"] = 1

        # The file's NAME, never `reg.path`: that is an absolute path on the
        # machine that ran the build, and this string is rendered into an
        # artifact that gets served, framed and shared. It also told the
        # reader nothing -- they cannot open it.
        ctx.set_header(subtitle=f"{len(bound)} of {len(reg.metrics)} defined metrics "
                                f"monitored from {os.path.basename(reg.path)}")
        if by_ds:
            self._date_range(ctx, reg.datasets, by_ds, frames)

        sections: dict[str, list[Metric]] = {}
        for m in bound:
            sections.setdefault(m.tags[0] if m.tags else "Other", []).append(m)
        for tag, ms in sections.items():
            ctx.add_section(tag.replace("_", " ").title(), [Grid(
                [_block(m, frames[m.dataset], reg.datasets[m.dataset]) for m in ms],
                min_width=_TILE_MIN_PX,
            )])

        rest = [m for m in reg.metrics.values() if m not in bound]
        if rest:
            ctx.add_section("Defined but not monitored", [
                DataTable(_not_monitored(rest), title="Bind these to a dataset in "
                          "metrics.yaml to monitor them here", static=True),
            ])

    def _date_range(self, ctx, datasets, by_ds, frames) -> None:
        """One untitled section: every DataSource, and the page's single
        shared control -- a date_range on the first time dataset, propagated
        onto every other time dataset's own time column. A dataset with no
        time axis is left alone: nothing on this bar could reach it, and
        every dimension it has is reachable from its own charts anyway.
        """
        main = next((ds for ds in by_ds if datasets[ds].time_column), None)
        comps: list = [DataSource(ds, frames[ds]) for ds in by_ds]
        if main:
            time_col = datasets[main].time_column
            propagate = {ds: {time_col: datasets[ds].time_column} for ds in by_ds
                         if ds != main and datasets[ds].time_column}
            comps.append(FilterBar(
                main, frames[main],
                filters=[{"column": time_col, "type": "date_range"}],
                propagate_to=propagate or None,
            ))
        ctx.add_section("", comps)


def _block(m: Metric, df: pd.DataFrame, ds: Dataset) -> Section:
    """One metric, whole: the claimed KPI and its chart, under an anchor
    derived from the metric id. One tile of the overview grid, and the unit
    ``?only=metric-<name>`` renders alone at full size, which is what a host
    embeds to show one metric."""
    comps: list = [KpiRow([_claim(m, df, ds)], dataset_id=m.dataset)]
    if (chart := _chart(m, df, ds)) is not None:
        comps.append(chart)
    return Section(m.label or m.name, comps, anchor=f"metric-{m.name}")


def _claim(m: Metric, df: pd.DataFrame, ds: Dataset) -> dict:
    """A bare claim, except ``time_agg: last``: the last period's value,
    computed here (slice 1) rather than re-aggregated under filters."""
    if m.time_agg != "last" or not ds.time_column or df.empty:
        return {"metric": m.name}
    last = df[df[ds.time_column] == df[ds.time_column].max()]
    return {"metric": m.name, "value": round(float(last[m.column].sum()), 4)}


def _chart(m: Metric, df: pd.DataFrame, ds: Dataset):
    """A trend over the time column, or a bar over the first dimension when
    the dataset has no time axis; None when there is nothing to pivot on.

    A trend carries this metric's breakdown control: a toggle over its own
    dataset's dimensions. "Total" maps to no column, which the chart runtime
    reads as "don't pivot", and being first it is the default.
    """
    x = ds.time_column or (ds.dimensions[0] if ds.dimensions else None)
    if x is None:
        return None
    label = m.label or m.name
    kw: dict = {"dataset_id": m.dataset, "title": label,
                "y_format": m.format if m.format in _CHART_FORMATS else None}
    if m.agg == "ratio":
        kw["ratios"] = [{"numerator": m.numerator, "denominator": m.denominator,
                         "label": label}]
        y = ""
    else:
        y = m.column or f"{m.name}_rows"
    if ds.time_column:
        if breakdowns := [d for d in ds.dimensions if d != x]:
            kw["stack_by_options"] = {"Total": "",
                                      **{d.replace("_", " ").title(): d for d in breakdowns}}
        return LineChart(df, x=x, y=y, **kw)
    # ponytail: a time-less dataset gets a plain bar over its first dimension
    # and no breakdown toggle -- BarChart has no stack_by_options, and the
    # bar's own x already IS a breakdown. StackedBar is the upgrade if a
    # second dimension on a snapshot dataset ever needs to be reachable.
    return BarChart(df, x=x, y=y, **kw)


def _not_monitored(rest: list[Metric]) -> pd.DataFrame:
    from trellum.cli.commands.metrics import _claims_by_metric
    from trellum.project import get_project_root

    claims = _claims_by_metric(get_project_root()) or {}
    return pd.DataFrame([{
        "metric": m.name,
        "label": m.label or m.name,
        "owner": m.owner,
        "spec": m.spec_text(),
        "reason": "no dataset binding" if m.executable else "descriptive (no agg)",
        "claimed by": ", ".join(claims.get(m.name) or []) or "-",
    } for m in rest])
