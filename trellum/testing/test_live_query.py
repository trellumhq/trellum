"""Live queries: declaration, artifacts, validation, and the redesigned
FilterBar-driven live control.

The invariants under test are the contract in docs/COMPATIBILITY.md, plus
the live-query filter redesign's own contract:

* ``_live_queries.json`` (schema v1) is the ONLY artifact carrying the SQL —
  data.json and index.html never do.
* A live query is a filter-engine DATASET: the snapshot lands in
  ``data.json`` as an ordinary ``_ds_{id}`` entry, and the ``live_data_source``
  component entry carries the declared param schema + bindings — never SQL.
* Standalone output degrades honestly: a live FilterBar renders disabled
  with the honest note, and the runtime only ever *enables* it — when the
  page has a host advertising ``live_query_url`` AND this isn't an
  anonymous share-link view (``window._fwShareLink``).
* Commits are auto-query for dropdown/toggle/date preset, transient-only
  (never queries) for a dragging slider, and explicit (Enter/blur) for text.
* The old ``DataTable(live=...)`` form is a clean break: it raises at
  render time and FAILs validation.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from trellum.components.filterable import FilterBar, LiveDataSource
from trellum.components.kpis import KpiRow
from trellum.components.tables import DataTable
from trellum.data.query import disable_cache, enable_cache
from trellum.rendering.html_builder import render_report
from trellum.rendering.js_runtime import RUNTIME_MODULES, host_flag_scripts
from trellum.report import ReportContext
from trellum.validation import validate_report

_EVENTS_SQL = """
SELECT ts, event, tier, amount, user_id FROM events
WHERE user_id = :user_id
  AND (:event_type = 'all' OR event = :event_type)
  AND (:tier = 'all' OR tier = :tier)
  AND amount >= :min_amount AND amount <= :max_amount
  AND ts >= :since AND ts <= :until
