"""Review-mode tests: identity stamping, session state, HTTP protocol, CLI.

Run with:  python3 -m pytest trellum/testing/test_review.py -v
"""

from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

from trellum.components import LineChart, RawHTML, RenderContext
from trellum.components.base import Component
from trellum.rendering.html_builder import _render_section_list
from trellum.review import ReviewInactive, ReviewState, inject_overlay
from trellum.themes import DefaultTheme

# ── Helpers ──────────────────────────────────────────────────

def _make_ctx() -> RenderContext:
    return RenderContext(theme=DefaultTheme)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "event_date": ["2026-04-05", "2026-04-06", "2026-04-07"],
        "revenue": [100.0, 200.0, 150.0],
    })


# ── Build-time identity stamping (Step 2) ────────────────────

class TestSectionStamping:
    def test_section_div_carries_title_attribute(self):
        ctx = _make_ctx()
        parts = _render_section_list([{"title": "Daily Revenue", "components": []}], ctx)
        assert len(parts) == 1
        assert 'data-fw-section-title="Daily Revenue"' in parts[0]
        assert 'id="sec-daily-revenue"' in parts[0]

    def test_section_title_attribute_is_escaped(self):
        ctx = _make_ctx()
        parts = _render_section_list(
            [{"title": 'The "Best" & <Worst>', "components": []}], ctx
        )
        assert "data-fw-section-title=\"The &quot;Best&quot; &amp; &lt;Worst&gt;\"" in parts[0]

    def test_collapsible_section_also_stamped(self):
        ctx = _make_ctx()
        parts = _render_section_list(
            [{"title": "Deep Dive", "components": [], "collapsible": True}], ctx
        )
        assert 'data-fw-section-title="Deep Dive"' in parts[0]
        assert "fw-collapsible" in parts[0]

    def test_untitled_section_has_no_title_attribute(self):
        ctx = _make_ctx()
        parts = _render_section_list([{"title": "", "components": ["<p>x</p>"]}], ctx)
        assert "data-fw-section-title" not in parts[0]


class TestComponentStamping:
    def test_component_root_carries_kind_and_title(self):
        ctx = _make_ctx()
        chart = LineChart(_sample_df(), x="event_date", y="revenue", title="Daily Revenue")
        out = ctx.render_child(chart)
        assert 'id="fw_c1" data-fw-kind="LineChart" data-fw-title="Daily Revenue"' in out

    def test_component_title_is_escaped(self):
        ctx = _make_ctx()
        chart = LineChart(_sample_df(), x="event_date", y="revenue", title='A "B" <C>')
        out = ctx.render_child(chart)
        assert 'data-fw-title="A &quot;B&quot; &lt;C&gt;"' in out

    def test_second_component_gets_its_own_id(self):
        ctx = _make_ctx()
        df = _sample_df()
        ctx.render_child(LineChart(df, x="event_date", y="revenue", title="One"))
        out2 = ctx.render_child(LineChart(df, x="event_date", y="revenue", title="Two"))
        assert 'data-fw-kind="LineChart" data-fw-title="Two"' in out2
        assert 'id="fw_c1"' not in out2

    def test_rawhtml_root_is_stamped_too(self):
        # RawHTML wraps content in an id-carrying div, so it is addressable
        # like any other component.
        ctx = _make_ctx()
        out = ctx.render_child(RawHTML("<p>hello</p>"))
        assert 'id="fw_c1" data-fw-kind="RawHTML"' in out

    def test_component_without_id_is_not_stamped(self):
        class _NoId(Component):
            def render_html(self, ctx):
                return "<div class='custom'>no framework id</div>"

        ctx = _make_ctx()
        out = ctx.render_child(_NoId())
        assert "data-fw-kind" not in out
        assert out == "<div class='custom'>no framework id</div>"

    def test_component_hiding_its_allocated_id_is_not_stamped(self):
        # A component that allocates an id but does not put it in its markup
        # must be left untouched (the marker string simply never matches).
        class _HiddenId(Component):
            def render_html(self, ctx):
                ctx.next_id()
                return "<div>id allocated but unused</div>"

        ctx = _make_ctx()
        out = ctx.render_child(_HiddenId())
        assert "data-fw-kind" not in out

    def test_string_child_untouched(self):
        ctx = _make_ctx()
        assert ctx.render_child("<p>plain</p>") == "<p>plain</p>"


