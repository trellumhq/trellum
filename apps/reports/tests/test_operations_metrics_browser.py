"""Operations polling preserves independent expanded-history statistics."""
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.template.loader import render_to_string

from .test_portal_mobile_runtime import (
    CONSOLE_CSS,
    CONSOLE_JS,
    PORTAL_CSS,
    PORTAL_HTML,
    PORTAL_JS,
    REPORT,
    UI_CSS,
    _capture,
    _ops_log,
    _ops_respond,
    _ops_status_payload,
    mobile_browser,
)


def _operations_fixture_html():
    """Use the shipped shell and portal styles with synthetic context and API data."""
    org = SimpleNamespace(slug="acme", name="Acme", pk=1)
    studio = SimpleNamespace(slug="analytics", name="Analytics", pk=1)
    shell = render_to_string("_shell.html", {
        "shell_org": org, "shell_studio": studio, "shell_nav_studio": studio,
        "shell_studios": [studio], "shell_nav_studio_role": "admin",
        "shell_nav_full_studio_visibility": True, "dashboard_shell": True,
        "console_page_title": "Operations", "org_default_mode": "light",
        "user": SimpleNamespace(email="admin@example.test", display_name="Test Admin"),
        "request": SimpleNamespace(
            resolver_match=SimpleNamespace(url_name="studio-operations"), GET={}
        ),
    })
    start = PORTAL_HTML.index('<div class="tl-console-shell"')
    end = PORTAL_HTML.index('<div data-console-inert>')
    html = PORTAL_HTML[:start] + shell + PORTAL_HTML[end:]
    html = html.replace('<head>', '<head><meta charset="UTF-8">')
    html = html.replace('<link rel="stylesheet" href="/static/ui.css">', '''
        <link rel="stylesheet" href="/_vendor/inter.css">
        <link rel="stylesheet" href="/static/themes.generated.css">
        <link rel="stylesheet" href="/static/ui.css">''')
    html = html.replace('</head>', '''
        <link rel="stylesheet" href="/static/choice-picker.css">
        <link rel="stylesheet" href="/static/console-report-host.css"></head>''')
    html = html.replace('<script src="/static/portal.js"></script>',
                        '<script src="/static/operations.js"></script><script src="/static/portal.js"></script>')
    return html.replace("initial_view:'reports'", "initial_view:'ops'").replace(
        '<h1>Reports</h1>', '<h1>Operations</h1>'
    )


