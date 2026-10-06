"""Conversion -- the store-to-purchase story, per title and in aggregate.

Merged from two reports that queried the same table (acquisition-funnel and
purchase-flow), because the demo shows features and one page can carry both:
the multi-scope switcher gives each title its own funnel, and the Sankey
states the portfolio-wide flow -- including where the leavers go, which a
funnel chart cannot draw. Signature charts say what they demonstrate in
their titles, because this is a demo.
"""

from datetime import timedelta

from trellum import BaseReport
from trellum.components import (
    AreaChart,
    BarChart,
    DataSource,
    FilterBar,
    FunnelChart,
    KpiRow,
    RawHTML,
    ScopedDataSource,
)
from trellum.data import query_df

from . import custom_sections, queries

_LOOKBACK_DAYS = 120
_DEFAULT_FILTER_DAYS = 30

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

        ctx.add_section("", [
            DataSource(ds, df),
            FilterBar(ds, df, filters=[
                {"column": "event_date", "label": "Date Range",
                 "type": "date_range", "default_days": _DEFAULT_FILTER_DAYS},
                {"column": "device", "label": "Device"},
            ]),
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

        # ── Where they go ────────────────────────────────────────────
        # The flow for THIS title: each scope ships its own payload under
        # "_flow_<scope>", and the one shared JS reads whichever scope is
        # active -- so the diagram follows the title switcher. A sankey is
        # RawHTML because a link between stages is a relationship no chart
        # component expresses; static per scope because the link geometry is
        # a difference between stages, computed once in pandas.
        ctx.add_section("Where They Go", [
            RawHTML(html=custom_sections.CUSTOM_HTML,
                    js=custom_sections.CUSTOM_JS,
                    data_key=f"_flow_{scope_key}",
                    data=self._build_flow(df)),
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
    def _build_flow(df) -> dict:
        """Turn stage totals into Sankey links, including the drop-off.

        Aggregation happens here in pandas rather than in SQL, per the
        framework rule -- and it has to, because a Sankey link is a
        *difference* between consecutive stages, which is awkward in the query
        and impossible to re-derive client-side from pre-grouped rows.
        """
        if df.empty:
            return {"links": [], "labels": {}}

        stages = (df.groupby(["step_order", "step"], as_index=False)["users"]
                    .sum()
                    .sort_values("step_order"))
        by_device = (df[df["step_order"] == stages["step_order"].min()]
                     .groupby("device", as_index=False)["users"].sum()
                     .sort_values("users", ascending=False))

        first_step = stages.iloc[0]["step"]
        links = [{"from": row["device"], "to": first_step,
                  "flow": int(row["users"])}
                 for _, row in by_device.iterrows() if row["users"] > 0]

        rows = list(stages.itertuples(index=False))
        for current, nxt in zip(rows, rows[1:]):
            carried = int(nxt.users)
            lost = int(current.users) - carried
            if carried > 0:
                links.append({"from": current.step, "to": nxt.step,
                              "flow": carried})
            if lost > 0:
                links.append({"from": current.step,
                              "to": f"left after {current.step}",
                              "flow": lost})

        # Chart.js labels nodes by key; give the drop-off nodes something
        # readable rather than the raw key.
        labels = {f"left after {r.step}": "left" for r in rows}
        labels.update({r.step: r.step.replace("_", " ") for r in rows})

        # Pin the columns. Left to its own devices the plugin puts every node
        # with no outgoing links in the final column, so all four drop-offs
        # stack on the right and their links become long bands crossing the
        # whole diagram. Placing each drop-off in the column of the stage it
        # failed to reach puts the loss next to its cause, which is the whole
        # point of drawing this as a flow.
        columns = {}
        for i, r in enumerate(rows):
            columns[r.step] = i + 1                    # devices occupy column 0
            columns[f"left after {r.step}"] = i + 2    # beside the next stage
        return {"links": links, "labels": labels, "columns": columns}