ORDER BY ts DESC
"""

_TOTALS_SQL = """
SELECT tier, COUNT(*) AS n FROM events
WHERE (:tier = 'all' OR tier = :tier)
GROUP BY tier
"""

_EVENTS_PARAMS = [
    {"name": "user_id", "type": "int", "required": True},
    {"name": "event_type", "type": "enum", "required": True,
     "values": ["all", "session_start", "purchase"]},
    {"name": "tier", "type": "enum", "required": True,
     "values": ["all", "free", "pro"]},
    {"name": "min_amount", "type": "float", "required": True},
    {"name": "max_amount", "type": "float", "required": True},
    {"name": "since", "type": "date", "required": True},
    {"name": "until", "type": "date", "required": True},
]
#: These MUST equal what slider_filter.py/date_range.py independently
#: compute as their own defaults from the snapshot DataFrame (user 7's 3
#: rows: amount in {0.0, 4.99, 19.99}, ts in {01-03, 01-10, 01-20}) --
#: range-mode slider and date_range BOTH commit unconditionally on init()
#: (unlike dropdown/toggle, which only act on a URL restore), so a mismatch
#: here fires a spurious query on every clean page load and desyncs every
#: browser test's request counts.
_EVENTS_DEFAULTS = {
    "user_id": 7, "event_type": "all", "tier": "all",
    "min_amount": 0.0, "max_amount": 19.99,
    "since": "2026-01-03", "until": "2026-01-20",
}
_TOTALS_PARAMS = [
    {"name": "tier", "type": "enum", "required": True,
     "values": ["all", "free", "pro"]},
]

_STANDALONE_NOTE = (
    "Live lookup — available when served by a host; "
    "showing data from the last build"
)
_SHARE_NOTE = "Live lookup isn't available on shared reports"


def _events_db(dirpath: str) -> str:
    """A tiny real warehouse: an events table for two users."""
    path = os.path.join(dirpath, "events.sqlite")
    if os.path.exists(path):
        return path
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events (user_id INT, ts TEXT, event TEXT, tier TEXT, amount REAL)"
    )
    rows = [
        (7, "2026-01-03", "session_start", "free", 0.0),
        (7, "2026-01-10", "purchase", "free", 4.99),
        (7, "2026-01-20", "purchase", "pro", 19.99),
        (8, "2026-01-04", "session_start", "pro", 0.0),
    ]
    conn.executemany("INSERT INTO events VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def _make_ctx(tmpdir: str, *, slug: str = "lq-test") -> ReportContext:
    db_path = _events_db(tmpdir)
    config = {
        "name": "Live Query Test",
        "description": "A report exercising declared live queries end to end.",
        "data_sources": [
            {"name": "db", "type": "sqlite", "path": db_path},
            {"name": "csvfile", "type": "file", "path": "x.csv"},
        ],
    }
    out = os.path.join(tmpdir, "out")
    return ReportContext(config=config, slug=slug, output_dir=out)


@pytest.fixture(autouse=True)
def _no_query_cache():
    """Snapshot executions must not leave cache files in the demo project."""
    disable_cache()
    yield
    enable_cache()


# ══════════════════════════════════════════════════════════════
# Declaration
# ══════════════════════════════════════════════════════════════

class TestDeclare:
    def test_bad_id_slug_raises(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        with pytest.raises(ValueError, match="slug"):
            ctx.declare_live_query("User Events!", _EVENTS_SQL,
                                   datasource="db", params=_EVENTS_PARAMS)

    def test_duplicate_id_raises(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS)
        with pytest.raises(ValueError, match="twice"):
            ctx.declare_live_query("q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS)

    def test_unknown_snapshot_key_raises(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        with pytest.raises(ValueError, match="not declared"):
            ctx.declare_live_query(
                "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
                snapshot_params={**_EVENTS_DEFAULTS, "surprise": 1})

    def test_missing_required_snapshot_param_raises(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        with pytest.raises(ValueError, match="required"):
            ctx.declare_live_query(
                "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
                snapshot_params={})

    def test_snapshot_executes_through_the_data_layer(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        df = ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
            snapshot_params=_EVENTS_DEFAULTS)
        ctx.close_connections()
        assert list(df.columns) == ["ts", "event", "tier", "amount", "user_id"]
        assert len(df) == 3          # user 7's rows, not user 8's
        assert (df["user_id"] == 7).all()

    def test_no_snapshot_returns_none(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        assert ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS) is None


# ══════════════════════════════════════════════════════════════
# Artifacts
# ══════════════════════════════════════════════════════════════

def _build(tmpdir: str, *, with_totals: bool = False) -> ReportContext:
    """Declare + snapshot + a live FilterBar over one dataset (optionally
    two, sharing a filter via propagate_to), rendered to ctx.output_dir."""
    ctx = _make_ctx(tmpdir)
    events = ctx.declare_live_query(
        "user_events", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
        snapshot_params=_EVENTS_DEFAULTS)

    components = [
        LiveDataSource("events", query="user_events", df=events),
    ]
    propagate_to = None
    if with_totals:
        totals = ctx.declare_live_query(
            "tier_totals", _TOTALS_SQL, datasource="db", params=_TOTALS_PARAMS,
            snapshot_params={"tier": "all"})
        components.append(LiveDataSource("totals", query="tier_totals", df=totals, bindings=[
            {"column": "tier", "param": "tier", "filter_type": "dropdown", "sentinel": "all"},
        ]))
        propagate_to = {"totals": {"tier": "tier"}}

    components.append(FilterBar("events", events, filters=[
        {"type": "text", "column": "user_id", "param": "user_id", "label": "User ID"},
        {"type": "toggle", "column": "event", "param": "event_type", "label": "Event"},
        {"type": "dropdown", "column": "tier", "param": "tier", "label": "Tier", "multi": False},
        {"type": "slider", "column": "amount", "min_param": "min_amount",
         "max_param": "max_amount", "label": "Amount"},
        {"type": "date_range", "column": "ts", "min_param": "since",
         "max_param": "until", "label": "Date"},
    ], propagate_to=propagate_to))

    components.append(DataTable(events, title="Events", dataset_id="events",
                                sortable=True))
    components.append(KpiRow(dataset_id="events", kpis=[
        {"label": "Rows", "format": "number", "agg": "count"},
    ]))
    if with_totals:
        from trellum.components.charts import BarChart
        components.append(BarChart(totals, x="tier", y="n", dataset_id="totals",
                                   title="By tier"))

    ctx.add_section("Events", components)
    render_report(ctx, ctx.output_dir, auto_refresh=False)
    ctx.close_connections()
    return ctx


class TestArtifacts:
    def test_manifest_schema(self, tmp_path):
        ctx = _build(str(tmp_path))
        with open(os.path.join(ctx.output_dir, "_live_queries.json"),
                  encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest == {
            "version": 1,
            "queries": {"user_events": {
                "sql": _EVENTS_SQL,
                "datasource": "db",
                "params": _EVENTS_PARAMS,
            }},
        }

    def test_sql_never_reaches_client_artifacts(self, tmp_path):
        ctx = _build(str(tmp_path))
        for fname in ("data.json", "index.html"):
            with open(os.path.join(ctx.output_dir, fname),
                      encoding="utf-8") as f:
                text = f.read()
            assert _EVENTS_SQL not in text, f"query text leaked into {fname}"
            assert "FROM events" not in text, f"SQL fragment leaked into {fname}"

    def test_live_data_source_entry_carries_schema_and_bindings_not_sql(self, tmp_path):
        ctx = _build(str(tmp_path))
        with open(os.path.join(ctx.output_dir, "data.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        entries = [c for c in data["components"].values()
                   if c.get("type") == "live_data_source"]
        assert len(entries) == 1
        live = entries[0]["live"]
        assert live["query_id"] == "user_events"
        assert "sql" not in live
        names = {p["name"] for p in live["params"]}
        assert names == {"user_id", "event_type", "tier", "min_amount",
                         "max_amount", "since", "until"}
        assert live["defaults"] == _EVENTS_DEFAULTS
        bound_params = {b.get("param") or (b.get("min_param"), b.get("max_param"))
                        for b in live["bindings"]}
        assert bound_params == {
            "user_id", "event_type", "tier",
            ("min_amount", "max_amount"), ("since", "until"),
        }

    def test_snapshot_rows_land_in_data_source_entry(self, tmp_path):
        ctx = _build(str(tmp_path))
        with open(os.path.join(ctx.output_dir, "data.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        assert data["_ds_events"]["_data"], "snapshot rows missing from _ds_events"
        assert len(data["_ds_events"]["_data"]) == 3

    def test_filter_bar_renders_disabled_with_honest_note_standalone(self, tmp_path):
        ctx = _build(str(tmp_path))
        with open(os.path.join(ctx.output_dir, "index.html"),
                  encoding="utf-8") as f:
            html = f.read()
        assert _STANDALONE_NOTE in html
        assert 'data-live-state="standalone"' in html
        assert "window._fwHasHost=false" in html
        # Belt-and-suspenders disabling baked at build time.
        assert "disabled" in html

    def test_propagated_second_dataset_gets_its_own_entry(self, tmp_path):
        ctx = _build(str(tmp_path), with_totals=True)
        with open(os.path.join(ctx.output_dir, "data.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        entries = {c["dataset_id"]: c for c in data["components"].values()
                  if c.get("type") == "live_data_source"}
        assert set(entries) == {"events", "totals"}
        assert entries["totals"]["live"]["query_id"] == "tier_totals"
        assert entries["totals"]["live"]["bindings"] == [
            {"column": "tier", "param": "tier", "filter_type": "dropdown", "sentinel": "all"},
        ]

    def test_stale_manifest_is_removed(self, tmp_path):
        ctx = _build(str(tmp_path))
        manifest = os.path.join(ctx.output_dir, "_live_queries.json")
        assert os.path.exists(manifest)
        # Rebuild the same slug without any declaration: the file must go.
        ctx2 = _make_ctx(str(tmp_path))
        ctx2.add_section("Plain", [DataTable(
            __import__("pandas").DataFrame({"a": [1]}), title="t")])
        render_report(ctx2, ctx.output_dir, auto_refresh=False)
        assert not os.path.exists(manifest)


# ══════════════════════════════════════════════════════════════
# Clean break: the removed M1 DataTable(live=...) form
# ══════════════════════════════════════════════════════════════

class TestLegacyApiCleanBreak:
    def test_datatable_live_raises_at_render(self, tmp_path):
        import pandas as pd

        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="db",
                               params=_EVENTS_PARAMS, snapshot_params=_EVENTS_DEFAULTS)
        ctx.add_section("S", [DataTable(
            pd.DataFrame({"a": [1]}), title="t",
            live={"query": "q", "param": "user_id"})])
        with pytest.raises(ValueError, match="LiveDataSource"):
            render_report(ctx, ctx.output_dir, auto_refresh=False)

    def test_datatable_live_fails_validation(self, tmp_path):
        import pandas as pd

        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="db",
                               params=_EVENTS_PARAMS, snapshot_params=_EVENTS_DEFAULTS)
        ctx.add_section("S", [DataTable(
            pd.DataFrame({"a": [1]}), title="t",
            live={"query": "q", "param": "user_id"})])
        result = validate_report(ctx)
        ids = [c.id for c in result.checks if c.level == "fail"]
        assert "live-query-legacy-config" in ids


# ══════════════════════════════════════════════════════════════
# Validation checks
# ══════════════════════════════════════════════════════════════

def _check_ids(ctx, level=None):
    result = validate_report(ctx)
    return [c.id for c in result.checks
            if c.id.startswith("live-query") and (level is None or c.level == level)]


class TestValidation:
    def test_unknown_query_id_fails(self, tmp_path):
        import pandas as pd
        ctx = _make_ctx(str(tmp_path))
        ctx.add_section("S", [LiveDataSource(
            "ds", query="never_declared", df=pd.DataFrame({"a": [1]}))])
        assert "live-query-unknown" in _check_ids(ctx, level="fail")

    def test_non_sql_source_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="csvfile",
                               params=_EVENTS_PARAMS)
        assert "live-query-source-not-sql" in _check_ids(ctx, level="fail")

    def test_missing_source_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="nowhere",
                               params=_EVENTS_PARAMS)
        assert "live-query-source-not-sql" in _check_ids(ctx, level="fail")

    def test_bad_param_type_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="db",
                               params=[{"name": "user_id", "type": "uuid"}])
        assert "live-query-param-schema" in _check_ids(ctx, level="fail")

    def test_enum_without_values_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="db",
                               params=[{"name": "tier", "type": "enum"}])
        assert "live-query-param-schema" in _check_ids(ctx, level="fail")

    def test_optional_param_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db",
            params=[{"name": "user_id", "type": "int", "required": False}])
        assert "live-query-param-schema" in _check_ids(ctx, level="fail")

    def test_undeclared_placeholder_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query(
            "q", "SELECT * FROM events WHERE account_id = :account_id",
            datasource="db", params=_EVENTS_PARAMS)
        assert "live-query-sql-params" in _check_ids(ctx, level="fail")

    def test_unused_declared_param_warns(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query(
            "q", "SELECT * FROM events", datasource="db",
            params=[{"name": "user_id", "type": "int", "required": True}])
        assert "live-query-sql-params" in _check_ids(ctx, level="warn")

    def test_no_snapshot_fails(self, tmp_path):
        # Escalated from WARN (the old per-table control) to FAIL: the
        # dataset IS the initial content now.
        import pandas as pd
        ctx = _make_ctx(str(tmp_path))
        ctx.declare_live_query("q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS)
        ctx.add_section("S", [LiveDataSource(
            "ds", query="q", df=pd.DataFrame(columns=["ts"]))])
        assert "live-query-no-snapshot" in _check_ids(ctx, level="fail")

    def test_duplicate_live_datasource_for_one_query_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        events = ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
            snapshot_params=_EVENTS_DEFAULTS)
        ctx.add_section("S", [
            LiveDataSource("a", query="q", df=events),
            LiveDataSource("b", query="q", df=events),
        ])
        assert "live-query-duplicate-datasource" in _check_ids(ctx, level="fail")

    def test_filter_missing_param_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        events = ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
            snapshot_params=_EVENTS_DEFAULTS)
        ctx.add_section("S", [
            LiveDataSource("events", query="q", df=events),
            FilterBar("events", events, filters=[
                {"type": "dropdown", "column": "tier"},  # no param!
            ]),
        ])
        assert "live-query-filter-missing-param" in _check_ids(ctx, level="fail")

    def test_uncovered_param_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        events = ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
            snapshot_params=_EVENTS_DEFAULTS)
        ctx.add_section("S", [
            LiveDataSource("events", query="q", df=events),
            FilterBar("events", events, filters=[
                {"type": "dropdown", "column": "tier", "param": "tier", "multi": False},
            ]),
        ])
        assert "live-query-param-uncovered" in _check_ids(ctx, level="fail")

    def test_filter_type_param_mismatch_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        events = ctx.declare_live_query(
            "q2", "SELECT ts, event, tier, amount, user_id FROM events WHERE user_id = :user_id",
            datasource="db", params=[{"name": "user_id", "type": "int", "required": True}],
            snapshot_params={"user_id": 7})
        ctx.add_section("S", [
            LiveDataSource("events2", query="q2", df=events),
            # A dropdown bound to an int param is a type mismatch: dropdown
            # binds an 'enum' param.
            FilterBar("events2", events, filters=[
                {"type": "dropdown", "column": "tier", "param": "user_id", "multi": False},
            ]),
        ])
        assert "live-query-filter-type-mismatch" in _check_ids(ctx, level="fail")

    def test_live_bound_dropdown_multi_true_fails(self, tmp_path):
        ctx = _make_ctx(str(tmp_path))
        events = ctx.declare_live_query(
            "q", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
            snapshot_params=_EVENTS_DEFAULTS)
        ctx.add_section("S", [
            LiveDataSource("events", query="q", df=events),
            FilterBar("events", events, filters=[
                {"type": "text", "column": "user_id", "param": "user_id"},
                {"type": "toggle", "column": "event", "param": "event_type"},
                {"type": "dropdown", "column": "tier", "param": "tier"},  # multi defaults True
                {"type": "slider", "column": "amount", "min_param": "min_amount",
                 "max_param": "max_amount"},
                {"type": "date_range", "column": "ts", "min_param": "since",
                 "max_param": "until"},
            ]),
        ])
        assert "live-query-dropdown-multi-unsupported" in _check_ids(ctx, level="fail")

    def test_clean_declaration_raises_nothing(self, tmp_path):
        ctx = _build(str(tmp_path), with_totals=True)
        assert _check_ids(ctx) == []


# ══════════════════════════════════════════════════════════════
# Runtime registration and host flags
# ══════════════════════════════════════════════════════════════

class TestRuntimeWiring:
    def test_module_is_in_the_load_order(self):
        assert "live_query" in RUNTIME_MODULES

    def test_no_host_stamps_false_and_no_url(self):
        head, body = host_flag_scripts(
            {"nav_html": "", "scripts": [], "live_query_url": ""}, "r1")
        assert "window._fwHasHost=false" in head
        assert "_fwLiveQueryUrl" not in head
        assert body == ""

    def test_slug_template_is_substituted(self):
        head, _ = host_flag_scripts(
            {"nav_html": "", "scripts": [],
             "live_query_url": "/api/reports/{slug}/live-query"}, "my-report")
        assert 'window._fwLiveQueryUrl="/api/reports/my-report/live-query"' in head

    def test_live_url_alone_means_hosted(self):
        head, _ = host_flag_scripts(
            {"nav_html": "", "scripts": [], "live_query_url": "/lq"}, "r1")
        assert "window._fwHasHost=true" in head


# ══════════════════════════════════════════════════════════════
# The demo report
# ══════════════════════════════════════════════════════════════

class TestDemoReport:
    def test_live_ops_monitor_builds_standalone(self, tmp_path):
        from trellum.project import get_project_root
        from trellum.testing.runner import test_report

        report_dir = os.path.join(get_project_root(), "reports",
                                  "user-event-log")
        if not os.path.isdir(report_dir):
            pytest.skip("user-event-log demo report not in this project")
        out = str(tmp_path / "out")
        result = test_report(report_dir, output_dir=out, screenshot=False)
        assert result.generation_ok, result.details
        with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
            html = f.read()
        assert _STANDALONE_NOTE in html
        with open(os.path.join(out, "data.json"), encoding="utf-8") as f:
            data = json.load(f)
        live_entries = [c for c in data["components"].values()
                        if c.get("type") == "live_data_source"]
        assert len(live_entries) == 2  # ops_events + top_actors
        assert all(data.get(f"_ds_{c['dataset_id']}") for c in live_entries)
        assert os.path.exists(os.path.join(out, "_live_queries.json"))

    def test_live_ops_monitor_validates_clean(self, tmp_path):
        from trellum.project import get_project_root
        from trellum.testing.runner import test_report

        report_dir = os.path.join(get_project_root(), "reports", "user-event-log")
        if not os.path.isdir(report_dir):
            pytest.skip("user-event-log demo report not in this project")
        out = str(tmp_path / "out")
        result = test_report(report_dir, output_dir=out, screenshot=False)
        assert result.validation_pass, (
            f"{result.validation_fails} validation FAIL(s) — {result.details}"
        )


# ══════════════════════════════════════════════════════════════
# Browser: the FilterBar-driven binder against a stubbed live endpoint
# ══════════════════════════════════════════════════════════════

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _LiveStubServer:
    """Serves a report directory plus a stub POST /live-query endpoint.

    Responses are keyed off ``query_id`` and, for the "user_events" query,
    off ``params.user_id`` for a couple of special test hooks (400 error,
    an artificial delay for the stale-response test). ``rate_limit_countdown``
    lets a test force the next N requests to 429 before succeeding.
    """

    def __init__(self, output_dir: str):
        self.port = _find_free_port()
        self.requests: list[dict] = []
        self.rate_limit_countdown = 0
        server_self = self

        from trellum.rendering.cdn import serve_vendor_request

        class Handler(SimpleHTTPRequestHandler):
            def __init__(inner, *args, **kwargs):
                super().__init__(*args, directory=output_dir, **kwargs)

            def log_message(inner, *args):
                pass

            def do_GET(inner):
                if serve_vendor_request(inner):
                    return
                super().do_GET()

            def do_POST(inner):
                length = int(inner.headers.get("Content-Length", "0"))
                body = json.loads(inner.rfile.read(length) or b"{}")
                body["_path"] = inner.path
                body["_report_header"] = inner.headers.get("X-Trellum-Report")
                body["_csrf"] = inner.headers.get("X-CSRFToken")
                server_self.requests.append(body)

                if server_self.rate_limit_countdown > 0:
                    server_self.rate_limit_countdown -= 1
                    payload = json.dumps({"error": "rate limit exceeded"}).encode()
                    inner.send_response(429)
                    inner.send_header("Retry-After", "1")
                    inner.send_header("Content-Type", "application/json")
                    inner.send_header("Content-Length", str(len(payload)))
                    inner.end_headers()
                    inner.wfile.write(payload)
                    return

                qid = body.get("query_id")
                params = body.get("params") or {}

                if qid == "user_events":
                    uid = params.get("user_id")
                    if uid == 400:
                        payload = json.dumps({"error": "no such user"}).encode()
                        inner.send_response(400)
                        inner.send_header("Content-Type", "application/json")
                        inner.send_header("Content-Length", str(len(payload)))
                        inner.end_headers()
                        inner.wfile.write(payload)
                        return
                    if uid == 999:
                        time.sleep(0.6)  # the "slow" response for the stale-response test
                    tag = f"uid{uid}-tier{params.get('tier')}-evt{params.get('event_type')}"
                    payload = json.dumps({
                        "columns": ["ts", "event", "tier", "amount", "user_id"],
                        "rows": [["2026-02-01", "purchase", params.get("tier") or "free",
                                 9.99, uid if isinstance(uid, int) else 0]],
                        "truncated": False,
                        "elapsed_ms": 3,
                        "_tag": tag,
                    }).encode()
                elif qid == "tier_totals":
                    payload = json.dumps({
                        "columns": ["tier", "n"],
                        "rows": [[params.get("tier") or "free", 42]],
                        "truncated": False,
                        "elapsed_ms": 2,
                    }).encode()
                else:
                    payload = json.dumps({"error": "unknown query_id"}).encode()
                    inner.send_response(400)
                    inner.send_header("Content-Type", "application/json")
                    inner.send_header("Content-Length", str(len(payload)))
                    inner.end_headers()
                    inner.wfile.write(payload)
                    return

                inner.send_response(200)
                inner.send_header("Content-Type", "application/json")
                inner.send_header("Content-Length", str(len(payload)))
                inner.end_headers()
                inner.wfile.write(payload)

        self.server = HTTPServer(("", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/index.html"

    def requests_for(self, query_id: str) -> list[dict]:
        return [r for r in self.requests if r.get("query_id") == query_id]

    def shutdown(self):
        self.server.shutdown()


def _build_for_browser(tmpdir: str, *, hosted: bool, with_totals: bool = True) -> str:
    """Render the tiny live report, hosted (endpoint stamped) or standalone."""
    prior = os.environ.get("FW_EXTENSIONS_JSON")
    if hosted:
        os.environ["FW_EXTENSIONS_JSON"] = json.dumps(
            {"live_query_url": "/live-query"})
    else:
        os.environ.pop("FW_EXTENSIONS_JSON", None)
    try:
        ctx = _build(tmpdir, with_totals=with_totals)
    finally:
        if prior is None:
            os.environ.pop("FW_EXTENSIONS_JSON", None)
        else:
            os.environ["FW_EXTENSIONS_JSON"] = prior
    return ctx.output_dir


@pytest.fixture(scope="class")
def live_browser():
    """One Chromium for the class — nested sync_playwright() calls would
    collide on the event loop, so every browser test shares this one."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture(scope="class")
