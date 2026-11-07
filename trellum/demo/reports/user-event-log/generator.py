"""Live Ops Monitor -- the live-queries teaching report.

Demonstrates the redesigned live-query architecture end to end: a live
query is a filter-engine DATASET, not a bespoke control. One declared query
("ops_events") backs a LiveDataSource, an ordinary FilterBar binds to it
(severity/service/actor/date-range, each naming the param it drives), and
four ordinary components -- a KPI row, two charts, and a table -- read from
it via dataset_id like they would from any other dataset. Changing any
filter re-queries once and every one of those four components updates
together. A second declared query ("top_actors") answers a grouped top-N
the raw rows can't cheaply answer client-side; its own LiveDataSource has
no FilterBar of its own -- it shares the primary FilterBar's service +
date-range filters via propagate_to, so one filter change can drive two
queries.

Standalone -- opened from disk, a share link, an email snapshot -- the
FilterBar renders disabled with an honest label and the snapshot rows
stand. Served by a host that advertises ``live_query_url`` (and, for a
logged-in viewer, isn't an anonymous share link), the filters wake up:
dropdowns/toggles/date presets auto-query on change, the actor id box
commits on Enter or blur.
"""

from datetime import timedelta

from trellum import BaseReport
from trellum.components import (
    BarChart,
    DataTable,
    FilterBar,
    KpiRow,
    LineChart,
    LiveDataSource,
    RawHTML,
)
from trellum.data import query_df

from . import queries

#: Example actor id, forced to a paying tier in the synthetic fixtures.
#: The build-time snapshot uses actor_id=0 (all actors), not this example.
SNAPSHOT_USER = 1042

_WINDOW_DAYS = 60
_VOLUME_DAYS = 60

_INTRO = """
<div style="font-size:13px;color:var(--text-secondary);line-height:1.6;
            max-width:72ch;">
<p>This standalone demo shows a <b>build-time snapshot</b> of synthetic events.
On a host with live queries enabled, severity, service, actor and date filters
request fresh results for the KPIs, charts and raw-events table.</p>
<p><b>Event scope:</b> these results contain up to the <b>300 most recent
matching events</b>. The Events, Warnings and Errors KPIs and the two charts
in Ops stream describe those returned rows, not the entire matching log.</p>
<p><b>Top actors</b> ranks the 15 most active actors across all events matching
the service and date range. Actor and severity selections do not narrow that
ranking. The final daily-volume chart is a separate build-time aggregate.</p>
</div>
"""


class UserEventLogReport(BaseReport):

    def generate(self, ctx):
        until = ctx._now_utc.strftime("%Y-%m-%d")
        since = (ctx._now_utc - timedelta(days=_WINDOW_DAYS)).strftime("%Y-%m-%d")

        events = ctx.declare_live_query(
            "ops_events",
            queries.OPS_EVENTS,
            datasource="demo_db",
            params=[
                {"name": "severity", "type": "enum", "required": True,
                 "values": ["all", "info", "warn", "error"]},
                {"name": "service", "type": "enum", "required": True,
                 "values": ["all", "Android", "iOS", "Web"]},
                {"name": "actor_id", "type": "int", "required": True},
                {"name": "since", "type": "date", "required": True},
                {"name": "until", "type": "date", "required": True},
            ],
            snapshot_params={
                "severity": "all", "service": "all", "actor_id": 0,
                "since": since, "until": until,
            },
        )

        actors = ctx.declare_live_query(
            "top_actors",
            queries.TOP_ACTORS,
            datasource="demo_db",
            params=[
                {"name": "service", "type": "enum", "required": True,
                 "values": ["all", "Android", "iOS", "Web"]},
                {"name": "since", "type": "date", "required": True},
                {"name": "until", "type": "date", "required": True},
            ],
            snapshot_params={"service": "all", "since": since, "until": until},
        )

        # Context the artifact can afford to compile: total volume by day.
        conn = ctx.get_connection("demo_db")
        vol_start = (ctx._now_utc - timedelta(days=_VOLUME_DAYS)).strftime("%Y-%m-%d")
        volume = query_df(conn, queries.DAILY_VOLUME, params={"start_date": vol_start})

        ctx.set_header(
            subtitle="Severity / service / actor / date -- one live query, four components",
            meta={"Last event": events["ts"].max() if not events.empty else None},
        )

        ctx.add_section("What this page is", [RawHTML(html=_INTRO)])

        ctx.add_section("", [
            LiveDataSource("ops", query="ops_events", df=events),
            LiveDataSource("actors", query="top_actors", df=actors, bindings=[
                {"column": "service", "param": "service", "filter_type": "dropdown",
                 "sentinel": "all"},
                {"column": "day", "min_param": "since", "max_param": "until",
                 "filter_type": "date_range"},
            ]),
            FilterBar("ops", events, filters=[
                {"type": "text", "column": "actor", "param": "actor_id",
                 "label": "Actor (user id)", "placeholder": f"e.g. {SNAPSHOT_USER}",
                 "sentinel": 0},
                {"type": "toggle", "column": "severity", "param": "severity",
                 "label": "Severity"},
                {"type": "dropdown", "column": "service", "param": "service",
                 "label": "Service", "multi": False},
                {"type": "date_range", "column": "day",
                 "min_param": "since", "max_param": "until", "label": "Date range"},
            ], propagate_to={"actors": {"service": "service", "day": "day"}}),
        ])

        ctx.add_section("Ops stream", [
            KpiRow(dataset_id="ops", kpis=[
                {"label": "Events", "format": "number", "agg": "count"},
                {"label": "Warnings", "format": "number", "agg": "sum", "column": "is_warn"},
                {"label": "Errors", "format": "number", "agg": "sum", "column": "is_error"},
            ]),
            BarChart(events, x="service", y="n", dataset_id="ops",
                     title="Events by service"),
            LineChart(events, x="day", y="n", dataset_id="ops",
                      title="Events per day"),
            DataTable(events, title="Raw events", dataset_id="ops",
                      columns=["ts", "actor", "service", "severity", "message"],
                      sortable=True, max_rows=100, searchable=True),
        ])

        ctx.add_section("Top actors — a second query sharing filters", [
            BarChart(actors, x="actor", y="events", dataset_id="actors",
                     horizontal=True, sort="desc",
                     title="Most active actors (service + date range shared with above)"),
        ])

        ctx.add_section("The whole log, for scale", [
            LineChart(
                volume, x="event_date", y=["events"],
                y_labels=["Events"],
                static=True,  # a compiled-in aggregate, deliberately not live
                title=f"Events per day, all users · last {_VOLUME_DAYS} days "
                      "· demo: this aggregate is compiled in — the raw rows "
                      "behind it are what the live filters above fetch on "
                      "demand",
            ),
        ])
