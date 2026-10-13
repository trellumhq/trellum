"""Private local captures, relay delivery, and HTTP trust boundaries."""

import base64
import io
import json
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from trellum.analysis_inbox import (
    discard_pending_capture,
    import_pending_capture,
    inbox_dir,
    list_pending_captures,
)
from trellum.review import http
from trellum.review.state import ReviewState


def capture():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return {"format": "trellum-analysis-capture", "version": 1,
            "image": {"mime_type": "image/png", "data_base64": base64.b64encode(stream.getvalue()).decode()},
            "source": {"report_slug": "source", "report_name": "Source", "url": "",
                       "component_id": "fw_c1", "component_title": "Revenue",
                       "captured_at": "2026-10-12T12:00:00Z", "source_built_at": None,
                       "filters": {"sales": {"region": "Europe"}}}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    target = tmp_path / "reports" / "decision"
    target.mkdir(parents=True)
    (target / "report.yaml").write_text("kind: analysis\nname: Decision\n")
    output = tmp_path / "output"
    output.mkdir()
    state = ReviewState()
    state.bind_dir(str(output))
    monkeypatch.setattr(http, "STATE", state)
    return tmp_path, target, output, state


@pytest.fixture
def server(project):
    _, _, output, _ = project

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)

        def do_GET(self):
            from trellum.rendering.cdn import serve_vendor_request
            if not http.handle_review_request(self, str(output)) and not serve_vendor_request(self):
                super().do_GET()

        def do_POST(self):
            if not http.handle_review_request(self, str(output)):
                self.send_error(404)

        def log_message(self, *args):
            pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{instance.server_port}"

    def request(path, body=None, headers=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + "/_fw/review/analysis" + path,
                                     data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    yield base, request
    instance.shutdown()
    instance.server_close()
    thread.join()


def submit(server, token, payload=None, request_id="a" * 32, **headers):
    base, request = server
    return request("/capture", {"capture": payload or capture(), "request_id": request_id,
                                "note": "Explain the filtered result"},
                   {"Origin": base, "X-Trellum-Analysis-Token": token, **headers})


def test_capture_poll_import_and_duplicate(server, project):
    root, target, output, state = project
    _, request = server
    code, active = request("/start", {"slug": "decision"})
    assert code == 200 and active["active"]
    assert request("")[1] == active
    code, result = submit(server, active["token"])
    assert code == 200 and not result["duplicate"]
    assert submit(server, active["token"])[1]["duplicate"]
    assert state.status()["queued"] == 1
    status, batches = state.poll(0, slug="decision")
    assert status == "feedback"
    assert batches[0]["note"] == "Explain the filtered result"
    assert batches[0]["items"][0]["data"] == {
        "type": "analysis-capture", "capture_id": result["capture_id"],
        "source_slug": "source", "component_title": "Revenue"}
    assert "data_base64" not in (output / ".review-state.json").read_text()
    pending = inbox_dir(root) / f"{result['capture_id']}.trellum-capture.json"
    assert pending.is_file()
    assert import_pending_capture(str(target), result["capture_id"], "revenue") == "![Evidence](evidence/revenue.png)"
    assert not pending.exists()
    assert json.loads((target / "evidence/revenue.json").read_text())["filters"] == capture()["source"]["filters"]
    assert submit(server, active["token"])[1]["duplicate"]


def test_restart_recovers_target_pending_and_queue(server, project, monkeypatch):
    root, _, output, first = project
    _, request = server
    active = request("/start", {"slug": "decision"})[1]
    result = submit(server, active["token"])[1]
    second = ReviewState()
    second.bind_dir(str(output))
    monkeypatch.setattr(http, "STATE", second)
    assert request("")[1] == active
    assert submit(server, active["token"])[1]["duplicate"]
    assert second.poll(0, "decision")[1][0]["items"][0]["data"]["capture_id"] == result["capture_id"]
    assert len(list(inbox_dir(root).glob("*.json"))) == 1


def test_failed_import_and_end_preserve_pending(server, project):
    root, target, _, state = project
    _, request = server
    active = request("/start", {"slug": "decision"})[1]
    capture_id = submit(server, active["token"])[1]["capture_id"]
    (target / "evidence").mkdir()
    (target / "evidence/revenue.png").write_text("already here")
    with pytest.raises(FileExistsError):
        import_pending_capture(str(target), capture_id, "revenue")
    assert len(list(inbox_dir(root).glob("*.json"))) == 1
    assert request("/end", {})[0] == 200
    assert not request("")[1]["active"]
    assert submit(server, active["token"])[0] == 409
    request("/start", {"slug": "decision"})
    state.end()
    assert not state.analysis_status()["active"]
    discard_pending_capture(str(target), capture_id)
    assert not list(inbox_dir(root).glob("*.json"))


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"}, {"Host": "evil.example"},
    {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"},
    {"X-Trellum-Analysis-Token": "wrong"},
])
def test_capture_denies_untrusted_browser(server, headers):
    active = server[1]("/start", {"slug": "decision"})[1]
    assert submit(server, active["token"], **headers)[0] == 403


