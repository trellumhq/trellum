"""A report whose auto-refresh is paused says so in the default list view and
in Operations, where it must not advertise a next run it will not make."""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from django.conf import settings

from .test_operations_metrics_browser import _operations_fixture_html
from .test_portal_mobile_runtime import (
    CONSOLE_CSS,
    CONSOLE_JS,
    PORTAL_CSS,
    PORTAL_HTML,
    PORTAL_JS,
    REPORT,
    UI_CSS,
    mobile_browser,  # noqa: F401 -- pytest fixture
)

PAUSED = {
    **REPORT, "slug": "paused-report", "name": "Paused report",
    "schedule": "0 6 * * *", "refresh_suspended_at": "2026-09-01T06:00:00Z",
}
ACTIVE = {
    **REPORT, "id": 2, "slug": "active-report", "name": "Active report",
    "schedule": "0 6 * * *", "refresh_suspended_at": None,
}


def _route(page, html, status):
    assets = {
        "/static/portal.js": PORTAL_JS, "/static/console-shell.js": CONSOLE_JS,
        "/static/ui.css": UI_CSS, "/static/portal.css": PORTAL_CSS,
        "/static/console-shell.css": CONSOLE_CSS,
    }
    for name in ("themes.generated.css", "choice-picker.css", "console-report-host.css"):
        assets[f"/static/{name}"] = (settings.BASE_DIR / "static" / name).read_text()

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/_vendor/"):
            asset = settings.BASE_DIR / "trellum" / "static" / "vendor" / path.removeprefix("/_vendor/")
            route.fulfill(
                content_type="text/css; charset=utf-8" if path.endswith(".css") else "font/ttf",
                body=asset.read_bytes(),
            )
            return
        if path in assets:
            route.fulfill(
                content_type="text/css; charset=utf-8" if path.endswith(".css") else "application/javascript; charset=utf-8",
                body=assets[path],
            )
            return
        if path.endswith("/api/registry"):
            payload = {"reports": [PAUSED, ACTIVE]}
        elif path.endswith("/api/system/status"):
            payload = status
        elif path.endswith("/api/system/run-stats"):
            payload = {"stats": {}}
        elif path.endswith("/api/system/git/status"):
            payload = {"configured": False}
        elif path.endswith("/api/me/favorites"):
            payload = {"favorites": []}
        elif "/api/" in path:
            payload = {"reports": {}}
        else:
            route.fulfill(content_type="text/html; charset=utf-8", body=html)
            return
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    page.route("**/*", respond)


def test_list_view_marks_paused_report(mobile_browser):  # noqa: F811
    context = mobile_browser.new_context(viewport={"width": 1280, "height": 844})
    page = context.new_page()
    _route(page, PORTAL_HTML, {})
    try:
        page.goto("http://portal.test/s/acme/analytics/")
        page.wait_for_selector('#content tr[data-slug="paused-report"]')
        paused = page.locator('#content tr[data-slug="paused-report"] td[data-label="Schedule"]')
        active = page.locator('#content tr[data-slug="active-report"] td[data-label="Schedule"]')
        assert "Auto-refresh paused" in paused.inner_text()
        assert "Operations" in paused.locator(".ui-badge").get_attribute("title")
        assert "Auto-refresh paused" not in active.inner_text()
    finally:
        context.close()


def test_operations_shows_pause_instead_of_next_run(mobile_browser):  # noqa: F811
    next_run = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    status = {
        "running": {}, "queue": [], "server_uptime_seconds": 60,
        # The server lists only reports the scheduler will actually fire.
        "scheduled_jobs": [{"id": "report-2", "slug": "active-report", "next_run": next_run}],
    }
    context = mobile_browser.new_context(viewport={"width": 1280, "height": 844})
    page = context.new_page()
    _route(page, _operations_fixture_html(), status)
    try:
        page.goto("http://portal.test/s/acme/analytics/operations")
        page.locator('[data-ops-row="paused-report"]').wait_for()
        page.wait_for_function("!_opsStatusInFlight")
        paused = page.locator('[data-ops-row="paused-report"]')
        active = page.locator('[data-ops-row="active-report"]')
        assert paused.locator(".ops-next-run").inner_text() == "Auto-refresh paused"
        assert active.locator(".ops-next-run").inner_text().startswith("next in ")
        assert paused.get_by_role("button", name="Resume").count() == 1
        assert active.get_by_role("button", name="Resume").count() == 0
    finally:
        context.close()