def live_page(live_browser):
    """A hosted build (two linked live datasets) served with the stub
    endpoint, opened in Chromium."""
    tmpdir = tempfile.mkdtemp(prefix="fw_lq_")
    out = _build_for_browser(tmpdir, hosted=True, with_totals=True)
    server = _LiveStubServer(out)
    page = live_browser.new_page()
    page.goto(server.url)
    page.wait_for_selector(".fw-table", timeout=15000)
    yield page, server
    page.close()
    server.shutdown()


def _wait_for_count(server: _LiveStubServer, query_id: str, at_least: int,
                    timeout: float = 5.0) -> list[dict]:
    """Poll (rather than a fixed sleep) until *query_id* has received at
    least *at_least* requests. A fixed ``wait_for_timeout`` after a commit
    is exactly the kind of assertion that gets flaky under CI/system load
    (250ms debounce + a real network round trip is usually well under a
    second, but "usually" is not "always") — polling is both faster on a
    fast machine and more reliable on a slow one."""
    deadline = time.monotonic() + timeout
    reqs = server.requests_for(query_id)
    while len(reqs) < at_least and time.monotonic() < deadline:
        time.sleep(0.05)
        reqs = server.requests_for(query_id)
    return reqs


class TestLiveInteraction:
    def test_control_wakes_up_when_hosted(self, live_page):
        page, _ = live_page
        assert page.get_attribute(".fw-live-bar", "data-live-state") == "ready"
        assert page.locator(".fw-live-note").is_hidden()

    def test_toggle_auto_queries_and_all_sentinel(self, live_page):
        page, server = live_page
        before = len(server.requests_for("user_events"))
        page.click(".fw-toggle-group[data-filter-id] .fw-toggle-btn:has-text('purchase')")
        reqs = _wait_for_count(server, "user_events", before + 1)
        assert len(reqs) == before + 1
        assert reqs[-1]["params"]["event_type"] == "purchase"

        # Click back to "All": engine's '__all__' sentinel maps to the
        # declared enum sentinel ("all").
        page.click(".fw-toggle-group[data-filter-id] .fw-toggle-btn:has-text('All')")
        reqs = _wait_for_count(server, "user_events", before + 2)
        assert len(reqs) == before + 2
        assert reqs[-1]["params"]["event_type"] == "all"

    def test_dropdown_single_select_auto_queries(self, live_page):
        page, server = live_page
        before = len(server.requests_for("user_events"))
        # SlimSelect renders its own control over the <select>; drive it
        # through its own JS API rather than simulating pointer clicks on
        # a rebuilt widget -- the contract under test is the engine/binder
        # routing on afterChange, not SlimSelect's own UI.
        page.evaluate("""
            () => {
                var sel = document.querySelector("[data-filter-col='tier']");
                var ss = sel && sel._fwSlimSelect;
                if (ss) ss.setSelected(['pro']);
            }
        """)
        reqs = _wait_for_count(server, "user_events", before + 1)
        assert len(reqs) == before + 1
        assert reqs[-1]["params"]["tier"] == "pro"

    def test_text_commits_on_enter_only(self, live_page):
        page, server = live_page
        before = len(server.requests_for("user_events"))
        inp = page.locator(".fw-text-filter")
        inp.fill("12345")
        page.wait_for_timeout(700)  # typing alone must never fire
        assert len(server.requests_for("user_events")) == before, \
            "typing fired a query — text commits must be explicit (Enter/blur)"
        inp.press("Enter")
        reqs = _wait_for_count(server, "user_events", before + 1)
        assert len(reqs) == before + 1
        assert reqs[-1]["params"]["user_id"] == 12345

    def test_slider_drag_fires_nothing_release_fires_once(self, live_page):
        page, server = live_page
        before = len(server.requests_for("user_events"))
        # A real pointer drag on noUiSlider's own handle -- this is the only
        # way to get noUiSlider to fire its OWN 'slide'/'change' events
        # (programmatically calling .set() fires 'update'/'set', never
        # 'slide' or 'change', so it would prove nothing about the
        # transient-vs-commit routing this test exists to check).
        handle = page.locator(".fw-slider .noUi-handle-lower")
        box = handle.bounding_box()
        assert box is not None, "slider handle not rendered"
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        # Several intermediate moves = several 'slide' ticks, all transient
        # -- none of them may reach the server while the button is down.
        for dx in (10, 20, 30):
            page.mouse.move(start_x + dx, start_y)
            page.wait_for_timeout(30)
        page.wait_for_timeout(300)
        assert len(server.requests_for("user_events")) == before, \
            "a mid-drag 'slide' tick must not fire; only 'change' (release) does"

        # Release -- noUiSlider fires 'change', which must commit exactly
        # once.
        page.mouse.up()
        reqs = _wait_for_count(server, "user_events", before + 1)
        assert len(reqs) == before + 1

    def test_date_range_trigger_opens_and_preset_commits(self, live_page):
        """Regression: the date-range trigger/preset buttons ship server-
        rendered `disabled` (bake_disabled() in live_filterable.py disables
        every <select>/<input>/<button> a filter plugin renders), and
        attachFilterBar()'s re-enable pass used to only target
        `button.fw-toggle-btn` -- toggle/flag's own class, not date_range's
        `.fw-date-trigger`/`.fw-date-preset`. An armed page left the
        date-range control permanently disabled: unclickable, unopenable,
        forever showing the build-time snapshot's own range."""
        page, server = live_page
        before = len(server.requests_for("user_events"))
        trigger = page.locator(".fw-date-trigger")
        assert trigger.is_enabled(), "date-range trigger is still disabled once armed"
        trigger.click()
        popover = page.locator(".fw-date-popover")
        assert "open" in (popover.get_attribute("class") or ""), \
            "clicking the trigger did not open the popover"

        # A preset commits immediately, like a dropdown/toggle -- and, since
        # user 7's rows only cover 2026-01-03..2026-01-20, "7D" is a real
        # change from the snapshot's own default window, not a no-op.
        preset = page.locator(".fw-date-preset", has_text="7D")
        assert preset.is_enabled(), "date-range preset buttons are still disabled once armed"
        preset.click()
        reqs = _wait_for_count(server, "user_events", before + 1)
        assert len(reqs) == before + 1, "the date-range control never re-queried"
        assert "since" in reqs[-1]["params"] and "until" in reqs[-1]["params"]

    def test_two_linked_datasets_fire_once_each_on_shared_filter(self, live_page):
        page, server = live_page
        before_events = len(server.requests_for("user_events"))
        before_totals = len(server.requests_for("tier_totals"))
        page.evaluate("""
            () => {
                var sel = document.querySelector("[data-filter-col='tier']");
                var ss = sel && sel._fwSlimSelect;
                if (ss) ss.setSelected(['free']);
            }
        """)
        _wait_for_count(server, "user_events", before_events + 1)
        _wait_for_count(server, "tier_totals", before_totals + 1)
        assert len(server.requests_for("user_events")) == before_events + 1
        assert len(server.requests_for("tier_totals")) == before_totals + 1
        assert server.requests_for("tier_totals")[-1]["params"]["tier"] == "free"

    def test_error_state_is_inline(self, live_page):
        page, _ = live_page
        inp = page.locator(".fw-text-filter")
        inp.fill("400")
        inp.press("Enter")
        page.wait_for_selector(".fw-live-status.fw-live-error", timeout=10000)
        assert "no such user" in page.inner_text(".fw-live-status")

    def test_live_commit_locks_column_widths(self, live_page):
        page, server = live_page
        inp = page.locator(".fw-text-filter")
        inp.fill("8")
        inp.press("Enter")
        page.wait_for_function(
            "sel => document.querySelectorAll(sel).length > 0",
            arg=".fw-live-bar[data-live-state='live']", timeout=10000,
        )
        info = page.evaluate(
            "() => { var t = document.querySelector('.fw-table');"
            " var cols = t ? t.querySelectorAll('colgroup col') : [];"
            " var ths = t ? t.querySelectorAll('thead th') : [];"
            " return { layout: t ? getComputedStyle(t).tableLayout : null,"
            "          nCols: cols.length, nTh: ths.length }; }"
        )
        assert info["layout"] == "fixed", info
        assert info["nCols"] > 0 and info["nCols"] == info["nTh"], info

    def test_429_then_one_automatic_retry(self, live_page):
        page, server = live_page
        server.rate_limit_countdown = 1
        before = len(server.requests_for("user_events"))
        inp = page.locator(".fw-text-filter")
        inp.fill("321")
        inp.press("Enter")
        # One 429 + one retry that succeeds == 2 requests for this commit.
        reqs = _wait_for_count(server, "user_events", before + 2, timeout=10)
        assert len(reqs) == before + 2

    def test_stale_response_does_not_overwrite_newer(self, live_page):
        page, server = live_page
        before = len(server.requests_for("user_events"))
        inp = page.locator(".fw-text-filter")
        # Fire the SLOW request first (user 999 sleeps server-side), then
        # immediately fire a FAST one for a different user. The slow
        # response must not clobber the fast one once it lands.
        inp.fill("999")
        inp.press("Enter")
        page.wait_for_timeout(50)
        inp.fill("111")
        inp.press("Enter")
        # Wait for BOTH the fast (111) and the slow (999, ~600ms server
        # delay) responses to land, then assert the engine reflects the
        # last-applied commit, not whichever response arrived last.
        _wait_for_count(server, "user_events", before + 2, timeout=10)
        page.wait_for_function(
            "() => { var r = window._fwFilterEngine.getFiltered('events');"
            " return r.length && r[0].user_id === 111; }",
            timeout=10000,
        )
        # The engine's committed dataset must reflect the LAST applied
        # params (111), not the stale 999 response that arrived after it.
        state = page.evaluate("""
            () => window._fwFilterEngine.getFiltered('events')[0]
        """)
        assert state["user_id"] == 111, state

    def test_standalone_stays_disabled(self, live_browser):
        tmpdir = tempfile.mkdtemp(prefix="fw_lq_solo_")
        out = _build_for_browser(tmpdir, hosted=False)
        server = _LiveStubServer(out)
        page = live_browser.new_page()
        try:
            page.goto(server.url)
            page.wait_for_selector(".fw-table", timeout=15000)
            assert page.get_attribute(".fw-live-bar", "data-live-state") == "standalone"
            assert page.locator(".fw-live-note").is_visible()
            assert _STANDALONE_NOTE in page.inner_text(".fw-live-note")
            assert server.requests == []
        finally:
            page.close()
            server.shutdown()

    def test_url_round_trip_fires_exactly_one_request(self, live_browser):
        # A URL carrying a non-default live filter value must fire exactly
        # one request (the URL-restore commit), not zero and not several.
        tmpdir = tempfile.mkdtemp(prefix="fw_lq_url_")
        out = _build_for_browser(tmpdir, hosted=True)
        server = _LiveStubServer(out)
        page = live_browser.new_page()
        try:
            page.goto(server.url + "?tier=pro")
            page.wait_for_function(
                "sel => document.querySelectorAll(sel).length > 0",
                arg=".fw-live-bar[data-live-state='live']", timeout=10000,
            )
            reqs = server.requests_for("user_events")
            assert len(reqs) == 1
            assert reqs[0]["params"]["tier"] == "pro"
        finally:
            page.close()
            server.shutdown()

    def test_share_link_disables_with_share_note(self, live_browser):
        tmpdir = tempfile.mkdtemp(prefix="fw_lq_share_")
        out = _build_for_browser(tmpdir, hosted=True)
        # Simulate a share-serve stamp: splice the flag before </head>,
        # exactly like apps.reports.views._share_head_extra does.
        index = os.path.join(out, "index.html")
        with open(index, encoding="utf-8") as f:
            html = f.read()
        html = html.replace(
            "</head>", "<script>window._fwShareLink=true;</script></head>", 1)
        with open(index, "w", encoding="utf-8") as f:
            f.write(html)
        server = _LiveStubServer(out)
        page = live_browser.new_page()
        try:
            page.goto(server.url)
            page.wait_for_selector(".fw-table", timeout=15000)
            assert page.get_attribute(".fw-live-bar", "data-live-state") == "standalone"
            assert _SHARE_NOTE in page.inner_text(".fw-live-note")
            assert server.requests == []
        finally:
            page.close()
            server.shutdown()


