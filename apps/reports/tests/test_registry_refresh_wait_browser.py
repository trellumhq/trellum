"""Reload Registry runs as a durable action a worker has to claim. When no
current worker does (for instance one still on an older version during an
upgrade), Operations says so and hands the button back instead of waiting on
"Scheduled..." forever, and still reports the result if a worker claims it
later. Time is Playwright's clock, so the minute of waiting costs nothing."""
import re
from itertools import pairwise

from django.conf import settings
from playwright.sync_api import expect

from .test_operations_metrics_browser import _operations_fixture_html
from .test_portal_mobile_runtime import (
    CONSOLE_CSS,
    PORTAL_CSS,
    REPORT,
    UI_CSS,
    _ops_respond,
    mobile_browser,  # noqa: F401 -- pytest fixture
)

START = "2026-10-12T12:00:00Z"
ACTION_ID = "5b3f1c1e-2f0a-4b7e-9d52-6f2d1a9b7c10"
STATUS_URL = f"/s/acme/analytics/api/system/actions/{ACTION_ID}"
MESSAGES = {
    "accepted": "Registry refresh scheduled",
    "running": "Registry refresh running",
    "succeeded": "Registry refreshed",
}
WAITING = "Still waiting for a worker. Check that every worker runs this version."


class RefreshApi:
    """The admission and status endpoints, with the action in whatever state the test sets."""

    def __init__(self):
        self.state = "accepted"
        self.posts = 0
        self.polls = 0

    def payload(self):
        body = {
            "ok": True, "operation_id": ACTION_ID, "status_url": STATUS_URL, "kind": "registry_refresh",
            "state": self.state, "terminal": self.state == "succeeded", "message": MESSAGES[self.state],
            "accepted_at": START, "finished_at": START if self.state == "succeeded" else None,
        }
        if self.state == "succeeded":
            body.update(reports=3, warning="")
        return body

    def handle(self, route, path, reply):
        if path.endswith("/api/system/registry/refresh"):
            self.posts += 1
            reply({**self.payload(), "scheduled": True, "duplicate": self.posts > 1}, "application/json", 202)
        elif path.endswith(f"/api/system/actions/{ACTION_ID}"):
            self.polls += 1
            reply(self.payload())
        else:
            return False
        return True


def _operations(browser, api):
    css = {"ui.css": UI_CSS, "portal.css": PORTAL_CSS, "console-shell.css": CONSOLE_CSS}
    for name in ("themes.generated.css", "choice-picker.css", "console-report-host.css"):
        css[name] = (settings.BASE_DIR / "static" / name).read_text()

    def extra(route, path, query, reply):
        if path.startswith("/_vendor/"):
            asset = settings.BASE_DIR / "trellum" / "static" / "vendor" / path.removeprefix("/_vendor/")
            route.fulfill(
                content_type="text/css; charset=utf-8" if path.endswith(".css") else "font/ttf",
                body=asset.read_bytes(),
            )
            return True
        return api.handle(route, path, reply)

    context = browser.new_context(viewport={"width": 1280, "height": 844})
    page = context.new_page()
    page.clock.install(time=START)
    page.route("**/*", _ops_respond(
        lambda: [REPORT], lambda: {"running": {}, "queue": [], "server_uptime_seconds": 60},
        css=css, extra=extra, html=_operations_fixture_html(),
    ))
    page.goto("http://portal.test/s/acme/analytics/operations")
    page.locator('[data-ops-action="refresh"]').wait_for()
    # From here time moves only when the test moves it.
    page.clock.pause_at("2026-10-12T12:30:00Z")
    page.wait_for_timeout(100)
    return context, page


def _step(page, ms):
    """Advance the page's clock, then give an answered request time to be handled."""
    page.clock.run_for(ms)
    page.wait_for_timeout(80)


def _until_waiting_is_reported(page, api):
    """Click Reload Registry on an action no worker claims, and let the wait run out."""
    button = page.locator('[data-ops-action="refresh"]')
    button.click()
    expect(button).to_have_text("Scheduled…")
    _step(page, 800)
    _step(page, 1000)
    assert api.polls == 2
    page.clock.fast_forward(60000)
    page.wait_for_timeout(150)
    return button


