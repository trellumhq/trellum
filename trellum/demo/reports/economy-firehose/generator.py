"""A Lot of Data: the economy firehose.

The demo's scale proof. Every other report is sized to its question; this one
is sized to make a point -- 2,013,984 warehouse rows, every currency source and
sink in the portfolio, streamed to the browser in monthly chunks and kept
fully interactive. Because this is a demo, the signature charts say exactly
what they are demonstrating in their titles.

Two real findings are buried in the volume, so scale has something to be FOR:
the forge cost rebalance (annotated) that flipped net coin flow from balanced
to inflating overnight, and the 14-day gacha banner cadence pulsing through
the gem sinks.

One technique worth stealing: the per-currency charts do NOT get their own
DataSources. Coins and gems live as zero-filled signed columns (`net_coins`,
`net_gems`) on the one shared frame, so every chart sums the same 2.01M rows
and nothing is paid for twice in data.json.
"""

from datetime import timedelta

import numpy as np

from trellum import BaseReport
from trellum.components import (
    BarChart,
    DataSource,
    FilterBar,
    Grid,
    HeatmapChart,
    KpiRow,
    LineChart,
    MiniKpi,
    RawHTML,
    StackedBar,
    Toggle,
    Visible,
)
from trellum.data import query_df

from . import queries

#: The whole table, and the whole table by DEFAULT: this is the a-lot-of-data
#: report, so it opens on every month of the ledger -- the visible chunk
#: streaming ("Loading older data...") is part of the demonstration, the
#: net-flow chart shows the forge step in context, and the heatmap gets four
#: years of columns instead of three. The ledger keeps 3.7x the marts'
#: history (ECONOMY_HISTORY_MULT in tools/make_fixtures.py), which is what
#: puts it over two million rows; chunking by month means that is nearly free
#: on first paint -- more months exist, the same three load inline.
#:
#: Two million is a measured ceiling, not an ambition: see that constant for
#: the ~859 bytes of browser heap each row costs and why this table is sized
#: to sit under half of Chrome's 4 GB limit.
_LOOKBACK_DAYS = 1554
_DEFAULT_FILTER_DAYS = 1554


class EconomyFirehoseReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.ECONOMY, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        # Signed amount makes "net flow" a plain sum: sources add, sinks
        # subtract, and any filtered subset nets correctly client-side. The
        # per-currency variants are zero-filled so both currency charts can
        # share the one DataSource instead of duplicating the payload.
        sign = df["kind"].map({"source": 1, "sink": -1})
        df["net"] = df["amount"] * sign
        df["net_coins"] = np.where(df["currency"] == "coins", df["net"], 0)
        df["net_gems"] = np.where(df["currency"] == "gems", df["net"], 0)
        df["month"] = df["event_date"].str.slice(0, 7)
        # Zero-filled source/sink splits so RTP -- sources paid out per sink
        # taken -- can be a live ratio: both sides re-sum from row grain under
        # any filter, and the division happens client-side after it.
        is_src = (df["kind"] == "source").to_numpy()
        df["src_amount"] = np.where(is_src, df["amount"], 0)
        df["sink_amount"] = np.where(~is_src, df["amount"], 0)
        df["src_coins"] = np.where(df["currency"] == "coins", df["src_amount"], 0)
        df["sink_coins"] = np.where(df["currency"] == "coins", df["sink_amount"], 0)
        df["src_gems"] = np.where(df["currency"] == "gems", df["src_amount"], 0)
        df["sink_gems"] = np.where(df["currency"] == "gems", df["sink_amount"], 0)

        # Derived, never stated: this report builds against whatever history
        # the project generated -- the full ledger (2.01M rows over 1,554 days)
        # on the published gallery, a slice of it in a default project
        # install -- and its claims must be true in both.
        n_rows = len(df)
        n_days = df["event_date"].nunique()
        ctx.set_header(
            subtitle=f"{n_rows:,} rows of sources and sinks over {n_days} "
                     f"days, live under the filters -- newest months inline, "
                     f"older ones streamed in chunks",
        )

        ctx.add_section("", [
            # chunk_by="month": the three newest months are embedded in
            # data.json, the rest are separate files fetched lazily. The
            # report is usable while history is still arriving.
            DataSource("econ", df, chunk_by="month", default_chunks=3),
            FilterBar("econ", df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "title", "label": "Title"},
                {"column": "platform", "label": "Platform", "type": "toggle"},
                {"column": "spender_tier", "label": "Spender Tier"},
                {"column": "kind", "label": "Flow", "type": "toggle"},
            ]),
        ])

        ctx.add_section("The Point", [
            Grid([
                # An int, not a pre-formatted string: MiniKpi's client
                # formatter owns the thousands separators, and hands a
                # non-numeric value back as "-".
                MiniKpi("Warehouse rows", n_rows),
                MiniKpi("Features tracked", int(df["feature"].nunique())),
                MiniKpi("Currencies", int(df["currency"].nunique())),
                MiniKpi("Days of history", int(df["event_date"].nunique())),
            ], columns=4),
            KpiRow([
                {"label": "Gross Volume", "agg": "sum", "column": "amount",
                 "format": "number"},
                {"label": "Net Flow", "agg": "sum", "column": "net",
                 "format": "number"},
                {"label": "Transactions", "agg": "sum", "column": "transactions",
                 "format": "number"},
            ], dataset_id="econ"),
            # The speed, measured rather than claimed. No framework component
            # can time the client's own filter pass (MiniKpi/KpiRow show
            # values, not measurements), so this is the one honestly-novel
            # strip: on every filter commit it takes the engine's filtered
            # rows and times one full pass over them in the visitor's own
            # browser.
            RawHTML(
                html=(
                    '<style>.econ-pulse { padding: 6px 2px; '
                    'font-size: var(--font-size-small); '
                    'color: var(--text-secondary); } '
                    '.econ-pulse b { color: var(--text-main); }</style>'
                    '<div class="econ-pulse" id="econ-pulse" '
                    'aria-live="polite">Measuring the first filter '
                    'pass&hellip;</div>'
                ),
                js="""
(function () {
  var el = document.getElementById('econ-pulse');
  function update() {
    var t0 = performance.now();
    var rows = fw.filterEngine.getFiltered('econ');
    var volume = 0;
    for (var i = 0; i < rows.length; i++) volume += rows[i].amount || 0;
    var ms = performance.now() - t0;
    if (!volume) return;  // keep the placeholder until data is real
    el.innerHTML = '\\u26a1 <b>' + rows.length.toLocaleString() +
      ' rows</b> in view \\u00b7 full pass over them in <b>' +
      (ms < 1 ? ms.toFixed(2) : ms.toFixed(1)) +
      ' ms</b> \\u2014 measured in your browser just now, and again on ' +
      'every filter change';
  }
  fw.filterEngine.onReady('econ', function () {
    fw.filterEngine.subscribe('econ', 'econ-pulse', update);
    update();
  });
})();
""",
            ),
        ])

        # ── One economy at a time ────────────────────────────────────
        # Coins and gems are different orders of magnitude; on one axis the
        # gem story disappears. Toggle + Visible swaps the whole view, and
        # both charts still read from the same shared DataSource.
        # Diverging stacks, per the owner's ask: every feature's daily net,
        # sources stacking up from zero and sinks stacking down, so a day's
        # bar IS the economy's ledger for that day. The signed per-currency
        # columns make this a plain stacked sum -- sources are positive, sinks
        # negative, by construction. max_stacks keeps the legend honest by
        # bundling the long tail; the tooltip breakdown still names them all.
        ctx.add_section("Net Flow", [
            Toggle("currency_view", options=["Coins", "Gems"], default="Coins"),
            Visible([
                StackedBar(df, x="event_date", y_cols=["net_coins"],
                           stack_by="feature", diverging=True,
                           stack_sort="volume_desc", max_stacks=14,
                           title="Daily Coin Flow by Feature · demo: diverging "
                                 "stacks -- sources up, sinks down, the forge "
                                 "rebalance visible as the bottom shrinking",
                           value_format="number", dataset_id="econ"),
            ], toggle_target="currency_view", toggle_value="Coins"),
            Visible([
                StackedBar(df, x="event_date", y_cols=["net_gems"],
                           stack_by="feature", diverging=True,
                           stack_sort="volume_desc", max_stacks=12,
                           title="Daily Gem Flow by Feature · demo: the 14-day "
                                 "gacha banner cadence -- gem sinks pulse "
                                 "downward every other week",
                           value_format="number", dataset_id="econ"),
            ], toggle_target="currency_view", toggle_value="Gems"),
        ])

        # ── Every feature on one axis ────────────────────────────────
        ctx.add_section("Sources Up, Sinks Down", [
            # net_color renders its own vertical +/- layout; horizontal and
            # sort are silently ignored on that path, so they are not passed
            # and the title does not promise a ranking.
            BarChart(df, x="feature", y="net",
                     title="Net by Feature · demo: net_color splits earn from "
                           "burn across all 36 features at once",
                     net_color=True,
                     y_format="number", dataset_id="econ"),
        ])

        # ── Is the economy balanced, and where is it heading ─────────
        # RTP (return to player): sources paid out per sink taken in. 100% is
        # a balanced loop; the coin line steps ABOVE 100% on the forge
        # rebalance date and never comes back, which is the whole incident in
        # one number. Live ratios -- both sides re-sum under the filters.
        ctx.add_section("Is the Economy Growing", [
            LineChart(df, x="event_date",
                      ratios=[
                          {"numerator": "src_amount",
                           "denominator": "sink_amount", "label": "Total RTP"},
                          {"numerator": "src_coins",
                           "denominator": "sink_coins", "label": "Coins RTP"},
                          {"numerator": "src_gems",
                           "denominator": "sink_gems", "label": "Gems RTP"},
                      ],
                      title="Economy RTP by Day · demo: three live ratios on "
                            "one axis -- 100% is balanced, the coin step is "
                            "the forge rebalance",
                      y_format="percent", dataset_id="econ"),
            # The running total answers "so how much has accumulated": a
            # cumulative sum cannot re-aggregate under filters (a sum of
            # partial cumsums is not a cumsum), so this one is deliberately
            # static over the whole window -- which is also the honest frame
            # for "is it growing or declining".
            LineChart(self._cumulative(df), x="event_date",
                      y=["cum_coins", "cum_gems"],
                      y_labels=["Coins in circulation (net)",
                                "Gems in circulation (net)"],
                      title="Cumulative Net Flow · demo: static by design -- "
                            "a running sum cannot re-aggregate under filters, "
                            "so it does not pretend to",
                      y_format="number", static=True),
        ])

        ctx.add_section("Every Feature, Every Month", [
            # normalize="row", because the features differ by three orders of
            # magnitude: on one global scale the biggest row owns the palette
            # and every row reads flat. Row-relative colour is what makes the
            # forge nerf (a cold streak mid-row) and the December peak visible
            # in every feature at once. Tooltips keep the raw values.
            HeatmapChart(df, x="month", y="feature", value="amount",
                         title="Flow by Feature and Month · demo: "
                               "row-normalised heatmap -- each feature against "
                               "its own range, so the forge nerf shows as a "
                               "cold streak",
                         normalize="row", value_format="number",
                         dataset_id="econ"),
        ])

    @staticmethod
    def _cumulative(df):
        """Daily net per currency, cumulatively summed over the window."""
        daily = (df.groupby("event_date", as_index=False)
                   [["net_coins", "net_gems"]].sum()
                   .sort_values("event_date"))
        daily["cum_coins"] = daily["net_coins"].cumsum()
        daily["cum_gems"] = daily["net_gems"].cumsum()
        return daily[["event_date", "cum_coins", "cum_gems"]]
