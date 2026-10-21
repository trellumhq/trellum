"""Local live queries resolve only the source selected by their manifest entry."""
from __future__ import annotations

import io
import json
import sqlite3
from types import SimpleNamespace

import pytest

from trellum.data.datasource_config import invalidate_datasource_cache
from trellum.project import get_project_root, set_project_root
from trellum.report import ReportContext
from trellum.runner.live_query_dev import handle_live_query_request


@pytest.fixture
def source_project(tmp_path):
    original = get_project_root()
    set_project_root(str(tmp_path))
    (tmp_path / "data-sources").mkdir()
    with sqlite3.connect(tmp_path / "events.sqlite") as conn:
        conn.executescript("""
            CREATE TABLE events (day TEXT, value INTEGER);
            INSERT INTO events VALUES ('2026-10-14', 7);
            CREATE VIEW recent AS SELECT * FROM events;
        """)
    try:
        yield tmp_path
    finally:
        set_project_root(original)
        invalidate_datasource_cache()


def _configure(root, extra):
    (root / "data-sources/config.yaml").write_text(
        "sources:\n  A:\n    type: sqlite\n    path: events.sqlite\n" + extra,
        encoding="utf-8",
    )
    invalidate_datasource_cache()


def _post(root, query_id):
    manifest = {"version": 1, "queries": {
        "a": {"datasource": "A", "sql": "SELECT value FROM events", "params": []},
        "view": {"datasource": "A", "sql": "WITH selected AS (SELECT * FROM recent) SELECT value FROM selected", "params": []},
        "b": {"datasource": "B", "sql": "SELECT value FROM events", "params": []},
    }}
    (root / "_live_queries.json").write_text(json.dumps(manifest), encoding="utf-8")
    body = json.dumps({"query_id": query_id, "params": {}}).encode()
    codes = []
    handler = SimpleNamespace(
        path="/_fw/live-query", command="POST", client_address=("127.0.0.1", 1234),
        headers={"Content-Length": str(len(body))}, rfile=io.BytesIO(body), wfile=io.BytesIO(),
        translate_path=lambda path: str(root), send_response=codes.append,
        send_header=lambda *args: None, end_headers=lambda: None,
    )
    assert handle_live_query_request(handler)
    return codes[0], json.loads(handler.wfile.getvalue())


@pytest.mark.parametrize("extra", ["", "  B: {}\n", "  B:\n", "  B:\n    type: postgres\n"])
@pytest.mark.parametrize("entrypoint", ["snapshot", "a", "view"])
def test_unrelated_incomplete_source_does_not_block_snapshot_or_selected_query(source_project, extra, entrypoint):
    _configure(source_project, extra)
    if entrypoint != "snapshot":
        status, payload = _post(source_project, entrypoint)
        assert status == 200, payload
        assert payload["rows"] == [[7]]
        return
    ctx = ReportContext(config={"data_sources": ["A", "B"]}, slug="source-test",
                        output_dir=str(source_project / "out"))
    try:
        snapshot = ctx.declare_live_query(
            "a", "SELECT value FROM recent", datasource="A", params=[], snapshot_params={})
        assert snapshot["value"].tolist() == [7]
    finally:
        ctx.close_connections()


@pytest.mark.parametrize("extra", ["", "  B: {}\n", "  B:\n", "  B:\n    type: postgres\n"])
def test_selected_incomplete_source_fails_with_name_and_remedy(source_project, extra):
    _configure(source_project, extra)
    status, payload = _post(source_project, "b")
    assert status == 400
    assert "Data source 'B'" in payload["error"]
    assert "credentials.local" in payload["error"]
    assert "_USER and _PASS in .env" in payload["error"]
