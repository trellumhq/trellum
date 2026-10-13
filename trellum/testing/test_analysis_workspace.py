"""Trust boundaries and queue semantics for the local analysis workspace."""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from trellum.review import http, inject
from trellum.review.state import ReviewState


@pytest.fixture
def workspace_project(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    reports = tmp_path / "reports"
    analysis = reports / "analysis-one"
    analysis.mkdir(parents=True)
    (analysis / "report.yaml").write_text("kind: analysis\nslug: analysis-one\nname: Analysis One\n")
    (analysis / "content.md").write_text("## Finding\n\n")
    source = reports / "source-one"
    source.mkdir()
    (source / "report.yaml").write_text("slug: source-one\nname: Source One\n")
    (source / "generator.py").write_text("# generator\n")
    (reports / "source-alias").symlink_to(source, target_is_directory=True)
    other_analysis = reports / "analysis-two"
    other_analysis.mkdir()
    (other_analysis / "report.yaml").write_text("kind: analysis\nslug: analysis-two\nname: Analysis Two\n")
    (other_analysis / "content.md").write_text("## Finding\n\n")
    hidden = reports / "_private"
    hidden.mkdir()
    (hidden / "report.yaml").write_text("slug: _private\n")
    (hidden / "generator.py").write_text("# generator\n")
    (tmp_path / "output").mkdir()
    state = ReviewState()
    state.bind_dir(str(tmp_path / "output"))
    monkeypatch.setattr(http, "STATE", state)
    monkeypatch.setattr("trellum.review.inject.STATE", state)
    return tmp_path, state


@pytest.fixture
def workspace_server(workspace_project):
    root, state = workspace_project
    output = root / "output"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)

        def do_GET(self):
            if not http.handle_review_request(self, str(output)):
                super().do_GET()

        def do_POST(self):
            if not http.handle_review_request(self, str(output)):
                self.send_error(404)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, *, headers=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    yield base, request, state
    server.shutdown()
    server.server_close()
    thread.join()


def start_workspace(request):
    code, started = request("/_fw/review/analysis/start", {"slug": "analysis-one"},
                            headers={"Content-Type": "application/json"}, method="POST")
    assert code == 200
    return started


def workspace_headers(base, session):
    return {"Content-Type": "application/json", "Origin": base,
            "X-Trellum-Analysis-Token": session["token"]}


def wsbody(session, **values):
    return {"session": session["session"], "analysis_slug": session["slug"], **values}


def test_workspace_bridge_assembles_shared_widget_without_legacy_network(monkeypatch):
    loaded = []
    def load_js(path):
        loaded.append(path)
        return "__FW_REVIEW_CSS__" if path == "review/styles.js" else "// " + path
    monkeypatch.setattr(inject, "load_js", load_js)
    monkeypatch.setattr(inject, "load_css", lambda _path: "body { color: red; }")

    bundle = inject.workspace_overlay_js()

    assert loaded == [
        "review/state.js", "review/styles.js", "review/dom.js",
        "review/identify.js", "review/select.js", "review/chart_target.js",
        "review/popover.js", "review/queue.js", "review/panel.js",
        "review/presence.js", "review/log.js", "review/keyboard.js",
        "review/workspace_adapter.js",
    ]
    assert "window._fwWorkspaceOverlayInit" in bundle
    assert "body { color: red; }" in bundle
    assert all(f"review/{omitted}.js" not in loaded for omitted in (
        "workspace_bridge", "net", "status", "end_session"))

    # The standard report review route keeps its original module set/guard.
    loaded.clear()
    ordinary = inject.overlay_js()
    assert loaded == [f"review/{name}.js" for name in inject.OVERLAY_MODULES]
    assert "window._fwReviewInit" in ordinary


def test_workspace_bridge_route_requires_active_session_and_serves_composed_bundle(
        workspace_server, monkeypatch):
    base, _request, _state = workspace_server
    session = start_workspace(_request)
    monkeypatch.setattr(http, "workspace_overlay_js", lambda: "assembled workspace widget")

    def get(path):
        req = urllib.request.Request(base + path)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    code, body = get("/_fw/review/workspace-bridge.js?fw_session=" + str(session["session"]))
    assert code == 200 and body == "assembled workspace widget"
    code, _ = get("/_fw/review/workspace-bridge.js?fw_session=" + str(session["session"] + 1))
    assert code == 403


