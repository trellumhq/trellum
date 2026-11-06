"""Independent synthetic, precomputed cohort observations and forecasts."""
import pandas as pd

from trellum.components import DataSource, FilterBar, PivotTable, RawHTML
from trellum.components.cohort_table import CohortTable

POPULATION = {"label": "Synthetic acquisition cohorts; each cohort belongs wholly to one platform",
              "as_of": "2026-02-04T00:00:00+00:00", "timezone": "UTC",
              "source_time": "2026-02-03T23:30:00+00:00"}
FORECAST = {"model": "Illustrative supplied model v2", "scenario": "Baseline",
            "issued_at": "2026-02-04T00:00:00+00:00",
            "scope": "Each named synthetic acquisition cohort independently; no pooled population"}


def cohort_frame(*, forecasts=True):
    rows = []
    # These are declared cells, not inferred from dates or source coverage.
    values = [
        ("2025-09", "Android", [0.34, 0.68, 0.91, 1.08, 1.17, None], 5),
        ("2025-10", "iOS", [0.0, 0.61, 0.82, 0.97, None, None], 4),
        ("2025-11", "Android", [0.29, 0.58, None, None, None, None], 3),
        ("2025-12", "iOS", [0.31, 0.63, None, None, None, None], 2),
        ("2026-01", "Android", [None] * 6, 0),
    ]
    for index, (month, platform, observations, mature_count) in enumerate(values):
        identity = month if index != 4 else "January acquisition cohort with a deliberately long authored population label"
        for pos, day in enumerate([7, 30, 60, 90, 120, 180]):
            observed = observations[pos]
            state = "observed" if observed is not None else "unavailable" if pos < mature_count else "not_mature"
            forecast = (0.32 + pos * .19 + index * .0123456789) if forecasts and observed is None else None
            # Old-enough unavailable data does not acquire an invented forecast.
            if month == "2025-11" and day == 60 or month == "2025-12" and day == 120:
                forecast = None
            if month == "2025-09" and day == 7 and forecasts:
                forecast = .37  # separate forecast preserved beside an observation
            rows.append({"cohort": identity, "month": month, "day": day, "observed": observed,
                         "state": state, "forecast": forecast, "platform": platform,
                         "reason": "Source observation absent despite declared eligibility" if state == "unavailable" else
                         "Author-declared horizon not mature at this snapshot" if state == "not_mature" else ""})
    return pd.DataFrame(rows)


def cohort_table(frame, title="", **kwargs):
    return CohortTable(frame, title, cohort="cohort", cohort_month="month", horizon="day",
                       observed="observed", observed_state="state", population=POPULATION,
                       reason="reason", **kwargs)


def add_sections(ctx):
    supplied = cohort_frame()
    ctx.add_section("Cohort tables — supplied snapshot and forecasts", [
        DataSource("showcase_cohorts_supplied", supplied),
        FilterBar("showcase_cohorts_supplied", supplied, filters=[
            {"column": "platform", "label": "Whole-cohort platform", "type": "toggle",
             "options": ["Android", "iOS", "Web"]}]),
        RawHTML(html="<p>Supplied ratios as of 4 February 2026. October D7 is measured zero; November D60 is unavailable; December D120 is not mature without a forecast. Choose Web for no matches. Filters select complete cohorts; values and model applicability remain author-declared.</p>"),
        cohort_table(supplied, "Supplied cells · milestone off", dataset_id="showcase_cohorts_supplied",
                     forecast="forecast", forecast_metadata=FORECAST),
        cohort_table(supplied, "Supplied cells · optional milestone", dataset_id="showcase_cohorts_supplied",
                     forecast="forecast", forecast_metadata=FORECAST,
                     milestone={"threshold": 1.0, "label": "Recovery milestone"}),
    ])
    observed = cohort_frame(forecasts=False).drop(columns="forecast")
    ctx.add_section("Cohort tables — observed only", [
        DataSource("showcase_cohorts_observed", observed),
        FilterBar("showcase_cohorts_observed", observed, filters=[
            {"column": "platform", "label": "Observed cohorts platform", "type": "toggle"}]),
        cohort_table(observed, "Observed-only cells", dataset_id="showcase_cohorts_observed"),
    ])
    pivot = observed.assign(horizon_label=observed["day"].map(lambda day: f"D{day}"))
    ctx.add_section("Cohort tables — existing PivotTable alternative", [
        RawHTML(html="<p>The existing PivotTable remains an exploratory aggregate alternative with its normal selectors. It does not provide CohortTable's explicit missingness and forecast semantics. Generic cumulative-horizon totals are not pooled recovery ratios.</p>"),
        PivotTable(pivot, rows=["horizon_label"], cols="month", values="observed", agg="avg",
                   value_format="percent", static=True, title="Existing PivotTable · generic average"),
    ])