# ── Session state and protocol (Step 3) ──────────────────────

def _batch(prompt="make it a percentage", **over):
    b = {
        "slug": "insert-coin",
        "page": "/insert-coin/index.html",
        "session": 1,
        "note": "",
        "items": [{
            "prompt": prompt,
            "selector": "#fw_c2",
            "tag": "div",
            "text": "Daily Revenue",
            "section": {"id": "sec-revenue", "title": "Revenue"},
            "component": {"id": "fw_c2", "kind": "LineChart", "title": "Daily Revenue"},
        }],
        "ts": time.time(),
    }
    b.update(over)
    return b


class TestReviewState:
    def test_poll_drains_queue_immediately(self):
        st = ReviewState()
        st.start()
        st.submit(_batch())
        st.submit(_batch("second"))
        status, batches = st.poll(timeout=0)
        assert status == "feedback"
        assert len(batches) == 2
        # destructive read: queue is empty afterwards
        assert st.status()["queued"] == 0

    def test_poll_parks_until_feedback_arrives(self):
        st = ReviewState()
        st.start()
        result = {}

        def poller():
            result["r"] = st.poll(timeout=5)

        t = threading.Thread(target=poller)
        t.start()
        time.sleep(0.2)
        assert st.status()["agent"]["state"] == "listening"
        st.submit(_batch())
        t.join(timeout=5)
        assert not t.is_alive()
        status, batches = result["r"]
        assert status == "feedback"
        assert len(batches) == 1

    def test_poll_timeout_returns_waiting(self):
        st = ReviewState()
        st.start()
        t0 = time.monotonic()
        status, batches = st.poll(timeout=0.1)
        assert status == "waiting"
        assert batches is None
        assert time.monotonic() - t0 < 5

    def test_end_wakes_parked_poll_with_ended(self):
        st = ReviewState()
        st.start()
        result = {}

        def poller():
            result["r"] = st.poll(timeout=5)

        t = threading.Thread(target=poller)
        t.start()
        time.sleep(0.2)
        st.end()
        t.join(timeout=5)
        assert not t.is_alive()
        assert result["r"] == ("ended", None)
        # ended poll self-disables: a fresh start begins a new session
        assert st.status()["active"] is False

    def test_send_and_end_delivers_feedback_then_ended(self):
        st = ReviewState()
        st.start()
        st.submit(_batch(), end_session=True)
        status, batches = st.poll(timeout=0)
        assert status == "feedback"
        assert len(batches) == 1
        assert st.status()["ended"] is True
        assert st.poll(timeout=0) == ("ended", None)

    def test_start_is_idempotent_while_active_but_resets_after_end(self):
        st = ReviewState()
        s1 = st.start()
        st.submit(_batch())
        assert st.start() == s1                    # no reset mid-session
        assert st.status()["queued"] == 1
        st.end()
        st.poll(timeout=0)                         # consume ended, disable
        s2 = st.start()
        assert s2 == s1 + 1
        assert st.status()["queued"] == 0

    def test_feedback_refused_when_inactive(self):
        st = ReviewState()
        with pytest.raises(ReviewInactive):
            st.submit(_batch())
        st.start()
        st.end()
        with pytest.raises(ReviewInactive):
            st.submit(_batch())

    def test_last_delivered_survives_drain(self):
        st = ReviewState()
        st.start()
        st.submit(_batch("recover me"))
        _, batches = st.poll(timeout=0)
        assert st.last() == batches
        assert st.last()[0]["items"][0]["prompt"] == "recover me"

    def test_poll_with_slug_drains_only_that_report(self):
        st = ReviewState()
        st.start()
        st.submit(_batch("fix alpha", slug="alpha"))
        st.submit(_batch("fix beta", slug="beta"))
        status, batches = st.poll(timeout=0, slug="alpha")
        assert status == "feedback"
        assert [b["slug"] for b in batches] == ["alpha"]
        # beta's batch is untouched, waiting for beta's agent
        assert st.status()["queued"] == 1
        status, batches = st.poll(timeout=0, slug="beta")
        assert [b["slug"] for b in batches] == ["beta"]

    def test_slugless_poll_still_drains_everything(self):
        st = ReviewState()
        st.start()
        st.submit(_batch(slug="alpha"))
        st.submit(_batch(slug="beta"))
        _, batches = st.poll(timeout=0)
        assert len(batches) == 2

    def test_slug_poll_parks_past_other_reports_feedback(self):
        st = ReviewState()
        st.start()
        st.submit(_batch(slug="beta"))
        status, batches = st.poll(timeout=0.1, slug="alpha")
        assert status == "waiting"
        assert st.status()["queued"] == 1

    def test_reply_unlocks_working_state(self):
        st = ReviewState()
        st.start()
        st.submit(_batch())
        st.poll(timeout=0)
        assert st.status()["agent"]["state"] == "working"
        n = st.reply("done, rebuilt")
        assert n == 1
        assert st.status()["agent"]["state"] == "absent"
        assert st.status()["replies"][0]["text"] == "done, rebuilt"


