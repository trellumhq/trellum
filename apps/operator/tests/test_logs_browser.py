"""Browser checks for the operator log browser using its real page assets."""
from __future__ import annotations

import json
import os

import pytest
from django.conf import settings


JS = (settings.BASE_DIR / "static" / "server-logs.js").read_text(encoding="utf-8")
CSS = (settings.BASE_DIR / "static" / "server-logs.css").read_text(encoding="utf-8")
TEMPLATE = (settings.BASE_DIR / "templates" / "operator" / "logs.html").read_text(encoding="utf-8")
UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def browser(request):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        try:
            instance = getattr(playwright, request.param).launch(headless=True)
        except Error as exc:
            pytest.skip(f"{request.param} unavailable: {exc}")
        yield instance
        instance.close()


def entry(id="e1", message="worker started", **extra):
    return {
        "id": id, "time": "2026-10-08T09:00:00Z", "level": "info",
        "service": "worker", "host": "host-a", "process": "pid 7",
        "logger": "build.worker", "message": message, "exception": "",
        "context": {"attempt": 1}, "worker_id": "worker-a", "run_id": None,
        "report_slug": "sales", "request_id": "req-1", "trigger": "scheduled",
        **extra,
    }


def open_logs(browser, width=1280, routes=()):
    context = browser.new_context(viewport={"width": width, "height": 850}, is_mobile=width < 768)
    page = context.new_page()
    for pattern, handler in routes:
        page.route(pattern, handler)
    # Keep the console shell contract in the harness; the component's markup and
    # JavaScript/CSS are the same files served by the application.
    document = """<!doctype html><html data-theme="light"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>""" + UI_CSS + "\n" + CSS + """</style></head><body class="mgmt"><header class="shell"><nav aria-label="Console"><a href="/operator/logs/" aria-current="page">Server logs</a></nav></header>
        <main><div class="server-logs" data-server-logs data-events-url="/operator/logs/events/" data-export-url="/operator/logs/export/" data-run-url="/operator/logs/runs/"><header class="page-head-row"><h1>Server logs</h1><button class="ui-btn ghost" data-refresh>Refresh</button></header><p>Application and retained build output. Host, kernel and container runtime logs are managed separately.</p><div class="panel logs-coverage" data-coverage hidden></div><form class="panel logs-filters" data-filter-form><div class="logs-filter-grid"><div class="ui-field"><label for="logs-since">Since</label><select class="ui-input" id="logs-since" name="since"><option value="1h">Last hour</option><option value="24h" selected>Last 24 hours</option><option value="7d">Last 7 days</option></select></div><div class="ui-field"><label for="logs-level">Minimum level</label><select class="ui-input" id="logs-level" name="level"><option value="INFO" selected>Info</option><option value="ERROR">Error</option></select></div><div class="ui-field"><label for="logs-service">Service</label><select class="ui-input" id="logs-service" name="service"><option value="">All services</option><option>worker</option><option>web</option></select></div><div class="ui-field"><label for="logs-report">Report slug</label><input class="ui-input" id="logs-report" name="report_slug"></div><div class="ui-field"><label for="logs-worker">Worker ID</label><input class="ui-input" id="logs-worker" name="worker_id"></div><div class="ui-field"><label for="logs-run">Run ID</label><input class="ui-input" id="logs-run" name="run_id"></div><div class="ui-field"><label for="logs-search">Search</label><input class="ui-input" id="logs-search" name="q" maxlength="200"></div><details class="logs-extra-filters"><summary>More filters</summary><input name="request_id"></details></div><div class="logs-actions"><button class="ui-btn primary" type="submit">Apply filters</button><button class="ui-btn ghost" data-live aria-pressed="true" type="button">Pause live</button><button class="ui-btn ghost" data-load-older type="button">Load older</button><a data-download href="/operator/logs/export/">Download NDJSON</a></div></form><div class="panel logs-table-panel"><p class="logs-status" data-status role="status" aria-live="polite">Loading</p><div class="ui-table-scroll logs-scroll" data-log-scroll><table class="ui-table dense logs-table"><thead><tr><th>Seen</th><th>Level</th><th>Service</th><th>Message</th><th>Details</th></tr></thead><tbody data-entries></tbody></table></div></div></div></main><script>""" + JS + "</script></body></html>"
    page.route("**/operator/logs/", lambda route: route.fulfill(status=200, content_type="text/html", body=document))
    page.goto("http://portal.test/operator/logs/")
    return context, page


