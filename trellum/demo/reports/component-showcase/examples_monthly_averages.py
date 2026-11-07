"""Observed-day monthly guides for explicitly declared synthetic daily quantities."""

import pandas as pd

from trellum.components import BarChart, DataSource, FilterBar, Grid, LineChart, RawHTML


def _average(start, end, aggregation="unique"):
    return {
        "metric": "daily_additive",
        "aggregation": aggregation,
        "start": start,
        "end": end,
        "unit": "orders",
    }


def add_sections(ctx):
    snapshot = pd.DataFrame(
        {
            "day": [
                "2028-01-30",
                "2028-01-31",
                "2028-02-01",
                "2028-02-02",
                "2028-02-29",
                "2028-03-01",
                "2028-03-02",
            ],
            "orders": [0, 12, 8, None, 16, None, 20],
        }
    )
    ctx.add_section(
        "Monthly averages — partial selection and observed days",
        [
            RawHTML(
                html="<p>Synthetic additive daily orders, January 30–March 2, 2028. January: 2/2 days, mean 6.0; February: 2/29 days, mean 12.0; March: 1/2 days, mean 20.0. Measured zero is observed; null and absent days are unknown. Both plots keep identical categories, bounds and month context.</p>"
            ),
            Grid(
                columns=2,
                min_width=280,
                children=[
                    LineChart(
                        snapshot,
                        "day",
                        "orders",
                        "Daily orders · default",
                        static=True,
                        month_context=True,
                        y_min=0,
                        y_max=24,
                    ),
                    LineChart(
                        snapshot,
                        "day",
                        "orders",
                        "Daily orders · monthly averages",
                        static=True,
                        month_context=True,
                        y_min=0,
                        y_max=24,
                        monthly_average=_average("2028-01-30", "2028-03-02"),
                    ),
                ],
            ),
        ],
    )
    contributions = pd.DataFrame(
        [
            {"day": day, "platform": platform, "orders": value}
            for platform, values in (("Web", [10, 0, 30, 40]), ("App", [3, 7, None, 19]))
            for day, value in zip(["2028-01-30", "2028-01-31", "2028-02-01", "2028-02-02"], values)
        ]
    )
    source = "monthly_average_platform_orders"
    ctx.add_section(
        "Monthly averages — complete selected platform contributions",
        [
            DataSource(source, contributions),
            FilterBar(
                source,
                contributions,
                filters=[
                    {"column": "day", "type": "date_range", "label": "Orders reporting interval"},
                    {"column": "platform", "type": "toggle", "label": "Orders platform"},
                ],
            ),
            RawHTML(
                html="<p>Distinct Web and App contributions sum to one daily observation. All platforms: January mean 10.0 (2 days), February mean 59.0 (1 day). Unknown App orders withhold February 1 through series availability. Select Web to restore that day: February mean 35.0 (2 days). Date controls change selected-day counts; platform controls change observations. The authored base interval is January 30–February 2.</p>"
            ),
            LineChart(
                contributions,
                "day",
                "orders",
                "Complete daily platform orders",
                dataset_id=source,
                y_labels=["Orders"],
                monthly_average=_average("2028-01-30", "2028-02-02", "sum_complete"),
                series_availability=[{"series": "Orders", "required_columns": ["orders"]}],
            ),
        ],
    )
    bars = pd.DataFrame(
        {
            "day": ["2028-02-27", "2028-02-28", "2028-02-29", "2028-03-01"],
            "orders": [18, 0, None, 27],
        }
    )
    complete = pd.DataFrame(
        [
            {"day": day, "channel": channel, "orders": value}
            for channel, values in (("Direct", [2, 0, 6]), ("Partner", [9, 5, 15]))
            for day, value in zip(["2028-02-28", "2028-02-29", "2028-03-01"], values)
        ]
    )
    source = "monthly_average_bar_orders"
    ctx.add_section(
        "Monthly averages — native daily bars",
        [
            DataSource(source, complete),
            FilterBar(
                source,
                complete,
                filters=[
                    {"column": "day", "type": "date_range", "label": "Bar reporting interval"},
                    {"column": "channel", "type": "toggle", "label": "Orders channel"},
                ],
            ),
            RawHTML(
                html="<p>Static bars retain unknown and zero points (February mean 9.0, 2/3 days). Complete finite bound contributions give February mean 8.0, 2/2 days, and March mean 21.0. Bound bars retain legacy null aggregation: if incomplete raw inputs still produce a plotted subtotal or zero, monthly averages are unavailable. Use a complete source or an explicit static daily snapshot.</p>"
            ),
            Grid(
                columns=2,
                min_width=280,
                children=[
                    BarChart(
                        bars,
                        "day",
                        "orders",
                        "Daily snapshot bars",
                        static=True,
                        monthly_average=_average("2028-02-27", "2028-03-01"),
                    ),
                    BarChart(
                        complete,
                        "day",
                        "orders",
                        "Complete selected daily bars",
                        dataset_id=source,
                        monthly_average=_average("2028-02-28", "2028-03-01", "sum_complete"),
                    ),
                ],
            ),
        ],
    )
    sparse = pd.DataFrame(
        {"day": ["2027-12-31", "2028-02-29", "2028-03-01"], "orders": [14, None, 0]}
    )
    ctx.add_section(
        "Monthly averages — year boundary and unknown leap month",
        [
            RawHTML(
                html="<p>A deliberately sparse synthetic December 31–March 1 interval. January is wholly absent; leap February has one supplied unknown and no observed days. Both months show a dash, with no guide or invented zero. March's measured zero is observed. These declarations apply to additive daily quantities only: never stocks, distinct users, cumulative measures, rates, forecasts or inferred maturity.</p>"
            ),
            LineChart(
                sparse,
                "day",
                "orders",
                "Sparse year and leap-month observations",
                static=True,
                monthly_average=_average("2027-12-31", "2028-03-01"),
            ),
        ],
    )
