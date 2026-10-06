"""Monetization: the money over time, then who pays, then what buys them.

Restructured after an owner review: a monetization page opens with revenue
per day (stacked by spender tier), then purchase behaviour (how many, how
big), and only then the mix questions -- who pays -- followed by the
acquisition side: UA spend, the journey from campaign to purchase, and
spend against budget.

Demonstrates a report reading from two data sources (the SQLite warehouse
and a CSV budget file on disk) plus a custom flow-map section driven by the
fact_journey table.
"""

from datetime import timedelta

from trellum import BaseReport
from trellum.components import (
    BarChart,
    ComparisonTable,
    DataSource,
    FilterBar,
    HeatmapChart,
    KpiRow,
    LineChart,
    Panel,
    PivotTable,
    RawHTML,
    ScatterChart,
    SplitPane,
    StackedBar,
    TabGroup,
    TreemapChart,
)
from trellum.data import query_df

from . import custom_sections, queries

_LOOKBACK_DAYS = 180
# The tier-by-month heatmap needs its six month-columns; a 90-day default
# gave it three, and three columns of row-stretch is a gradient, not a
# pattern.
_DEFAULT_FILTER_DAYS = 180


class MonetizationReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        params = {"start_date": start, "end_date": ctx.today}

        df_rev = query_df(conn, queries.REVENUE_DAILY, params=params)
        df_ua = query_df(conn, queries.UA_SPEND, params=params)

        # Month is used as a heatmap axis and to join against the budget file.
        df_rev["month"] = df_rev["event_date"].str.slice(0, 7)
        df_ua["month"] = df_ua["event_date"].str.slice(0, 7)
        df_rev["total_revenue"] = df_rev["iap_revenue"] + df_rev["ad_revenue"]

        ctx.set_header(subtitle="Revenue mix and acquisition efficiency")

        # ── Main filters ─────────────────────────────────────────────
        ctx.add_section("", [
            DataSource("rev", df_rev),
            FilterBar("rev", df_rev, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "title", "label": "Title"},
                {"column": "region", "label": "Region"},
                {"column": "platform", "label": "Platform", "type": "toggle"},
            ]),
        ])

        # Claimed metrics · demo: each dict names a metrics.yaml id and the
        # definition (label, format, agg, column) expands at build time --
        # `python -m trellum metrics` lists what is claimable. The other demo
        # reports keep inline KPI dicts on purpose: claims are opt-in per KPI,
        # and both forms aggregate identically in the client.
        ctx.add_section("Revenue", [
            KpiRow([
                {"metric": "gross_revenue"},
                {"metric": "iap_revenue"},
                {"metric": "ad_revenue"},
                {"metric": "transactions"},
            ], dataset_id="rev"),
        ])

        # ── The money over time: the chart a monetization page opens with.
        # Daily revenue stacked by spender tier -- the height is the
        # business, the layers are who is paying for it, and the
        # non-spender layer is pure ad revenue by definition (they never
        # buy), which keeps that segment honestly in the picture.
        ctx.add_section("Revenue by Day", [
            StackedBar(df_rev, x="event_date", y_cols=["total_revenue"],
                       stack_by="spender_tier",
                       title="Daily Revenue by Spender Tier · demo: stacked "
                             "days -- the non-spender layer is pure ad "
                             "revenue",
                       value_format="currency", dataset_id="rev"),
        ])

        # ── What they bought, per day: how many purchases, and how big.
        # The average is a live ratio (sum over sum, divided after the
        # filters), never a pre-computed column that could not re-aggregate.
        ctx.add_section("Purchases by Day", [
            SplitPane(
                left=Panel([
                    LineChart(df_rev, x="event_date", y="transactions",
                              title="Purchases per Day",
                              y_format="number", dataset_id="rev"),
                ], title="How Many"),
                right=Panel([
                    LineChart(df_rev, x="event_date",
                              ratios=[{"numerator": "iap_revenue",
                                       "denominator": "transactions",
                                       "label": "Avg Purchase Value"}],
                              title="Average Purchase Value · demo: live "
                                    "ratio -- division happens after the "
                                    "filters",
                              y_format="currency", dataset_id="rev"),
                ], title="How Big"),
                ratio="1:1",
            ),
        ])

        # ── Who pays ─────────────────────────────────────────────────
        # Renamed from "Revenue Mix", which said nothing. Three views of one
        # question behind tabs; the heatmap is row-normalised because the
        # whale row otherwise owns the palette and every other tier reads
        # flat. drop_empty removes the non_spender row -- they never buy, so
        # a page called "who pays" has nothing to say about them here. (Their
        # rows stay in the DataSource: the ad-revenue KPIs above need them.)
        ctx.add_section("Who Pays", [
            TabGroup([
                {"label": "Tier by Month", "content": [
                    HeatmapChart(df_rev, x="month", y="spender_tier",
                                 value="iap_revenue",
                                 title="IAP Revenue Heat, Tier x Month · demo: "
                                       "row-normalised, each paying tier "
                                       "against its own range; empty rows "
                                       "dropped",
                                 normalize="row", drop_empty=True,
                                 value_format="currency", dataset_id="rev"),
                ]},
                {"label": "Title & Platform", "content": [
                    TreemapChart(df_rev, group_cols=["title", "platform"],
                                 value="iap_revenue",
                                 title="IAP Revenue Share",
                                 value_format="currency", dataset_id="rev"),
                ]},
                {"label": "Pivot", "content": [
                    # `rows` is a list of dimension columns, not a single
                    # name -- a bare string fails at render time in the
                    # browser, not during validation.
                    PivotTable(df_rev, rows=["title"], cols="platform",
                               values="iap_revenue", agg="sum",
                               title="IAP Revenue by Title and Platform",
                               value_format="currency", dataset_id="rev"),
                ]},
            ], id="revenue_mix_tabs"),
        ])

        # ── User acquisition, with its own section-scoped filters ────
        # Paid networks only (excluded in the SQL): this section is about
        # what spend buys, and organic's spend is zero by definition -- on
        # the scatter those points pile up on the axis and say nothing.
        ctx.add_section("User Acquisition", [
            DataSource("ua", df_ua),
            FilterBar("ua", df_ua, filters=[
                {"column": "campaign_type", "label": "Campaign"},
            ]),
            # SplitPane: the ranked spend and its efficiency scatter are one
            # comparison, so they sit side by side rather than stacked.
            # left/right each take ONE component -- a list renders as an
            # empty section -- hence the Panels.
            SplitPane(
                left=Panel([
                    BarChart(df_ua, x="campaign_type", y="spend",
                             title="Spend by Campaign Type · demo: "
                                   "cross-filter, section-scoped filters",
                             y_format="currency", cross_filter=True,
                             dataset_id="ua"),
                ], title="Where It Goes"),
                right=Panel([
                    ScatterChart(df_ua, x="spend", y="installs",
                                 size="clicks", color_by="campaign_type",
                                 title="Spend vs Installs · demo: bubble "
                                       "scatter, size is a third measure",
                                 x_format="currency", y_format="number",
                                 dataset_id="ua"),
                ], title="What It Buys"),
                ratio="1:1",
            ),
        ])

        # ── The Journey: what those installs then DO ─────────────────
        # Acquisition to purchase as a living metro map, on its own
        # section-scoped DataSource (fact_journey transitions). The custom
        # JS walks agents through the measured rates; the section's chips
        # are summed straight from the same filtered rows.
        df_journey = query_df(conn, queries.JOURNEY, params=params)
        ctx.add_section("The Journey", [
            DataSource("journey", df_journey),
            FilterBar("journey", df_journey, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": 60},
                {"column": "title", "label": "Title"},
                # Ordinal slider, not a dropdown: the tiers already have the
                # order a reader drags through (non-spenders up to whales).
                # Range mode commits the CONTIGUOUS SPAN through the same
                # 'in' mode a multi-select dropdown would -- narrow to just
                # the big spenders and watch the river reshape below.
                {"column": "spender_tier", "label": "Spender Tier",
                 "type": "slider",
                 "values": ["non_spender", "minnow", "dolphin", "whale"],
                 "labels": {"non_spender": "Non-spender", "minnow": "Minnow",
                            "dolphin": "Dolphin", "whale": "Whale"},
                 "mode": "range", "default_min": "non_spender",
                 "default_max": "whale"},
            ]),
            RawHTML(
                html=custom_sections.JOURNEY_HTML,
                js=custom_sections.JOURNEY_JS,
                data_key="_journey",
                data={"tiers": ["whale", "dolphin", "minnow", "non_spender"]},
            ),
        ])

        # ── Budget comparison (static: joins an external file) ───────
        budget = ctx.read_source("ua_budget")
        months = sorted(df_ua["month"].unique())
        budget_window = budget[budget["month"].isin(months)]

        actual = (df_ua.groupby("title", as_index=False)["spend"].sum()
                  .rename(columns={"spend": "Actual Spend"}))
        planned = (budget_window.groupby("title", as_index=False)["budget_usd"].sum()
                   .rename(columns={"budget_usd": "Budget"}))
        # The join key comes from two different systems (warehouse and a
        # hand-maintained CSV), so normalise its dtype before merging rather
        # than trusting both sides to agree.
        actual["title"] = actual["title"].astype(str)
        planned["title"] = planned["title"].astype(str)
        vs_budget = actual.merge(planned, on="title", how="outer").fillna(0)
        vs_budget = vs_budget.rename(columns={"title": "Title"})

        ctx.add_section("Spend vs Budget", [
            ComparisonTable(vs_budget, title=f"UA Spend vs Budget ({months[0]} – {months[-1]})",
                            value_col="Actual Spend",
                            compare_cols=["Budget"],
                            static=True),
        ])

        # No trailing Detail dump -- the pivot and comparison tables above are
        # the tables this page earns; a raw-row appendix is what owners delete.