class TestStateSurvivesServerSwap:
    """Dev servers get replaced (second agent session, --restart-server,
    crash); the session must not die with the process."""

    def test_successor_process_adopts_the_session(self, tmp_path):
        first = ReviewState()
        first.bind_dir(str(tmp_path))
        first.start()
        first.submit(_batch("survive me"))
        first.reply("hello", slug="alpha")

        second = ReviewState()               # fresh process, default state
        second.bind_dir(str(tmp_path))
        assert second.status()["active"] is True
        assert second.status()["session"] == first.session
        status, batches = second.poll(timeout=0)
        assert status == "feedback"
        assert batches[0]["items"][0]["prompt"] == "survive me"
        assert second.status()["replies"][0]["text"] == "hello"

    def test_ended_session_stays_ended_across_swap(self, tmp_path):
        first = ReviewState()
        first.bind_dir(str(tmp_path))
        first.start()
        first.end()
        second = ReviewState()
        second.bind_dir(str(tmp_path))
        assert second.status()["ended"] is True
        assert second.poll(timeout=0) == ("ended", None)

    def test_unbound_state_still_works_without_persistence(self):
        st = ReviewState()                   # never bound: pure in-memory
        st.start()
        st.submit(_batch())
        assert st.poll(timeout=0)[0] == "feedback"


class TestInjectOverlay:
    def test_injects_before_last_body_close(self):
        out = inject_overlay(b"<html><body><p>hi</p></body></html>")
        assert out == (
            b'<html><body><p>hi</p><script src="/_fw/review.js" defer>'
            b"</script></body></html>"
        )

    def test_appends_when_no_body_close(self):
        out = inject_overlay(b"<p>fragment</p>")
        assert out.startswith(b"<p>fragment</p>")
        assert b'<script src="/_fw/review.js" defer></script>' in out


# ── Server integration (Step 4) ──────────────────────────────

import json as _json
import socket
import urllib.error
import urllib.request

from trellum import review as review_mod