def test_inactive_invalid_target_invalid_capture_and_duplicate_id(server, project):
    _, request = server
    assert submit(server, "")[0] == 409
    assert request("/start", {"slug": "../decision"})[0] == 400
    active = request("/start", {"slug": "decision"})[1]
    invalid = capture()
    invalid["image"]["data_base64"] = base64.b64encode(b"broken").decode()
    assert submit(server, active["token"], invalid)[0] == 400
    invalid = capture()
    invalid["source"]["url"] = "https://example.test/?token=secret"
    assert submit(server, active["token"], invalid)[0] == 400
    assert submit(server, active["token"])[0] == 200
    modified = capture()
    modified["source"]["component_title"] = "different"
    assert submit(server, active["token"], modified)[0] == 409
    assert project[3].status()["queued"] == 1


def test_http_rejects_oversize_framing_and_missing_origin(server):
    _, request = server
    active = request("/start", {"slug": "decision"})[1]
    headers = {"Origin": server[0], "X-Trellum-Analysis-Token": active["token"]}
    assert request("/capture", {}, {**headers, "Content-Length": str(40 * 1024 * 1024)})[0] == 413
    assert request("/capture", {}, {**headers, "Content-Length": "-1"})[0] == 400
    assert request("/capture", {}, {**headers, "Content-Length": "bad"})[0] == 400
    assert request("/capture", {}, {"X-Trellum-Analysis-Token": active["token"]})[0] == 403


def test_remote_client_denied_before_read():
    class Handler:
        path = "/_fw/review/analysis/capture"
        client_address = ("192.0.2.8", 1234)
        wfile = io.BytesIO()

        def send_response(self, code):
            self.code = code

        def send_header(self, *args):
            pass

        def end_headers(self):
            pass

    handler = Handler()
    assert http.handle_review_request(handler, ".")
    assert handler.code == 403


@pytest.mark.parametrize("capture_id", ["../evil", "C:\\evil", "a" * 31, "z" * 32])
def test_pending_import_rejects_unsafe_id(project, capture_id):
    with pytest.raises(ValueError):
        import_pending_capture(str(project[1]), capture_id)


def test_pending_cannot_import_into_another_target(server, project):
    active = server[1]("/start", {"slug": "decision"})[1]
    capture_id = submit(server, active["token"])[1]["capture_id"]
    other = project[0] / "reports/other"
    other.mkdir()
    (other / "report.yaml").write_text("kind: analysis\nname: Other\n")
    with pytest.raises(ValueError, match="another analysis"):
        import_pending_capture(str(other), capture_id)
    with pytest.raises(ValueError, match="another analysis"):
        discard_pending_capture(str(other), capture_id)
    other_active = server[1]("/start", {"slug": "other"})[1]
    other_id = submit(server, other_active["token"])[1]["capture_id"]
    assert list_pending_captures(str(project[1]))[0]["capture_id"] == capture_id
    discard_pending_capture(str(project[1]), capture_id)
    assert not list_pending_captures(str(project[1]))
    assert list_pending_captures(str(other))[0]["capture_id"] == other_id