def test_workspace_poll_context_explains_catalog_derived_actions(capsys):
    from trellum.cli.commands.review import _print_review_batches

    _print_review_batches([{"slug": "analysis-one", "workspace": True, "items": [
        {"tag": "figure", "origin_slug": "source-one", "origin_kind": "report",
         "analysis_slug": "analysis-one", "default_target_slug": "analysis-one",
         "workspace_action": "add-evidence", "prompt": "Use this chart"},
        {"tag": "section", "origin_slug": "analysis-one", "origin_kind": "analysis",
         "analysis_slug": "analysis-one", "default_target_slug": "analysis-one",
         "workspace_action": "edit-analysis", "prompt": "Tighten this paragraph"},
    ]}])
    output = capsys.readouterr().out
    assert "report selections add evidence" in output
    assert "Prompts do not change the destination" in output
    assert "action: add evidence to active analysis analysis-one from report source-one" in output
    assert "action: edit active analysis analysis-one" in output


def test_workspace_session_checks_catalog_and_state(workspace_server):
    base, request, _ = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    code, state = request("/_fw/review/workspace/state")
    assert code == 200
    assert state["analysis"]["slug"] == "analysis-one"
    assert [row["slug"] for row in state["reports"]] == ["source-one"]
    assert state["token"] == session["token"]

    wrong = dict(headers, Origin="http://evil.example")
    code, _ = request("/_fw/review/workspace/open", wsbody(session, slug="source-one",
        request_id="open-request-000001"), headers=wrong, method="POST")
    assert code == 403
    invalid_token = {**headers, "X-Trellum-Analysis-Token": "wrong-token"}
    code, _ = request("/_fw/review/workspace/open", wsbody(session, slug="source-one",
        request_id="open-request-000004"), headers=invalid_token, method="POST")
    assert code == 403
    code, _ = request("/_fw/review/workspace/open", wsbody(session, slug="missing",
        request_id="open-request-000002"), headers=headers, method="POST")
    assert code == 400
    foreign_source = workspace_server[0] + "-foreign"
    assert foreign_source not in [row["slug"] for row in state["reports"]]
    stale = dict(session, session=session["session"] + 1)
    code, _ = request("/_fw/review/workspace/open", wsbody(stale, slug="source-one",
        request_id="open-request-000003"), headers=headers, method="POST")
    assert code == 403
    request("/_fw/review/analysis/end", {}, headers={"Content-Type": "application/json"}, method="POST")
    code, ended = request("/_fw/review/workspace/state")
    assert code == 200 and not ended["active"] and ended["ended"]


def test_async_build_is_deduplicated_and_failure_keeps_last_good_output(workspace_server, monkeypatch):
    from trellum.review import workspace
    base, request, _ = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    old_output = Path(workspace_server[2]._dir).parent / "source-one"
    old_output.mkdir()
    (old_output / "index.html").write_text("last good output")
    (old_output / "_meta.json").write_text(json.dumps({"slug": "source-one", "last_status": "success"}))
    os.utime(old_output / "_meta.json", (1, 1))
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        started.set()
        release.wait(3)
        raise RuntimeError("mock build failure")

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)
    body = wsbody(session, slug="source-one", request_id="build-request-000001")
    code, result = request("/_fw/review/workspace/open", body, headers=headers, method="POST")
    assert code == 200 and result["build_state"] == "building"
    assert started.wait(2)
    _, duplicate = request("/_fw/review/workspace/open", body, headers=headers, method="POST")
    assert duplicate["build_state"] == "building" and len(calls) == 1
    release.set()
    deadline = time.time() + 2
    while workspace.WORKSPACE.job(Path(workspace_server[2]._dir).parent,
                                  "source-one").get("state") != "failed" and time.time() < deadline:
        time.sleep(.01)
    _, state = request("/_fw/review/workspace/state")
    assert state["reports"][0]["build_state"] == "failed"
    assert state["reports"][0]["error"] == "mock build failure"
    assert (old_output / "index.html").read_text() == "last good output"