# ══════════════════════════════════════════════════════════════
# Browser: `trellum serve`'s own DEV live-query endpoint (no stub host)
# ══════════════════════════════════════════════════════════════
# Unlike every test above, which serves the build through _LiveStubServer
# (a hand-rolled stand-in for a host), this class serves it through the
# REAL trellum.runner.serve._serve -- the actual `python -m trellum.run` /
# `trellum serve` dev server -- against a STANDALONE build (extensions
# empty, window._fwHasHost=false baked in). If the control still arms and
# a filter commit still returns real rows, the dev server's own
# advertise-then-answer path (trellum.runner.live_query_dev) is what made
# that happen, not a build-time flag.

def _build_for_dev_serve(tmpdir: str) -> str:
    """Like ``_build()`` above, but the datasource is declared through
    CENTRAL config (``data-sources/config.yaml``) instead of an inline
    dict in ``report.yaml`` -- the shape ``trellum.runner.live_query_dev.
    _run``'s central-config-only resolution actually supports (see its
    docstring), and the shape a real project uses.

    Self-contained on purpose: it points ``trellum.project``'s project
    root at *tmpdir* itself, rather than depending on the demo project's
    own (large, separately-generated-by-``make_fixtures.py``) sqlite
    fixture. The dev-serve endpoint resolves its datasource LAZILY, at
    request time, in the server's background thread -- long after this
    function returns -- so the project-root override is intentionally
    left in place; the caller (the ``dev_served_page`` fixture below)
    restores it once the browser session serving from it is done, not
    right after this call.
    """
    from trellum.data.datasource_config import invalidate_datasource_cache
    from trellum.project import set_project_root

    ds_dir = os.path.join(tmpdir, "data-sources")
    os.makedirs(ds_dir, exist_ok=True)
    _events_db(ds_dir)  # writes <tmpdir>/data-sources/events.sqlite
    with open(os.path.join(ds_dir, "config.yaml"), "w", encoding="utf-8") as f:
        f.write("sources:\n  db:\n    type: sqlite\n    path: data-sources/events.sqlite\n")

    set_project_root(tmpdir)
    invalidate_datasource_cache()

    config = {
        "name": "Live Query Dev-Serve Test",
        "description": "A report exercising declared live queries through the real dev server.",
        "data_sources": ["db"],  # bare string: central-config resolution, not an inline dict
    }
    out = os.path.join(tmpdir, "out")
    ctx = ReportContext(config=config, slug="lq-dev-test", output_dir=out)
    events = ctx.declare_live_query(
        "user_events", _EVENTS_SQL, datasource="db", params=_EVENTS_PARAMS,
        snapshot_params=_EVENTS_DEFAULTS)

    ctx.add_section("Events", [
        LiveDataSource("events", query="user_events", df=events),
        FilterBar("events", events, filters=[
            {"type": "text", "column": "user_id", "param": "user_id", "label": "User ID"},
            {"type": "toggle", "column": "event", "param": "event_type", "label": "Event"},
            {"type": "dropdown", "column": "tier", "param": "tier", "label": "Tier", "multi": False},
            {"type": "slider", "column": "amount", "min_param": "min_amount",
             "max_param": "max_amount", "label": "Amount"},
            {"type": "date_range", "column": "ts", "min_param": "since",
             "max_param": "until", "label": "Date"},
        ]),
        DataTable(events, title="Events", dataset_id="events", sortable=True),
        KpiRow(dataset_id="events", kpis=[
            {"label": "Rows", "format": "number", "agg": "count"},
        ]),
    ])
    render_report(ctx, ctx.output_dir, auto_refresh=False)
    ctx.close_connections()
    return out


