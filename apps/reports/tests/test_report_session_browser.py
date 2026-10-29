"""Signed-in report pages: viewers send no share-management request, and an
expired session sends the whole window (including the console shell) to the
login page once, with the report as the return page.

The portal's own session runtime (static/report_session.js) is loaded exactly
as the server splices it into a response; the framework's real auto-refresh
runtime performs the polling. Only the network is mocked.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings

from apps.reports.tests.test_console_report_host_runtime import (
    _catalog_html,
    _report_html as _console_report_html,
)
from apps.reports.views import _report_session_script

STATIC = settings.BASE_DIR / "static"
MENU_JS = (STATIC / "report_menu.js").read_text(encoding="utf-8")
SHARE_JS = (STATIC / "report_share.js").read_text(encoding="utf-8")
REFRESH_JS = (
    settings.BASE_DIR / "trellum" / "static" / "js" / "runtime" / "auto_refresh.js"
).read_text(encoding="utf-8")

REPORT_PATH = "/s/demo/casino/r/player-overview/index.html"
REPORT_URL = f"http://reports.test{REPORT_PATH}?range=30d#revenue"
SHARE_LINKS = "/s/demo/casino/api/reports/player-overview/share_links"

# What the framework runtime expects from the rest of the page, with a refresh
# fast enough to observe several rounds.
RUNTIME_STUBS = (
    "<script>var _fwConfig={refreshMs:120};function _saveState(){return {}}"
    "function _restoreState(){}</script>"
    f"<script>{REFRESH_JS}</script>"
)


def _session(developer: bool) -> str:
    return _report_session_script(developer=developer).decode("utf-8")


def _standalone_report(developer: bool, *, refresh: bool = True) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">{_session(developer)}</head><body>
<div class="fw-header"><div class="fw-header-left"><h1>Player overview</h1></div>
 <div class="fw-header-right"><button id="reportOption">Options</button></div></div>
<main id="reportBody">Report body</main>
{RUNTIME_STUBS if refresh else ""}
<script>{MENU_JS}</script><script>{SHARE_JS}</script></body></html>"""


def _console_report(developer: bool) -> str:
    html = _console_report_html().replace(
        '<head><meta charset="utf-8">',
        f'<head><meta charset="utf-8">{_session(developer)}',
        1,
    )
    return html.replace("</body>", RUNTIME_STUBS + "</body>")


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def report_browser(request):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        try:
            browser = getattr(playwright, request.param).launch(headless=True)
        except Error as exc:
            pytest.skip(f"{request.param} unavailable: {exc}")
        yield browser
        browser.close()


