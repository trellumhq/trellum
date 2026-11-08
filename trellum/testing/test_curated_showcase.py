"""The visitor tour stays compact, navigable and truthful under local filters."""

import csv
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.runner.discovery import discover_report
from trellum.runner.events import local_annotation_events
from trellum.validation import validate_report


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_curated_tour_navigation_filters_and_views(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    source = Path(__file__).resolve().parents[1] / 'demo/reports/component-showcase'
    report = discover_report(str(source))
    ctx = ReportContext(config=report.load_config(str(source)), slug='component-showcase', output_dir=str(tmp_path))
    report().generate(ctx)
    assert len(ctx.sections) == 8
    events = local_annotation_events(ctx.config)
    result = validate_report(ctx, events=events, details=compute_details(ctx))
    assert result.summary['warn'] == result.summary['fail'] == result.summary['suppressed'] == 0, result.to_dict()
    render_report(ctx, str(tmp_path), auto_refresh=False, data={'_events': events})
    copy_vendor_tree(tmp_path)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors, missing = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            path = tmp_path / unquote(urlparse(route.request.url).path).lstrip('/')
            if path.is_file():
                route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            else:
                missing.append(route.request.url)
                route.fulfill(status=404, body='Missing asset')

        page.route('http://tour.test/**', serve)
        try:
            page.goto('http://tour.test/index.html', wait_until='networkidle')
            nav = page.get_by_role('navigation', name='Component families')
            expect(nav.get_by_role('link')).to_have_count(6)
            assert page.evaluate('''() => [...document.querySelectorAll('.tour-nav a')].every(a => document.getElementById(a.hash.slice(1)))''')
            metrics = page.locator('#sec-metrics')
            metrics.get_by_role('button', name='iOS', exact=True).click()
            expect(metrics.locator('.fw-kpi-value').first).to_have_text('$18.4k')
            if width == 390:
                metrics.get_by_role('button', name='+ 2 more', exact=True).click()
                expect(metrics.locator('.fw-kpi-grid').first.locator('.fw-kpi-card:visible')).to_have_count(6)
            trends = page.locator('#sec-time-series')
            trends.get_by_role('button', name='Android', exact=True).click()
            line = trends.locator('canvas[data-fw-title="Daily revenue"]')
            line.scroll_into_view_if_needed()
            line_id = line.get_attribute('id')
            page.wait_for_function('id => window._chartInstances[id]?.data.datasets[0].data[0] === 441', arg=line_id)
            assert round(line.bounding_box()['height']) == 220
            fit = page.evaluate('id => _chartInstances[id]._fwTrends', line_id)
            assert fit['reason'] == '' and fit['series'][0]['fit']['n'] == 31
            assert trends.locator('.fw-adaptive-events').count() >= 1
            expect(trends.get_by_role('button', name='Hide trend', exact=True)).to_be_visible()
            expect(metrics.locator('.fw-kpi-value').first).to_have_text('$18.4k')

            tables = page.locator('#sec-tables')
            tables.scroll_into_view_if_needed()
            table = tables.get_by_role('tabpanel').filter(visible=True)
            table.locator('input[data-table-search]').fill('Search')
            expect(table.locator('.fw-table-note').first).to_contain_text('16 matching')
            table.get_by_role('columnheader').filter(has_text='Spend').click()
            expect(table.locator('tbody tr')).to_have_count(10)
            with page.expect_download() as download:
                table.get_by_role('button', name='Download CSV', exact=True).click()
            with open(download.value.path(), encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 16 and {r['Channel'] for r in rows} == {'Search'}
            spends = [float(r['Spend']) for r in rows]
            assert spends == sorted(spends, reverse=True)
            table.get_by_role('button', name='Search · select Channel', exact=True).first.click()
            page.wait_for_function("fw.filterEngine.getFiltered('tour_campaigns').length === 16")
            expect(metrics.locator('.fw-kpi-value').first).to_have_text('$18.4k')
            for label in ('Comparison', 'Pivot', 'Hierarchy', 'Cohorts'):
                tables.get_by_role('tab', name=label, exact=True).click()
                expect(tables.get_by_role('tab', name=label, exact=True)).to_have_attribute('aria-selected', 'true')
                expect(tables.locator('table:visible')).to_have_count(1)
            cohorts = tables.locator('.fw-cohort')
            expect(cohorts.locator('tbody td')).to_have_count(30)

            patterns = page.locator('#sec-patterns-and-journeys')
            patterns.get_by_role('button', name='iOS', exact=True).click()
            patterns.get_by_role('tab', name='Sankey', exact=True).click()
            expect(patterns.locator('.fw-sankey')).to_have_attribute('data-sankey-state', 'ready')
            expect(patterns.locator('.fw-sankey-population')).to_contain_text('500 players')
            # Every family and each hidden chart view is genuinely initialized.
            for owner in ['tour_trend_views', 'tour_category_views', 'tour_pattern_views']:
                nav_group = page.locator(f'[data-tab-id="{owner}"]')
                for button in nav_group.get_by_role('tab').all():
                    button.click()
                    expect(button).to_have_attribute('aria-selected', 'true')
                    for canvas in page.locator('canvas:visible').all():
                        if canvas.locator('xpath=ancestor::*[contains(@class, "fw-sankey")]').count():
                            continue
                        canvas.scroll_into_view_if_needed()
                        page.wait_for_function('id => !!window._chartInstances[id]', arg=canvas.get_attribute('id'))
            # All compact charts retain distinct metric colors and their height.
            grid = page.locator('#sec-layouts-and-controls .fw-grid').first
            colors = []
            for canvas in grid.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
                cid = canvas.get_attribute('id')
                page.wait_for_function('id => !!window._chartInstances[id]', arg=cid)
                assert round(canvas.bounding_box()['height']) == 160
                colors.append(page.evaluate('id => window._chartInstances[id].data.datasets[0].borderColor', cid))
            assert len(colors) == len(set(colors)) == 6
            assert colors == page.evaluate('getThemeColors().chart_colors.slice(0,6)')
            page.evaluate("switchTheme('trellum dark')")
            assert grid.locator('canvas').evaluate_all('''els => els.map(el =>
                window._chartInstances[el.id].data.datasets[0].borderColor)''') == page.evaluate(
                    'getThemeColors().chart_colors.slice(0,6)')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            # Default route is a guided tour, not loading/error fixture content.
            assert 'No matching data' not in page.locator('body').inner_text()
            assert not errors and not missing
        finally:
            browser.close()