def test_template_keeps_operator_shell_contract_and_coverage_note():
    assert "data-server-logs" in TEMPLATE
    assert "{% url 'operator-log-events' %}" in TEMPLATE
    assert "{% url 'operator-log-export' %}" in TEMPLATE
    assert "Host, kernel and container runtime logs are managed separately" in TEMPLATE
    assert "aria-live=\"polite\"" in TEMPLATE
    assert 'value="INFO" selected' in TEMPLATE


def test_filters_pause_older_catchup_and_untrusted_text(browser):
    calls = []
    def events(route):
        calls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "entries": [entry(message='<img src=x onerror=alert(1)>')], "has_more": True,
            "next_before": "oldest", "next_after": "newest",
            "coverage": {"enabled": True, "retention_days": 14, "max_rows": 25000},
        }))
    def run_detail(route):
        route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"run": {
            "id": "7ad2481b-c8a4-4d78-99c8-a80deeb7116d", "status": "error", "slug": "sales",
            "trigger": "scheduled", "worker_id": "worker-a", "exit_code": 2,
            "memory_limit_mb": 512, "peak_memory_mb": 300, "stdout": "ok", "stderr": "trace",
        }}))
    context, page = open_logs(browser, width=390, routes=(
        ("**/operator/logs/events/**", events), ("**/operator/logs/runs/**", run_detail),
    ))
    try:
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.wait_for_selector("[data-entry-id='e1']")
        assert page.locator("img").count() == 0
        assert "14 days" in page.locator("[data-coverage]").inner_text()
        assert page.locator("[data-entry-id='e1'] .logs-message").inner_text() == "<img src=x onerror=alert(1)>"
        page.get_by_role("button", name="Load older").click()
        page.wait_for_timeout(100)
        assert len(calls) >= 2 and "before=oldest" in calls[1]
        page.locator("#logs-service").select_option("worker")
        page.locator("#logs-level").select_option("ERROR")
        page.locator("#logs-search").fill("worker started")
        page.get_by_role("button", name="Apply filters").click()
        page.wait_for_timeout(100)
        assert "service=worker" in calls[-1] and "level=ERROR" in calls[-1] and "q=worker+started" in calls[-1]
        assert "q=worker+started" in page.locator("[data-download]").get_attribute("href")
        page.locator("#logs-search").fill("unsaved draft")
        page.get_by_role("button", name="Refresh").click()
        page.wait_for_timeout(100)
        assert "q=worker+started" in calls[-1] and "unsaved" not in calls[-1]
        assert page.locator(".server-logs").evaluate("el => el.getBoundingClientRect().width") <= 390
    finally:
        context.close()


def test_expanded_entry_run_details_and_pause_resume(browser):
    current = {"entries": [entry(run_id="7ad2481b-c8a4-4d78-99c8-a80deeb7116d")], "has_more": False,
               "next_before": "e1", "next_after": "e1", "coverage": {"enabled": True}}
    def respond(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(current))
    def run_detail(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": {
        "status": "success", "memory_limit_mb": 512, "peak_memory_mb": 300, "stdout": "build output", "stderr": ""
    }}))
    context, page = open_logs(browser, routes=(("**/operator/logs/events/**", respond), ("**/operator/logs/runs/**", run_detail)))
    try:
        page.wait_for_selector("[data-entry-id='e1']")
        page.get_by_role("button", name="Details", exact=True).click()
        page.get_by_role("button", name="Build details").click()
        page.wait_for_function("document.querySelector('.logs-detail-row')?.innerText.includes('512 MB')")
        assert "512 MB" in page.locator(".logs-detail-row").inner_text()
        assert "Memory allocation" in page.locator(".logs-detail-row").inner_text()
        assert "build output" in page.locator(".logs-detail-row").inner_text()
        page.get_by_role("button", name="Pause live").click()
        assert page.get_by_role("button", name="Resume live").get_attribute("aria-pressed") == "false"
        page.locator("#logs-worker").fill("worker-b")
        page.get_by_role("button", name="Apply filters").click()
        assert "worker_id=worker-b" in page.locator("[data-download]").get_attribute("href")
        page.get_by_role("button", name="Resume live").click()
    finally:
        context.close()


