"""Explicit availability of supplied LineChart observations, using native charts."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML


def add_sections(ctx):
    observations = pd.DataFrame([
        {"day": day, "platform": platform, "orders": value}
        for platform, values in (("Web", [12, 0, 24, 31]), ("App", [17, 0, None, 43]))
        for day, value in zip(["2026-09-28", "2026-09-29", "2026-10-01", "2026-10-02"], values)
    ])
    source = "line_availability_orders"
    ctx.add_section("Line availability — selected supplied observations", [
        DataSource(source, observations),
        FilterBar(source, observations, filters=[{"column": "platform", "type": "toggle", "label": "Orders platform"}]),
        RawHTML(html="<p>Synthetic orders by day and platform. September 29 is a measured zero; September 30 has no supplied rows and stays absent. The supplied App value on October 1 is unknown. Select Web to restore a complete point and remove its reason.</p>"),
        Grid(columns=2, min_width=280, children=[
            LineChart(observations, "day", "orders", "Legacy selected subtotal", dataset_id=source, y_labels=["Orders"], y_min=0, y_max=100),
            LineChart(observations, "day", "orders", "Complete selected orders", dataset_id=source, y_labels=["Orders"], y_min=0, y_max=100,
                      series_availability=[{"series": "Orders", "required_columns": ["orders"]}]),
        ]),
    ])
    totals = pd.DataFrame([
        {"day": f"2026-10-{i + 3:02d}", "channel": channel, "installs": count, "sessions": sessions,
         "state": "unavailable" if channel == "Paid" and i == 2 else "available"}
        for channel, counts, visits in (("Organic", [21, 24, 27, 30], [46, 51, 55, 62]),
                                        ("Paid", [33, None, 39, 42], [73, 78, 84, 89]))
        for i, (count, sessions) in enumerate(zip(counts, visits))
    ])
    source = "line_availability_contributors"
    ctx.add_section("Line availability — complete contributor totals and supplied state", [
        DataSource(source, totals),
        FilterBar(source, totals, filters=[{"column": "channel", "type": "toggle", "label": "Contributor channel"}]),
        RawHTML(html="<p>Organic and Paid supply distinct daily contributions. Unknown Paid installs withhold October 4's total. A finite sessions observation with unavailable supplied state withholds October 5's sessions total. Other dates and the independent metric remain.</p>"),
        LineChart(totals, "day", ["installs", "sessions"], "Selected complete daily totals", dataset_id=source,
                  y_labels=["Installs", "Sessions"], series_availability=[
                      {"series": "Installs", "required_columns": ["installs"]},
                      {"series": "Sessions", "required_columns": ["sessions"], "state_column": "state"}]),
    ])
    rates = pd.DataFrame([
        {"day": f"2026-10-{i + 7:02d}", "platform": platform, "conversions": num, "visits": den}
        for platform, nums, dens in (("Web", [7, None, 0, 0, 15], [31, 37, 0, 43, 47]),
                                     ("App", [11, 13, 0, 0, 19], [53, 59, 0, 61, 67]))
        for i, (num, den) in enumerate(zip(nums, dens))
    ])
    source = "line_availability_rates"
    ctx.add_section("Line availability — supplied numerator and denominator", [
        DataSource(source, rates),
        FilterBar(source, rates, filters=[{"column": "platform", "type": "toggle", "label": "Rate platform"}]),
        RawHTML(html="<p>Selected conversions divided by selected visits. A null numerator and a zero denominator withhold their supplied dates. A genuine zero numerator with positive visits remains 0%. The header averages only plotted daily rates, including zero.</p>"),
        LineChart(rates, "day", title="Complete supplied conversion rate", dataset_id=source, y_format="percent",
                  ratios=[{"numerator": "conversions", "denominator": "visits", "label": "Conversion rate"}],
                  header_summary={"stat": "mean", "series": 0, "label": "Mean plotted daily rate", "format": "percent"},
                  series_availability=[{"series": "Conversion rate", "required_columns": ["conversions", "visits"]}]),
    ])
    snapshot = pd.DataFrame({
        "day": ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"],
        "observed": [18, 0, 26, 29, 34], "forecast": [20, 23, 25, 31, 36],
        "state": ["available", "available", "unavailable", "available", "available"],
    })
    ctx.add_section("Line availability — static snapshot with supplied forecast", [
        RawHTML(html="<p>A static snapshot preserves supplied row positions and values. The supplied state withholds one finite observed value; the independent authored forecast keeps its dashed style. The month row and any configured dated-event descriptions precede availability context.</p>"),
        LineChart(snapshot, "day", ["observed", "forecast"], "Static supplied observations and forecast", static=True,
                  y_labels=["Observed", "Supplied forecast"], forecast_series=["Supplied forecast"], month_context=True,
                  tension=.25, header_context="Synthetic snapshot; supplied on September 28",
                  series_availability=[{"series": "Observed", "required_columns": ["observed"], "state_column": "state",
                      "reason": "This supplied observation is awaiting source verification. <Source & status> is literal explanatory text; the separately supplied forecast is independent and does not replace the unknown observation."}]),
    ])
