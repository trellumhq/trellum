"""Nova Play's A/B program: three experiments, one page.

Each test keeps the section, the metrics, and the statistical read it had as
a standalone report; ``report.yaml``'s ``ab_test`` list carries one entry per
test, which is how all three keep their own row and lifecycle on the portal's
Experiments overview (running / past its end / concluded, matched to their
ABCompare payloads by ``test_name``).

The three stories, deliberately different:

- **checkout_v2** exercises the ABCompare front door in full: SRM badge and
  the winsorisation / CUPED / bootstrap variance-reduction modes.
- **starter_pack_price** is the trap read: the lower price converts
  meaningfully more buyers, but each one spends less, so ARPU lands close to
  flat -- a conversion-only readout would call an unambiguous win and the
  revenue row says otherwise.
- **onboarding_v2** is about engagement, not money: ``converted`` in the
  warehouse row means "returned on day 1" here and ``transactions`` means
  "sessions logged on day 1" -- generic columns aliased to what THIS test
  measures. Revenue is declared (the front door requires one currency
  metric) but should read flat while Day-1 Return moves.

Console note: onboarding's frame makes ``validate_user_df`` print a harmless
``payer-no-gross`` WARN at build time -- it assumes the ``rate`` metric means
"paid", so returners with no revenue look like a data gap. Expected, not a
validation FAIL.
"""

from trellum import BaseReport
from trellum.components import (
    ABCompare,
    ABMethodologyNote,
    DataSource,
    DoughnutChart,
    FilterBar,
    KpiRow,
)
from trellum.data import query_df
from trellum.stats import Metric

from . import queries


def _start_date(ctx, test_name):
    """The declared start of one test, from report.yaml's ab_test list --
    the dates live there (the overview reads them) and nowhere else."""
    entries = ctx.config.get("ab_test") or []
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        if entry.get("test_name") == test_name:
            return entry.get("start_date", "")
    return ""


class ExperimentsReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")

        def load(experiment, net_factor=0.70):
            df = query_df(conn, queries.EXPERIMENT_USERS,
                          params={"experiment": experiment})
            # Net of a 30% store cut -- gives the modes a second currency
            # metric.
            df["net_revenue"] = (df["revenue"] * net_factor).round(2)
            return df

        df_checkout = load("checkout_v2")
        df_pricing = load("starter_pack_price")
        df_onboarding = load("onboarding_v2")

        ctx.set_header(
            subtitle=(
                f"Three tests, three reads — "
                f"{len(df_checkout) + len(df_pricing) + len(df_onboarding):,} "
                f"assigned users"
            ),
        )

        # ── checkout_v2: the full front door ─────────────────────────
        ctx.add_section("New Checkout (checkout_v2)", [
            DataSource("checkout_users", df_checkout),
            FilterBar("checkout_users", df_checkout, filters=[
                {"column": "variant", "label": "Variant", "type": "toggle"},
                {"column": "platform", "label": "Platform"},
                {"column": "region", "label": "Region"},
                {"column": "converted", "label": "Converted",
                 "type": "flag", "flag_value": 1},
            ]),
            ABCompare.from_users(
                df_checkout,
                variant_col="variant",
                control="control",
                test="variant_b",
                control_label="Control",
                test_label="Test",
                metrics=[
                    Metric("revenue", kind="mean", fmt="currency",
                           pre_col="pre_period_revenue"),
                    Metric("net_revenue", kind="mean", fmt="currency"),
                    Metric("converted", kind="rate"),
                    Metric("transactions", kind="mean"),
                ],
                exposure_col="active_days",
                n_days=28,
                expected_split=(50, 50),
                iters=2000,
                pre_window_days=28,
                default_mode="all_users",
                title="checkout_v2",
                test_name="checkout_v2",
                start_date=_start_date(ctx, "checkout_v2"),
                description=(
                    "New one-page checkout versus the existing multi-step "
                    "flow. Switch modes to see how winsorisation and CUPED "
                    "change the confidence intervals."
                ),
            ),
            # Claimed · demo: transactions is the same metrics.yaml definition
            # monetization and player-overview claim off their own datasets --
            # a genuine completed-IAP-purchase count per user in this
            # experiment, not a relabelled column (compare onboarding below,
            # where the same column means something else and is deliberately
            # left unclaimed). avg_transaction_value is a currency ratio
            # (descriptive on purpose, see metrics.yaml), computed once here
            # from this section's own revenue/transactions columns.
            KpiRow([
                {"label": "Users", "agg": "count", "format": "number"},
                {"label": "Conversions", "agg": "sum", "column": "converted",
                 "format": "number"},
                {"label": "Conversion Rate", "agg": "ratio",
                 "numerator": "converted", "denominator": "exposed",
                 "format": "percent"},
                {"label": "Revenue", "agg": "sum", "column": "revenue",
                 "format": "currency"},
                {"metric": "transactions"},
                {"metric": "avg_transaction_value",
                 "value": _avg_txn(df_checkout)},
            ], dataset_id="checkout_users"),
            DoughnutChart(df_checkout, label="platform", value="revenue",
                          title="Revenue by Platform",
                          cross_filter=True, dataset_id="checkout_users"),
        ])

        # ── starter_pack_price: the trap read ────────────────────────
        ctx.add_section("Starter Pack Pricing (starter_pack_price)", [
            DataSource("pricing_users", df_pricing),
            FilterBar("pricing_users", df_pricing, filters=[
                {"column": "variant", "label": "Price", "type": "toggle"},
                {"column": "platform", "label": "Platform"},
                {"column": "region", "label": "Region"},
                {"column": "converted", "label": "Converted",
                 "type": "flag", "flag_value": 1},
            ]),
            ABCompare.from_users(
                df_pricing,
                variant_col="variant",
                control="control",
                test="variant_b",
                control_label="$4.99 (Control)",
                test_label="$3.99 (Test)",
                metrics=[
                    Metric("revenue", kind="mean", fmt="currency",
                           pre_col="pre_period_revenue"),
                    Metric("net_revenue", kind="mean", fmt="currency"),
                    Metric("converted", kind="rate"),
                    Metric("transactions", kind="mean"),
                ],
                exposure_col="active_days",
                n_days=28,
                expected_split=(50, 50),
                iters=2000,
                pre_window_days=28,
                default_mode="all_users",
                title="starter_pack_price",
                test_name="starter_pack_price",
                start_date=_start_date(ctx, "starter_pack_price"),
                description=(
                    "$4.99 versus $3.99 on the starter IAP pack. The lower "
                    "price converts more buyers; watch whether Revenue "
                    "still moves once each buyer is spending less."
                ),
            ),
            # avg_transaction_value is the number this test's whole story
            # turns on: more buyers, smaller average.
            KpiRow([
                {"label": "Users", "agg": "count", "format": "number"},
                {"label": "Conversions", "agg": "sum", "column": "converted",
                 "format": "number"},
                {"label": "Conversion Rate", "agg": "ratio",
                 "numerator": "converted", "denominator": "exposed",
                 "format": "percent"},
                {"label": "Revenue", "agg": "sum", "column": "revenue",
                 "format": "currency"},
                {"metric": "transactions"},
                {"metric": "avg_transaction_value",
                 "value": _avg_txn(df_pricing)},
            ], dataset_id="pricing_users"),
            DoughnutChart(df_pricing, label="platform", value="revenue",
                          title="Revenue by Platform",
                          cross_filter=True, dataset_id="pricing_users"),
        ])

        # ── onboarding_v2: engagement, not money ─────────────────────
        # No claimed metrics here on purpose: `transactions` in this frame
        # counts Day-1 sessions, not IAP purchases, so claiming it against
        # the catalog's definition would be a stretched claim -- the pointed
        # example the metrics catalog documentation leans on.
        ctx.add_section("Onboarding Tutorial (onboarding_v2)", [
            DataSource("onboarding_users", df_onboarding),
            FilterBar("onboarding_users", df_onboarding, filters=[
                {"column": "variant", "label": "Tutorial", "type": "toggle"},
                {"column": "platform", "label": "Platform"},
                {"column": "region", "label": "Region"},
                {"column": "converted", "label": "Returned Day 1",
                 "type": "flag", "flag_value": 1},
            ]),
            ABCompare.from_users(
                df_onboarding,
                variant_col="variant",
                control="control",
                test="variant_b",
                control_label="Text Tutorial",
                test_label="Interactive Tutorial",
                metrics=[
                    Metric("revenue", kind="mean", fmt="currency",
                           pre_col="pre_period_revenue", label="Revenue"),
                    Metric("net_revenue", kind="mean", fmt="currency"),
                    Metric("converted", kind="rate", label="Day-1 Return"),
                    Metric("transactions", kind="mean",
                           label="Sessions (Day 1)"),
                ],
                exposure_col="active_days",
                n_days=21,
                expected_split=(50, 50),
                iters=2000,
                pre_window_days=21,
                default_mode="all_users",
                title="onboarding_v2",
                test_name="onboarding_v2",
                start_date=_start_date(ctx, "onboarding_v2"),
                description=(
                    "Text tutorial versus an interactive one. The row to "
                    "watch is Day-1 Return, not Revenue -- a tutorial does "
                    "not sell anything, so any revenue gap here is noise."
                ),
            ),
            KpiRow([
                {"label": "Users", "agg": "count", "format": "number"},
                {"label": "Day-1 Returns", "agg": "sum", "column": "converted",
                 "format": "number"},
                {"label": "Day-1 Return Rate", "agg": "ratio",
                 "numerator": "converted", "denominator": "exposed",
                 "format": "percent"},
                {"label": "Sessions (Day 1)", "agg": "sum",
                 "column": "transactions", "format": "number"},
            ], dataset_id="onboarding_users"),
        ])

        # One methodology note for the page: the modes it explains are the
        # same machinery in every section above.
        ctx.add_section("Methodology", [
            ABMethodologyNote(),
        ])

        # No trailing Detail dump. The one table an experiment page earns is
        # a decision table, and each ABCompare's drill-down already is one.


def _avg_txn(df):
    """Average transaction value over the whole frame, or 0.0 with no
    transactions -- a static KPI value, computed once at build time."""
    txn_sum = df["transactions"].sum()
    return round(float(df["revenue"].sum() / txn_sum), 2) if txn_sum else 0.0