class Site:
    """Routes for one page: a mutable data.json outcome plus request logs."""

    def __init__(self, page, *, developer=False, console=False, sharing_enabled=True):
        self.mode = "ok"
        self.developer = developer
        self.console = console
        self.sharing_enabled = sharing_enabled
        self.links = []
        self.data_requests = 0
        self.login_requests = []
        self.share_requests = []
        self.revokes = []
        self.warnings = []
        self.cross_origin_status = None
        page.on("console", lambda msg: self.warnings.append(msg.text) if msg.type == "warning" else None)
        page.route("**/*", self._route)
        self.page = page

    def _route(self, route):
        request = route.request
        url = urlsplit(request.url)
        if url.path == "/login":
            self.login_requests.append((request.url, request.frame == self.page.main_frame))
            route.fulfill(content_type="text/html", body="<p>Sign in</p>")
        elif url.hostname == "other.test":
            route.fulfill(status=self.cross_origin_status or 200, content_type="text/plain", body="x")
        elif url.path.endswith("/data.json"):
            self.data_requests += 1
            self._data(route)
        elif url.path.endswith("/share_links"):
            self.share_requests.append(request.method)
            route.fulfill(content_type="application/json", body=json.dumps({
                "share_links": self.links,
                "sharing_enabled": self.sharing_enabled,
                "can_manage_policy": True,
                "require_password": False,
                "max_expiry_days": None,
                "embed_links_enabled": True,
            }))
        elif url.path.endswith("/revoke"):
            self.revokes.append(url.path)
            link = dict(self.links[0], revoked_at="2026-10-29T10:00:00Z")
            route.fulfill(content_type="application/json", body=json.dumps({"ok": True, "share_link": link}))
        elif "/r/broken/" in url.path:
            route.fulfill(content_type="text/html", body="<p>never ready</p>")
        elif "/r/" in url.path:
            body = (_console_report if self.console and request.frame != self.page.main_frame
                    else _standalone_report)(self.developer)
            route.fulfill(content_type="text/html", body=body)
        elif self.console:
            route.fulfill(content_type="text/html", body=_catalog_html())
        else:
            route.fulfill(content_type="text/html", body="<p>other</p>")

    def _data(self, route):
        if self.mode == "401":
            route.fulfill(status=401, content_type="application/json",
                          body='{"error": "session_expired", "reason": "idle"}')
        elif self.mode == "redirect":
            route.fulfill(status=302, headers={
                "location": "/login?reason=idle&next=" + "%2Fs%2Fdemo%2Fcasino%2Fr%2Fplayer-overview%2Fdata.json",
            })
        elif self.mode in ("403", "500"):
            route.fulfill(status=int(self.mode), content_type="application/json", body="{}")
        elif self.mode == "offline":
            route.abort()
        else:
            route.fulfill(content_type="application/json", body='{"components": {}}')

    def wait_for_login(self):
        self.page.wait_for_url(re.compile(r"/login\?"), timeout=8000)
        return parse_qs(urlsplit(self.page.url).query)

    def wait_for_refreshes(self, count=3):
        start = self.data_requests
        deadline_ms = 6000
        while self.data_requests < start + count and deadline_ms > 0:
            self.page.wait_for_timeout(50)
            deadline_ms -= 50
        assert self.data_requests >= start + count


@pytest.fixture
def open_page(report_browser):
    contexts = []

    def _open(url, **site_options):
        context = report_browser.new_context(viewport={"width": 1100, "height": 760})
        contexts.append(context)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        site = Site(page, **site_options)
        return page, site, errors

    yield _open
    for context in contexts:
        context.close()


# ── expired session ───────────────────────────────────────────────────────

def test_expired_refresh_sends_the_window_to_login_with_the_report_as_return_page(open_page):
    page, site, errors = open_page(REPORT_URL)
    site.mode = "401"
    page.goto(REPORT_URL, wait_until="load")
    query = site.wait_for_login()
    assert query["next"] == [f"{REPORT_PATH}?range=30d#revenue"]
    assert len(site.login_requests) == 1
    assert not any("Refresh failed" in text for text in site.warnings)
    assert errors == []


@pytest.fixture
def redirecting_portal():
    """A real HTTP server that, like the portal, answers an expired session's
    plain GET with a redirect to /login -- which a mocked route cannot do."""
    logins = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = urlsplit(self.path).path
            if path == REPORT_PATH:
                self._send(200, "text/html", _standalone_report(False))
            elif path.endswith("/data.json"):
                self.send_response(302)
                self.send_header(
                    "Location",
                    "/login?reason=idle&next=%2Fs%2Fdemo%2Fcasino%2Fr%2Fplayer-overview%2Fdata.json",
                )
                self.end_headers()
            elif path == "/login":
                logins.append(self.path)
                self._send(200, "text/html", "<p>Sign in</p>")
            else:
                self._send(404, "text/plain", "missing")

        def _send(self, status, content_type, body):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", logins
    server.shutdown()
    server.server_close()


