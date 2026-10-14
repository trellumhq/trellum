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
        # Use the real expansion control; each expansion renders and loads history.
        for row in rows:
            page.locator(f'[data-ops-row="{row["slug"]}"] [data-label="Report"]').click()
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
        # Uptime is not part of the bounded status contract; the Worker tile
        # reports the capacity the status read does carry.
        assert summary.last.inner_text() == "Capacity 10"
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