def test_source_changes_make_ready_output_stale_and_rebuildable(workspace_server, monkeypatch):
    import ast

    from trellum.review import workspace
    base, request, state = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    calls = []

    def successful_run(argv, **kwargs):
        code = argv[-1]
        call = ast.parse(code).body[1].value
        stage = Path(next(k.value.value for k in call.keywords if k.arg == "output_dir"))
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "index.html").write_text("new output")
        (stage / "_meta.json").write_text(json.dumps({"slug": "source-one", "last_status": "success"}))
        calls.append(code)

    monkeypatch.setattr(workspace.subprocess, "run", successful_run)
    first = wsbody(session, slug="source-one", request_id="build-request-000002")
    assert request("/_fw/review/workspace/open", first, headers=headers, method="POST")[1]["build_state"] == "building"
    deadline = time.time() + 2
    output = Path(state._dir).parent / "output" / "source-one"
    while (not (output / "index.html").exists() or workspace.WORKSPACE.job(Path(state._dir).parent, "source-one").get("state") != "ready") and time.time() < deadline:
        time.sleep(.01)
    assert (output / "index.html").read_text() == "new output"
    _, ready = request("/_fw/review/workspace/state")
    assert ready["reports"][0]["build_state"] == "ready"
    source_code = Path(state._dir).parent / "reports" / "source-one" / "generator.py"
    source_code.write_text("# changed after successful build\n")
    _, stale = request("/_fw/review/workspace/state")
    assert stale["reports"][0]["build_state"] == "stale"
    second = wsbody(session, slug="source-one", request_id="build-request-000003")
    assert request("/_fw/review/workspace/open", second, headers=headers, method="POST")[1]["build_state"] == "building"
    deadline = time.time() + 2
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(.01)
    assert len(calls) == 2


def test_initial_staleness_includes_queries_py(workspace_server):
    from trellum.review.workspace import _output_info
    state = workspace_server[2]
    root = Path(state._dir).parent
    source = root / "reports" / "source-one"
    now = time.time()
    for name in ("report.yaml", "generator.py"):
        os.utime(source / name, (now - 10, now - 10))
    queries = source / "queries.py"
    queries.write_text("# fresh dependency\n")
    os.utime(queries, (now - 1, now - 1))
    output = root / "output" / "source-one"
    output.mkdir()
    (output / "index.html").write_text("built")
    meta = output / "_meta.json"
    meta.write_text(json.dumps({"slug": "source-one", "last_status": "success"}))
    os.utime(meta, (now - 3, now - 3))
    assert not state.workspace_builds
    assert _output_info(root / "output", "source-one", source)[0] == "stale"


def test_workspace_feedback_routes_to_analysis_scope_with_per_item_source_and_dedupe(workspace_server):
    base, request, state = workspace_server
    state.start()
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    # An ordinary source review remains in its own queue bucket.
    code, _ = request("/_fw/review/feedback", {"slug": "source-one", "page": "/source-one/",
        "session": state.session, "items": [{"tag": "ordinary"}]},
        headers={"Content-Type": "application/json"}, method="POST")
    assert code == 200
    item = {"tag": "chart", "prompt": "Explain this chart", "origin_slug": "source-one",
            "filters": {"region": "west"}, "source_built_at": "2026-10-13T10:00:00Z", "frame_id": None,
            "capture_id": None, "section": None, "component": None, "data": None,
            "origin_name": "Source One", "origin_kind": "report"}
    analysis_item = {"tag": "section", "prompt": "Rewrite this analysis finding",
                     "origin_slug": "analysis-one", "origin_name": "Analysis One",
                     "origin_kind": "analysis", "section": {"id": "finding-1"}}
    body = wsbody(session, request_id="feedback-request-000001", note="Please review",
                  items=[item, analysis_item])
    code, result = request("/_fw/review/workspace/feedback", body, headers=headers, method="POST")
    assert code == 200 and result["ok"]
    _, duplicate = request("/_fw/review/workspace/feedback", body, headers=headers, method="POST")
    assert duplicate["duplicate"]
    assert request("/_fw/review/workspace/feedback", dict(body, note="changed"),
                   headers=headers, method="POST")[0] == 409
    status, source_batches = state.poll(0, slug="source-one")
    assert status == "feedback" and source_batches[0]["items"][0]["tag"] == "ordinary"
    status, analysis_batches = state.poll(0, slug="analysis-one")
    assert status == "feedback" and len(analysis_batches) == 1
    batch = analysis_batches[0]
    assert batch["workspace"] and batch["slug"] == "analysis-one"
    source_result, analysis_result = batch["items"]
    assert source_result["origin_slug"] == "source-one"
    assert source_result["origin_name"] == "Source One"
    assert source_result["origin_kind"] == "report"
    assert source_result["filters"] == {"region": "west"}
    assert source_result["source_built_at"] == "2026-10-13T10:00:00Z"
    assert source_result["default_target_slug"] == "analysis-one"
    assert source_result["destination"] == "analysis-one"
    assert source_result["workspace_action"] == "add-evidence"
    assert source_result["analysis_slug"] == "analysis-one"
    assert analysis_result["origin_slug"] == "analysis-one"
    assert analysis_result["section"] == {"id": "finding-1"}
    assert analysis_result["default_target_slug"] == "analysis-one"
    assert analysis_result["destination"] == "analysis-one"
    assert analysis_result["workspace_action"] == "edit-analysis"
    invalid_source = dict(body, request_id="feedback-request-000002", items=[dict(item, origin_slug="analysis-two")])
    assert request("/_fw/review/workspace/feedback", invalid_source, headers=headers, method="POST")[0] == 400
    invalid_capture = dict(body, request_id="feedback-request-000003",
                           items=[dict(item, capture_id="f" * 32)])
    assert request("/_fw/review/workspace/feedback", invalid_capture, headers=headers, method="POST")[0] == 400
    explicit_target = dict(body, request_id="feedback-request-000004",
                           items=[dict(item, destination="source-one")])
    assert request("/_fw/review/workspace/feedback", explicit_target, headers=headers, method="POST")[0] == 400
    explicit_analysis_target = dict(body, request_id="feedback-request-000005",
                                    items=[dict(item, destination="analysis-one")])
    assert request("/_fw/review/workspace/feedback", explicit_analysis_target,
                   headers=headers, method="POST")[0] == 200