@pytest.mark.parametrize("path,method", [("", "GET"), ("/start", "POST"), ("/end", "POST")])
def test_all_analysis_endpoints_deny_bad_host_origin(server, path, method):
    assert server[1](path, {} if method == "POST" else None, {"Host": "evil.example"}, method)[0] == 403
    assert server[1](path, {} if method == "POST" else None, {"Origin": "https://evil.example"}, method)[0] == 403


def test_oversize_png_rejected(server):
    from trellum.analysis_capture import MAX_IMAGE_BYTES
    active = server[1]("/start", {"slug": "decision"})[1]
    oversized = capture()
    oversized["image"]["data_base64"] = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
    assert submit(server, active["token"], oversized)[0] == 400


def test_analysis_target_slug_must_match_directory(server, project):
    (project[1] / "report.yaml").write_text("kind: analysis\nname: Decision\nslug: other\n")
    code, result = server[1]("/start", {"slug": "decision"})
    assert code == 400 and "slug must match" in result["error"]


def test_real_browser_send_and_download_fallback(server, project, tmp_path):
    from playwright.sync_api import sync_playwright

    from trellum.runner.execute import run_report
    from trellum.testing.test_analysis_journey import _GENERATOR

    root, target, _, state = project
    source = root / "reports/source"
    source.mkdir()
    (source / "report.yaml").write_text("name: Source\ntheme: trellum light\n")
    (source / "generator.py").write_text(_GENERATOR)
    run_report(str(source))
    active = server[1]("/start", {"slug": "decision"})[1]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1360, "height": 960})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(server[0] + "/source/", wait_until="networkidle")
            try:
                page.wait_for_function("window._chartInstances && Object.keys(window._chartInstances).length > 0")
            except Exception:
                pytest.fail(f"Source chart did not initialize; browser errors: {errors}")
            page.evaluate("fw.filterEngine.setFilter('conversions', 'test-region', 'region', 'equals', 'NL')")
            page.evaluate("void fw.captureForAnalysis()")
            page.locator(".fw-chart-container.fw-analysis-selectable").click()
            dialog = page.get_by_role("dialog")
            dialog.wait_for(state="visible")
            page.get_by_label("Note for your agent (optional)").fill("Investigate mobile conversion")
            page.screenshot(path=str(tmp_path / "capture-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(tmp_path / "capture-mobile.png"))
            assert dialog.evaluate("el => el.getBoundingClientRect().width <= innerWidth")
            page.route("**/analysis/capture", lambda route: route.abort())
            page.get_by_role("button", name="Send to agent", exact=True).click()
            page.get_by_role("status").filter(has_text="Retry or download").wait_for(state="visible")
            assert page.get_by_label("Note for your agent (optional)").input_value() == "Investigate mobile conversion"
            page.unroute("**/analysis/capture")
            page.get_by_role("button", name="Send to agent", exact=True).click()
            dialog.wait_for(state="detached")
            assert "Capture sent" in page.get_by_role("status").inner_text()
            batches = state.poll(0, "decision")[1]
            assert batches[0]["note"] == "Investigate mobile conversion"
            capture_id = batches[0]["items"][0]["data"]["capture_id"]
            import_pending_capture(str(target), capture_id, "conversion")
            provenance = json.loads((target / "evidence/conversion.json").read_text())
            assert provenance["filters"]["conversions"]["test-region"]["value"] == "NL"
            state.end_analysis()
            with page.expect_download() as downloaded:
                page.evaluate("void fw.captureForAnalysis()")
                page.locator(".fw-chart-container.fw-analysis-selectable").click()
            assert downloaded.value.suggested_filename.endswith(".trellum-capture.json")
            assert not page.get_by_role("dialog").count()
            assert not errors
        finally:
            browser.close()
