"""Template report -- copy this directory to start a new one.

The shape below is the canonical one: fetch raw rows, put them in a
DataSource inside an *untitled* first section together with the FilterBar,
then add titled sections of charts that bind back via dataset_id.
"""

from datetime import timedelta

from trellum import BaseReport
from trellum.components import (
    DataSource,
    FilterBar,
    KpiRow,
    LineChart,
)
from trellum.data import query_df

from . import queries

_LOOKBACK_DAYS = 90
_DEFAULT_FILTER_DAYS = 30


class TemplateReport(BaseReport):
    """Exactly one BaseReport subclass per generator.py -- the runner picks
    the first one it finds."""

    def generate(self, ctx):
        # 1. Raw rows.
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.DAILY, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        # 2. Derived columns go here, BEFORE the DataSource, so the client can
        #    aggregate them live.

        # 3. DataSource + FilterBar in an untitled section (required for the
        #    sticky filter bar).
        ctx.add_section("", [
            DataSource("main", df),
            FilterBar("main", df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "title", "label": "Title"},
                {"column": "platform", "label": "Platform", "type": "toggle"},
            ]),
        ])

        # 4. KPIs. `agg` reads from the filtered rows of the linked dataset.
        #    Note: agg "ratio" always returns a percentage -- only pair it
        #    with format "percent".
        ctx.add_section("Overview", [
            KpiRow([
                {"label": "IAP Revenue", "agg": "sum", "column": "iap_revenue",
                 "format": "currency"},
                {"label": "Active User Days", "agg": "sum", "column": "dau",
                 "format": "number"},
            ], dataset_id="main"),
        ])

        # 5. Charts get the SAME raw df plus dataset_id, so the FilterBar
        #    drives them. Every column referenced must exist in that df.
        ctx.add_section("Trend", [
            LineChart(df, x="event_date", y="iap_revenue",
                      stack_by="platform",
                      title="IAP Revenue by Platform",
                      y_format="currency", dataset_id="main"),
        ])
