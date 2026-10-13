"""Browser regression for accepted inactive filters in the health badge."""

from __future__ import annotations

import pytest

from trellum.assets import load_js


@pytest.fixture
def health_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<!doctype html><html><body>
      <div id="chart" class="fw-chart-container"></div>
      <div id="fwLoading"><span class="fw-loading-text"></span></div>
    </body></html>""")
    page.route("**/_meta.json", lambda route: route.fulfill(json={"last_run": "test"}))
    page.route("**/data.json*", lambda route: route.fulfill(json={"components": {}}))
    page.add_script_tag(content=load_js("data_loader.js"))
    page.evaluate("window._fwHealthAdminResolved = true; window._fwHealthAdmin = true;")
    yield page
    browser.close()
    pw.stop()


def _show(page, filters):
    page.evaluate("""filters => {
      var chart = document.getElementById('chart');
      chart.innerHTML = '';
      var comps = {chart: {type: 'line', dataset_id: 'actors'}};
      window._reportData = {_filter_health: {actors: filters}};
      window._attachFilterHealthBadges(comps);
    }""", filters)


def test_accepted_inactive_cells_do_not_count_but_remain_auditable(health_page):
    filters = [
        {"column": "service", "status": "active"},
        {"column": "date", "status": "propagated"},
        {"column": "actor", "status": "inactive", "suppressed": True},
        {"column": "severity", "status": "inactive"},
    ]
    _show(health_page, filters)

    assert health_page.locator(".fw-filter-health-badge").inner_text().startswith("⚠ 1/4 filter dead")
    assert health_page.locator(".fw-fhb-popover-item").count() == 4
    accepted = health_page.locator(".fw-fhb-popover-item").filter(has_text="actor")
    assert "accepted inactive" in accepted.inner_text()
    assert "Accepted by report validation" in accepted.inner_text()


def test_all_accepted_inactive_cells_emit_no_warning(health_page):
    _show(health_page, [
        {"column": "actor", "status": "inactive", "suppressed": True},
        {"column": "severity", "status": "inactive", "suppressed": True},
    ])

    assert health_page.locator(".fw-filter-health-badge").count() == 0


def test_empty_and_missing_health_do_not_revive_a_stale_badge(health_page):
    _show(health_page, [{"column": "actor", "status": "inactive"}])
    assert health_page.locator(".fw-filter-health-badge").count() == 1

    health_page.evaluate("window._reportData = {_filter_health: {actors: []}}; window._attachFilterHealthBadges({chart: {type: 'line', dataset_id: 'actors'}});")
    assert health_page.locator(".fw-filter-health-badge").count() == 0

    _show(health_page, [{"column": "actor", "status": "inactive"}])
    health_page.evaluate("window._reportData = {}; window._attachFilterHealthBadges({chart: {type: 'line', dataset_id: 'actors'}});")
    assert health_page.locator(".fw-filter-health-badge").count() == 0