_REPORT_HTML = (
    '<!DOCTYPE html><html><head><title>alpha</title></head>'
    '<body data-report-slug="alpha"><div class="fw-section" id="sec-overview" '
    'data-fw-section-title="Overview"><h2>Overview</h2>'
    '<div id="fw_c1" data-fw-kind="KPIRow" style="padding:20px">metric: 42</div>'
    '</div></body></html>'
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _http(method, url, body=None, timeout=10):
    data = _json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture(scope="module")
def review_server(tmp_path_factory):
    """The REAL runner._serve_all wiring on a temp output tree."""
    import threading as _threading

    from trellum import runner

    out = tmp_path_factory.mktemp("review_output")
    report = out / "alpha"
    report.mkdir()
    (report / "index.html").write_text(_REPORT_HTML, encoding="utf-8")
    (report / "_meta.json").write_text('{"slug": "alpha", "last_run": "t1"}', encoding="utf-8")
    # A gallery index ON DISK: the per-request gallery branch must still win
    # over file serving, and must never be injected.
    (out / "index.html").write_text("<html><body>disk gallery</body></html>", encoding="utf-8")

    port = _free_port()
    t = _threading.Thread(target=runner._serve_all, args=(str(out), port), daemon=True)
    t.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            _http("GET", base + "/_fw/server.json", timeout=2)
            break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail("review test server did not come up")

    yield {"base": base, "out": out}
    # daemon thread; dies with the test process


@pytest.fixture(autouse=True)
def _fresh_state():
    review_mod.STATE.reset_for_tests()
    yield
    review_mod.STATE.reset_for_tests()


class TestServerIntegration:
    def test_html_served_verbatim_when_review_inactive(self, review_server):
        code, body = _http("GET", review_server["base"] + "/alpha/index.html")
        assert code == 200
        assert b"review.js" not in body
        assert body == _REPORT_HTML.encode()

    def test_start_injects_script_and_disk_is_unchanged(self, review_server):
        code, _ = _http("POST", review_server["base"] + "/_fw/review/start", {})
        assert code == 200
        code, body = _http("GET", review_server["base"] + "/alpha/index.html")
        assert code == 200
        assert b'<script src="/_fw/review.js" defer></script></body>' in body
        on_disk = (review_server["out"] / "alpha" / "index.html").read_bytes()
        assert b"review.js" not in on_disk

    def test_directory_url_is_injected_too(self, review_server):
        _http("POST", review_server["base"] + "/_fw/review/start", {})
        code, body = _http("GET", review_server["base"] + "/alpha/")
        assert code == 200
        assert b"review.js" in body

    def test_gallery_index_is_never_injected(self, review_server):
        _http("POST", review_server["base"] + "/_fw/review/start", {})
        for path in ("/", "/index.html"):
            code, body = _http("GET", review_server["base"] + path)
            assert code == 200
            assert b"review.js" not in body, f"gallery at {path} must not get the overlay"

    def test_review_js_served_with_js_content_type(self, review_server):
        req = urllib.request.Request(review_server["base"] + "/_fw/review.js")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert "javascript" in resp.headers.get("Content-Type", "")
            assert b"_fwReviewInit" in resp.read()

    def test_status_data_version_changes_when_meta_rewritten(self, review_server):
        import os as _os

        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        meta = review_server["out"] / "alpha" / "_meta.json"

        _, body = _http("GET", base + "/_fw/review/status?slug=alpha")
        v1 = _json.loads(body)["data_version"]
        assert v1 is not None

        st = _os.stat(meta)
        _os.utime(meta, (st.st_atime, st.st_mtime + 10))
        _, body = _http("GET", base + "/_fw/review/status?slug=alpha")
        v2 = _json.loads(body)["data_version"]
        assert v2 != v1

    def test_full_loop_feedback_poll_reply_status(self, review_server):
        base = review_server["base"]
        code, body = _http("POST", base + "/_fw/review/start", {})
        assert code == 200

        code, body = _http("POST", base + "/_fw/review/feedback", {
            "slug": "alpha", "page": "/alpha/index.html", "session": 1,
            "note": "overall: tighter", "items": _batch()["items"],
        })
        assert code == 200
        assert _json.loads(body)["queued"] == 1

        code, body = _http("GET", base + "/_fw/review/poll?timeout=0")
        assert code == 200
        polled = _json.loads(body)
        assert polled["status"] == "feedback"
        assert polled["batches"][0]["note"] == "overall: tighter"
        assert polled["batches"][0]["items"][0]["component"]["kind"] == "LineChart"

        _, body = _http("GET", base + "/_fw/review/status?slug=alpha")
        assert _json.loads(body)["agent"]["state"] == "working"

        code, body = _http("POST", base + "/_fw/review/reply", {"text": "done - rebuilt"})
        assert code == 200
        _, body = _http("GET", base + "/_fw/review/status?slug=alpha")
        status = _json.loads(body)
        assert status["agent"]["state"] == "absent"
        assert status["replies"][0]["text"] == "done - rebuilt"

        # crash recovery: the delivered batch is still readable
        _, body = _http("GET", base + "/_fw/review/last")
        assert _json.loads(body)["status"] == "feedback"

        code, _ = _http("POST", base + "/_fw/review/end", {})
        assert code == 200
        _, body = _http("GET", base + "/_fw/review/poll?timeout=0")
        assert _json.loads(body)["status"] == "ended"

    def test_injection_stops_the_moment_the_session_ends(self, review_server):
        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        _, body = _http("GET", base + "/alpha/index.html")
        assert b"review.js" in body
        _http("POST", base + "/_fw/review/end", {})
        # No poll has consumed the ended state -- a fresh page load must
        # still come back clean, without the overlay.
        _, body = _http("GET", base + "/alpha/index.html")
        assert b"review.js" not in body
        assert body == _REPORT_HTML.encode()

    def test_slug_tagged_replies_reach_only_their_report(self, review_server):
        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        _http("POST", base + "/_fw/review/reply", {"text": "to everyone"})
        _http("POST", base + "/_fw/review/reply", {"text": "alpha only", "slug": "alpha"})
        _, body = _http("GET", base + "/_fw/review/status?slug=alpha")
        texts = [r["text"] for r in _json.loads(body)["replies"]]
        assert texts == ["to everyone", "alpha only"]
        _, body = _http("GET", base + "/_fw/review/status?slug=beta")
        texts = [r["text"] for r in _json.loads(body)["replies"]]
        assert texts == ["to everyone"]
        # the CLI's slug-less status sees everything
        _, body = _http("GET", base + "/_fw/review/status")
        assert len(_json.loads(body)["replies"]) == 2

    def test_feedback_refused_when_inactive_over_http(self, review_server):
        code, body = _http("POST", review_server["base"] + "/_fw/review/feedback", {
            "slug": "alpha", "items": [],
        })
        assert code == 409

    def test_unknown_review_route_404s(self, review_server):
        code, _ = _http("GET", review_server["base"] + "/_fw/review/nonsense")
        assert code == 404

    @pytest.mark.parametrize("mode", ["single", "all"])
    def test_review_keeps_live_query_flags(self, mode, tmp_path, monkeypatch):
        from trellum import runner
        from trellum.review import http as review_http
        from trellum.review import inject as review_inject
        from trellum.review.state import ReviewState
        from trellum.runner import serve as serve_mod

        # The process-global STATE binds its served directory once. Give this
        # test its own instance so its temporary path cannot leak to siblings.
        isolated_state = ReviewState()
        monkeypatch.setattr(review_mod, "STATE", isolated_state)
        monkeypatch.setattr(review_http, "STATE", isolated_state)
        monkeypatch.setattr(review_inject, "STATE", isolated_state)

        output = tmp_path / "out"
        report = output if mode == "single" else output / "alpha"
        report.mkdir(parents=True)
        html = b"<html><head></head><body><script>runtime</script>report</body></html>"
        (report / "index.html").write_bytes(html)
        (report / "_live_queries.json").write_text(
            '{"version": 1, "queries": {"q": {}}}', encoding="utf-8"
        )

        servers = []
        serve_forever = serve_mod._ReuseHTTPServer.serve_forever

        def tracked_serve_forever(server, *args, **kwargs):
            servers.append(server)
            return serve_forever(server, *args, **kwargs)

        monkeypatch.setattr(
            serve_mod._ReuseHTTPServer, "serve_forever", tracked_serve_forever
        )
        port = _free_port()
        serve = runner._serve if mode == "single" else runner._serve_all
        served_dir = output if mode == "all" else report
        thread = threading.Thread(
            target=serve, args=(str(served_dir), port), daemon=True
        )
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                try:
                    _http("GET", base + "/_fw/server.json", timeout=1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail(f"{mode} server did not start")

            path = "/index.html" if mode == "single" else "/alpha/index.html"
            directory_path = "/" if mode == "single" else "/alpha/"
            live_url = "/_fw/live-query" if mode == "single" else "/alpha/_fw/live-query"
            disk_bytes = (report / "index.html").read_bytes()

            code, body = _http("GET", base + path)
            assert code == 200 and b"_fwHasHost" in body
            assert body.count(live_url.encode()) == 1
            assert body.index(b"_fwHasHost") < body.index(b"</head>") < body.index(b"runtime")
            assert b"review.js" not in body

            assert _http("POST", base + "/_fw/review/start", {})[0] == 200
            code, body = _http("GET", base + directory_path)
            assert code == 200
            assert body.count(b"_fwHasHost") == 1 and body.count(b"review.js") == 1
            assert body.count(live_url.encode()) == 1
            assert body.index(b"_fwHasHost") < body.index(b"</head>") < body.index(b"runtime")

            assert _http("POST", base + "/_fw/review/end", {})[0] == 200
            code, body = _http("GET", base + path)
            assert code == 200 and b"_fwHasHost" in body
            assert live_url.encode() in body and b"review.js" not in body
            assert (report / "index.html").read_bytes() == disk_bytes
        finally:
            if servers:
                servers[0].shutdown()
                servers[0].server_close()
            thread.join(timeout=5)
            isolated_state.reset_for_tests()


class TestLoopbackOnly:
    class _FakeHandler:
        """Just enough of BaseHTTPRequestHandler for handle_review_request."""

        def __init__(self, path, command="GET", client=("10.0.0.5", 4242)):
            import io

            self.path = path
            self.command = command
            self.client_address = client
            self.headers = {}
            self.rfile = io.BytesIO(b"")
            self.wfile = io.BytesIO()
            self.status = None

        def send_response(self, code):
            self.status = code

        def send_header(self, *a):
            pass

        def end_headers(self):
            pass

        def send_error(self, code):
            self.status = code

    def test_non_loopback_is_refused(self):
        h = self._FakeHandler("/_fw/review/status", client=("10.0.0.5", 4242))
        assert review_mod.handle_review_request(h, ".") is True
        assert h.status == 403

    def test_loopback_is_allowed(self):
        review_mod.STATE.reset_for_tests()
        h = self._FakeHandler("/_fw/review/status", client=("127.0.0.1", 4242))
        assert review_mod.handle_review_request(h, ".") is True
        assert h.status == 200

    def test_non_review_path_is_ignored(self):
        h = self._FakeHandler("/alpha/index.html")
        assert review_mod.handle_review_request(h, ".") is False
        assert h.status is None


# ── The overlay in a real browser ────────────────────────────
# CI installs Chromium and forbids silent skips; locally this skips the
# way every other Playwright suite does.

class TestOverlayInRealBrowser:
    def test_select_queue_send_arrives_at_poll(self, review_server):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            pytest.skip("Playwright not installed")

        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/alpha/index.html", wait_until="networkidle")
            page.wait_for_selector("#fwrvBar")

            page.click("#fwrvSelect")
            comp = page.locator("#fw_c1")
            comp.hover()                       # sets the snap target
            comp.click()
            page.wait_for_selector("#fwrvPop", state="visible")
            page.fill("#fwrvPopText", "browser smoke test")
            page.click("#fwrvPopQueue")

            # queueing keeps select mode on and grows the toolbar Send
            page.wait_for_selector("#fwrvBarSend", state="visible")
            page.click("#fwrvBarSend")
            # badge resets only after the POST succeeded
            page.wait_for_selector("#fwrvBarSend", state="hidden")
            browser.close()
            assert errors == [], f"overlay raised in-browser: {errors}"

        code, body = _http("GET", base + "/_fw/review/poll?timeout=5")
        polled = _json.loads(body)
        assert polled["status"] == "feedback"
        item = polled["batches"][0]["items"][0]
        assert item["prompt"] == "browser smoke test"
        assert item["component"]["kind"] == "KPIRow"
        assert item["section"]["title"] == "Overview"


# ── CLI (Step 5) ─────────────────────────────────────────────

from trellum import cli as cli_mod
from trellum.cli.commands import review as review_cmd


def _server_port(review_server) -> int:
    return int(review_server["base"].rsplit(":", 1)[1])


class TestReviewCli:
    def test_poll_without_server_explains_and_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: None)
        code = cli_mod.main(["review", "poll"])
        assert code == 1
        assert "review start" in capsys.readouterr().err

    def test_poll_json_and_exit_codes(self, review_server, monkeypatch, capsys):
        port = _server_port(review_server)
        monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: port)
        base = review_server["base"]

        # nothing queued, tight timeout -> exit 2
        assert cli_mod.main(["review", "poll", "--timeout", "0.3", "--json"]) == 2
        capsys.readouterr()

        # queued feedback -> exit 0, JSON payload on stdout
        _http("POST", base + "/_fw/review/start", {})
        _http("POST", base + "/_fw/review/feedback", {
            "slug": "alpha", "note": "hi", "items": _batch()["items"],
        })
        assert cli_mod.main(["review", "poll", "--json"]) == 0
        out = _json.loads(capsys.readouterr().out)
        assert out["status"] == "feedback"
        assert out["batches"][0]["note"] == "hi"

        # ended -> exit 3
        _http("POST", base + "/_fw/review/end", {})
        assert cli_mod.main(["review", "poll"]) == 3

    def test_poll_human_output_names_the_component(self, review_server,
                                                   monkeypatch, capsys):
        port = _server_port(review_server)
        monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: port)
        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        _http("POST", base + "/_fw/review/feedback", {
            "slug": "alpha", "items": _batch("make it weekly")["items"],
        })
        assert cli_mod.main(["review", "poll"]) == 0
        out = capsys.readouterr().out
        assert 'LineChart "Daily Revenue"' in out
        assert 'section "Revenue"' in out
        assert "make it weekly" in out
        assert "--no-serve" in out          # the rebuild instruction

    def test_poll_prints_chart_data_targets(self, review_server, monkeypatch, capsys):
        port = _server_port(review_server)
        monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: port)
        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        items = _batch("why is this so high")["items"]
        items[0]["data"] = {"type": "chart-point", "series": "Arcade floor",
                            "x": "2026-06-14", "value": 104213}
        items.append(dict(items[0], prompt="this whole window looks off",
                          data={"type": "chart-range", "x_from": "2026-05-25",
                                "x_to": "2026-06-02",
                                "series": [{"label": "Online", "n": 9,
                                            "min": 40, "max": 90, "points": []}]}))
        _http("POST", base + "/_fw/review/feedback", {"slug": "alpha", "items": items})
        assert cli_mod.main(["review", "poll"]) == 0
        out = capsys.readouterr().out
        assert "data point: Arcade floor @ 2026-06-14 = 104213" in out
        assert "data range: 2026-05-25 -> 2026-06-02 (1 series)" in out
        assert "Online: n=9 min=40 max=90" in out

    def test_poll_report_flag_leaves_other_reports_queued(self, review_server,
                                                          monkeypatch, capsys):
        port = _server_port(review_server)
        monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: port)
        base = review_server["base"]
        _http("POST", base + "/_fw/review/start", {})
        _http("POST", base + "/_fw/review/feedback",
              {"slug": "alpha", "items": _batch("alpha ask")["items"]})
        _http("POST", base + "/_fw/review/feedback",
              {"slug": "beta", "items": _batch("beta ask")["items"]})
        assert cli_mod.main(["review", "poll", "--report", "alpha"]) == 0
        out = capsys.readouterr().out
        assert "alpha ask" in out
        assert "beta ask" not in out
        _, body = _http("GET", base + "/_fw/review/status")
        assert _json.loads(body)["queued"] == 1     # beta still waiting

    def test_reply_lands_in_status(self, review_server, monkeypatch, capsys):
        port = _server_port(review_server)
        monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: port)
        _http("POST", review_server["base"] + "/_fw/review/start", {})
        assert cli_mod.main(["review", "reply", "on it"]) == 0
        capsys.readouterr()
        assert cli_mod.main(["review", "status", "--json"]) == 0
        status = _json.loads(capsys.readouterr().out)
        assert status["replies"][0]["text"] == "on it"

    def test_start_builds_when_output_missing(self, tmp_path, monkeypatch, capsys):
        # A minimal project: config.yaml root, one report, no output yet.
        (tmp_path / "config.yaml").write_text("project: t\n", encoding="utf-8")
        rdir = tmp_path / "reports" / "alpha"
        rdir.mkdir(parents=True)
        (rdir / "report.yaml").write_text("slug: alpha\ntitle: Alpha\n",
                                          encoding="utf-8")

        import trellum.project as project_mod
        import trellum.runner as runner_mod

        monkeypatch.setattr(project_mod, "get_project_root",
                            lambda *a, **k: str(tmp_path))
        built = []
        monkeypatch.setattr(runner_mod, "run_report",
                            lambda d, *a, **k: built.append(d))
        monkeypatch.setattr(review_cmd, "_ensure_background_server",
                            lambda *a, **k: (8050, True))
        calls = []

        def fake_http(port, method, path, payload=None, timeout=10.0):
            calls.append((method, path))
            return 200, {"ok": True, "session": 7}

        monkeypatch.setattr(review_cmd, "_review_http", fake_http)

        code = cli_mod.main(["review", "start",
                             str(rdir), "--no-browser"])
        assert code == 0
        assert built and built[0] == str(rdir)
        assert ("POST", "/_fw/review/start") in calls
        out = capsys.readouterr().out
        assert "review poll" in out
        assert "/alpha/index.html" in out

        # second start with output present: no rebuild without --rebuild
        outdir = tmp_path / "output" / "alpha"
        outdir.mkdir(parents=True)
        (outdir / "index.html").write_text("<html></html>", encoding="utf-8")
        built.clear()
        assert cli_mod.main(["review", "start", str(rdir), "--no-browser"]) == 0
        assert built == []

    def test_review_bare_prints_overview(self, capsys):
        assert cli_mod.main(["review"]) == 0
        out = capsys.readouterr().out
        assert "review poll" in out
        assert "review start" in out


# ── Drift guards (Step 6): the feature must stay advertised ──

class TestReviewStaysAdvertised:
    def test_describe_advertises_review(self):
        out = cli_mod._describe()
        assert "trellum review start" in out
        assert "trellum review poll" in out

    def test_guide_topic_review_resolves(self):
        from trellum.agent import agentdoc

        assert "review" in agentdoc.TOPICS
        section = agentdoc.guide("review")
        assert section is not None, "AGENTS.md lost its <!-- topic: review --> anchor"
        assert "review poll" in section.body
        assert "--no-serve" in section.body       # the rebuild instruction
        assert "--reply" in section.body

    def test_readme_documents_review(self):
        from trellum.agent.agentdoc import PACKAGE_ROOT

        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        assert "### `python -m trellum review`" in readme
