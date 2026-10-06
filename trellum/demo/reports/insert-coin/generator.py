"""Insert Coin -- the report that is a minigame. v2: Ride the Metric.

The wildest end of what a custom section can be: a playable side-scroller
whose level is the query result -- and, after the owner's design review,
one that speaks the report's own visual language instead of wearing an
arcade costume. The level IS a line chart: terrain is daily active users
drawn with grid, axis and area fill; revenue is orbs of light sized by
their dollar value; the outage is a literal gap in the line; events.yaml
entries stand in the world as the same dashed annotations every chart uses.

Two owner rules define v2. Nothing moves until the user presses Start --
at rest the section is an honest static chart of the filtered window. And
themes work everywhere: every color is read from the theme tokens at draw
time, so the game reskins with the page. The game subscribes to the same
client-side filter engine as the KPI row: change any filter and the world
rebuilds from that segment's rows -- the axis rescales, the orb value
recalibrates (a fixed denomination would starve the small tiers), and a
single selected spender tier changes the comet you ride as.
"""

from datetime import timedelta

from trellum import BaseReport
from trellum.components import DataSource, FilterBar, KpiRow, RawHTML
from trellum.data import query_df

from . import custom_sections, queries

_LOOKBACK_DAYS = 180
#: A 60-day default window is a ~25 second run -- long enough to meet the
#: outage gap and a coin sale, short enough to replay after every filter
#: change. The date filter doubles as the difficulty slider.
_DEFAULT_FILTER_DAYS = 60

#: How each spender tier plays, when the filter isolates one. Speed and
#: magnetic pull are the game-feel of the segment: whales are slow, huge
#: and pull revenue in; non-spenders are the fastest runner in the game
#: and there is nothing for them to collect -- which is the honest
#: portrait of that segment. `size` is the comet radius in px.
_TIERS = {
    "whale":       {"speed": 0.85, "pull": 62, "size": 19},
    "dolphin":     {"speed": 1.00, "pull": 30, "size": 14},
    "minnow":      {"speed": 1.12, "pull": 16, "size": 11},
    "non_spender": {"speed": 1.28, "pull": 0,  "size": 9},
    "default":     {"speed": 1.00, "pull": 22, "size": 14},
}

#: A day below this fraction of the window's median DAU has no floor. With
#: no filters the APAC outage is a dip you ride through; filter Region to
#: APAC and the same days lose their ground entirely.
_PIT_FRACTION = 0.55

#: Player-days verdict thresholds: the season "won" at 6M+, "lost" under 5M.
#: The sum is read from the filter engine, never re-parsed from the compact
#: label, and re-applied on every re-render (filters, theme, data refresh)
#: because a re-render rebuilds the card's children.
_PLAYER_DAYS_VERDICT_JS = """
(function () {
  var GREEN_AT = 6e6, RED_BELOW = 5e6;
  function verdict() {
    var rows = fw.filterEngine.getFiltered('arcade');
    if (!rows || !rows.length) return;
    var sum = 0;
    for (var i = 0; i < rows.length; i++) sum += Number(rows[i].dau) || 0;
    var cards = document.querySelectorAll('.fw-kpi-card');
    for (var j = 0; j < cards.length; j++) {
      var lbl = cards[j].querySelector('.fw-kpi-label');
      if (!lbl || lbl.textContent.trim() !== 'Player-days') continue;
      var val = cards[j].querySelector('.fw-kpi-value');
      if (!val) continue;
      val.style.color = sum < RED_BELOW ? 'var(--accent-red)'
                      : sum >= GREEN_AT ? 'var(--accent-green)' : '';
    }
  }
  fw.filterEngine.onReady('arcade', function () {
    var tries = 0;
    var timer = setInterval(function () {
      var card = document.querySelector('.fw-kpi-card');
      if (!card && ++tries < 100) return;
      clearInterval(timer);
      if (!card) return;
      var grid = card.closest('.fw-kpi-grid') || card.parentElement;
      new MutationObserver(verdict).observe(grid, { childList: true, subtree: true });
      verdict();
    }, 150);
  });
})();
"""


class InsertCoinReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.DAILY, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        # Collapse country to region: the game never filters below region,
        # and the collapse halves the wire for identical filtered sums.
        df = (df.groupby(["event_date", "title", "platform", "region",
                          "spender_tier"], as_index=False)
                [["dau", "payers", "iap_revenue", "ad_revenue"]].sum())
        # Claimed · demo: gross_revenue needs this column, the same
        # derivation player-overview and monetization use.
        df["total_revenue"] = df["iap_revenue"] + df["ad_revenue"]

        n_rows = len(df)
        n_days = df["event_date"].nunique()
        ctx.set_header(
            subtitle=f"A chart you can be inside: {n_rows:,} warehouse rows "
                     f"over {n_days} days. At rest it is a line chart; press "
                     f"Start and you ride it -- every filter rebuilds the "
                     f"world",
        )

        ctx.add_section("", [
            DataSource("arcade", df),
            FilterBar("arcade", df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "title", "label": "Title"},
                {"column": "platform", "label": "Platform", "type": "toggle"},
                {"column": "spender_tier", "label": "Spender Tier"},
                {"column": "region", "label": "Region"},
            ]),
        ])

        # The honest numbers the game gamifies, from the same DataSource --
        # the end-of-run screen restates the IAP sum so the agreement with
        # this row is visible on screen.
        # Player-days wears a verdict color: red under 5M, green at 6M and
        # up, neutral between. The thresholds are judged against the exact
        # filtered sum from the engine, not re-parsed from the compact "6.0M"
        # label -- 5.96M rounds to that same label and must NOT turn green.
        #
        # Claimed · demo: gross/iap/ad_revenue, payer_share and ad_share are
        # the same metrics.yaml definitions player-overview claims off its
        # own dataset -- ad_share doubles as the second claimant for the
        # catalog's stale-badge demo (see metrics.yaml's file header).
        # arpdau is a currency ratio (descriptive on purpose, see
        # metrics.yaml), so it is computed once here in Python, same pattern
        # as player-overview's Efficiency section.
        _dau_sum = df["dau"].sum()
        _arpdau = float(df["iap_revenue"].sum() / _dau_sum) if _dau_sum else 0.0
        ctx.add_section("The Season, Scored", [
            KpiRow([
                {"label": "Player-days", "agg": "sum", "column": "dau",
                 "format": "number"},
                {"metric": "gross_revenue"},
                {"metric": "iap_revenue"},
                {"metric": "ad_revenue"},
                # Not a `ratio` agg: that one is percent-scaled by design
                # (built for conversion rates), so ARPDAU would render as
                # dollars x100. avg_by_date is the honest per-day average.
                {"label": "Avg Daily Players", "agg": "avg_by_date",
                 "column": "dau", "format": "number"},
                {"metric": "payer_share"},
                {"metric": "ad_share"},
                {"metric": "arpdau", "value": round(_arpdau, 4)},
            ], dataset_id="arcade"),
            RawHTML(html="", js=_PLAYER_DAYS_VERDICT_JS),
        ])

        ctx.add_section("The Game", [
            RawHTML(
                html=custom_sections.CABINET_HTML,
                js=custom_sections.CABINET_JS,
                data_key="_arcade",
                data={
                    "tiers": _TIERS,
                    "pit_fraction": _PIT_FRACTION,
                },
            ),
        ])
        # No Detail dump, and no other chart either: the game is the report.
        # The tame version of every number on screen is one report over in
        # Player Overview.
