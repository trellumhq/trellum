"""Mobile browser coverage for the report catalog's interactive layers."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import pytest
from django.conf import settings

UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
PORTAL_CSS = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
PORTAL_JS = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
CONSOLE_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
    encoding="utf-8"
)
CONSOLE_JS = (settings.BASE_DIR / "static" / "console-shell.js").read_text(
    encoding="utf-8"
)
PORTAL_TEMPLATE = (settings.BASE_DIR / "templates" / "portal" / "index.html").read_text(
    encoding="utf-8"
)
DRAWERS_HTML = PORTAL_TEMPLATE[
    PORTAL_TEMPLATE.index('<div class="error-log-overlay"') : PORTAL_TEMPLATE.index(
        '<script id="portal-ctx"'
    )
]

REPORT = {
    "id": 1,
    "slug": "revenue-overview",
    "name": "Player Overview",
    "description": (
        "The Nova Play flagship: player KPIs, revenue by tier, geography, "
        "overlapping player behaviours and a long explanatory ending."
    ),
    "category": "Overview",
    "studio": "analytics",
    "tags": ["DEMO", "engagement", "revenue", "retention", "geography", "segmentation"],
    "has_output": True,
    "html_entry": "index.html",
    "last_status": "success",
    "last_run": "2026-09-06T08:00:00Z",
}
REPORTS = [REPORT] + [
    {
        **REPORT,
        "id": number,
        "slug": f"report-{number}",
        "name": f"Report {number}",
        "description": "Another report",
        "category": f"Category {number}",
        "tags": [],
    }
    for number in range(2, 12)
]


def _capture(page, browser, name: str) -> None:
    if os.environ.get("TRELLUM_CAPTURE_PORTAL") != "1":
        return
    if browser.browser_type.name != "chromium":
        return
    output = settings.BASE_DIR / ".artifacts" / "portal"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=output / name, full_page=True)


def _capture_list(page, browser) -> None:
    if os.environ.get("TRELLUM_CAPTURE_PORTAL") != "1":
        return
    output = settings.BASE_DIR / ".artifacts" / "list-fix"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=output / f"list-390-{browser.browser_type.name}.png", full_page=True
    )

PORTAL_HTML = """<!doctype html>
<html data-theme="light"><head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="/static/ui.css">
  <link rel="stylesheet" href="/static/portal.css">
  <link rel="stylesheet" href="/static/console-shell.css">
</head><body class="mgmt tl-console-page console-dashboard">
  <div class="tl-console-shell" data-console-shell data-console-default-mode="light">
    <aside class="tl-console-sidebar" id="consoleSidebar">
      <a href="/">Home</a><button type="button" data-console-close>Close</button>
      <button type="button" data-console-collapse>Collapse</button>
    </aside>
    <header class="tl-console-header">
      <button type="button" data-console-toggle aria-controls="consoleSidebar">Menu</button>
      <div class="tl-console-search"><input id="searchInput" aria-label="Search reports"></div>
    </header>
    <button type="button" data-console-backdrop>Close navigation</button>
  </div>
  <div data-console-inert>
    <header class="page-head">
      <div class="ui-label page-kicker scope-studio">Studio · Analytics</div>
      <h1>Reports</h1>
    </header>
    <div class="folder-bar"><div class="folder-rail"><div class="folder-inner" id="folderTabs"></div><div class="catalog-toolbar" id="catalogToolbar"></div></div></div>
    <div class="studio-bar" id="studioBar"><div class="studio-inner" id="studioFilters"></div></div>
    <div class="portal-body"><div class="portal-body-inner" id="content"></div></div>
  </div>
  __DRAWERS__
  <script>
    window.PORTAL_CTX = {org:{slug:'acme'}, studio:{slug:'analytics'},
      prefix:'/s/acme/analytics', user:{role:'admin'}, production:false,
      initial_view:'reports'};
    window.deliveryClicks = [];
    window.ReportDelivery = {open:function(prefix, slug){deliveryClicks.push([prefix, slug]);}};
  </script>
  <script src="/static/portal.js"></script>
  <script src="/static/console-shell.js"></script>
