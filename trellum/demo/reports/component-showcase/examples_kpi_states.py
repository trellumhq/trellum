"""Deterministic supplied-input, cohort and paired-period KPI examples."""

import pandas as pd

from trellum.components import DataSource, FilterBar, KpiCard, KpiRow, RawHTML


def _input(column):
    return {"required_columns": [column], "empty": "unavailable"}


def _rate(numerator, denominator):
    return {"agg": "ratio", "numerator": numerator, "denominator": denominator,
            "unit": "fraction"}


def add_sections(ctx):
    frame = pd.DataFrame([
        {"case": case, "observation": obs, "revenue": revenue, "spend": spend}
        for case, obs, revenue, spend in [
            ("Complete", "A", 120, 60), ("Measured zero", "B", 0, 40),
            ("Missing", "C", None, 30), ("Partial", "D", 75, 25),
            ("Partial", "E", None, 15), ("Zero denominator", "F", 42, 0),
        ]
    ])
    ctx.add_section("KPI states — supplied observation completeness", [
        DataSource("showcase_kpi_states", frame),
        FilterBar("showcase_kpi_states", frame, filters=[
            {"column": "case", "label": "Input case", "type": "toggle", "default": "Complete"},
        ]),
        RawHTML(html="<p>Synthetic observations: completeness checks only the supplied matching rows. "
                "Choose Missing, Partial or Zero denominator; measured zeros remain valid. "
                "No declaration keeps the legacy subtotal. A filter with no matches keeps count zero.</p>"),
        KpiRow([
            {"label": "Legacy revenue subtotal", "agg": "sum", "column": "revenue", "format": "currency"},
            {"label": "Complete revenue", "agg": "sum", "column": "revenue", "format": "currency",
             "availability": {**_input("revenue"), "reason": "Revenue needs complete supplied observations."}},
            {"label": "ROAS", "agg": "ratio", "numerator": "revenue", "denominator": "spend", "format": "percent",
             "availability": {"required_columns": ["revenue", "spend"], "empty": "unavailable",
                              "ratio_denominator": "nonzero"}},
            {"label": "Observation count", "agg": "count"},
            KpiCard(label="Missing supplied constant", value=None, format="currency",
                    availability={"state": "unavailable", "reason": "External estimate not supplied."}),
            {"label": "Mixed percent constant", "value": 8, "format": "percent", "precision": 2},
        ], dataset_id="showcase_kpi_states", mobile_collapse=4),
    ])

    cohorts = pd.DataFrame([
        {"cohort": "Young", "age": 3, "state": "immature", "observed_revenue": 8,
         "cost": 100, "final_revenue": None, "forecast_revenue": 15},
        {"cohort": "Mature", "age": 7, "state": "mature", "observed_revenue": 28,
         "cost": 200, "final_revenue": 28, "forecast_revenue": 30},
    ])
    maturity = {"state_column": "state", "age_column": "age", "horizon": 7,
                "cohort_column": "cohort", "observed": _rate("observed_revenue", "cost")}
    ctx.add_section("KPI states — declared cohort age and horizon", [
        DataSource("showcase_kpi_cohorts", cohorts),
        FilterBar("showcase_kpi_cohorts", cohorts, filters=[
            {"column": "cohort", "label": "Single cohort", "type": "toggle", "default": "Young"},
        ]),
        RawHTML(html="<p>Select Young for observed D3 8.00% and a supplied D7 15.00% forecast. "
                "Mature retains D7 14.00%. Both selected is unavailable: no common cohort age is inferred. "
                "Forecasts are illustrative supplied estimates, not generated predictions.</p>"),
        KpiRow([
            {"label": "Final D7 · supplied forecast", "agg": "ratio", "numerator": "final_revenue",
             "denominator": "cost", "format": "percent", "precision": 2,
             "maturity": {**maturity, "forecast": _rate("forecast_revenue", "cost")}},
            {"label": "Final D7 · no forecast", "agg": "ratio", "numerator": "final_revenue",
             "denominator": "cost", "format": "percent", "precision": 2, "maturity": maturity},
        ], dataset_id="showcase_kpi_cohorts"),
    ])

    periods = pd.DataFrame([
        {"region": "North", "current_converted": 12, "current_population": 50,
         "prior_converted": 6, "prior_population": 40, "current_revenue": 480, "prior_revenue": 0},
        {"region": "South", "current_converted": 32, "current_population": 150,
         "prior_converted": 34, "prior_population": 160, "current_revenue": 720, "prior_revenue": 600},
        {"region": "Unknown baseline", "current_converted": 9, "current_population": 30,
         "prior_converted": None, "prior_population": 60, "current_revenue": 300, "prior_revenue": None},
    ])
    comparison = {"current": _rate("current_converted", "current_population"),
                  "baseline": _rate("prior_converted", "prior_population")}
    ctx.add_section("KPI states — weighted paired-period comparisons", [
        DataSource("showcase_kpi_periods", periods),
        FilterBar("showcase_kpi_periods", periods, filters=[
            {"column": "region", "label": "Paired-period regions", "type": "dropdown"},
        ]),
        RawHTML(html="<p>Select North and South together: 44/200 versus 40/200, or 22% versus 20%. "
                "Each period aggregates its own numerator and denominator before comparison. "
                "Unknown baseline withholds only the comparison; North revenue has a zero baseline.</p>"),
        KpiRow([
            {"label": label, "agg": "ratio", "numerator": "current_converted", "denominator": "current_population",
             "format": "percent", "precision": 2,
             "comparison": {**comparison, "measure": measure}}
            for label, measure in (("Conversion · relative", "relative_percent"),
                                   ("Conversion · percentage points", "percentage_points"))
        ] + [
            {"label": "Revenue · relative", "agg": "sum", "column": "current_revenue", "format": "currency",
             "comparison": {"measure": "relative_percent",
                            "current": {"agg": "sum", "column": "current_revenue", "unit": "number"},
                            "baseline": {"agg": "sum", "column": "prior_revenue", "unit": "number"}}},
        ], dataset_id="showcase_kpi_periods"),
    ])

    ctx.add_section("KPI states — independent supplied values", [
        RawHTML(html="<p>These independent snapshots do not follow the filters above. "
                "Card percentages are fractions; metadata explicitly declares fraction/percent units.</p>"),
        KpiRow([
            KpiCard(label="D7 · observed D3", value=None, format="percent", precision=2,
                    maturity={"state": "immature", "age": 3, "horizon": 7,
                              "observed": {"value": 8, "unit": "percent"},
                              "forecast": {"value": .15, "unit": "fraction"}}),
            KpiCard(label="D7 · mature", value=.14, format="percent", precision=2,
                    maturity={"state": "mature", "age": 7, "horizon": 7,
                              "observed": {"value": .14, "unit": "fraction"}}),
            KpiCard(label="D7 · no forecast", value=None, format="percent", precision=2,
                    maturity={"state": "immature", "age": 3, "horizon": 7,
                              "observed": {"value": .08, "unit": "fraction"}}),
            KpiCard(label="Unchanged", value=.22, format="percent",
                    comparison={"measure": "relative_percent", "current": {"value": 22, "unit": "percent"},
                                "baseline": {"value": .22, "unit": "fraction"}}),
            KpiCard(label="Lower cost · favorable", value=90, format="currency", delta_direction="up",
                    comparison={"measure": "relative_percent", "current": {"value": 90, "unit": "number"},
                                "baseline": {"value": 100, "unit": "number"}}),
            KpiCard(label="Unavailable snapshot", value=None,
                    availability={"state": "unavailable", "reason": "Estimate not supplied for this snapshot."}),
        ], static=True),
        KpiCard(label="Standalone unavailable reading", value=None,
                availability={"state": "unavailable", "reason": "This independent reading has not been supplied."}),
    ])

    empty = pd.DataFrame(columns=["kind", "amount"])
    ctx.add_section("KPI states — declared empty supplied population", [
        DataSource("showcase_kpi_empty", empty),
        FilterBar("showcase_kpi_empty", empty, filters=[
            {"column": "kind", "label": "Empty supplied population", "type": "dropdown"},
        ]),
        RawHTML(html="<p>This registered, empty supplied population has no choices. "
                "The author can declare empty revenue unknown or deliberately zero; its observation count is zero. "
                "An empty population is not a simulated source-loading or failure state.</p>"),
        KpiRow([
            {"label": "Empty revenue · unknown", "agg": "sum", "column": "amount", "format": "currency",
             "availability": {"required_columns": ["amount"], "empty": "unavailable"}},
            {"label": "Empty revenue · deliberate zero", "agg": "sum", "column": "amount", "format": "currency",
             "availability": {"required_columns": ["amount"], "empty": "zero"}},
            {"label": "Empty observation count", "agg": "count"},
        ], dataset_id="showcase_kpi_empty"),
    ])