def test_expired_session_redirect_to_login_is_recognised(report_browser, redirecting_portal):
    base, logins = redirecting_portal
    context = report_browser.new_context()
    try:
        page = context.new_page()
        warnings = []
        page.on("console", lambda msg: warnings.append(msg.text) if msg.type == "warning" else None)
        page.goto(f"{base}{REPORT_PATH}?range=30d#revenue", wait_until="load")
        page.wait_for_url(re.compile(r"/login\?"), timeout=8000)
        query = parse_qs(urlsplit(page.url).query)
        # The return page is the report, not the data.json the portal named.
        assert query["next"] == [f"{REPORT_PATH}?range=30d#revenue"]
        assert query["reason"] == ["idle"]
        # One request is the redirected fetch itself; the other is the single
        # navigation carrying the report as its return page.
        assert [path for path in logins if "index.html" in path] == [logins[-1]]
        assert len(logins) == 2
        assert not any("Refresh failed" in text for text in warnings)
    finally:
        context.close()


def test_concurrent_expired_requests_redirect_once_and_stay_unconsumed(open_page):
    page, site, _errors = open_page(REPORT_URL)
    page.goto(REPORT_URL, wait_until="load")
    settled = []
    page.expose_function("settled", lambda outcome: settled.append(outcome))
    site.mode = "401"
    # A refresh, a live-query style POST and a stray widget call all hit the
    # dead session together.
    page.evaluate("""() => {
        const note = outcome => () => window.settled(outcome);
        fetch('data.json').then(note('refresh'), note('refresh-error'));
        fetch('data.json', {method: 'POST', body: '{}'}).then(note('query'), note('query-error'));
        fetch('data.json?widget=1', {credentials: 'same-origin'}).then(note('widget'), note('widget-error'));
    }""")
    site.wait_for_login()
    assert len(site.login_requests) == 1
    # No caller is handed the dead response. (A browser may cancel a request
    # still in flight when the page unloads; that rejection is not a result.)
    assert [outcome for outcome in settled if not outcome.endswith("-error")] == []


def test_aborted_request_still_rejects_while_the_page_leaves(open_page):
    page, site, _errors = open_page(REPORT_URL)
    page.goto(REPORT_URL, wait_until="load")
    site.mode = "401"
    outcomes = []
    page.expose_function("outcome", lambda value: outcomes.append(value))
    page.evaluate("""() => {
        const controller = new AbortController();
        fetch('data.json?x=1', {signal: controller.signal})
            .then(() => window.outcome('resolved'), error => window.outcome(error.name));
        setTimeout(() => controller.abort(), 0);
    }""")
    site.wait_for_login()
    assert "resolved" not in outcomes


@pytest.mark.parametrize("mode", ["403", "500", "offline"])
def test_ordinary_failures_do_not_leave_the_report(open_page, mode):
    page, site, errors = open_page(REPORT_URL)
    site.mode = mode
    page.goto(REPORT_URL, wait_until="load")
    site.wait_for_refreshes(3)  # keeps polling quietly instead of being parked
    assert site.login_requests == []
    assert page.url == REPORT_URL
    assert any("Refresh failed" in text for text in site.warnings)
    site.mode = "ok"
    before = site.data_requests
    site.wait_for_refreshes(1)
    assert site.data_requests > before
    assert errors == []


def test_cross_origin_401_is_not_a_session_expiry(open_page):
    page, site, _errors = open_page(REPORT_URL)
    site.cross_origin_status = 401
    page.goto(REPORT_URL, wait_until="load")
    status = page.evaluate("fetch('http://other.test/api', {mode: 'no-cors'}).then(r => r.status, () => 'blocked')")
    assert status in (0, "blocked")
    assert site.login_requests == []


def test_page_without_the_portal_runtime_is_untouched(open_page):
    page, site, _errors = open_page(REPORT_URL)
    page.route("**/s/demo/casino/r/**/index.html*", lambda route: route.fulfill(
        content_type="text/html",
        body="<!doctype html><html><head></head><body><p id='ok'>public</p></body></html>",
    ))
    page.goto(REPORT_URL, wait_until="load")
    assert page.evaluate("typeof window.TrellumReportSession") == "undefined"
    site.mode = "401"
    status = page.evaluate("fetch('data.json').then(r => r.status)")
    assert status == 401
    assert site.login_requests == []


