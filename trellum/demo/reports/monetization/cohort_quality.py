"""Monthly retention quality, independent of daily revenue and paid spend."""

import calendar

import pandas as pd

from trellum.components import (
    CohortTable,
    DataSource,
    DataTable,
    FilterBar,
    HeatmapChart,
    RawHTML,
    TabGroup,
)
from trellum.data import query_df

HORIZONS = (1, 3, 7, 14, 30)
DATASET = "ua_quality_cohorts"
HEATMAP = "ua_quality_observed"
SUMMARY = "ua_quality_mature"


def monthly_cells(raw, as_of):
    """Pool daily numerators/denominators only for complete mature months.

    A monthly horizon matures when the last day in that calendar month has
    reached it. Missing daily observations or a zero denominator withhold
    that cell; immature months never contribute partial population rates.
    """
    frame = raw.copy()
    frame["cohort_month"] = frame["cohort_date"].astype(str).str.slice(0, 7)
    as_of = pd.Timestamp(as_of).normalize().tz_localize(None)
    rows = []
    identities = frame[["cohort_month", "title", "platform"]].drop_duplicates()
    for month, title, platform in identities.itertuples(index=False, name=None):
        year, number = map(int, month.split("-"))
        days = calendar.monthrange(year, number)[1]
        month_end = pd.Timestamp(year, number, days)
        expected = set(pd.date_range(f"{month}-01", month_end).strftime("%Y-%m-%d"))
        cohort = f"{month} · {title} · {platform}"
        group = frame[(frame.cohort_month == month) & (frame.title == title) &
                      (frame.platform == platform)]
        for horizon in HORIZONS:
            source = group[group.day_number == horizon]
            state, reason = "observed", "Complete calendar-month cohort; retained / eligible users"
            eligible = retained = rate = None
            if month_end + pd.Timedelta(days=horizon) > as_of:
                state, reason = "not_mature", "The last cohort date in this month has not reached this horizon"
            elif (set(source.cohort_date.astype(str)) != expected or
                  source.cohort_date.duplicated().any() or
                  source[["cohort_size", "retained_users"]].isna().any().any()):
                state, reason = "unavailable", "Complete daily observations for this month and horizon are unavailable"
            else:
                eligible = int(source.cohort_size.sum())
                retained = int(source.retained_users.sum())
                if eligible <= 0:
                    state, reason = "unavailable", "No eligible cohort users were supplied for this horizon"
                    eligible = retained = None
                else:
                    rate = retained / eligible
            rows.append(dict(cohort=cohort, cohort_month=month, title=str(title),
                             platform=str(platform), day=horizon, day_label=f"D{horizon:02d}",
                             retention=rate, state=state, reason=reason,
                             eligible=eligible, retained=retained))
    return pd.DataFrame(rows, columns=[
        "cohort", "cohort_month", "title", "platform", "day", "day_label",
        "retention", "state", "reason", "eligible", "retained",
    ])


def mature_summary(cells):
    """One row per D30-mature cohort, with independent horizon weights."""
    rows = []
    for identity, group in cells.groupby("cohort", sort=False, observed=True):
        # No partial or unavailable cohort contributes to comparison totals.
        if set(group.day) != set(HORIZONS) or not group.state.eq("observed").all():
            continue
        first = group.iloc[0]
        row = {"Cohort": identity, "cohort_month": first.cohort_month,
               "title": first.title, "platform": first.platform}
        for day in (1, 7, 30):
            cell = group[group.day == day].iloc[0]
            row[f"D{day}"] = cell.retention
            row[f"retained_d{day}"] = int(cell.retained)
            row[f"eligible_d{day}"] = int(cell.eligible)
        rows.append(row)
    columns = ["Cohort", "cohort_month", "title", "platform"]
    for day in (1, 7, 30):
        columns += [f"D{day}", f"retained_d{day}", f"eligible_d{day}"]
    return pd.DataFrame(rows, columns=columns)


def add_section(ctx, conn, sql):
    # Six complete calendar-month identities at most per title/platform;
    # the current month remains visible with explicit maturity states.
    start = (pd.Timestamp(ctx.today).to_period("M") - 5).start_time.strftime("%Y-%m-%d")
    raw = query_df(conn, sql, params={"start_date": start, "end_date": ctx.today})
    cells = monthly_cells(raw, ctx.today)
    observed = cells[cells.state == "observed"].copy()
    summary = mature_summary(cells)
    titles = sorted(cells.title.unique())
    platforms = sorted(cells.platform.unique())
    mapping = {"title": "title", "platform": "platform"}
    ctx.add_section("Acquisition Quality by Cohort", [
        DataSource(DATASET, cells),
        DataSource(HEATMAP, observed),
        DataSource(SUMMARY, summary),
        FilterBar(DATASET, cells, filters=[
            {"column": "title", "label": "Cohort title", "type": "toggle",
             "default": titles[0] if titles else "All"},
            {"column": "platform", "label": "Cohort platform", "type": "toggle",
             "default": "iOS" if "iOS" in platforms else platforms[0] if platforms else "All"},
        ], propagate_to={HEATMAP: mapping, SUMMARY: mapping}),
        RawHTML(html=(
            "<p>Monthly retention quality from all acquisition sources, by title and platform. "
            "These cohorts have no campaign attribution, so they do not measure paid ROAS. "
            "Choose a title and platform to compare six months. Each horizon waits for the "
            "entire month to mature; missing observations remain unavailable. "
            "Rates divide retained users by eligible users at that horizon.</p>"
        )),
        TabGroup([
            {"label": "Cohort observations", "content": [
                CohortTable(
                    cells, "Monthly Retention · Observed Horizons",
                    cohort="cohort", cohort_month="cohort_month", horizon="day",
                    observed="retention", observed_state="state", reason="reason",
                    population={"label": "Calendar-month cohorts from fact_retention; all acquisition sources",
                                "as_of": ctx.now_utc, "timezone": "UTC"},
                    dataset_id=DATASET,
                ),
            ]},
            {"label": "Retention heat", "content": [
                HeatmapChart(
                    observed, x="day_label", y="cohort", value="retention",
                    title="Observed Retention by Cohort and Horizon",
                    value_format="percent", dataset_id=HEATMAP, log_scale=False,
                    header_context="One rate per coordinate; shared absolute colour scale. Unavailable and immature cells are absent.",
                ),
            ]},
        ], id="ua_cohort_quality_tabs"),
        RawHTML(html=(
            "<p>The comparison below includes only complete D30-mature cohorts. "
            "Each total is total retained / total eligible at its own horizon; rates are never summed. "
            "Illustrative reference targets: D7 ≥ 15% and D30 ≥ 7%. "
            "Search narrows the comparison and its totals.</p>"
        )),
        DataTable(
            summary, "D30-Mature Cohorts · Weighted Retention", dataset_id=SUMMARY,
            columns=["Cohort", "D1", "D7", "D30"],
            sortable=True, searchable=True, row_limit=10,
            column_formats={f"D{day}": {"format": "percent", "precision": 1} for day in (1, 7, 30)},
            cell_shading={"D7": {"target": .15, "direction": "higher"},
                          "D30": {"target": .07, "direction": "higher"}},
            totals={f"D{day}": {"agg": "ratio", "numerator": f"retained_d{day}",
                                "denominator": f"eligible_d{day}", "format": "percent", "precision": 1}
                    for day in (1, 7, 30)},
            pin_header=True, pin_footer=True,
        ),
    ])
