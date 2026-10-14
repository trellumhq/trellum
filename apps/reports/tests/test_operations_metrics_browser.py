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
    html = html.replace('<script src="/static/portal.js">', '<script src="/static/operations.js"></script><script src="/static/portal.js">')
    return html.replace("initial_view:'reports'", "initial_view:'ops'").replace(
        '<h1>Reports</h1>', '<h1>Operations</h1>'
    )


@pytest.mark.parametrize("width", [1280, 390])
def test_polling_updates_summary_with_two_expanded_histories(mobile_browser, width):
    rows = [
        REPORT,
        {**REPORT, "id": 2, "slug": "second-report", "name": "Second report"},
    ]
    status = {"state": "ready", "rows": [], "max_concurrent": 2, "worker_alive": True}
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

    def respond(route):
        path = urlsplit(route.request.url).path
        assets = {
            "/static/portal.js": PORTAL_JS, "/static/console-shell.js": CONSOLE_JS,
            "/static/ui.css": UI_CSS, "/static/portal.css": PORTAL_CSS,
            "/static/console-shell.css": CONSOLE_CSS,
            "/static/operations.js": (settings.BASE_DIR / "static" / "operations.js").read_text(),
        }
        for name in ("themes.generated.css", "choice-picker.css", "console-report-host.css"):
            assets[f"/static/{name}"] = (settings.BASE_DIR / "static" / name).read_text()
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
        if path.endswith("/api/operations/catalog"):
            payload = {
                "state": "ready", "rows": [{**row, "state": "ok", "build_status": "success"} for row in rows],
                "counts": {"total": 2, "running": 0, "queued": 0, "waiting": 0, "ok": 2, "error": 0},
                "pagination": {"page": 1, "pages": 1, "total": 2}, "folders": None,
            }
        elif path.endswith("/api/operations/sources"):
            payload = {"state": "ready", "rows": [], "pagination": {"page": 1, "pages": 1, "total": 0}}
        elif path.endswith("/api/operations/status"):
            status_calls.append(status.copy())
            payload = status
        elif path.endswith("/api/operations/stats"):
            payload = {"state": "ready", "stats": stats}
        elif path.endswith("/api/system/git/status"):
            payload = {"configured": False}
        elif path.endswith("/status") and "/api/reports/" in path:
            payload = {"aggregates": agg, "history": history}
        elif path.endswith("/log"):
            payload = {"status": "success", "stdout": "completed output"}
        elif path.endswith("/api/me/favorites"):
            payload = {"favorites": []}
        elif "/api/" in path:
            payload = {"reports": {}}
        else:
            route.fulfill(
                content_type="text/html; charset=utf-8", body=html,
            )
            return
        route.fulfill(content_type="application/json", body=json.dumps(payload))

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
        # The bounded status carries a run for one page row plus studio-wide
        # totals; it no longer reports server uptime (the Worker tile keeps its
        # capacity label).
        status.update(
            rows=[{"slug": "second-report", "status": "running", "started_at": "2026-09-06T08:00:00Z"}],
            active_jobs_total={"running": 1, "queued": 1},
        )
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
        assert summary.last.inner_text() == "Capacity 2"
        assert page.locator('[data-ops-row="second-report"] .ops-row-btn.stop').count() == 1
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
