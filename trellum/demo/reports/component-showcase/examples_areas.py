"""Deterministic examples for ordinary and additive stacked areas."""

import pandas as pd

from trellum.components import AreaChart, DataSource, FilterBar, Grid, RawHTML


def add_sections(ctx):
    days = pd.date_range("2026-10-01", periods=12).strftime("%Y-%m-%d")
    rows = []
    for i, day in enumerate(days):
        for platform, scale in (("iOS", 1.0), ("Android", 0.72)):
            organic = 1800 + i * 75 + (i % 4) * 120
            paid = 950 + i * 48 + ((i + 1) % 3) * 160
            partner = 500 + i * 32 + (i % 5) * 75
            rows.append({"day": day, "platform": platform,
                         "organic": organic * scale, "paid": paid * scale,
                         "partner": partner * scale,
                         "zero_series": 0 if i in (3, 8) else (i % 3) * 40 * scale})
    frame = pd.DataFrame(rows)

    ctx.add_section("Area example filters", [
        DataSource("showcase_areas", frame),
        FilterBar("showcase_areas", frame, filters=[
            {"column": "platform", "label": "Area examples: platform",
             "type": "toggle"},
        ]),
    ])
    ctx.add_section("Area charts — overlapping and additive", [
        RawHTML(html=(
            "<p>Ordinary areas overlap around a shared baseline. Stacked areas "
            "show additive composition: each visible band begins where the "
            "previous visible series ends. They are appropriate when series "
            "are components of a total. Existing AreaChart plot heights are "
            "unchanged.</p>"
        )),
        Grid(children=[
            AreaChart(df=frame, x="day", y=["organic", "paid", "partner"],
                      y_labels=["Organic", "Paid", "Partner"],
                      title="Overlapping sources · live filter",
                      dataset_id="showcase_areas", y_format="currency"),
            AreaChart(df=frame, x="day", y=["organic", "paid", "partner"],
                      y_labels=["Organic", "Paid", "Partner"], stacked=True,
                      title="Additive source mix · live filter",
                      dataset_id="showcase_areas", y_format="currency"),
        ]),
        Grid(children=[
            AreaChart(df=frame.iloc[::2].reset_index(drop=True), x="day",
                      y=["organic", "paid", "partner"],
                      y_labels=["Organic", "Paid", "Partner"],
                      title="Overlapping sources · static fixture",
                      static=True),
            AreaChart(df=frame.iloc[::2].reset_index(drop=True), x="day",
                      y=["organic", "paid", "partner"],
                      y_labels=["Organic", "Paid", "Partner"], stacked=True,
                      title="Additive source mix · static fixture",
                      static=True),
        ]),
    ])

    zero_frame = frame.iloc[::2][["day", "zero_series"]]
    ctx.add_section("Area chart — measured zero values", [
        AreaChart(df=zero_frame, x="day", y="zero_series",
                  y_labels=["Sparse measured series"],
                  title="Sparse and zero observations",
                  static=True),
    ])