def test_capture_is_frozen_deduplicated_and_not_queued(workspace_server):
    from trellum.analysis_inbox import inbox_dir
    from trellum.review.state import ReviewState
    base, request, state = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    capture = {"format": "trellum-analysis-capture", "version": 1,
        "image": {"mime_type": "image/png", "data_base64": base64.b64encode(image.getvalue()).decode()},
        "source": {"report_slug": "source-one", "report_name": "Source One",
            "url": "http://localhost:8050/source-one/", "component_id": "fw_c1",
            "component_title": "Revenue", "captured_at": "2026-10-13T10:00:00Z",
            "source_built_at": None, "filters": {"region": "west"}}}
    body = wsbody(session, slug="source-one", request_id="capture-request-000001", capture=capture)
    code, result = request("/_fw/review/workspace/capture", body, headers=headers, method="POST")
    assert code == 200
    assert state.status()["queued"] == 0
    stored = inbox_dir(Path(workspace_server[2]._dir).parent)
    capture_path = stored / f"{result['capture_id']}.trellum-capture.json"
    saved = json.loads(capture_path.read_text())
    assert saved["_analysis_inbox"]["target_slug"] == "analysis-one"
    assert "data_base64" in saved["image"]
    _, duplicate = request("/_fw/review/workspace/capture", body, headers=headers, method="POST")
    assert duplicate["capture_id"] == result["capture_id"] and duplicate["duplicate"]
    wrong = dict(body, slug="analysis-one")
    assert request("/_fw/review/workspace/capture", wrong, headers=headers, method="POST")[0] == 400
    conflicting = json.loads(json.dumps(body))
    conflicting["capture"]["source"]["component_title"] = "Different chart"
    assert request("/_fw/review/workspace/capture", conflicting, headers=headers, method="POST")[0] == 409
    restarted = ReviewState()
    restarted.bind_dir(str(Path(workspace_server[2]._dir)))
    assert "capture-request-000001" in restarted.workspace_captures


def test_end_feedback_retry_is_acknowledged_without_accepting_new_writes(workspace_server):
    base, request, state = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    body = wsbody(session, request_id="end-request-0000001", note="Finished",
                  items=[], end_session=True)
    code, first = request("/_fw/review/workspace/feedback", body, headers=headers, method="POST")
    assert code == 200 and first["ok"]
    code, duplicate = request("/_fw/review/workspace/feedback", body, headers=headers, method="POST")
    assert code == 200 and duplicate["duplicate"]
    new_body = dict(body, request_id="end-request-0000002")
    assert request("/_fw/review/workspace/feedback", new_body, headers=headers, method="POST")[0] == 409
    assert request("/_fw/review/workspace/open", wsbody(session, slug="source-one",
        request_id="open-after-end-0001"), headers=headers, method="POST")[0] == 409
    assert state.status()["queued"] == 1
