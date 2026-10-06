"""Store Health -- the whole Northwind Threads picture on one page.

Merged from store-performance and product-returns because an owner does not
read "how did we sell" and "what came back" as separate subjects: kept margin
is one number and it needs both halves. The page runs top to bottom as a
Monday morning does -- what changed, the trend, where the money comes from,
what came back, what to restock. Signature charts carry demo subtitles,
because this is a demo.

The interesting weeks are baked into the fixtures and annotated: a
mid-season sale that lifted sessions a third while discounts ate the revenue
lift, and a four-day stockout of the best-selling sneaker.
"""

from datetime import timedelta

import pandas as pd

from trellum import BaseReport
from trellum.components import (
    ComparisonTable,
    DataSource,
    DoughnutChart,
    FilterBar,
    Grid,
    KpiCard,
    KpiRow,
    LineChart,
    ScatterChart,
    TreemapChart,
)
from trellum.data import query_df

from . import queries

_LOOKBACK_DAYS = 90
_DEFAULT_FILTER_DAYS = 30
_PERIOD_DAYS = 28


class StoreHealthReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.ORDERS, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        # One row is one order line; the unit column makes count a summable
        # and gives ratio aggs a denominator. Kept margin and refunds are
        # derived per row so any filtered subset sums correctly.
        df["orders_n"] = 1
        df["kept_margin"] = df["margin"] * (1 - df["returned"])
        df["refunded"] = df["gross_revenue"] * df["returned"]

        ctx.set_header(subtitle="Sales, margin and returns -- the Monday-morning page")

        ctx.add_section("", [
            DataSource("orders", df),
            FilterBar("orders", df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "channel", "label": "Channel"},
                {"column": "device", "label": "Device", "type": "toggle"},
                {"column": "category", "label": "Category"},
                {"column": "discount_pct", "label": "Discount %",
                 "type": "slider", "format": "percent"},
            ]),
        ])

        # ── What changed ─────────────────────────────────────────────
        # Static delta cards for two fixed windows (the date filter must not
        # move a card that names its own period). The trend follows; the
        # live headline row stays below it for readers who need exact totals.
        current, prior = self._split_periods(df, ctx.today)
        ctx.add_section(f"Last {_PERIOD_DAYS} Days vs Prior", [
            Grid([
                self._delta_card("Revenue", current["gross_revenue"].sum(),
                                 prior["gross_revenue"].sum(), "currency"),
                self._delta_card("Orders", len(current), len(prior), "number"),
                self._delta_card("Kept Margin", current["kept_margin"].sum(),
                                 prior["kept_margin"].sum(), "currency"),
                self._delta_card("Refunded", current["refunded"].sum(),
                                 prior["refunded"].sum(), "currency"),
            ], columns=4, card=False),
        ])

        # ── The trend, with the sale annotated ───────────────────────
        ctx.add_section("Daily Trend", [
            LineChart(df, x="event_date", y=["gross_revenue", "kept_margin"],
                      y_labels=["Revenue", "Kept Margin"],
                      title="Revenue and Kept Margin by Day · demo: the "
                            "annotated sale week looks fine on revenue -- "
                            "only the margin line says what it cost",
                      y_format="currency", dataset_id="orders"),
            LineChart(df, x="event_date",
                      ratios=[{"numerator": "gross_revenue",
                               "denominator": "orders_n",
                               "label": "Avg Order Value"}],
                      title="Average Order Value by Day · demo: live ratios, "
                            "divided after your filters",
                      y_format="currency", dataset_id="orders"),
        ])

        ctx.add_section("Current Selection", [
            KpiRow([
                {"label": "Revenue", "agg": "sum", "column": "gross_revenue",
                 "format": "currency"},
                {"label": "Orders", "agg": "count", "format": "number"},
                {"label": "Margin %", "agg": "ratio",
                 "numerator": "margin", "denominator": "gross_revenue",
                 "format": "percent"},
                {"label": "Return Rate", "agg": "ratio",
                 "numerator": "returned", "denominator": "orders_n",
                 "format": "percent"},
                {"label": "Kept Margin", "agg": "sum", "column": "kept_margin",
                 "format": "currency"},
                # Claimed · demo: refund_rate is revenue-weighted (refunded $
                # over gross $), a different question than the order-count
                # "Return Rate" above -- footwear can be both the best seller
                # AND the highest refund_rate if its orders skew expensive.
                {"metric": "refund_rate"},
            ], dataset_id="orders"),
        ])

        # ── Where the money comes from ───────────────────────────────
        ctx.add_section("Mix", [
            TreemapChart(df, group_cols=["category", "product"],
                         value="gross_revenue",
                         title="Revenue by Category and Product · demo: "
                               "two-level treemap from order-grain rows",
                         value_format="currency", dataset_id="orders"),
            DoughnutChart(df, label="channel", value="gross_revenue",
                          title="Revenue by Channel",
                          cross_filter=True, dataset_id="orders"),
        ])

        # ── What comes back: the restock decision ────────────────────
        # Ranked by KEPT margin, which disagrees with the sales ranking
        # because footwear returns a quarter of what it ships. Static: this
        # is the page's conclusion, not another view of the filters.
        ctx.add_section("Sellers vs Senders-Back", [
            ComparisonTable(
                self._product_table(df),
                title=f"By Product, Last {_LOOKBACK_DAYS} Days · demo: "
                      f"conditional formats mark return rates",
                value_col="Kept Margin",
                compare_cols=["Revenue", "Refunded"],
                static=True,
                conditional_formats={
                    "Return %": [
                        {"op": ">", "value": 18, "color": "var(--accent-red)"},
                        {"op": "<", "value": 8, "color": "var(--accent-green)"},
                    ],
                },
            ),
            ScatterChart(self._product_points(df), x="orders", y="return_pct",
                         size="revenue",
                         title="Volume vs Return Rate (bubble = revenue)",
                         y_format="percent", static=True),
        ])

        # The lag trap, named where the reader is already looking.
        ctx.add_section("Return Trend (recent days flatter -- returns lag)", [
            LineChart(df, x="event_date",
                      ratios=[{"numerator": "returned",
                               "denominator": "orders_n",
                               "label": "Return Rate"}],
                      title="Return Rate by Order Date",
                      y_format="percent", dataset_id="orders"),
        ])

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _split_periods(df: pd.DataFrame, today: str):
        """Split into the trailing period and the one before it."""
        end = pd.Timestamp(today)
        cur_start = end - pd.Timedelta(days=_PERIOD_DAYS)
        prior_start = cur_start - pd.Timedelta(days=_PERIOD_DAYS)
        dates = pd.to_datetime(df["event_date"])
        current = df[(dates > cur_start) & (dates <= end)]
        prior = df[(dates > prior_start) & (dates <= cur_start)]
        return current, prior

    @staticmethod
    def _delta_card(label: str, current: float, prior: float, fmt: str) -> KpiCard:
        """KpiCard with a period-over-period delta; "n/a" beats an absurd
        percentage when the prior window is empty."""
        if not prior:
            return KpiCard(label=label, value=current, format=fmt,
                           sub="no prior data")
        pct = (current - prior) / prior * 100
        return KpiCard(
            label=label,
            value=current,
            format=fmt,
            delta=f"{pct:+.1f}%",
            delta_direction="up" if pct >= 0 else "down",
            sub=f"vs prior {_PERIOD_DAYS}d",
        )

    @staticmethod
    def _product_table(df):
        """Per-product economics, ranked by what the store keeps."""
        g = df.groupby("product").agg(
            Category=("category", "first"),
            Orders=("orders_n", "sum"),
            Revenue=("gross_revenue", "sum"),
            Refunded=("refunded", "sum"),
            Returned=("returned", "sum"),
            Kept=("kept_margin", "sum"),
        ).reset_index()
        g["Return %"] = (g["Returned"] / g["Orders"] * 100).round(1)
        g = g.rename(columns={"product": "Product", "Kept": "Kept Margin"})
        for col in ("Revenue", "Refunded", "Kept Margin"):
            g[col] = g[col].round(0)
        return (g[["Product", "Category", "Orders", "Revenue", "Refunded",
                   "Return %", "Kept Margin"]]
                .sort_values("Kept Margin", ascending=False))

    @staticmethod
    def _product_points(df):
        g = df.groupby("product").agg(
            orders=("orders_n", "sum"),
            returns=("returned", "sum"),
            revenue=("gross_revenue", "sum"),
        ).reset_index()
        # Fraction, not x100: percent formatting is the axis's job.
        g["return_pct"] = g["returns"] / g["orders"]
        return g