def test_unclaimed_refresh_says_so_hands_the_button_back_and_still_reports_late(mobile_browser):  # noqa: F811
    api = RefreshApi()
    context, page = _operations(mobile_browser, api)
    try:
        button = page.locator('[data-ops-action="refresh"]')
        button.click()
        expect(button).to_have_text("Scheduled…")
        # The first minute is the usual quiet poll: 800 ms, then every second.
        _step(page, 799)
        assert api.polls == 0
        _step(page, 1)
        assert api.polls == 1
        _step(page, 1000)
        assert api.polls == 2
        expect(button).to_have_text("Scheduled…")
        assert "running" in button.get_attribute("class").split()
        assert page.locator(".toast.warning").count() == 0

        page.clock.fast_forward(60000)
        page.wait_for_timeout(150)
        expect(page.locator(".toast.warning")).to_have_text(WAITING)
        # The button works again, and nothing was announced as done or failed.
        expect(button).to_contain_text("Reload Registry")
        assert "running" not in button.get_attribute("class").split()
        assert page.locator(".toast.success, .toast.error").count() == 0
        assert api.posts == 1

        # The server keeps the action, so it is watched slowly and quietly.
        polls = api.polls
        _step(page, 14000)
        assert api.polls == polls
        _step(page, 1000)
        assert api.polls == polls + 1
        _step(page, 15000)
        assert api.polls == polls + 2
        assert page.locator(".toast.warning").count() == 0  # said once

        # A worker claims it later: the result shows as it always did.
        api.state = "succeeded"
        _step(page, 15000)
        expect(page.locator(".toast.success")).to_have_text("Registry refreshed (3 reports)")
        expect(button).to_contain_text("Reload Registry")
        late = api.polls
        _step(page, 60000)
        assert api.polls == late  # the watch ended with the result
    finally:
        context.close()


def test_reloading_again_after_the_wait_does_not_stack_pollers(mobile_browser):  # noqa: F811
    api = RefreshApi()
    context, page = _operations(mobile_browser, api)
    try:
        button = _until_waiting_is_reported(page, api)
        expect(page.locator(".toast.warning")).to_have_text(WAITING)

        button.click()
        expect(button).to_have_text("Scheduled…")
        assert api.posts == 2  # the server answers with the action it already holds
        counts = [api.polls]
        for _ in range(17):
            _step(page, 1000)
            counts.append(api.polls)
        steps = [after - before for before, after in pairwise(counts)]
        assert max(steps) == 1, steps  # one poller: never two requests in a step
        assert sum(steps) >= 4, steps  # and it keeps going
        # It waits another full minute before saying so again.
        assert page.locator(".toast.warning").count() == 0
        expect(button).to_have_text("Scheduled…")
        page.clock.fast_forward(60000)
        page.wait_for_timeout(150)
        expect(page.locator(".toast.warning")).to_have_text(WAITING)
        expect(button).to_contain_text("Reload Registry")
    finally:
        context.close()


def test_refresh_a_worker_claims_in_time_shows_its_result(mobile_browser):  # noqa: F811
    api = RefreshApi()
    context, page = _operations(mobile_browser, api)
    try:
        button = page.locator('[data-ops-action="refresh"]')
        button.click()
        expect(button).to_have_text("Scheduled…")
        api.state = "running"
        _step(page, 800)
        expect(button).to_have_text("Running…")
        # A scan that takes longer than the wait for a worker is not a missing worker.
        page.clock.fast_forward(90000)
        page.wait_for_timeout(150)
        expect(button).to_have_text("Running…")
        assert page.locator(".toast.warning").count() == 0

        api.state = "succeeded"
        _step(page, 3000)
        expect(page.locator(".toast.success")).to_have_text("Registry refreshed (3 reports)")
        expect(button).to_have_text(re.compile(r"Reload Registry"))
        assert page.locator(".toast.warning, .toast.error").count() == 0
        done = api.polls
        _step(page, 60000)
        assert api.polls == done
    finally:
        context.close()
