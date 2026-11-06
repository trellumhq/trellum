"""Explicit forecast styling for supplied ordinary LineChart series."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML


def add_sections(ctx):
    days = pd.date_range("2026-10-01", periods=10, freq="D").strftime("%Y-%m-%d")
    observed = [42, 45, 43, 48, 51, 49, 54]
    forecast = [52, 55, 57]
    horizon = pd.DataFrame({
        "day": days,
        "observed": observed + [None, None, None],
        "supplied_forecast": [None] * 7 + forecast,
    })
    ctx.add_section("Line forecasts — supplied horizon and opt-in styling", [
        RawHTML(html=(
            "<p>Synthetic daily values. The observed series ends October 7; the "
            "separately supplied forecast is dated October 8–10. No anchor or "
            "forecast values are generated. The side-by-side charts use the same "
            "values, colors and plotting options.</p>"
        )),
        Grid(columns=2, min_width=300, children=[
            LineChart(horizon, "day", ["observed", "supplied_forecast"],
                      "Same data · ordinary lines", y_labels=["Observed", "Forecast"],
                      colors=["#2563eb", "#c2410c"], static=True),
            LineChart(horizon, "day", ["observed", "supplied_forecast"],
                      "Forecast styling enabled", y_labels=["Observed", "Forecast"],
                      colors=["#2563eb", "#c2410c"], forecast_series=["Forecast"],
                      static=True, header_context="Synthetic; supplied as of October 7"),
        ]),
    ])

    historical = pd.DataFrame([
        {"day": f"2026-10-{day:02d}", "platform": platform,
         "actual": actual + offset, "earlier_forecast": prediction + offset}
        for platform, offset in (("Desktop", 0), ("Mobile", 8))
        for day, actual, prediction in (
            (1, 42, 41), (2, 45, 46), (3, 43, 44), (4, 48, 47),
            (5, 51, 50), (6, 49, 52), (7, 54, 53),
        )
    ])
    source = "forecast_history_platform"
    ctx.add_section("Line forecasts — filtered complete historical comparison", [
        DataSource(source, historical),
        FilterBar(source, historical, filters=[
            {"column": "platform", "type": "toggle", "label": "Forecast comparison: platform"},
        ]),
        RawHTML(html=(
            "<p>Complete synthetic realized and earlier-supplied values for each "
            "date and platform. Every compared date supplies both values; "
            "no forecast is generated.</p>"
        )),
        LineChart(historical, "day", ["actual", "earlier_forecast"],
                  "Actual and earlier forecast", y_labels=["Realized", "Earlier forecast"],
                  dataset_id=source, forecast_series=["Earlier forecast"],
                  header_context="Synthetic; earlier forecast supplied before each date"),
    ])

    dense = pd.DataFrame({
        "day": pd.date_range("2026-09-01", periods=180, freq="D").strftime("%Y-%m-%d"),
        "observed": [30 + (i % 11) for i in range(180)],
        "forecast": [31 + (i % 9) for i in range(180)],
    })
    sparse = dense.iloc[::18].reset_index(drop=True)
    ctx.add_section("Line forecasts — inherited adaptive markers and curves", [
        RawHTML(html=(
            "<p>The dense chart suppresses crowded point markers, while the "
            "sparse chart keeps its visible observations. The curve is optional "
            "interpolation; it does not calculate forecast values.</p>"
        )),
        Grid(columns=2, min_width=300, children=[
            LineChart(dense, "day", ["observed", "forecast"], "Dense supplied series",
                      y_labels=["Observed", "Forecast"], forecast_series=[1],
                      colors=["#2563eb", "#c2410c"], tension=0.25, static=True),
            LineChart(sparse, "day", ["observed", "forecast"], "Sparse supplied series",
                      y_labels=["Observed", "Forecast"], forecast_series=["Forecast"],
                      colors=["#2563eb", "#c2410c"], static=True),
        ]),
    ])