@pytest.mark.parametrize("width", [1280, 390])
def test_polling_updates_summary_with_two_expanded_histories(mobile_browser, width):
    rows = [
        REPORT,
        {**REPORT, "id": 2, "slug": "second-report", "name": "Second report"},
    ]
    status = {"running": {}, "queue": [], "server_uptime_seconds": 60}
    history = [{"status": "success", "duration_seconds": 30, "peak_memory_mb": 128}]
    agg = {
        "runs": 2, "success_rate": 0.5, "avg_duration_s": 30,
        "p95_duration_s": 30, "avg_peak_memory_mb": 128, "max_peak_memory_mb": 256,
    }
    stats = {
        row["slug"]: {
            "last": {"duration_s": 1, "status": "error", "peak_memory_mb": 256},
            "agg": agg,
        }
        for row in rows
    }
    context = mobile_browser.new_context(viewport={"width": width, "height": 844})
    page = context.new_page()
    status_calls = []
    html = _operations_fixture_html()

    def extra(route, path, query, reply):
        assets = {"/static/ui.css": UI_CSS, "/static/portal.css": PORTAL_CSS,
                  "/static/console-shell.css": CONSOLE_CSS}
        for name in ("themes.generated.css", "choice-picker.css", "console-report-host.css"):
            assets[f"/static/{name}"] = (settings.BASE_DIR / "static" / name).read_text()
        if path.startswith("/_vendor/"):
            asset = settings.BASE_DIR / "trellum" / "static" / "vendor" / path.removeprefix("/_vendor/")
            route.fulfill(
                content_type="text/css; charset=utf-8" if path.endswith(".css") else "font/ttf",
                body=asset.read_bytes(),
            )
        elif path in assets:
            route.fulfill(content_type="text/css; charset=utf-8", body=assets[path])
        elif path.endswith("/api/operations/status"):
            status_calls.append(status.copy())
            reply(_ops_status_payload(status))
        elif path.endswith("/api/operations/stats"):
            reply({"version": 1, "state": "ready", "stats": stats, "window_days": 30})
        elif path.endswith("/status") and "/api/reports/" in path:
            reply({"aggregates": agg, "history": history})
        elif "/api/operations/reports/" in path and path.endswith("/log"):
            reply(_ops_log("success", stdout="completed output"))
        else:
            return False
        return True

    respond = _ops_respond(lambda: rows, lambda: status, extra=extra, html=html)

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/operations")
        page.locator('[data-ops-row="revenue-overview"]').wait_for()
        # Use the real expansion control; each expansion opens Terminal first and loads history in the Runs tab.
        for row in rows:
            page.locator(f'[data-ops-row="{row["slug"]}"] [data-label="Report"]').click()
            page.locator(f'.ops-panel[data-ops-panel="{row["slug"]}"] [data-ops-tab="runs"]').click()
            page.locator(f'#opsHist_{row["slug"]} .ops-history-stats').wait_for()
        histories = page.locator(".ops-history-stats")
        assert histories.count() == 2
        before = histories.all_text_contents()
        assert all("256 MB" in text for text in before)
        page.evaluate("""window.__metricHistoryOwners =
            Array.from(document.querySelectorAll('.ops-history-stats'))""")
        page.wait_for_function("!_opsStatusInFlight")
        status = {
            "running": {"unlisted-running": {"elapsed_seconds": 12}},
            "queue": [{"slug": "unlisted-queued"}], "server_uptime_seconds": 3661,
        }
        calls_before = len(status_calls)
        # Drive the actual scheduled network poll rather than calling the updater directly.
        page.evaluate("""window.__previousStatusCycle = _opsStatusCycle;
            _opsScheduleStatusPoll(0)""")
        page.wait_for_function(
            "_opsStatusCycle > window.__previousStatusCycle && !_opsStatusInFlight"
        )
        assert len(status_calls) > calls_before
        assert histories.all_text_contents() == before
        summary = page.locator(".ops-summary:not(.ops-history-stats) .ops-stat-value")
        assert summary.first.inner_text() == "2"
        assert summary.nth(1).inner_text() == "1"
        assert summary.nth(2).inner_text() == "1"
        assert summary.last.inner_text() == "1h 1m"
        assert page.evaluate("""window.__metricHistoryOwners.every((el, i) =>
            el === document.querySelectorAll('.ops-history-stats')[i])""")
        assert page.evaluate("Object.keys(_opsExpandedSlugs).length") == 2
        labels = page.locator(".ops-history-stats .ops-stat-label").all_text_contents()
        assert labels.count("Avg duration (successful)") == 2
        assert labels.count("P95 duration (successful)") == 2
        duration = page.locator('[data-ops-row="revenue-overview"] [data-label="Duration"]')
        assert duration.locator(".ops-metric").inner_text() == "1s"
        assert "p95 successful 30s" in duration.inner_text()
        page.evaluate("document.fonts.ready")
        page.evaluate("window.scrollTo(0, 0)")
        _capture(page, mobile_browser, f"operations-metrics-{width}.png")
        if os.environ.get("TRELLUM_CAPTURE_PORTAL") == "1" and mobile_browser.browser_type.name == "chromium":
            measure = """() => ({document: document.documentElement.scrollWidth,
                viewport: innerWidth, histories: Array.from(document.querySelectorAll('.ops-history-stats'))
                    .map(el => ({width: el.getBoundingClientRect().width, scroll: el.scrollWidth}))})"""
            current = page.evaluate(measure)
            page.evaluate("""window.__durationLabelSnapshots = Array.from(
                document.querySelectorAll('.ops-stat-label, .ops-metric-sub'))
                .map(el => [el, el.textContent]);
                window.__durationLabelSnapshots.forEach(([el, text]) => {
                    el.textContent = text.replace(' (successful)', '').replace('p95 successful ', 'p95 ');
                })""")
            baseline = page.evaluate(measure)
            _capture(page, mobile_browser, f"operations-metrics-baseline-labels-{width}.png")
            page.evaluate("window.__durationLabelSnapshots.forEach(([el, text]) => el.textContent = text)")
            (settings.BASE_DIR / ".artifacts" / "portal" / f"operations-metrics-layout-{width}.json").write_text(
                json.dumps({"current": current, "baseline_labels": baseline}, indent=2)
            )
    finally:
        context.close()


