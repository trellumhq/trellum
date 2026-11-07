"""Deterministic native metric tiles, with Grid cycling omitted or enabled."""

import pandas as pd

from trellum.components import AreaChart, BarChart, DataSource, FilterBar, Grid, LineChart, RawHTML

DATASET = "showcase_grid_colors_daily"
METRICS = ("revenue", "orders", "visits", "units", "returns", "signups")


def daily_frame():
    """One row per day/platform; all six quantities are additive."""
    return pd.DataFrame([
        {"day": day, "platform": platform,
         **{metric: (10 + i * 2 + (i % 3) * 3 + j * 4) * weight
            for j, metric in enumerate(METRICS)}}
        for i, day in enumerate(pd.date_range("2026-10-01", periods=7).strftime("%Y-%m-%d"))
        for platform, weight in (("Desktop", 2), ("Mobile", 1))
    ])


def metric_grid(frame, *, bound, enabled):
    """Six independent native charts; no scripts or chart substitutes."""
    data = frame if bound else frame.groupby("day", as_index=False)[list(METRICS)].sum()
    kinds = (LineChart, AreaChart, BarChart) * 2
    charts = [kind(data, "day", metric, metric.title(),
                   **({"dataset_id": DATASET} if bound else {"static": True}))
              for kind, metric in zip(kinds, METRICS)]
    return Grid(charts, columns=3, min_width=280, color_cycle="theme" if enabled else None)


def add_sections(ctx):
    """Unwired showcase integration hook, also builds independently."""
    frame = daily_frame()
    ctx.add_section("Grid color example filters", [
        DataSource(DATASET, frame),
        FilterBar(DATASET, frame, filters=[
            {"column": "platform", "label": "Grid examples: platform", "type": "toggle"},
        ]),
    ])
    for bound in (False, True):
        for enabled in (False, True):
            mode = "Filter-bound" if bound else "Fixed snapshot"
            state = "theme cycle" if enabled else "default colors"
            ctx.add_section(f"{mode} · {state}", [
                RawHTML(html=(
                    "<p>Synthetic daily quantities, October 1–7, 2026. "
                    + ("The platform selection updates these metric tiles. " if bound else
                       "Fixed all-platform totals for comparison; these tiles do not follow the filter. ")
                    + ("Colors follow authored tile positions in the active theme palette.</p>" if enabled else
                       "Grid color cycling is omitted; each single series keeps its ordinary color.</p>")
                )),
                metric_grid(frame, bound=bound, enabled=enabled),
            ])