def test_console_host_report_navigates_the_whole_shell(open_page):
    page, site, errors = open_page("http://console.test/s/demo/casino/?view=list", console=True)
    page.goto("http://console.test/s/demo/casino/?view=list", wait_until="load")
    page.locator("#report").click()
    frame_handle = page.locator("[data-console-report-frame]")
    frame_handle.wait_for(state="visible")
    shell_location = page.evaluate("location.pathname + location.search + location.hash")
    assert "_console_host" not in shell_location

    site.mode = "401"
    query = site.wait_for_login()
    # The login request is the shell's own navigation, not the iframe's.
    assert site.login_requests == [(site.login_requests[0][0], True)]
    assert len(site.login_requests) == 1
    assert query["next"] == [shell_location]
    assert "_console_host" not in query["next"][0]
    assert errors == []


def test_console_host_ordinary_failure_keeps_the_shell(open_page):
    page, site, _errors = open_page("http://console.test/s/demo/casino/?view=list", console=True)
    page.goto("http://console.test/s/demo/casino/?view=list", wait_until="load")
    page.locator("#report").click()
    page.locator("[data-console-report-frame]").wait_for(state="visible")
    site.mode = "403"
    site.wait_for_refreshes(3)
    assert site.login_requests == []
    assert page.locator("[data-console-report-frame]").is_visible()


def test_replaced_console_report_cannot_move_the_shell(open_page):
    page, site, _errors = open_page("http://console.test/s/demo/casino/?view=list", console=True)
    page.goto("http://console.test/s/demo/casino/?view=list", wait_until="load")
    page.locator("#report").click()
    page.locator("[data-console-report-frame]").wait_for(state="visible")
    old_frame = page.frames[-1]
    # Same-origin parent no longer vouches for this window once it is replaced.
    page.evaluate("document.querySelector('[data-console-report-frame]').src = 'about:blank'")
    page.wait_for_function("document.querySelector('[data-console-report-frame]').contentWindow.location.href === 'about:blank'")
    assert old_frame.url != "http://console.test/login"
    site.mode = "401"
    page.wait_for_timeout(500)
    assert site.login_requests == []


# ── share-management capability ───────────────────────────────────────────

def test_viewer_sends_no_share_management_request(open_page):
    page, site, errors = open_page(REPORT_URL)
    page.goto(REPORT_URL, wait_until="load")
    site.wait_for_refreshes(2)
    assert site.share_requests == []
    assert page.locator('[data-item-id="share"]').count() == 0
    assert errors == []


def test_viewer_in_console_host_sends_no_share_management_request(open_page):
    page, site, _errors = open_page("http://console.test/s/demo/casino/?view=list", console=True)
    page.goto("http://console.test/s/demo/casino/?view=list", wait_until="load")
    page.locator("#report").click()
    page.locator("[data-console-report-frame]").wait_for(state="visible")
    site.wait_for_refreshes(2)
    assert site.share_requests == []


def test_developer_manages_share_while_sharing_is_disabled(open_page):
    link = {
        "id": 7, "url": "http://reports.test/share/tok/", "created_by": "Dev",
        "created_at": "2026-10-01T09:00:00Z", "expires_at": None, "allow_export": False,
        "has_password": False, "embed": False, "embed_origins": [], "embed_theme": "",
        "embed_hide_filters": False, "revoked_at": None, "view_count": 2,
        "last_viewed_at": None, "blocked_by_policy": False,
    }
    page, site, errors = open_page(REPORT_URL, developer=True, sharing_enabled=False)
    site.links = [link]
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(REPORT_URL, wait_until="load")
    page.locator("#fwOptionsBtn").click()
    page.locator('[data-item-id="share"]').click()
    page.get_by_text("Public share links are disabled for this organization.").wait_for()
    assert site.share_requests == ["GET"]
    page.locator("#rswToggleInactive").click()
    page.locator("[data-revoke]").click()
    page.get_by_text("Revoked").first.wait_for()
    assert site.revokes == ["/api/share_links/7/revoke"]
    assert errors == []
