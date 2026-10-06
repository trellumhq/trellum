"""Cart Funnel -- where Northwind loses buyers between landing and paying.

The report exists for one question: where does the money leak, and for whom.
So the funnel is the headline, the leak is quantified in the KPI row (seven
in ten carts die), and every breakdown is a comparison across the people
dimensions -- device and channel -- rather than a tour of chart types.

Two frames from one table: the wide daily counts drive the KPIs and the
per-channel conversion lines, and a melted long copy drives the funnel chart,
because a funnel is stages-as-rows and the warehouse stores stages-as-columns.
Both are registered as DataSources and both FilterBars stay in step via the
shared columns.
"""

from datetime import timedelta

import pandas as pd

from trellum import BaseReport
from trellum.components import (
    ABCompare,
    DataSource,
    FilterBar,
    FunnelChart,
    HeatmapChart,
    KpiRow,
    LineChart,
    RawHTML,
)
from trellum.data import query_df
from trellum.stats import Metric

from . import custom_sections, queries

_LOOKBACK_DAYS = 90
_DEFAULT_FILTER_DAYS = 30

#: Stage columns in funnel order. The melt preserves this order and the chart
#: draws rows in first-seen order, which is how acquisition-funnel's stages
#: stay sorted too.
_STAGES = ["sessions", "product_views", "add_to_cart", "checkouts", "purchases"]
_STAGE_LABELS = {
    "sessions": "Sessions",
    "product_views": "Viewed a product",
    "add_to_cart": "Added to cart",
    "checkouts": "Started checkout",
    "purchases": "Purchased",
}


class CartFunnelReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.TRAFFIC, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        # The number the report is named after: carts that never became
        # orders. A derived column, so the ratio agg can stay live under
        # filters -- a pre-computed rate could not re-aggregate.
        df["lost_after_cart"] = df["add_to_cart"] - df["purchases"]

        long = df.melt(
            id_vars=["event_date", "channel", "device"],
            value_vars=_STAGES, var_name="stage", value_name="count")
        long["stage"] = long["stage"].map(_STAGE_LABELS)
        # Categorical sort keeps funnel order stable regardless of how the
        # melt interleaves rows.
        long["stage"] = pd.Categorical(long["stage"],
                                       [_STAGE_LABELS[s] for s in _STAGES],
                                       ordered=True)
        long = long.sort_values(["stage", "event_date"], kind="stable")
        long["stage"] = long["stage"].astype(str)

        ctx.set_header(subtitle="Where buyers leak out, and for whom")

        ctx.add_section("", [
            DataSource("traffic", df),
            FilterBar("traffic", df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "channel", "label": "Channel"},
                {"column": "device", "label": "Device", "type": "toggle"},
            ]),
        ])

        ctx.add_section("The Leak", [
            KpiRow([
                {"label": "Sessions", "agg": "sum", "column": "sessions",
                 "format": "number"},
                {"label": "Carts", "agg": "sum", "column": "add_to_cart",
                 "format": "number"},
                {"label": "Purchases", "agg": "sum", "column": "purchases",
                 "format": "number"},
                {"label": "Conversion", "agg": "ratio",
                 "numerator": "purchases", "denominator": "sessions",
                 "format": "percent"},
                {"label": "Cart Abandonment", "agg": "ratio",
                 "numerator": "lost_after_cart", "denominator": "add_to_cart",
                 "format": "percent"},
            ], dataset_id="traffic"),
            # The same numbers as something that happens rather than something
            # stated: particles flow in as sessions, each gate passes the live
            # measured rate for the current filters, and the rest drip out.
            # Pure canvas -- no vendor library, no extra_cdn -- which is the
            # one custom-section variant the other demos do not show. The
            # server sends only the stage layout; every count on the drawing
            # is recomputed client-side from the filter engine.
            RawHTML(html=custom_sections.CUSTOM_HTML,
                    js=custom_sections.CUSTOM_JS,
                    data_key="_leak",
                    data={"stages": [
                        {"col": col, "label": _STAGE_LABELS[col]}
                        for col in _STAGES
                    ]}),
        ])

        # ── The funnel itself ────────────────────────────────────────
        # Its own DataSource: FunnelChart needs stages as rows. The second
        # FilterBar carries the same columns, so narrowing either one tells
        # the same story from both frames.
        ctx.add_section("Session to Purchase", [
            DataSource("stages", long),
            # Same default window as the main bar. Without the date filter
            # this section showed all 90 days while The Leak above showed 30,
            # and two sections disagreeing on "sessions" reads as a bug --
            # correctly.
            FilterBar("stages", long, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "channel", "label": "Channel"},
                {"column": "device", "label": "Device", "type": "toggle"},
            ]),
            FunnelChart(long, label="stage", value="count",
                        title="The Funnel",
                        show_percentages=True, dataset_id="stages"),
        ])

        # ── Who converts ─────────────────────────────────────────────
        # Ratios through `ratios=`, never pre-divided: the division happens
        # after filtering, so the lines stay honest when the user narrows.
        # The mid-season sale annotation lands on a visible conversion dip --
        # bargain traffic browses more and buys less.
        ctx.add_section("Who Converts", [
            LineChart(df, x="event_date",
                      ratios=[{"numerator": "purchases",
                               "denominator": "sessions",
                               "label": "Conversion"}],
                      stack_by="channel",
                      title="Conversion by Channel",
                      y_format="percent", dataset_id="traffic"),
            HeatmapChart(long, x="stage", y="device", value="count",
                         title="Volume by Stage and Device",
                         log_scale=True, value_format="number",
                         dataset_id="stages"),
        ])

        # ── The fix we tried ─────────────────────────────────────────
        # The leak above provoked a test: one button's colour at checkout.
        # ABCompare is self-contained (no DataSource/FilterBar needed), and
        # the lift is deliberately small relative to n, so unlike Nova Play's
        # checkout test this one is not guaranteed to reach significance --
        # a portfolio where every test wins is not a believable portfolio,
        # and the bootstrap CI says so plainly when a delta straddles zero.
        # The ab_test block in report.yaml puts this section on the portal's
        # Experiments overview as Northwind's entry.
        df_ab = query_df(conn, queries.EXPERIMENT_USERS,
                         params={"experiment": "checkout_button_color"})
        # Kept margin at Northwind's typical ~55% -- this test's "net"
        # currency slot, the shop's equivalent of a store cut.
        df_ab["net_revenue"] = (df_ab["revenue"] * 0.55).round(2)
        ctx.add_section("The Fix We Tried (checkout_button_color)", [
            ABCompare.from_users(
                df_ab,
                variant_col="variant",
                control="control",
                test="variant_b",
                control_label="Blue Button",
                test_label="Green Button",
                metrics=[
                    Metric("revenue", kind="mean", fmt="currency",
                           pre_col="pre_period_revenue"),
                    Metric("net_revenue", kind="mean", fmt="currency",
                           label="Kept Margin"),
                    Metric("converted", kind="rate"),
                    Metric("transactions", kind="mean"),
                ],
                exposure_col="active_days",
                n_days=14,
                expected_split=(50, 50),
                iters=2000,
                pre_window_days=14,
                default_mode="all_users",
                title="checkout_button_color",
                test_name="checkout_button_color",
                start_date=ctx.config.get("ab_test", {}).get("start_date", ""),
                description=(
                    "Blue versus green checkout button, everything else "
                    "unchanged. The smallest tweak in this warehouse, on the "
                    "smallest audience -- check the confidence interval "
                    "before reading anything into the point estimate."
                ),
            ),
        ])
        # No trailing Detail dump: every number above is already the filtered
        # answer, and a row appendix is the thing report owners delete first.