def test_delayed_old_filter_response_cannot_replace_newer_results(browser):
    first = {"entries": [entry(id="first", message="initial")], "has_more": False,
             "next_before": "first", "next_after": "first", "coverage": {"enabled": True}}
    def respond(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(first))
    context, page = open_logs(browser, routes=(("**/operator/logs/events/**", respond),))
    try:
        page.wait_for_selector("[data-entry-id='first']")
        page.evaluate("""(() => {
          const nativeFetch = window.fetch.bind(window);
          const response = (id, message, time) => new Response(JSON.stringify({
            entries:[{id,time,level:'info',service:'worker',message}],has_more:false,next_before:id,next_after:id
          }), {status:200,headers:{'Content-Type':'application/json'}});
          window.fetch = (input, init) => {
            const url = new URL(input instanceof URL ? input.href : typeof input === 'string' ? input : input.url);
            if (url.searchParams.get('q') === 'old') return new Promise(resolve => {
              window.__releaseOld = () => resolve(response('stale', 'stale response', '2026-10-08T09:00:00Z'));
            });
            if (url.searchParams.get('q') === 'new') return Promise.resolve(response('fresh', 'fresh response', '2026-10-08T10:00:00Z'));
            return nativeFetch(input, init);
          };
        })()""")
        page.locator("#logs-search").fill("old")
        page.get_by_role("button", name="Apply filters").click()
        page.wait_for_function("typeof window.__releaseOld === 'function'")
        page.locator("#logs-search").fill("new")
        page.get_by_role("button", name="Apply filters").click()
        page.wait_for_selector("text=fresh response")
        page.evaluate("window.__releaseOld()")
        page.wait_for_timeout(50)
        assert page.locator("[data-entry-id='fresh']").count() == 1
        assert page.locator("[data-entry-id='stale']").count() == 0
    finally:
        context.close()


def test_run_detail_response_is_invalidated_by_filter_change(browser):
    run_id = "7ad2481b-c8a4-4d78-99c8-a80deeb7116d"
    def respond(route):
        filtered = "q=new" in route.request.url
        payload = {"entries": [entry(id="same", message="filtered entry" if filtered else "initial entry", run_id=run_id)],
                   "has_more": False, "next_before": "same", "next_after": "same", "coverage": {"enabled": True}}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
    context, page = open_logs(browser, routes=(("**/operator/logs/events/**", respond),))
    try:
        page.wait_for_selector("[data-entry-id='same']")
        page.evaluate("""(() => {
          const nativeFetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const path = new URL(input instanceof URL ? input.href : typeof input === 'string' ? input : input.url, location.origin).pathname;
            if (path.includes('/runs/')) {
              window.__runPath = path;
              return new Promise(resolve => { window.__releaseRun = () => resolve(new Response(JSON.stringify({
                run:{status:'success',stdout:'obsolete build output',memory_limit_mb:512}
              }), {status:200,headers:{'Content-Type':'application/json'}})); });
            }
            return nativeFetch(input, init);
          };
        })()""")
        page.get_by_role("button", name="Build details").click()
        page.wait_for_function("typeof window.__releaseRun === 'function'", timeout=3000)
        page.locator("#logs-search").fill("new")
        page.get_by_role("button", name="Apply filters").click()
        page.get_by_text("filtered entry").wait_for()
        page.evaluate("window.__releaseRun()")
        page.wait_for_timeout(50)
        assert page.evaluate("window.__runPath").endswith("/")
        assert page.locator(".logs-detail-row").count() == 0
        assert "obsolete build output" not in page.locator("[data-entries]").inner_text()
    finally:
        context.close()