</body></html>""".replace("__DRAWERS__", DRAWERS_HTML)


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def mobile_browser(request):
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


@pytest.mark.parametrize("view,expected", (("reports", "report"), ("analyses", "analysis")))
def test_catalog_kind_filter_search_categories_and_favorites(mobile_browser, view, expected):
    rows = [
        {**REPORT, "slug": "report", "id": 1, "name": "Data report", "category": "Data", "tags": ["raw"]},
        {**REPORT, "slug": "analysis", "id": 2, "kind": "analysis", "name": "Quarterly finding", "category": "Strategy", "tags": ["evidence"]},
    ]
    context = mobile_browser.new_context(viewport={"width": 1024, "height": 740})
    page = context.new_page()

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": rows}))
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body=json.dumps({"favorites": [
                {"org_slug": "acme", "studio_slug": "analytics", "slug": r["slug"]} for r in rows
            ]}))
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body='{"studios":[],"reports":{}}')
        elif path.endswith(".css") or path.endswith(".js"):
            route.fulfill(body="")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML.replace("initial_view:'reports'", f"initial_view:'{view}'"))

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/" + ("analyses" if view == "analyses" else ""))
        page.wait_for_selector(f'#content tr[data-slug="{expected}"]')
        assert set(page.locator("#content tr[data-slug]").evaluate_all("rows => rows.map(r => r.dataset.slug)")) == {expected}
        assert page.evaluate("Array.from(getCategories().keys())") == (["Strategy"] if expected == "analysis" else ["Data"])
        page.wait_for_function("isFav('report') && isFav('analysis')")
        page.evaluate("activeFolder='_favorites';render()")
        assert page.locator('#folderTabs [data-folder="_favorites"] .count').inner_text() == "1"
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("render()")
        assert page.locator('#folderTabs option[value="all"]').inner_text() == "All (1)"
        assert page.locator('#folderTabs option[value="_favorites"]').inner_text() == "Favorites (1)"
        assert page.locator(f'#content tr[data-slug="{expected}"]').count() == 1
        page.locator("#searchInput").fill("evidence")
        assert bool(page.locator('#content tr[data-slug="analysis"]').count()) == (expected == "analysis")
    finally:
        context.close()


def test_operations_state_filters_follow_live_state_and_scope(mobile_browser):
    states = [
        ("failed", "Failed report", "error"),
        ("oom", "OOM report", "oom_killed"),
        ("timeout", "Timed report", "timeout"),
        ("success", "Success report", "success"),
        ("stopped", "Stopped report", "stopped"),
        ("never", "Not run report", "not_run"),
        ("waiting", "Waiting report", "success"),
        ("running", "Running report", "error"),
        ("queued", "Queued report", "not_run"),
    ]
    ops_reports = [
        {**REPORT, "id": index, "slug": slug, "name": name,
         "category": "Data" if index <= 3 else "Other",
         "last_status": "error" if slug == "oom" else "success" if slug in {"timeout", "stopped"} else result,
         "build_status": result,
         "waiting": slug == "waiting"}
        for index, (slug, name, result) in enumerate(states, 1)
    ]
    ops_status = {
        "running": {"running": {"elapsed_seconds": 4}}, "queue": ["queued"],
    }
    context = mobile_browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = context.new_page()

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/ui.css"):
            route.fulfill(content_type="text/css", body=UI_CSS)
        elif path.endswith("/static/portal.css"):
            route.fulfill(content_type="text/css", body=PORTAL_CSS)
        elif path.endswith("/static/console-shell.css"):
            route.fulfill(content_type="text/css", body=CONSOLE_CSS)
        elif path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/static/console-shell.js"):
            route.fulfill(content_type="application/javascript", body=CONSOLE_JS)
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": ops_reports}))
        elif path.endswith("/api/system/status"):
            route.fulfill(content_type="application/json", body=json.dumps(ops_status))
        elif path.endswith("/api/system/git/status"):
            route.fulfill(content_type="application/json", body='{"configured":false}')
        elif path.endswith("/api/system/run-stats"):
            route.fulfill(content_type="application/json", body='{"stats":{}}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body='{}')
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML.replace(
                "initial_view:'reports'", "initial_view:'ops'"
            ))

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/operations", wait_until="load")
        page.locator('[data-ops-row="failed"]').wait_for()
        count = lambda name: int(page.locator(f'[data-ops-filter="{name}"] .ops-filter-count').inner_text())
        assert count("error") == 3
        assert count("ok") == 1
        assert count("waiting") == 1
        assert count("running") == count("queued") == count("stopped") == 1
        assert count("not_run") == 1
        assert page.locator('[data-ops-row="oom"] .ops-status-text').inner_text() == "Out of memory"
        assert page.locator('[data-ops-row="timeout"] .ops-status-text').inner_text() == "Timed out"
        assert page.locator('[data-ops-row="stopped"] .ops-status-text').inner_text() == "Stopped"

        page.locator('[data-ops-filter="error"]').click()
        assert page.locator('[data-ops-row]:visible').count() == 3
        page.locator('[data-ops-filter="oom"]').click()
        assert page.locator('[data-ops-row]:visible').evaluate_all("rows => rows.map(r => r.dataset.opsRow)") == ["oom"]
        assert page.locator('[data-ops-filter="oom"]').get_attribute("aria-pressed") == "true"

        # A hidden previous row must not steal focus from its visible neighbour.
        visible_button = page.locator('[data-ops-row="oom"] .ops-run-dropdown > .ops-row-btn')
        visible_button.focus()
        page.evaluate("_opsApplyStatus({running:{running:{elapsed_seconds:4}},queue:['queued']},_opsStatusRevision,_opsStatusCycle)")
        assert visible_button.evaluate("el => el === document.activeElement")

        page.locator('[data-ops-filter="all"]').click()
        page.evaluate("activeFolder='Data';renderOperations()")
        page.wait_for_function("document.querySelector('[data-ops-filter=all] .ops-filter-count')?.textContent === '3'")
        assert count("error") == 3
        page.evaluate("activeFolder='all';searchQuery='OOM';renderOperations()")
        page.wait_for_function("document.querySelector('[data-ops-filter=all] .ops-filter-count')?.textContent === '1'")
        assert count("oom") == 1
        page.locator('[data-ops-filter="timeout"]').click()
        assert page.locator('[data-ops-row]:visible').count() == 0
        assert page.locator('[data-ops-empty]').is_visible()
        page.locator('[data-ops-filter="all"]').click()

        page.evaluate("searchQuery='';renderOperations()")
        page.wait_for_function("document.querySelector('[data-ops-filter=all] .ops-filter-count')?.textContent === '9'")
        page.locator('[data-ops-filter="error"]').click()
        failed_row = page.locator('[data-ops-row="failed"]')
        page.evaluate("_opsExpandedSlugs.failed=true;renderOperations()")
        page.wait_for_selector('[data-ops-row="failed"]')
        page.wait_for_selector('[data-ops-row="failed"] + .ops-expand-row')
        failed_row = page.locator('[data-ops-row="failed"]')
        failed_row.evaluate("row => row.nextElementSibling.querySelector('.ops-terminal-body').setAttribute('tabindex','-1')")
        page.locator('[data-ops-row="failed"] + .ops-expand-row .ops-terminal-body').focus()
        page.evaluate("_opsApplyStatus({running:{failed:{elapsed_seconds:8},running:{elapsed_seconds:4}},queue:['queued']},_opsStatusRevision,_opsStatusCycle)")
        assert failed_row.is_hidden()
        assert failed_row.evaluate("row => row.nextElementSibling.hidden")
        assert page.locator('[data-ops-filter="error"]').evaluate("el => el === document.activeElement")
        assert count("error") == 2

        ops_reports[-1]["build_status"] = "error"
        page.evaluate("_opsApplyStatus({running:{failed:{elapsed_seconds:8},running:{elapsed_seconds:4}},queue:[]},_opsStatusRevision,_opsStatusCycle)")
        page.wait_for_function("document.querySelector('[data-ops-row=queued] .ops-status-text')?.textContent === 'Failed'")
        assert count("error") == 3
        assert not page.locator('[data-ops-row="queued"]').is_hidden()

        ops_reports[0]["build_status"] = "success"
        page.evaluate("_opsApplyStatus({running:{running:{elapsed_seconds:4}},queue:[]},_opsStatusRevision,_opsStatusCycle)")
        page.wait_for_function("document.querySelector('[data-ops-filter=error] .ops-filter-count')?.textContent === '2'")
        assert page.locator('[data-ops-row="failed"]').is_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_catalog_renders_before_slow_favorites_and_subscriptions(mobile_browser):
    context = mobile_browser.new_context()
    page = context.new_page()
    page.add_init_script("""(() => {
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = typeof input === 'string' ? input : input.url;
        if (url.endsWith('/api/me/favorites')) return new Promise(resolve => {
          window.finishFavorites = body => resolve(new Response(JSON.stringify(body), {
            status: 200, headers: {'Content-Type':'application/json'}
          }));
        });
        if (url.endsWith('/api/my-subscriptions')) return new Promise(resolve => {
          window.finishSubscriptions = body => resolve(new Response(JSON.stringify(body), {
            status: 200, headers: {'Content-Type':'application/json'}
          }));
        });
        return nativeFetch(input, init);
      };
    })();""")

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/ui.css"):
            route.fulfill(content_type="text/css", body=UI_CSS)
        elif path.endswith("/static/portal.css"):
            route.fulfill(content_type="text/css", body=PORTAL_CSS)
        elif path.endswith("/static/console-shell.css"):
            route.fulfill(content_type="text/css", body=CONSOLE_CSS)
        elif path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/static/console-shell.js"):
            route.fulfill(content_type="application/javascript", body=CONSOLE_JS)
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": REPORTS[:2]}))
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body='{"studios":[]}')
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML)

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/")
        page.locator('#content tr[data-slug="revenue-overview"]').wait_for()
        page.evaluate("activeFolder='Overview';render()")
        assert page.locator('#content tr[data-slug="revenue-overview"]').count() == 1
        page.evaluate("activeFolder='_favorites';render()")
        assert "Loading favorites" in page.locator("#content").inner_text()
        page.evaluate("finishFavorites({favorites:[{org_slug:'acme',studio_slug:'analytics',slug:'revenue-overview'}]})")
        page.locator('#content tr[data-slug="revenue-overview"]').wait_for()
        page.evaluate("finishSubscriptions({reports:{}})")
        assert page.locator('#content tr[data-slug="revenue-overview"]').count() == 1
    finally:
        context.close()


def test_favorites_failure_is_distinct_from_an_empty_list_and_retryable(mobile_browser):
    context = mobile_browser.new_context()
    page = context.new_page()
    attempts = []

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": REPORTS[:2]}))
        elif path.endswith("/api/me/favorites"):
            attempts.append(1)
            if len(attempts) == 1:
                route.fulfill(status=503, content_type="application/json", body='{}')
            else:
                route.fulfill(content_type="application/json", body=json.dumps({"favorites": []}))
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body='{"studios":[]}')
        elif path.endswith(".css") or path.endswith(".js"):
            route.fulfill(body="")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML)

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/")
        page.locator('#content tr[data-slug="revenue-overview"]').wait_for()
        page.evaluate("activeFolder='_favorites';render()")
        assert "Favorites could not be loaded" in page.locator("#content").inner_text()
        assert page.locator("[data-favorites-retry]").is_visible()
        page.locator("[data-favorites-retry]").click()
        page.wait_for_function("document.querySelector('#content').innerText.includes('No reports in this category')")
        assert len(attempts) == 2
    finally:
        context.close()


def test_favorites_body_timeout_remains_bounded_after_headers(mobile_browser):
    context = mobile_browser.new_context()
    page = context.new_page()
    page.add_init_script("""(() => {
      const nativeTimeout = window.setTimeout;
      window.setTimeout = (fn, ms, ...args) => nativeTimeout(fn, ms === 8000 ? 250 : ms, ...args);
      const nativeFetch = window.fetch.bind(window);
      let favoritesCalls = 0;
      window.fetch = (input, init) => {
        const url = typeof input === 'string' ? input : input.url;
        if (url.endsWith('/api/me/favorites') && ++favoritesCalls === 1) {
          let streamController;
          const body = new ReadableStream({start(controller) { streamController = controller; }});
          init.signal.addEventListener('abort', () => streamController.error(new DOMException('Aborted', 'AbortError')));
          return Promise.resolve(new Response(body, {status:200,headers:{'Content-Type':'application/json'}}));
        }
        return nativeFetch(input, init);
      };
    })();""")

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": REPORTS[:2]}))
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body='{"favorites":[]}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body='{"studios":[]}')
        elif path.endswith(".css") or path.endswith(".js"):
            route.fulfill(body="")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML)

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/")
        page.locator('#content tr[data-slug="revenue-overview"]').wait_for()
        page.evaluate("activeFolder='_favorites';render()")
        page.wait_for_function("document.querySelector('#content').innerText.includes('Favorites could not be loaded')")
    finally:
        context.close()


@pytest.mark.parametrize("kind,allow_export,capture", (("report", True, True), ("analysis", True, False), ("report", False, False)))
@pytest.mark.parametrize("marker", ("html", "body"))
@pytest.mark.parametrize("width", (390, 1024))
def test_capture_menu_calls_framework_only_for_exportable_data_reports(mobile_browser, kind, allow_export, capture, marker, width):
    menu = (settings.BASE_DIR / "static" / "report_menu.js").read_text(encoding="utf-8")
    body = f'''<html {f'data-content-kind="{kind}"' if marker == 'html' else ''}><head><style>
        .fw-export-wrap{{display:{"block" if allow_export else "none"}}}
        </style></head><body {f'data-content-kind="{kind}"' if marker == 'body' else ''}><div class="fw-header-right">
        <div class="fw-export-wrap"><div id="fwExportMenu"></div></div></div>
        <script>window.captures=0;window.fw={{captureForAnalysis:function(){{captures++;return Promise.resolve();}}}};</script>
        <script>{menu}</script></body></html>'''
    context = mobile_browser.new_context(viewport={"width": width, "height": 740})
    page = context.new_page()
    page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=body))
    try:
        page.goto("http://portal.test/share/example/")
        item = page.locator('[data-item-id="capture-analysis"]')
        assert item.count() == (1 if capture else 0)
        if capture:
            if width < 768:
                page.get_by_role("combobox", name="Report options").select_option(label="Capture for analysis")
            else:
                page.locator("#fwOptionsBtn").click()
                item.click()
            assert page.evaluate("window.captures") == 1
        else:
            assert page.evaluate("window.captures") == 0
    finally:
        context.close()


@pytest.mark.parametrize("width", (390, 1024))
def test_catalog_defaults_to_grouped_list(mobile_browser, width):
    context = mobile_browser.new_context(
        viewport={"width": width, "height": 740},
        has_touch=width < 768,
        is_mobile=width < 768,
    )
    context.add_init_script(
        "localStorage.setItem('trellum_portal_recent', "
        'JSON.stringify(["report-3", "report-2"]));'
    )
    page = context.new_page()

    def respond(route):
        path = urlsplit(route.request.url).path
        assets = {
            "/static/ui.css": ("text/css", UI_CSS),
            "/static/portal.css": ("text/css", PORTAL_CSS),
            "/static/console-shell.css": ("text/css", CONSOLE_CSS),
            "/static/portal.js": ("application/javascript", PORTAL_JS),
            "/static/console-shell.js": ("application/javascript", CONSOLE_JS),
        }
        if path in assets:
            content_type, body = assets[path]
            route.fulfill(content_type=content_type, body=body)
        elif path.endswith("/api/registry"):
            route.fulfill(
                content_type="application/json", body=json.dumps({"reports": REPORTS})
            )
        elif path.endswith("/api/me/favorites"):
            route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "favorites": [{
                        "org_slug": "acme",
                        "studio_slug": "analytics",
                        "slug": "report-2",
                        "report_id": 2,
                    }]
                }),
            )
        elif path.endswith("/api/my-subscriptions"):
            route.fulfill(content_type="application/json", body='{"reports":{}}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body="{}")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML)

    page.route("**/*", respond)
    page.goto("http://portal.test/s/acme/analytics/", wait_until="load")
    page.locator(".list-table tbody tr").first.wait_for()

    assert page.evaluate("localStorage.getItem('trellum_portal_view')") is None
    assert page.locator('[data-setview="list"]').evaluate(
        "el => el.classList.contains('active')"
    )
    assert page.locator("a.card").count() == 0
    assert page.locator(".section-title").all_text_contents() == [
        "Favorites",
        "Recently Viewed",
    ]
    assert page.locator(".list-view").count() == 1
    assert page.locator(".list-table tbody").evaluate(
        """body => Array.from(body.children).slice(0, 8).map(row =>
          row.classList.contains('list-group-row')
            ? 'group:' + row.textContent.trim()
            : row.dataset.slug)"""
    ) == [
        "group:Favorites",
        "report-2",
        "group:Recently Viewed",
        "report-3",
        "report-2",
        "group:Overview",
        "revenue-overview",
        "group:Category 2",
    ]
    assert page.locator(".category-label").all_text_contents()[:3] == [
        "Overview",
        "Category 2",
        "Category 3",
    ]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    first_row = page.locator(".list-table tbody tr[data-slug]").first
    assert first_row.evaluate("el => getComputedStyle(el).display") == (
        "grid" if width < 768 else "table-row"
    )
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    title = first_row.locator("a.list-title")
    assert title.evaluate(
        "el => getComputedStyle(el).color === getComputedStyle(el.parentElement).color"
    )
    assert title.evaluate("el => getComputedStyle(el).textDecorationLine") == "none"
    if width < 768:
        box = first_row.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
        assert box["height"] <= 400
    context.close()


@pytest.mark.parametrize("width", (390, 430))
def test_mobile_catalog_drawers_and_report_taps(mobile_browser, width):
    context = mobile_browser.new_context(
        viewport={"width": width, "height": 740}, has_touch=True, is_mobile=True
    )
    context.add_init_script(
        "if (localStorage.getItem('trellum_portal_view') === null) "
        "localStorage.setItem('trellum_portal_view', 'cards')"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/ui.css"):
            route.fulfill(content_type="text/css", body=UI_CSS)
        elif path.endswith("/static/portal.css"):
            route.fulfill(content_type="text/css", body=PORTAL_CSS)
        elif path.endswith("/static/console-shell.css"):
            route.fulfill(content_type="text/css", body=CONSOLE_CSS)
        elif path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/static/console-shell.js"):
            route.fulfill(content_type="application/javascript", body=CONSOLE_JS)
        elif "/r/revenue-overview/" in path:
            route.fulfill(content_type="text/html", body="<h1>Report opened</h1>")
        elif path.endswith("/api/registry"):
            route.fulfill(
                content_type="application/json", body=json.dumps({"reports": REPORTS})
            )
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body='{"favorites":[]}')
        elif path.endswith("/api/my-subscriptions"):
            route.fulfill(content_type="application/json", body='{"reports":{}}')
        elif path.endswith("/api/system/log"):
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"log": "line\n" * 200}),
            )
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body="{}")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML)

    page.route("**/*", respond)
    page.goto("http://portal.test/s/acme/analytics/", wait_until="load")
    page.locator("a.card").first.wait_for()

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    category = page.locator(".category-select select")
    assert category.is_visible()
    assert category.locator("option").count() == 12
    assert category.locator("option").first.text_content() == "All (11)"
    if width == 390:
        _capture(page, mobile_browser, "catalog-390.png")
    assert (
        page.locator("#searchInput").evaluate("el => getComputedStyle(el).fontSize")
        == "16px"
    )
    assert (
        page.locator("#cardSortSelect").evaluate("el => getComputedStyle(el).fontSize")
        == "16px"
    )
    sidebar = page.locator("#consoleSidebar")
    assert sidebar.get_attribute("aria-hidden") == "true"
    assert sidebar.get_attribute("inert") == ""
    assert sidebar.evaluate("el => getComputedStyle(el).visibility") == "hidden"
    assert sidebar.evaluate("el => getComputedStyle(el).pointerEvents") == "none"
    for drawer_id in ("errorLogDrawer", "validationDrawer", "serverLogDrawer"):
        drawer = page.locator(f"#{drawer_id}")
        assert drawer.get_attribute("aria-hidden") == "true"
        assert drawer.get_attribute("inert") == ""
        assert drawer.evaluate("el => getComputedStyle(el).display") == "none"
        assert drawer.evaluate("el => getComputedStyle(el).visibility") == "hidden"
        assert drawer.evaluate("el => getComputedStyle(el).pointerEvents") == "none"

    initial_url = page.url
    page.locator("[data-tag='revenue']").tap()
    assert "q=revenue" in page.url
    assert page.locator("a.card").count() == 1

    page.locator("[data-email='revenue-overview']").tap()
    assert page.evaluate("deliveryClicks") == [
        ["/s/acme/analytics", "revenue-overview"]
    ]
    assert page.url != initial_url
    assert "r/revenue-overview" not in page.url

    error_button = page.locator("#errorLogBtn")
    error_button.focus()
    error_button.tap()
    drawer = page.locator("#errorLogDrawer")
    assert drawer.get_attribute("aria-hidden") == "false"
    assert drawer.get_attribute("inert") is None
    page.wait_for_timeout(300)
    box = drawer.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert drawer.evaluate("el => el.contains(document.activeElement)")
    assert (
        drawer.locator(".error-log-body").evaluate(
            "el => getComputedStyle(el).overflowY"
        )
        == "auto"
    )
    page.keyboard.press("Escape")
    assert drawer.get_attribute("aria-hidden") == "true"
    assert error_button.evaluate("el => el === document.activeElement")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    with page.expect_navigation(wait_until="load"):
        page.locator("a.card").tap(position={"x": 40, "y": 70})
    assert urlsplit(page.url).path.endswith("/r/revenue-overview/index.html")
    assert "display=console" in page.url
    assert errors == []
    context.close()


@pytest.mark.parametrize("width", (320, 390, 430, 768, 1024, 1440))
def test_mobile_category_and_saved_list_mode_are_reachable(mobile_browser, width):
    context = mobile_browser.new_context(
        viewport={"width": width, "height": 740},
        has_touch=width < 768,
        is_mobile=width < 768,
    )
    context.add_init_script(
        "if (localStorage.getItem('trellum_portal_view') === null) "
        "localStorage.setItem('trellum_portal_view', 'cards')"
    )
    page = context.new_page()

    def respond(route):
        path = urlsplit(route.request.url).path
        assets = {
            "/static/ui.css": ("text/css", UI_CSS),
            "/static/portal.css": ("text/css", PORTAL_CSS),
            "/static/console-shell.css": ("text/css", CONSOLE_CSS),
            "/static/portal.js": ("application/javascript", PORTAL_JS),
            "/static/console-shell.js": ("application/javascript", CONSOLE_JS),
        }
        if path in assets:
            content_type, body = assets[path]
            route.fulfill(content_type=content_type, body=body)
        elif path.endswith("/api/registry"):
            route.fulfill(
                content_type="application/json", body=json.dumps({"reports": REPORTS})
            )
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body='{"favorites":[]}')
        elif path.endswith("/api/my-subscriptions"):
            route.fulfill(content_type="application/json", body='{"reports":{}}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body="{}")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML)

    page.route("**/*", respond)
    page.goto("http://portal.test/s/acme/analytics/", wait_until="load")
    page.locator("a.card").first.wait_for()
    summary_text = page.locator(".summary-bar").inner_text().lower()
    assert "healthy" not in summary_text
    assert "errors" not in summary_text
    assert "not run" not in summary_text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.locator("#searchInput").evaluate(
        """el => {
          el.value = 'Player';
          el.dispatchEvent(new Event('input', {bubbles:true}));
        }"""
    )
    page.locator(".summary-search").wait_for()
    assert "Found 1 report" in " ".join(
        page.locator(".summary-search").inner_text().split()
    )
    page.locator(".search-clear").click()
    assert page.locator("#searchInput").input_value() == ""

    if width >= 768:
        page.locator("#searchInput").evaluate(
            """el => {
              el.value = 'x'.repeat(200);
              el.dispatchEvent(new Event('input', {bubbles:true}));
            }"""
        )
        clear = page.locator(".search-clear")
        clear.wait_for()
        assert clear.bounding_box()["x"] + clear.bounding_box()["width"] <= width
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        clear.click()

    page.locator("#cardSortSelect").select_option("name")
    assert page.evaluate("localStorage.getItem('trellum_portal_card_sort')") == "name"

    if width < 768:
        page.locator(".category-select select").select_option(label="Category 2 (1)")
        assert page.locator("a.card").count() == 1
        assert "tab=Category+2" in page.url
        page.locator(".category-select select").select_option("all")

    page.locator('[data-setview="list"]').click()
    row = page.locator('.list-table tbody tr[data-slug="revenue-overview"]')
    expected_display = "grid" if width < 768 else "table-row"
    assert row.evaluate("el => getComputedStyle(el).display") == expected_display
    if width < 768:
        sort_select = page.locator("#listSortSelect")
        assert sort_select.is_visible()
        assert sort_select.evaluate("el => getComputedStyle(el).minHeight") == "44px"
        assert page.locator(".list-sort-label").evaluate(
            "el => getComputedStyle(el).display"
        ) == "flex"
        direction = page.locator("#listSortDirection")
        toggle = page.locator(".summary-right .view-toggle")
        assert direction.is_visible()
        assert direction.bounding_box()["height"] >= 44
        assert toggle.locator(".view-btn").first.bounding_box()["height"] >= 44
        sort_select.select_option("category")
    else:
        sort_select = page.locator("#listSortSelect")
        direction = page.locator("#listSortDirection")
        toggle = page.locator(".summary-right .view-toggle")
        assert sort_select.is_visible() and direction.is_visible()
        assert page.locator(".list-sort-label").evaluate(
            "el => getComputedStyle(el).display"
        ) == "flex"
        centers = [
            (element.bounding_box()["y"] + element.bounding_box()["height"] / 2)
            for element in (sort_select, direction, toggle)
        ]
        assert max(centers) - min(centers) < 1
        page.locator('[data-sort="category"]').first.click()
    assert page.evaluate("localStorage.getItem('trellum_portal_view')") == "list"
    assert page.evaluate("localStorage.getItem('trellum_portal_sort_col')") == "category"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    if width < 768:
        row_box = row.bounding_box()
        assert row_box["x"] >= 0 and row_box["x"] + row_box["width"] <= width
        assert row_box["height"] <= 400
        tags = row.locator(".list-tags .tag")
        assert tags.count() == 6
        assert tags.evaluate_all(
            "(els, right) => els.every(el => el.getBoundingClientRect().right <= right + 0.5)",
            row_box["x"] + row_box["width"],
        )
        description = row.locator(".list-desc")
        assert description.get_attribute("title") == REPORT["description"]
        assert description.evaluate("el => getComputedStyle(el).webkitLineClamp") == "2"

        category = row.locator('[data-label="Category"]')
        studio = row.locator('[data-label="Studio"]')
        status = row.locator('[data-label="Status"]')
        schedule = row.locator('[data-label="Schedule"]')
        assert abs(category.bounding_box()["y"] - studio.bounding_box()["y"]) < 1
        assert abs(status.bounding_box()["y"] - schedule.bounding_box()["y"]) < 1
        for selector in (".fav-btn", ".email-btn"):
            target = row.locator(selector).bounding_box()
            assert target["width"] >= 44 and target["height"] >= 44

        # A single unbroken user-defined tag must stay inside the record too.
        tags.last.evaluate(
            "el => el.textContent = 'segmentation-with-a-very-long-unbroken-custom-label'"
        )
        assert tags.last.bounding_box()["x"] + tags.last.bounding_box()["width"] <= (
            row.bounding_box()["x"] + row.bounding_box()["width"] + 0.5
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if width == 390:
            _capture_list(page, mobile_browser)

    page.reload(wait_until="load")
    page.locator(".list-table tbody tr").first.wait_for()
    assert page.evaluate("localStorage.getItem('trellum_portal_view')") == "list"
    assert page.locator('[data-setview="list"]').evaluate(
        "el => el.classList.contains('active')"
    )
    page.set_viewport_size({"width": 1200, "height": 740})
    assert page.evaluate("localStorage.getItem('trellum_portal_view')") == "list"
    assert page.locator(".list-table tbody tr").first.evaluate(
        "el => getComputedStyle(el).display"
    ) == "table-row"
    context.close()


@pytest.mark.parametrize("width", (390, 768, 1024, 1641))
def test_operations_records_and_actions_are_reachable(mobile_browser, width):
    context = mobile_browser.new_context(
        viewport={"width": width, "height": 740},
        has_touch=width < 768,
        is_mobile=width < 768,
    )
    page = context.new_page()
    ops_reports = [
        {
            **report,
            "validation": {
                "summary": {"pass": 2, "warn": 0, "fail": 0},
                "checks": [],
            },
        }
        for report in REPORTS[:2]
    ]
    ops_html = PORTAL_HTML.replace("initial_view:'reports'", "initial_view:'ops'").replace(
        "<h1>Reports</h1>", "<h1>Operations</h1>"
    )

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/ui.css"):
            route.fulfill(content_type="text/css", body=UI_CSS)
        elif path.endswith("/static/portal.css"):
            route.fulfill(content_type="text/css", body=PORTAL_CSS)
        elif path.endswith("/static/console-shell.css"):
            route.fulfill(content_type="text/css", body=CONSOLE_CSS)
        elif path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/static/console-shell.js"):
            route.fulfill(content_type="application/javascript", body=CONSOLE_JS)
        elif path.endswith("/api/registry"):
            route.fulfill(
                content_type="application/json", body=json.dumps({"reports": ops_reports})
            )
        elif path.endswith("/api/system/status"):
            route.fulfill(
                content_type="application/json",
                body='{"running":{},"queue":[],"max_concurrent":10}',
            )
        elif path.endswith("/api/system/git/status"):
            route.fulfill(content_type="application/json", body='{"configured":false}')
        elif path.endswith("/api/system/run-stats"):
            route.fulfill(content_type="application/json", body='{"stats":{}}')
        elif "/api/reports/" in path and path.endswith("/status"):
            route.fulfill(
                content_type="application/json", body='{"aggregates":{},"history":[]}'
            )
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body='{"favorites":[]}')
        elif path.endswith("/api/my-subscriptions"):
            route.fulfill(content_type="application/json", body='{"reports":{}}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body="{}")
        else:
            route.fulfill(content_type="text/html", body=ops_html)

    page.route("**/*", respond)
    page.goto("http://portal.test/s/acme/analytics/operations", wait_until="load")
    row = page.locator("[data-ops-row]").first
    row.wait_for()
    assert row.evaluate("el => getComputedStyle(el).display") == (
        "grid" if width < 768 else "table-row"
    )
    for label in ("Report", "Schedule", "Status"):
        assert row.locator(f'td[data-label="{label}"]').is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # The integration JS renders these closed at every width. Normalize the
    # older fixture copy so this CSS-focused test exercises that final state.
    page.locator(".ops-secondary").evaluate_all(
        "elements => elements.forEach(element => { element.open = false; })"
    )

    run_button = row.locator(".ops-run-dropdown > .ops-row-btn")
    more_button = row.locator(".ops-secondary > summary")
    assert run_button.is_visible()
    assert more_button.is_visible()
    run_box = run_button.bounding_box()
    more_box = more_button.bounding_box()
    assert max(
        more_box["x"] - (run_box["x"] + run_box["width"]),
        more_box["y"] - (run_box["y"] + run_box["height"]),
    ) >= 7
    if width < 768:
        assert run_box["height"] >= 44 and more_box["height"] >= 44

    run_button.click()
    assert row.locator(".ops-run-menu").is_visible()
    page.evaluate("document.body.click()")
    assert not row.locator(".ops-run-menu").is_visible()

    secondary = row.locator(".ops-secondary")
    assert secondary.get_attribute("open") is None
    if width == 390:
        _capture(page, mobile_browser, "operations-collapsed-390.png")
    more_button.click()
    assert secondary.get_attribute("open") == ""
    menu = row.locator(".ops-secondary-menu")
    assert menu.is_visible()
    menu_box = menu.bounding_box()
    assert menu_box["x"] >= 0 and menu_box["x"] + menu_box["width"] <= width
    actions = menu.locator(".ops-row-btn")
    assert actions.count() == 3
    assert [action.get_attribute("title") for action in actions.all()] == [
        "Open report",
        "Toggle log",
        "Report details",
    ]
    action_boxes = [action.bounding_box() for action in actions.all()]
    assert all(
        action_boxes[index + 1]["y"]
        - (action_boxes[index]["y"] + action_boxes[index]["height"])
        >= 3
        for index in range(len(action_boxes) - 1)
    )
    if width < 768:
        assert all(box["height"] >= 44 for box in action_boxes)

    log_button = row.locator('.ops-secondary-menu button[title="Toggle log"]')
    assert log_button.is_visible()
    log_button.click()
    page.locator(".ops-expand-row").wait_for()
    if width == 390:
        _capture(page, mobile_browser, "operations-expanded-390.png")
    assert page.locator(".ops-terminal-body").evaluate(
        "el => ['auto','scroll'].includes(getComputedStyle(el).overflowY)"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    context.close()


def test_operations_transition_reconciliation_cannot_overwrite_newer_action(mobile_browser):
    context = mobile_browser.new_context()
    page = context.new_page()
    ops_reports = [{**REPORT, "last_status": "error", "last_error": "previous failure"}]
    statuses = [
        {"running": {REPORT["slug"]: {"elapsed_seconds": 12}}, "queue": []},
        {"running": {}, "queue": []},
        {"running": {REPORT["slug"]: {"elapsed_seconds": 30}}, "queue": []},
    ]
    status_calls = []

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/static/console-shell.js"):
            route.fulfill(content_type="application/javascript", body=CONSOLE_JS)
        elif path.endswith(".css"):
            route.fulfill(content_type="text/css", body="")
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": ops_reports}))
        elif path.endswith("/api/system/status"):
            status_calls.append(len(status_calls))
            value = statuses[min(len(status_calls) - 1, len(statuses) - 1)]
            route.fulfill(content_type="application/json", body=json.dumps(value))
        elif path.endswith("/api/system/git/status"):
            route.fulfill(content_type="application/json", body='{"configured":false}')
        elif path.endswith("/api/system/run-stats"):
            route.fulfill(content_type="application/json", body='{"stats":{}}')
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body='{"favorites":[]}')
        elif path.endswith("/api/my-subscriptions"):
            route.fulfill(content_type="application/json", body='{"reports":{}}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body="{}")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML.replace(
                "initial_view:'reports'", "initial_view:'ops'"
            ))

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/operations", wait_until="load")
        row = page.locator(f'[data-ops-row="{REPORT["slug"]}"]')
        row.wait_for()
        page.evaluate("window.__rowIdentity = document.querySelector('[data-ops-row]')")
        page.evaluate("""(() => {
          const nativeFetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const url = typeof input === 'string' ? input : input.url;
            if (window.__holdRegistry && url.includes('/api/registry')) {
              window.__holdRegistry = false;
              return new Promise(resolve => { window.__releaseRegistry = () => resolve(
                new Response(JSON.stringify({reports:[{slug:'revenue-overview',name:'Player Overview',last_status:'success'}]}),
                  {status:200,headers:{'Content-Type':'application/json'}})); });
            }
            return nativeFetch(input, init);
          };
          window.__holdRegistry = true;
          _opsScheduleStatusPoll(0);
        })();""")
        page.wait_for_function("typeof window.__releaseRegistry === 'function'")
        assert page.evaluate("_opsStatusInFlight")
        page.evaluate("_opsRefreshAfterAction('revenue-overview', false)")
        page.evaluate("window.__releaseRegistry()")
        page.wait_for_function("document.querySelector('[data-ops-row] .ops-status.running') !== null")
        assert page.evaluate("reports[0].last_status") == "error"
        assert page.evaluate("window.__rowIdentity === document.querySelector('[data-ops-row]')")
        assert page.evaluate("_opsStatusInFlight") is False
    finally:
        context.close()


def test_operations_rerun_replaces_terminal_only_with_current_run(mobile_browser):
    context = mobile_browser.new_context()
    page = context.new_page()
    ops_reports = [{**REPORT, "last_status": "error", "last_error": "previous failure"}]
    latest_log = {"status": "error", "stderr": "previous failure"}
    ops_status = {"running": {}, "queue": []}
    run_rejected = False

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/static/portal.js"):
            route.fulfill(content_type="application/javascript", body=PORTAL_JS)
        elif path.endswith("/static/console-shell.js"):
            route.fulfill(content_type="application/javascript", body=CONSOLE_JS)
        elif path.endswith(".css"):
            route.fulfill(content_type="text/css", body="")
        elif path.endswith("/api/registry"):
            route.fulfill(content_type="application/json", body=json.dumps({"reports": ops_reports}))
        elif path.endswith("/api/system/status"):
            route.fulfill(content_type="application/json", body=json.dumps(ops_status))
        elif path.endswith("/api/system/git/status"):
            route.fulfill(content_type="application/json", body='{"configured":false}')
        elif path.endswith("/api/system/run-stats"):
            route.fulfill(content_type="application/json", body='{"stats":{}}')
        elif path.endswith("/api/me/favorites"):
            route.fulfill(content_type="application/json", body='{"favorites":[]}')
        elif path.endswith("/api/my-subscriptions"):
            route.fulfill(content_type="application/json", body='{"reports":{}}')
        elif path.endswith("/api/reports/revenue-overview/status"):
            route.fulfill(content_type="application/json", body='{"aggregates":{},"history":[]}')
        elif path.endswith("/api/reports/revenue-overview/log/live"):
            route.fulfill(content_type="application/json", body='{"slug":"revenue-overview","stdout_tail":""}')
        elif path.endswith("/api/reports/revenue-overview/log"):
            route.fulfill(content_type="application/json", body=json.dumps(latest_log))
        elif path.endswith("/api/reports/revenue-overview/run"):
            if run_rejected:
                route.fulfill(status=503, content_type="application/json", body='{"ok":false,"message":"queue unavailable"}')
            else:
                route.fulfill(content_type="application/json", body='{"ok":true,"status":"queued"}')
        elif "/api/" in path:
            route.fulfill(content_type="application/json", body="{}")
        else:
            route.fulfill(content_type="text/html", body=PORTAL_HTML.replace(
                "initial_view:'reports'", "initial_view:'ops'"
            ))

    page.route("**/*", respond)
    try:
        page.goto("http://portal.test/s/acme/analytics/operations", wait_until="load")
        row = page.locator(f'[data-ops-row="{REPORT["slug"]}"]')
        row.wait_for()
        queued_terminal = page.evaluate("""(() => {
          _opsExpandedSlugs['revenue-overview'] = true;
          const holder = document.createElement('div');
          holder.innerHTML = _buildOpsHTML({running:{},queue:[{slug:'revenue-overview'}]}, reports);
          const terminal = holder.querySelector('#opsTerm_revenue-overview');
          const state = {title:terminal.querySelector('.ops-terminal-title').textContent,
            failed:terminal.classList.contains('error'), body:terminal.querySelector('.ops-terminal-body').textContent};
          delete _opsExpandedSlugs['revenue-overview'];
          return state;
        })()""")
        assert "Queued" in queued_terminal["title"]
        assert queued_terminal["failed"] is False
        assert "Failed" not in queued_terminal["body"]
        page.evaluate("""(() => {
          const nativeFetch = window.fetch.bind(window);
          const nativeNow = Date.now.bind(Date);
          window.__nativeNow = nativeNow;
          window.__runStartedAt = nativeNow();
          window.__advanceRunClock = () => window.__runStartedAt + 11000;
          window.fetch = (input, init) => {
            const url = typeof input === 'string' ? input : input.url;
            if (window.__holdRun && url.includes('/api/reports/revenue-overview/run')) {
              window.__holdRun = false;
              return new Promise(resolve => { window.__releaseRun = () => resolve(
                new Response(JSON.stringify({ok:true,status:'queued'}),
                  {status:200,headers:{'Content-Type':'application/json'}})); });
            }
            if (window.__holdOldStatus && url.includes('/api/system/status')) {
              window.__holdOldStatus = false;
              return new Promise(resolve => { window.__releaseOldStatus = () => resolve(
                new Response(JSON.stringify({running:{},queue:[]}),
                  {status:200,headers:{'Content-Type':'application/json'}})); });
            }
            if (window.__holdOldLog && url.includes('/log?run=0')) {
              window.__holdOldLog = false;
              return new Promise(resolve => { window.__releaseOldLog = () => resolve(
                new Response(JSON.stringify({status:'error',stderr:'previous failure'}),
                  {status:200,headers:{'Content-Type':'application/json'}})); });
            }
            return nativeFetch(input, init);
          };
          window.__holdOldLog = true;
          opsToggleExpand('revenue-overview');
        })();""")
        page.wait_for_function("typeof window.__releaseOldLog === 'function'")
        terminal = page.locator("#opsTerm_revenue-overview")
        assert "Failed" in terminal.locator(".ops-terminal-title").inner_text()
        assert "error" in (terminal.get_attribute("class") or "")

        page.wait_for_function("!_opsStatusInFlight")
        page.evaluate("window.__holdOldStatus = true; _opsScheduleStatusPoll(0)")
        page.wait_for_function("typeof window.__releaseOldStatus === 'function'")
        page.evaluate("window.__holdRun = true")
        page.evaluate("opsRun('revenue-overview', 'normal')")
        assert "Starting" in terminal.locator(".ops-terminal-title").inner_text()
        assert "error" not in (terminal.get_attribute("class") or "")
        page.evaluate("window.__releaseOldStatus()")
        page.wait_for_timeout(50)
        assert "Starting" in terminal.locator(".ops-terminal-title").inner_text()
        page.evaluate("window.__releaseOldLog()")
        page.wait_for_timeout(50)
        assert "Starting" in terminal.locator(".ops-terminal-title").inner_text()
        assert "previous failure" not in terminal.inner_text()
        page.wait_for_function("typeof window.__releaseRun === 'function'")
        page.evaluate("window.__pendingRow = document.querySelector('[data-ops-row]'); renderOperations()")
        page.wait_for_function("document.querySelector('[data-ops-row]') !== window.__pendingRow")
        assert "Starting" in terminal.locator(".ops-terminal-title").inner_text()
        assert "error" not in (terminal.get_attribute("class") or "")
        assert "Starting" in row.locator('[data-label="Status"]').inner_text()
        assert row.locator(".ops-run-dropdown").count() == 0
        page.wait_for_function("!_opsStatusInFlight")
        page.evaluate("window.__holdOldStatus = true; delete window.__releaseOldStatus; _opsScheduleStatusPoll(0)")
        page.wait_for_function("typeof window.__releaseOldStatus === 'function'")
        ops_status = {"running": {}, "queue": [{"slug": REPORT["slug"]}]}
        page.evaluate("window.__releaseRun()")
        page.wait_for_function("!window._opsRunPending['revenue-overview']")
        page.evaluate("window.__releaseOldStatus()")
        page.wait_for_timeout(50)
        assert "error" not in (terminal.get_attribute("class") or "")
        assert "previous failure" not in terminal.inner_text()
        assert row.locator(".ops-status.error").count() == 0

        page.evaluate("_opsApplyStatus({running:{},queue:[{slug:'revenue-overview'}]},_opsStatusRevision,_opsStatusCycle)")
        assert "Queued" in terminal.locator(".ops-terminal-title").inner_text()
        assert "error" not in (terminal.get_attribute("class") or "")
        ops_status = {"running": {REPORT["slug"]: {"elapsed_seconds": 12}}, "queue": []}
        page.evaluate("_opsApplyStatus({running:{'revenue-overview':{elapsed_seconds:12}},queue:[]},_opsStatusRevision,_opsStatusCycle)")
        page.evaluate("Date.now = window.__advanceRunClock; _opsFetchLive('revenue-overview')")
        page.wait_for_timeout(50)
        assert "Running" in terminal.locator(".ops-terminal-title").inner_text()
        assert "error" not in (terminal.get_attribute("class") or "")
        page.evaluate("Date.now = window.__nativeNow")

        ops_reports[0] = {**REPORT, "last_status": "success"}
        latest_log = {"status": "success", "stdout": "new successful output"}
        ops_status = {"running": {}, "queue": []}
        page.evaluate("_opsApplyStatus({running:{},queue:[]},_opsStatusRevision,_opsStatusCycle)")
        page.wait_for_function("document.querySelector('#opsTerm_revenue-overview .ops-terminal-body')?.textContent.includes('new successful output')")
        assert "Last run" in terminal.locator(".ops-terminal-title").inner_text()
        assert "error" not in (terminal.get_attribute("class") or "")
        assert terminal.locator(".ops-terminal-body").evaluate("el => !el.classList.contains('live')")
        assert page.evaluate("!!_opsLivePollers['revenue-overview']") is False

        page.evaluate("opsRun('revenue-overview', 'normal')")
        page.wait_for_function("!window._opsRunPending['revenue-overview']")
        assert "error" not in (terminal.get_attribute("class") or "")
        ops_status = {"running": {REPORT["slug"]: {"elapsed_seconds": 2}}, "queue": []}
        page.evaluate("_opsApplyStatus({running:{'revenue-overview':{elapsed_seconds:2}},queue:[]},_opsStatusRevision,_opsStatusCycle)")
        ops_reports[0] = {**REPORT, "last_status": "error", "last_error": "new failure"}
        latest_log = {"status": "error", "stderr": "new failure"}
        ops_status = {"running": {}, "queue": []}
        page.evaluate("_opsApplyStatus({running:{},queue:[]},_opsStatusRevision,_opsStatusCycle)")
        page.wait_for_function("document.querySelector('#opsTerm_revenue-overview .ops-terminal-title')?.textContent.includes('Failed')")
        assert "error" in (terminal.get_attribute("class") or "")
        page.wait_for_function("document.querySelector('#opsTerm_revenue-overview .ops-terminal-body')?.textContent.includes('new failure')")
        assert "new failure" in terminal.inner_text()
        assert terminal.locator(".ops-terminal-body").evaluate("el => !el.classList.contains('live')")
        assert page.evaluate("!!_opsLivePollers['revenue-overview']") is False

        run_rejected = True
        page.evaluate("opsRun('revenue-overview', 'normal')")
        page.wait_for_function("document.querySelector('#opsTerm_revenue-overview .ops-terminal-title')?.textContent.includes('Run request unconfirmed')")
        assert "error" not in (terminal.get_attribute("class") or "")
    finally:
        context.close()