class TestDevServeLiveQuery:
    """Served through the REAL ``trellum.runner.serve._serve`` -- the
    actual `python -m trellum.run` / `trellum serve` dev server -- against
    a STANDALONE build (extensions empty, ``window._fwHasHost=false``
    baked in). If the control still arms and a filter commit still
    returns real rows, the dev server's own advertise-then-answer path
    (``trellum.runner.live_query_dev``) is what made that happen, not a
    build-time flag.
    """

    @pytest.fixture(scope="class")
    def dev_served_page(self, live_browser, tmp_path_factory):
        import urllib.request

        from trellum.data.datasource_config import invalidate_datasource_cache
        from trellum.project import get_project_root, set_project_root
        from trellum.runner.serve import _serve

        tmp_root = str(tmp_path_factory.mktemp("fw_lq_devserve_root"))
        original_root = get_project_root()
        # No FW_EXTENSIONS_JSON: a plain standalone build, window._fwHasHost
        # baked false -- only the dev server's own serve-time injection can
        # arm what follows. _build_for_dev_serve leaves the project root
        # pointed at tmp_root -- the dev server resolves its datasource
        # against it lazily, per request, for as long as this fixture (and
        # the browser session using it) is alive.
        out = _build_for_dev_serve(tmp_root)

        port = _find_free_port()
        thread = threading.Thread(target=_serve, args=(out, port), daemon=True)
        thread.start()

        base = f"http://localhost:{port}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(base + "/index.html", timeout=0.5)
                break
            except Exception:
                time.sleep(0.1)
        else:
            set_project_root(original_root)
            invalidate_datasource_cache()
            pytest.fail("dev server never came up")

        page = live_browser.new_page()
        try:
            page.goto(base + "/index.html")
            # Not .fw-table (DataTable isn't in the priority render pass,
            # so it may render lazily, IntersectionObserver-gated, well
            # after load) and not merely `[data-live-state]`
            # (FilterBar.render_html bakes data-live-state="standalone"
            # server-side as the honest before-JS-runs default -- that
            # attribute is already present at first paint, so waiting for
            # its mere presence proves nothing). Wait for the value JS
            # only sets once attachFilterBar's arming logic has run.
            page.wait_for_selector(".fw-live-bar[data-live-state='ready']", timeout=15000)
            yield page
        finally:
            page.close()
            # Only now: the daemon server thread is abandoned (never shut
            # down -- nothing else can reach its port), but the project
            # root and datasource-config cache are global process state
            # and must not leak into whatever test runs next.
            set_project_root(original_root)
            invalidate_datasource_cache()

    def test_arms_with_no_stub_host_at_all(self, dev_served_page):
        """The build was standalone; only the dev server's own serve-time
        injection could have armed this."""
        page = dev_served_page
        assert page.get_attribute(".fw-live-bar", "data-live-state") == "ready"
        assert page.locator(".fw-live-note").is_hidden()

    def test_filter_change_requeries_the_real_datasource_and_updates(self, dev_served_page):
        """A live commit through `trellum serve` must reach a real sqlite
        database (through live_query_guard's coercion + the framework's
        own SQL binding) and the result must reach the filter
        engine/DOM, not just resolve some inert promise."""
        page = dev_served_page
        page.click(".fw-toggle-group[data-filter-id] .fw-toggle-btn:has-text('purchase')")
        page.wait_for_function(
            "sel => { var el = document.querySelector(sel);"
            " return !!el && el.getAttribute('data-live-state') === 'live'; }",
            arg=".fw-live-bar", timeout=10000,
        )
        assert "live" in page.inner_text(".fw-live-freshness").lower()
        rows = page.evaluate("() => window._fwFilterEngine.getFiltered('events')")
        assert isinstance(rows, list) and rows
        assert all(r.get("event") == "purchase" for r in rows)

    def test_a_build_with_no_live_query_is_never_touched(self, live_browser):
        """The dev server must not stamp _fwHasHost on every report it
        serves -- only ones that actually declared a live query. Otherwise
        every ordinary compiled report gained a host it never asked for
        (and, via the unrelated admin-badge check, a 404 of its own)."""
        import urllib.request

        import pandas as pd

        from trellum.components.tables import DataTable
        from trellum.rendering.html_builder import render_report
        from trellum.report import ReportContext
        from trellum.runner.serve import _serve

        tmpdir = tempfile.mkdtemp(prefix="fw_lq_nolive_")
        ctx = ReportContext(
            config={"name": "No Live Query", "description": "plain"},
            slug="no-live", output_dir=os.path.join(tmpdir, "out"),
        )
        df = pd.DataFrame({"x": [1, 2, 3]})
        ctx.add_section("S", [DataTable(df, title="T")])
        render_report(ctx, ctx.output_dir, auto_refresh=False)

        port = _find_free_port()
        thread = threading.Thread(target=_serve, args=(ctx.output_dir, port), daemon=True)
        thread.start()
        base = f"http://localhost:{port}"
        deadline = time.monotonic() + 10
        html = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(base + "/index.html", timeout=0.5) as resp:
                    html = resp.read().decode("utf-8")
                break
            except Exception:
                time.sleep(0.1)
        assert html is not None, "dev server never came up"
        assert "_fwHasHost=true" not in html
        # Not a bare "_fwLiveQueryUrl" substring check: the shared JS
        # runtime bundled into EVERY report (runtime/live_query.js) mentions
        # window._fwLiveQueryUrl in its own comments regardless of whether
        # this report declared anything live -- the assignment is the only
        # thing that would mean the dev server armed it.
        assert "window._fwLiveQueryUrl=" not in html
