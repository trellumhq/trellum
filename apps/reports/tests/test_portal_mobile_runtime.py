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
        assert page.locator("#listSortSelect").is_visible()
        page.locator("#listSortSelect").select_option("category")
    else:
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
