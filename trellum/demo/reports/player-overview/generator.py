"""Player Overview -- the Nova Play flagship, one page per question a
publisher asks about its players.

Consolidated from four reports on purpose: the demo shows features, and a
feature is better demonstrated as a section of a real page than as a page of
its own. In order: how is the player base doing (KPIs, engagement, revenue),
WHERE is it (choropleth), WHO is it (behaviour venn), and does it COME BACK
(cohort retention). Because this is a demo, the signature charts say what
they are demonstrating in their titles.

Three DataSources, deliberately: fact_daily at daily grain, fact_retention at
cohort grain, fact_player_segments at membership grain. Forcing them into one
frame is the LEFT-JOIN anti-pattern the validator exists to catch -- separate
grains get separate DataSources, each with the filters that make sense for
its grain.
"""

from datetime import timedelta

from trellum import BaseReport
from trellum.components import (
    BarChart,
    ComboChart,
    DataSource,
    DoughnutChart,
    FilterBar,
    HeatmapChart,
    KpiRow,
    LineChart,
    RawHTML,
    StackedBar,
)
from trellum.data import query_df

from . import custom_sections, queries

# Fetch a wider window than the filter defaults to, so the user can widen the
# date range without a re-run.
_LOOKBACK_DAYS = 120
_DEFAULT_FILTER_DAYS = 30
_RETENTION_LOOKBACK_DAYS = 365

#: The warehouse stores country names; the shipped atlas keys on its own.
#: These are the ones that differ. An explicit table, not fuzzy matching: a
#: silently mismatched country is a hole in the map nobody notices.
_ATLAS_NAMES = {
    "United States": "United States of America",
}


class PlayerOverviewReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.PLAYER_DAILY, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        # Derived columns must exist on the raw frame so the client can sum
        # them live; ARPDAU and payer rate are expressed as ratio pairs below
        # rather than precomputed, because a mean of ratios is not a ratio.
        df["total_revenue"] = df["iap_revenue"] + df["ad_revenue"]

        ctx.set_header(
            subtitle="Health, geography, behaviour and retention on one page",
        )

        # ── Filters ──────────────────────────────────────────────────
        ctx.add_section("", [
            DataSource("main", df),
            FilterBar("main", df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "region", "label": "Region"},
                {"column": "country", "label": "Country", "depends_on": "region"},
                {"column": "title", "label": "Title"},
                {"column": "platform", "label": "Platform", "type": "toggle"},
                {"column": "is_promo", "label": "Promo Days",
                 "type": "flag", "flag_value": 1},
            ]),
        ])

        # ── Headline numbers ─────────────────────────────────────────
        # Claimed · demo: gross/iap/ad_revenue and transactions are the same
        # metrics.yaml definitions monetization claims off its own dataset --
        # two reports, two datasets, one definition each, by construction.
        # `agg: "ratio"` always returns a percentage (the runtime multiplies
        # by 100), so it is only correct with `format: "percent"`. Currency
        # ratios such as ARPDAU belong in a chart's `ratios=` instead -- see
        # "ARPDAU by Platform". payer_share is the same ratio monetization's
        # own dataset also claims, and ad_share is the demo's stale-badge
        # metric (v2 in metrics.yaml -- see its file header) claimed here and
        # by insert-coin.
        ctx.add_section("Overview", [
            KpiRow([
                {"metric": "gross_revenue"},
                {"metric": "iap_revenue"},
                {"metric": "ad_revenue"},
                {"metric": "transactions"},
                {"label": "New Users", "agg": "sum", "column": "new_users",
                 "format": "number"},
                {"label": "Sessions", "agg": "sum", "column": "sessions",
                 "format": "number"},
                {"metric": "payer_share"},
                {"metric": "ad_share"},
            ], dataset_id="main"),
        ])

        # ── Efficiency ───────────────────────────────────────────────
        # Claimed · demo: arpdau and avg_transaction_value are currency
        # RATIOS, which metrics.yaml deliberately defines as descriptive
        # (see metrics.yaml's header) -- the live KpiRow ratio agg always
        # multiplies by 100 for percent display, which is wrong by 100x for
        # a dollar figure. A descriptive claim still carries the shared
        # label/format; the number itself is computed here, once, in Python.
        _dau_sum = df["dau"].sum()
        _arpdau = float(df["iap_revenue"].sum() / _dau_sum) if _dau_sum else 0.0
        _txn_sum = df["transactions"].sum()
        _avg_txn = float(df["iap_revenue"].sum() / _txn_sum) if _txn_sum else 0.0
        ctx.add_section("Efficiency", [
            # No dataset_id: both cards are static values, computed once
            # above rather than re-aggregated live (see the comment on why).
            KpiRow([
                {"metric": "arpdau", "value": round(_arpdau, 4)},
                {"metric": "avg_transaction_value", "value": round(_avg_txn, 2)},
            ]),
        ])

        # ── Engagement ───────────────────────────────────────────────
        ctx.add_section("Engagement", [
            LineChart(df, x="event_date", y="dau",
                      stack_by="platform",
                      title="Daily Active Users by Platform",
                      dataset_id="main", y_format="number"),
            ComboChart(df, x="event_date",
                       bar_cols=["new_users"],
                       line_cols=["sessions"],
                       bar_labels=["New Users"],
                       line_labels=["Sessions"],
                       bar_format="number", line_format="number",
                       title="Acquisition vs Session Volume · demo: dual-axis "
                             "combo, bars and line from one DataSource",
                       dataset_id="main"),
        ])

        # ── Revenue ──────────────────────────────────────────────────
        ctx.add_section("Revenue", [
            StackedBar(df, x="event_date", y_cols=["iap_revenue"],
                       stack_by="spender_tier",
                       stack_by_options={
                           "Spender Tier": "spender_tier",
                           "Platform": "platform",
                           "Region": "region",
                           "Title": "title",
                       },
                       normalize_toggle=True,
                       stack_sort="volume_desc",
                       value_format="currency",
                       title="IAP Revenue by Spender Tier · demo: stack_by "
                             "switcher and normalise toggle, no rebuild",
                       dataset_id="main"),
            LineChart(df, x="event_date",
                      ratios=[{"numerator": "iap_revenue",
                               "denominator": "dau",
                               "label": "ARPDAU"}],
                      stack_by="platform",
                      title="ARPDAU by Platform · demo: live ratios -- the "
                            "division happens after your filters",
                      dataset_id="main", y_format="currency"),
        ])

        # ── Where ────────────────────────────────────────────────────
        # The map states the shape of the selected window; the ranked bar
        # beside it stays live and cross-filters. A choropleth that silently
        # dropped a country would look like a country with no revenue, so the
        # client checks its atlas and names anything it could not place.
        ctx.add_section("Where the Money Comes From", [
            RawHTML(html=custom_sections.MAP_HTML,
                    js=custom_sections.MAP_JS,
                    data_key="_revenue_map",
                    data=self._build_map(df)),
            BarChart(df, x="country", y="iap_revenue",
                     title="IAP Revenue by Country · demo: cross-filter -- "
                           "click a bar to filter the page",
                     horizontal=True, sort="desc",
                     cross_filter=True, y_format="currency",
                     dataset_id="main"),
            DoughnutChart(df, label="spender_tier", value="iap_revenue",
                          title="Revenue Share by Spender Tier",
                          cross_filter=True, dataset_id="main"),
        ])

        # ── Who ──────────────────────────────────────────────────────
        # Membership grain, its own DataSource and section filters. The venn
        # recounts every intersection client-side from raw rows, because
        # aggregated set sizes cannot answer "how many of them overlap".
        seg = query_df(conn, queries.PLAYER_SEGMENTS)
        ctx.add_section("Which Behaviours Overlap", [
            DataSource("segments", seg),
            FilterBar("segments", seg, filters=[
                {"column": "platform", "label": "Platform", "type": "toggle"},
                {"column": "title", "label": "Title"},
            ]),
            RawHTML(html=custom_sections.VENN_HTML,
                    js=custom_sections.VENN_JS,
                    data_key="_player_segments",
                    data=self._build_venn_layout(seg)),
        ])

        # ── Do they come back ────────────────────────────────────────
        # Cohort grain, chunked: a year of cohorts is more than one payload
        # should carry, so the newest months are inline and history streams
        # in when the range widens.
        ret_start = (ctx._now_utc - timedelta(days=_RETENTION_LOOKBACK_DAYS)
                     ).strftime("%Y-%m-%d")
        ret = query_df(conn, queries.RETENTION, params={
            "start_date": ret_start,
            "end_date": ctx.today,
        })
        ret["cohort_month"] = ret["cohort_date"].str.slice(0, 7)
        ret["day_label"] = "D" + ret["day_number"].astype(str)

        # Claimed · demo: retention_d1/retention_d7 pin a single day offset,
        # which the generic ratio agg can't express (it sums whatever rows
        # the page's own filters leave, across every day_number at once). So
        # each is computed once here, at its own day_number, and claimed with
        # a Python value -- same metrics.yaml definition, same numerator and
        # denominator, just evaluated before the client ever sees the row.
        def _retention_pct(day_number: int) -> float:
            day = ret[ret["day_number"] == day_number]
            size = day["cohort_size"].sum()
            return float(day["retained_users"].sum() / size * 100) if size else 0.0

        retention_d1 = _retention_pct(1)
        retention_d7 = _retention_pct(7)

        ctx.add_section("Do They Come Back", [
            DataSource("cohorts", ret, chunk_by="month", default_chunks=3),
            # Default to the full year: the cohort heatmap needs its twelve
            # month-columns to show a pattern, and a 90-day default gave it
            # three. Chunking keeps the cost honest -- older months stream in.
            FilterBar("cohorts", ret, filters=[
                {"column": "cohort_date", "label": "Cohort Date",
                 "type": "date_range", "default_days": _RETENTION_LOOKBACK_DAYS},
                {"column": "title", "label": "Title"},
                {"column": "platform", "label": "Platform", "type": "toggle"},
            ]),
            KpiRow([
                {"label": "Cohort Size", "agg": "sum", "column": "cohort_size",
                 "format": "number"},
                {"label": "Retention Rate", "agg": "ratio",
                 "numerator": "retained_users", "denominator": "cohort_size",
                 "format": "percent"},
                {"metric": "retention_d1", "value": round(retention_d1, 1)},
                {"metric": "retention_d7", "value": round(retention_d7, 1)},
            ], dataset_id="cohorts"),
            LineChart(ret, x="cohort_date",
                      ratios=[{"numerator": "retained_users",
                               "denominator": "cohort_size",
                               "label": "Retention"}],
                      stack_by="day_label",
                      title="Retention by Day Offset · demo: a year of "
                            "cohorts, chunked -- newest months load first",
                      y_format="percent", dataset_id="cohorts"),
            # normalize="row": D1 retains an order of magnitude more players
            # than D30, so on a global scale the D30 row is uniformly cold and
            # says nothing. Row-relative colour asks the question the section
            # is named after -- is each day-offset getting better or worse
            # across cohort months.
            HeatmapChart(ret, x="cohort_month", y="day_label",
                         value="retained_users",
                         title="Retained Users by Cohort Month · demo: "
                               "row-normalised -- each day-offset against its "
                               "own range",
                         normalize="row",
                         value_format="number", dataset_id="cohorts"),
        ])

        # ── Snapshot ─────────────────────────────────────────────────
        # Deliberately static: the title commits to a fixed day, so the date
        # filter must not move it.
        top_titles = (
            df[df["event_date"] == ctx.yesterday]
            .groupby("title", as_index=False)["iap_revenue"].sum()
            .sort_values("iap_revenue", ascending=False)
        )
        ctx.add_section("Yesterday", [
            BarChart(top_titles, x="title", y="iap_revenue",
                     title=f"IAP Revenue by Title ({ctx.yesterday})",
                     horizontal=True, static=True, y_format="currency"),
        ])

    @staticmethod
    def _build_map(df) -> dict:
        """Per-country totals, keyed by the name the atlas uses."""
        if df.empty:
            return {"by_country": {}, "max": 0, "label": "Revenue",
                    "prefix": "$"}
        totals = df.groupby("country", as_index=False)["total_revenue"].sum()
        by_country = {
            _ATLAS_NAMES.get(row["country"], row["country"]):
                round(float(row["total_revenue"]), 2)
            for _, row in totals.iterrows()
        }
        return {
            "by_country": by_country,
            "max": float(totals["total_revenue"].max()),
            "label": "Revenue",
            "prefix": "$",
        }

    @staticmethod
    def _build_venn_layout(seg) -> dict:
        """Circle ordering and cohort size; the client counts the regions."""
        if seg.empty:
            return {"order": [], "cohort": 0}
        sizes = (seg.groupby("segment")["user_id"].nunique()
                    .sort_values(ascending=False))
        return {
            "order": [str(s) for s in sizes.index[:3]],
            "cohort": int(seg["user_id"].nunique()),
        }