@pytest.mark.parametrize("mobile_browser", ["chromium"], indirect=True)
def test_run_log_toggle_rebuild_does_not_block_registry_refresh(mobile_browser):
    """Toggling a report log updates in place without rebuilding toolbar actions."""
    from playwright.sync_api import expect

    status = {"running": {}, "queue": []}
    posts = []

    def extra(route, path, query, reply):
        if path.endswith("/api/system/registry/refresh"):
            posts.append(route.request.method)
            reply({"operation_id": "refresh-1", "state": "accepted",
                   "status_url": "/refresh-status"}, code=202)
        elif path == "/refresh-status":
            reply({"state": "succeeded", "terminal": True, "message": "Registry refreshed"})
        else:
            return False
        return True

    context = mobile_browser.new_context()
    page = context.new_page()
    page.route("**/*", _ops_respond(
        lambda: [REPORT], lambda: status, extra=extra,
        run=lambda reply: reply({"ok": True, "status": "queued"}),
        log=lambda: _ops_log("success", stdout="Published to LocalBackend"),
    ))
    try:
        page.goto("http://portal.test/s/acme/analytics/operations")
        row = page.locator('[data-ops-row="revenue-overview"]')
        row.locator('.ops-run-dropdown .ops-row-btn').click()
        page.locator('#opsRunMenu_revenue-overview .ops-run-menu-item', has_text='Generate').click()
        log = page.locator('#opsTermBody_revenue-overview')
        # Accepted runs already open their own log; Log is a toggle, not Open.
        expect(log).to_contain_text("Published to LocalBackend")
        page.evaluate("window.__refreshBeforeToggle = document.querySelector('[data-ops-action=refresh]')")
        row.locator('details.ops-secondary summary').click()
        row.get_by_role('button', name='Log', exact=True).click()
        expect(log).to_have_count(0)
        assert page.evaluate("!_opsExpandedSlugs['revenue-overview']")
        assert page.evaluate("window.__refreshBeforeToggle.isConnected")
        with page.expect_response(lambda r: r.url.endswith('/api/system/registry/refresh')) as accepted:
            page.locator('[data-ops-action=refresh]').click()
        assert accepted.value.status == 202
        expect(page.locator('body')).to_contain_text("Registry refreshed")
        assert posts == ['POST']
    finally:
        context.close()