def test_live_entry_preserves_expansion_focus_and_scrolled_row(browser):
    old_entries = [entry(id=f"c{number:02d}", message=f"event {number}") for number in range(60)]
    old_entries.extend([entry(id="2", message="numeric id two"), entry(id="10", message="numeric id ten")])
    latest = {"entries": old_entries, "has_more": True, "next_before": "older-cursor",
              "next_after": "c00", "coverage": {"enabled": True}}
    calls = []
    def respond(route):
        calls.append(route.request.url)
        if "before=" in route.request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "entries": [], "has_more": False, "next_before": "older-end", "coverage": {"enabled": True},
            }))
        elif "after=c00" in route.request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "entries": [entry(id="new", time="2026-10-08T11:00:00Z", message="new event")],
                "has_more": True, "next_before": "wrong-older-cursor", "next_after": "cursor-2", "coverage": {"enabled": True},
            }))
        elif "after=cursor-2" in route.request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "entries": [], "has_more": False, "next_before": "wrong-older-end", "next_after": "cursor-3", "coverage": {"enabled": True},
            }))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(latest))
    context, page = open_logs(browser, routes=(("**/operator/logs/events/**", respond),))
    try:
        page.wait_for_selector("[data-entry-id='c59']")
        ids = page.locator("tbody tr[data-entry-id]").evaluate_all("rows => rows.map(row => row.dataset.entryId)")
        assert ids.index("10") < ids.index("2")
        row = page.locator("[data-entry-id='c30']")
        row.get_by_role("button", name="Details", exact=True).click()
        scroll = page.locator("[data-log-scroll]")
        copy_button = page.locator("[data-entry-id='c30'] button[data-action='copy']")
        copy_button.focus()
        scroll.evaluate("el => { el.scrollTop = 800; }")
        before = row.evaluate("el => el.getBoundingClientRect().top - el.closest('[data-log-scroll]').getBoundingClientRect().top")
        page.wait_for_selector("[data-entry-id='new']", timeout=5000)
        page.wait_for_timeout(50)
        after = page.locator("[data-entry-id='c30']").evaluate("el => el.getBoundingClientRect().top - el.closest('[data-log-scroll]').getBoundingClientRect().top")
        assert abs(before - after) < 2
        assert page.locator("[data-entry-id='c30'] button[data-action='copy']").evaluate("el => el === document.activeElement")
        assert page.locator("[data-entry-id='c30'] + .logs-detail-row").count() == 1
        page.get_by_role("button", name="Load older").click()
        page.wait_for_timeout(100)
        assert len(calls) == 4 and "before=older-cursor" in calls[-1]
    finally:
        context.close()


@pytest.mark.parametrize(("width", "filename"), ((1280, "desktop.png"), (390, "mobile-390.png")))
def test_capture_mocked_ui_screenshots(browser, width, filename):
    if os.environ.get("TRELLUM_CAPTURE_LOGS") != "1" or browser.browser_type.name != "chromium":
        pytest.skip("Set TRELLUM_CAPTURE_LOGS=1 to capture Chromium review screenshots")
    payload = {"entries": [entry(run_id="7ad2481b-c8a4-4d78-99c8-a80deeb7116d")], "has_more": False,
               "next_before": "e1", "next_after": "e1", "coverage": {"enabled": True, "retention_days": 14, "max_rows": 25000}}
    def respond(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
    context, page = open_logs(browser, width=width, routes=(("**/operator/logs/events/**", respond),))
    try:
        page.wait_for_selector("[data-entry-id='e1']")
        output = settings.BASE_DIR.parent.parent / ".artifacts" / "server-logs"
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output / filename, full_page=True)
    finally:
        context.close()


def test_manual_refresh_drains_all_new_pages_while_paused(browser):
    from urllib.parse import parse_qs, urlparse
    calls = []

    def respond(route):
        params = parse_qs(urlparse(route.request.url).query)
        calls.append(params)
        after = params.get("after", [None])[0]
        ids = list(range(2, 202)) if after == "1" else [202] if after == "201" else [1]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "entries": [entry(id=i) for i in ids], "has_more": after == "1",
            "next_before": min(ids), "next_after": max(ids), "coverage": {"enabled": True},
        }))

    context, page = open_logs(browser, routes=(("**/operator/logs/events/**", respond),))
    try:
        page.wait_for_selector("[data-entry-id='1']")
        page.get_by_role("button", name="Pause live").click()
        page.get_by_role("button", name="Refresh", exact=True).click()
        page.wait_for_selector("[data-entry-id='202']")
        assert page.locator("tr[data-entry-id]").count() == 202
        assert [call.get("after") for call in calls] == [None, ["1"], ["201"]]
        assert page.get_by_role("button", name="Resume live").count() == 1
        assert page.get_by_role("button", name="Load older").is_disabled()
    finally:
        context.close()


def test_active_build_details_can_be_refetched(browser):
    run_id = "7ad2481b-c8a4-4d78-99c8-a80deeb7116d"
    requests = []

    def events(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "entries": [entry(id=1, run_id=run_id)], "has_more": False,
            "next_after": 1, "next_before": 1, "coverage": {"enabled": True},
        }))

    def run(route):
        requests.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "run": {"status": "running" if len(requests) == 1 else "success",
                    "stdout": "first output" if len(requests) == 1 else "completed output"},
        }))

    context, page = open_logs(browser, routes=(("**/operator/logs/events/**", events),
                                             ("**/operator/logs/runs/**", run)))
    try:
        page.get_by_role("button", name="Build details").click()
        page.get_by_text("first output", exact=True).wait_for()
        page.get_by_role("button", name="Build details").click()
        page.get_by_text("completed output", exact=True).wait_for()
        assert len(requests) == 2
    finally:
        context.close()
