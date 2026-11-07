"""Store-to-purchase observations by title, date and device.

The title switcher keeps funnel and device detail together. A native Sankey
reconciles adjacent aggregate stage counts using the same date/device filters.
These illustrative links do not establish observed individual transitions.
"""

from datetime import timedelta

import pandas as pd

from trellum import BaseReport
from trellum.components import (
    AreaChart,
    BarChart,
    DataSource,
    FilterBar,
    FunnelChart,
    KpiRow,
    SankeyChart,
    ScopedDataSource,
)
from trellum.data import query_df

from . import queries

_LOOKBACK_DAYS = 120
_DEFAULT_FILTER_DAYS = 30
_FUNNEL_STAGES = ("store_view", "item_view", "add_to_cart", "checkout", "purchase")

# Every scope must emit the same sections and components in the same order --
# only the default scope renders HTML, and the others supply data for it.
_SCOPES = [
    ("coral_quest", "Coral Quest"),
    ("iron_vanguard", "Iron Vanguard"),
    ("neon_racer", "Neon Racer"),
]


class ConversionReport(BaseReport):

    def generate(self, ctx):
        conn = ctx.get_connection("demo_db")
        start = (ctx._now_utc - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        df = query_df(conn, queries.FUNNEL, params={
            "start_date": start,
            "end_date": ctx.today,
        })

        ctx.set_header(subtitle="Store funnel by device — switch title above")

        for scope_key, title in _SCOPES:
            ctx.set_scope(scope_key, label=title)
            self._build_scope(ctx, scope_key, df[df["title"] == title].copy())

    def _build_scope(self, ctx, scope_key: str, df) -> None:
        # Dataset ids are global across the whole report, not per scope, so
        # each scope needs its own -- reusing one id makes later scopes
        # overwrite earlier ones in data.json.
        ds = f"funnel_{scope_key}"
        ds_device = f"device_{scope_key}"
        ds_flow = f"flow_{scope_key}"
        flow, stages = self._build_flow(df)

        ctx.add_section("", [
            DataSource(ds, df),
            DataSource(ds_flow, flow),
            FilterBar(ds, df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "device", "label": "Device"},
            ], propagate_to={ds_flow: {"event_date": "event_date", "device": "device"}}),
        ])

        ctx.add_section("Conversion", [
            KpiRow([
                {"label": "Store Views", "agg": "sum", "column": "users",
                 "format": "number"},
                {"label": "Steps Tracked", "agg": "count", "format": "number"},
            ], dataset_id=ds),
            FunnelChart(df, label="step", value="users",
                        title="Store to Purchase · demo: one funnel per "
                              "title via the scope switcher above",
                        show_percentages=True, dataset_id=ds),
        ])

        ctx.add_section("Volume Over Time", [
            # NOT stacked: stacking is only meaningful with several series, and
            # a "stacked" area of one column renders as a solid opaque block
            # (charts.py uses the flat colour when stacked, 20% alpha when not).
            AreaChart(df, x="event_date", y="users",
                      title="Funnel Volume by Day",
                      y_format="number", dataset_id=ds),
        ])

        # Adjacent stage observations retain their date/device grain. The
        # native edges diagram follows the same filters as the funnel; it
        # makes no claim to have reconstructed individual complete paths.
        ctx.add_section("Where They Go", [
            SankeyChart(
                flow, "Stage-count reconciliation", mode="edges",
                stages=stages, weight="users", unit="stage users",
                source="from_node", target="to_node",
                source_stage="from_stage", target_stage="to_stage",
                dataset_id=ds_flow, height=320,
                header_context=("Illustrative links derived from aggregate stage counts, "
                                "not observed transitions. Net differences are reconciled "
                                "per date and device."),
            ),
        ])

        # ── Device drill-down on a scoped view of the same data ──────
        # ScopedDataSource inherits the parent's filters without duplicating
        # the payload; its own FilterBar composes on top (parent AND child).
        ctx.add_section("By Device", [
            ScopedDataSource(ds_device, ds),
            FilterBar(ds_device, df, filters=[
                {"column": "step", "label": "Step"},
            ]),
            BarChart(df, x="device", y="users",
                     title="Users by Device · demo: ScopedDataSource -- this "
                           "section's Step filter stays local, the main "
                           "filters still apply",
                     y_format="number", cross_filter=True,
                     dataset_id=ds_device),
        ])

    @staticmethod
    def _build_flow(df):
        """Derive adjacent edges at the supplied date/title/device grain.

        Independently sampled stage counts may increase. In that case an
        explicit additional-arrivals node reconciles the next stage rather
        than introducing a negative exit or silently changing the funnel.
        """
        # The warehouse declares this stage order. An absent stage stays
        # absent rather than making its neighbours appear adjacent.
        if not set(df["step"].dropna()) <= set(_FUNNEL_STAGES):
            raise ValueError("Unexpected fact_funnel stage")
        stages = [{"key": step, "label": step.replace("_", " ").title()}
                  for step in _FUNNEL_STAGES]
        columns = ["event_date", "title", "device", "from_stage", "to_stage",
                   "from_node", "to_node", "users"]
        rows = []
        for keys, group in df.groupby(["event_date", "title", "device"], observed=True):
            counts = group.groupby("step", observed=True)["users"].sum()
            for current, nxt in zip(stages, stages[1:]):
                # Missing stage observations are not invented zeroes.
                if current["key"] not in counts or nxt["key"] not in counts:
                    continue
                before, after = int(counts[current["key"]]), int(counts[nxt["key"]])
                base = dict(zip(("event_date", "title", "device"), keys))
                base.update(from_stage=current["key"], to_stage=nxt["key"])
                for source, target, weight in [
                    (current["label"], nxt["label"], min(before, after)),
                    (current["label"], "Net exits", max(before - after, 0)),
                    ("Additional arrivals", nxt["label"], max(after - before, 0)),
                ]:
                    if weight:
                        rows.append({**base, "from_node": source, "to_node": target, "users": weight})
        return pd.DataFrame(rows, columns=columns), stages