@pytest.mark.parametrize("mobile_browser", ["chromium"], indirect=True)
def test_compact_tabbed_panel_zero_shift_and_run_log_switch(mobile_browser):
    """Terminal opens first in a fixed-height tabbed panel; live updates & tab switches do not shift rows."""
    from playwright.sync_api import expect

    older_id = "11111111-1111-4111-8111-111111111111"
    latest_id = "22222222-2222-4222-8222-222222222222"
    rows = [
        {**REPORT, "last_status": "error", "last_error": "Snowflake query timeout",
         "validation": {"summary": {"pass": 5, "warn": 1, "fail": 0},
                        "checks": [{"id": "freshness", "level": "warn", "message": "Data is 2h old"}]}},
        {**REPORT, "id": 2, "slug": "second-report", "name": "Second report", "last_status": "success"},
    ]
    status = {"running": {}, "queue": [], "server_uptime_seconds": 120}
    history = [
        {"id": latest_id, "status": "error", "trigger": "manual", "duration_seconds": 45, "peak_memory_mb": 512,
         "created_at": "2026-10-18T10:00:00Z"},
        {"id": older_id, "status": "success", "trigger": "schedule", "duration_seconds": 18, "peak_memory_mb": 256,
         "created_at": "2026-10-18T09:00:00Z"},
    ]
    agg = {"runs": 2, "success_rate": 0.5, "avg_duration_s": 18, "p95_duration_s": 18,
           "avg_peak_memory_mb": 256, "max_peak_memory_mb": 512}
    log_queries = []
    release_history = []

    def extra(route, path, query, reply):
        assets = {"/static/ui.css": UI_CSS, "/static/portal.css": PORTAL_CSS,
                  "/static/console-shell.css": CONSOLE_CSS}
        for name in ("themes.generated.css", "choice-picker.css", "console-report-host.css"):
            assets[f"/static/{name}"] = (settings.BASE_DIR / "static" / name).read_text()
        if path.startswith("/_vendor/"):
            asset = settings.BASE_DIR / "trellum" / "static" / "vendor" / path.removeprefix("/_vendor/")
            route.fulfill(
                content_type="text/css; charset=utf-8" if path.endswith(".css") else "font/ttf",
                body=asset.read_bytes(),
            )
        elif path in assets:
            route.fulfill(content_type="text/css; charset=utf-8", body=assets[path])
        elif path.endswith("/status") and "/api/reports/" in path:
            release_history.append(lambda: reply({"aggregates": agg, "history": history}))
        elif "/api/operations/reports/" in path and path.endswith("/log"):
            log_queries.append(query)
            if query.get("run") == [older_id]:
                reply(_ops_log("success", stdout="older run output OK"))
            else:
                reply(_ops_log("error", stderr="latest failure traceback"))
        else:
            return False
        return True

    context = mobile_browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/*", _ops_respond(lambda: rows, lambda: status, extra=extra, html=_operations_fixture_html()))
    try:
        page.goto("http://portal.test/s/acme/analytics/operations")
        page.locator('[data-ops-row="second-report"]').wait_for()
        page.evaluate("clearTimeout(_opsStatusTimer); window.__secondRowNode = document.querySelector('[data-ops-row=\"second-report\"]')")

        # Live status transitions on a row must not change the row height or shift rows below.
        before_height = page.locator('[data-ops-row="revenue-overview"]').bounding_box()["height"]
        before_second_y = page.locator('[data-ops-row="second-report"]').bounding_box()["y"]
        for st in (
            {"running": {}, "queue": [{"slug": "revenue-overview"}]},
            {"running": {"revenue-overview": {"elapsed_seconds": 12}}, "queue": []},
            {"running": {}, "queue": []},
        ):
            page.evaluate("s => _opsApplyStatus(s, _opsStatusRevision, _opsStatusCycle)", st)
            assert abs(page.locator('[data-ops-row="revenue-overview"]').bounding_box()["height"] - before_height) < 1
            assert abs(page.locator('[data-ops-row="second-report"]').bounding_box()["y"] - before_second_y) < 1

        # Clicking a report expands in place (without rebuilding sibling rows) onto Terminal first.
        page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()
        assert page.evaluate("window.__secondRowNode === document.querySelector('[data-ops-row=\"second-report\"]')")
        panel = page.locator('.ops-panel[data-ops-panel="revenue-overview"]')
        expect(panel.locator('[data-ops-tab="terminal"]')).to_have_attribute("aria-selected", "true")
        expect(panel.locator('[data-ops-tab="terminal"]')).to_have_attribute("tabindex", "0")
        expect(panel.locator('[data-ops-tab="runs"]')).to_have_attribute("tabindex", "-1")
        expect(panel.locator('[data-ops-tab="info"]')).to_have_attribute("tabindex", "-1")
        expect(panel.locator("#opsTabTerm_revenue-overview")).to_have_attribute("aria-labelledby", "opsTabBtnTerm_revenue-overview")
        expect(panel.locator("#opsTabRuns_revenue-overview")).to_have_attribute("aria-labelledby", "opsTabBtnRuns_revenue-overview")
        expect(panel.locator("#opsTabInfo_revenue-overview")).to_have_attribute("aria-labelledby", "opsTabBtnInfo_revenue-overview")
        expect(panel.locator("#opsTermBody_revenue-overview")).to_be_visible()
        expect(panel.locator("#opsTermBody_revenue-overview")).to_contain_text("latest failure traceback")
        expect(panel.locator("#opsTabRuns_revenue-overview")).to_be_hidden()

        # Async history arrival must update the Runs badge & health strip with 0px shift on rows below.
        y_before_history = page.locator('[data-ops-row="second-report"]').bounding_box()["y"]
        assert len(release_history) == 1
        release_history.pop()()
        expect(panel.locator("#opsRunsBadge_revenue-overview")).to_have_text("1 err")
        expect(panel.locator("#opsHealth_revenue-overview")).to_contain_text("Last run failed")
        assert abs(page.locator('[data-ops-row="second-report"]').bounding_box()["y"] - y_before_history) < 1
        # The run selector must stay compact on the right and never crush the terminal title.
        select_box = panel.locator("#opsRunSelect_revenue-overview").bounding_box()
        title_box = panel.locator("#opsTerm_revenue-overview .ops-terminal-title").bounding_box()
        assert select_box["width"] <= 260
        assert title_box["width"] >= 200 and title_box["height"] <= 28

        # Switching across Runs and Checks & Info tabs must cause 0px shift and keep roving tabindex.
        panel.locator('[data-ops-tab="terminal"]').focus()
        page.keyboard.press("ArrowRight")
        expect(panel.locator('[data-ops-tab="runs"]')).to_have_attribute("aria-selected", "true")
        expect(panel.locator('[data-ops-tab="runs"]')).to_have_attribute("tabindex", "0")
        expect(panel.locator('[data-ops-tab="terminal"]')).to_have_attribute("tabindex", "-1")
        expect(panel.locator("#opsHist_revenue-overview .ops-history-table")).to_be_visible()
        assert abs(page.locator('[data-ops-row="second-report"]').bounding_box()["y"] - y_before_history) < 1

        panel.locator('[data-ops-tab="info"]').click()
        expect(panel.locator('[data-ops-tab="info"]')).to_have_attribute("tabindex", "0")
        expect(panel.locator('[data-ops-tab="runs"]')).to_have_attribute("tabindex", "-1")
        expect(panel.locator("#opsTabInfo_revenue-overview")).to_contain_text("Data is 2h old")
        assert abs(page.locator('[data-ops-row="second-report"]').bounding_box()["y"] - y_before_history) < 1

        # Clicking "View log" on an older run switches back to Terminal and loads that run's log.
        panel.locator('[data-ops-tab="runs"]').click()
        panel.locator(f'[data-ops-view-run="{older_id}"]').click()
        expect(panel.locator('[data-ops-tab="terminal"]')).to_have_attribute("aria-selected", "true")
        expect(panel.locator("#opsTermBody_revenue-overview")).to_contain_text("older run output OK")
        assert any(q.get("run") == [older_id] for q in log_queries)
    finally:
        context.close()
