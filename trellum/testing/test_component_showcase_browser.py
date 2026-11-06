"""The combined demo must render each module and keep its filters independent."""

import csv
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.runner.discovery import discover_report
from trellum.runner.events import local_annotation_events, _weekday_highlight_config


def test_component_showcase_sections_and_independent_filters(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    source = Path(__file__).resolve().parents[1] / "demo/reports/component-showcase"
    report = discover_report(str(source))
    ctx = ReportContext(config=report.load_config(str(source)),
                        slug="component-showcase", output_dir=str(tmp_path))
    report().generate(ctx)
    events = local_annotation_events(ctx.config)
    assert len(events) == 13
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report
    result = validate_report(ctx, events=events, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, result.to_dict()
    render_report(ctx, str(tmp_path), auto_refresh=False, data={
        "_events": events, "_weekday_highlight": _weekday_highlight_config(ctx.config)})
    copy_vendor_tree(tmp_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
                if path.is_file():
                    route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                                  or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="Missing test asset")

            page.route("http://showcase.test/**", serve)
            page.goto("http://showcase.test/index.html", wait_until="networkidle")
            page.get_by_role("link", name="KPI metrics", exact=True).click()
            cards = page.locator("#sec-kpi-cards-and-compact-metrics .fw-kpi-grid")
            live = page.locator("#sec-filterable-kpi-row")
            assert cards.locator(".fw-kpi-value").first.inner_text() == "$184.2k"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$42.5k"
            live.get_by_role("button", name="iOS", exact=True).click()
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"

            page.get_by_role("link", name="line charts", exact=True).click()
            page.locator("#sec-line-example-filters").get_by_role(
                "button", name="Android", exact=True).click()
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="report controls", exact=True).click()
            tabs = page.locator('[data-tab-id="showcase_controls_tabs"]')
            tabs.get_by_role("tab", name="Revenue", exact=True).click()
            tabs.get_by_role("tab", name="Orders across all supported platforms — daily supplied totals").click()
            assert page.get_by_text("Orders by platform", exact=True).is_visible()
            page.get_by_role("link", name="line result states", exact=True).click()
            line_results = page.locator('#sec-line-results-—-local-subset-and-no-matches')
            line_results.scroll_into_view_if_needed()
            expect(line_results.locator('.fw-line-result-overlay')).to_have_text('No matching data')
            line_results.get_by_role('button', name='Measured zero', exact=True).click()
            expect(line_results.locator('.fw-line-result-overlay')).to_be_hidden()
            line_id = line_results.locator('canvas').get_attribute('id')
            assert page.evaluate('id => window._chartInstances[id].data.datasets[0].data', line_id) == [0, 0, 0]
            assert page.get_by_role('link', name='Live Result Examples', exact=True).get_attribute('href') == '../live-result-examples/'
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="data tables", exact=True).click()
            table = page.locator('.fw-section').filter(has=page.get_by_role(
                "heading", name="DataTable — filtered population", exact=True))
            table.locator("tbody tr").first.wait_for()
            table.locator('input[data-table-search]').fill("Reactivation")
            table.get_by_role("button", name="Android", exact=True).click()
            expect(table.locator(".fw-table-note")).to_contain_text("16 matching")
            table.get_by_role("columnheader").filter(has_text="Revenue").click()
            with page.expect_download() as download_info:
                table.get_by_role("button", name="Download CSV", exact=True).click()
            download = download_info.value
            assert download.suggested_filename == "component-showcase_table.csv"
            with open(download.path(), encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            assert len(rows) == 16
            assert all(row['Platform'] == 'Android' and row['Campaign'] == 'Reactivation'
                       for row in rows)
            expected = sorted(range(49, 80, 2), key=lambda i: -(95 + i * 31 % 140 + i / 1000))
            assert [row['Event ID'] for row in rows] == [f'{i + 1:05d}' for i in expected]
            assert table.locator('tbody tr').count() == 10
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="date ranges", exact=True).click()
            dates = page.locator('[data-fw-ds-id="showcase_dates_daily"].fw-filter-bar')
            dates.locator('.fw-date-trigger').click()
            dates.get_by_role("button", name="14D", exact=True).click()
            assert page.evaluate("window._fwFilterEngine.getFiltered('showcase_dates_daily').length") == 15
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            assert table.locator('tbody tr').count() == 10
            assert "16 matching" in table.locator('.fw-table-note').inner_text()
            page.keyboard.press("Escape")
            page.get_by_role("link", name="area charts", exact=True).click()
            area = page.locator('canvas[data-fw-title="Additive source mix · live filter"]')
            area.scroll_into_view_if_needed()
            page.locator('#sec-area-example-filters').get_by_role(
                "button", name="Android", exact=True).click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 1296",
                                   arg=area.get_attribute("id"))
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="comparison tables", exact=True).click()
            comparison = page.locator('#sec-filterable-comparison-context')
            comparison.get_by_role("button", name="Availability", exact=True).click()
            assert comparison.locator('tbody tr').count() == 3
            assert comparison.locator('tbody tr').first.locator('td').last.inner_text() == "—\nZero baseline"
            assert comparison.locator('tbody tr').first.locator('td').nth(2).inner_text() == "$480"
            assert table.locator('tbody tr').count() == 10
            page.get_by_role("link", name="doughnuts", exact=True).click()
            doughnut = page.locator('.fw-section').filter(has=page.get_by_role(
                "heading", name="Doughnuts — report filters and ring visibility", exact=True))
            doughnut.get_by_role("button", name="Android", exact=True).click()
            assert doughnut.locator('.fw-doughnut-center-value').inner_text() == "$40,000.00"
            doughnut.locator('.fw-doughnut-entry').first.click()
            assert doughnut.locator('.fw-doughnut-center-value').inner_text() == "$40,000.00"
            page.get_by_role("link", name="doughnut summaries", exact=True).click()
            summaries = page.locator('#sec-doughnut-summaries-—-filtered-category-amounts')
            summaries.scroll_into_view_if_needed()
            expect(summaries.locator('.fw-doughnut-summary-value')).to_have_text(['120', '90', '3'])
            summaries.get_by_role('button', name='iOS', exact=True).click()
            expect(summaries.locator('.fw-doughnut-summary-value')).to_have_text(['40', '30', '3'])
            assert doughnut.locator('.fw-doughnut-center-value').inner_text() == "$40,000.00"
            page.get_by_role('link', name='funnel summaries', exact=True).click()
            funnels = page.locator('#sec-funnel-summaries-—-filtered-stages')
            funnels.scroll_into_view_if_needed()
            expect(funnels.locator('.fw-chart-summary-value')).to_have_text(['60', '540', '3'])
            funnels.get_by_role('button', name='iOS', exact=True).click()
            expect(funnels.locator('.fw-chart-summary-value')).to_have_text(['20', '180', '3'])
            expect(summaries.locator('.fw-doughnut-summary-value')).to_have_text(['40', '30', '3'])
            page.get_by_role('link', name='Traffic composition', exact=True).click()
            traffic = page.locator('[id="sec-traffic-composition-—-shared-controls,-220px-plots"]')
            traffic.scroll_into_view_if_needed()
            traffic.get_by_role('button', name='iOS', exact=True).click()
            traffic.locator('[data-toggle-id="showcase_traffic_shared_220_mode"]').get_by_role(
                'button', name='Percentage', exact=True).click()
            stacks = page.evaluate('''() => Object.entries(_reportData.components)
                .filter(([id,cfg]) => cfg.stacked_bar_controls && cfg.dataset_id === 'showcase_traffic_shared_220')
                .map(([id]) => ({height: document.getElementById(id).parentElement.getBoundingClientRect().height,
                    firstTotal: _chartInstances[id].data.datasets.reduce((sum, ds) => sum + ds.data[0], 0)}))''')
            assert len(stacks) == 2
            assert all(s['height'] == 220 and abs(s['firstTotal'] - 100) < 1e-8 for s in stacks)
            page.get_by_role('link', name='Marketing geography', exact=True).click()
            marketing = page.locator('#sec-marketing-geography')
            expect(marketing.get_by_role('tab', name='Summary table', exact=True)).to_have_attribute('aria-selected', 'true')
            expect(marketing.locator('.fw-data-table:visible tbody tr')).to_have_count(4)
            expect(marketing.locator('.fw-data-table:visible tbody tr').first).to_contain_text('108.75%')
            marketing.get_by_role('tab', name='Geography panels', exact=True).click()
            marketing.locator('[data-fw-kind="MiniKpi"]').first.scroll_into_view_if_needed()
            marketing.get_by_role('button', name='iOS', exact=True).click()
            expect(marketing.locator('.fw-mini-val').first).to_have_text('840')
            page.get_by_role('link', name='Scatter summaries', exact=True).click()
            scatter = page.locator('#sec-scatter-summaries-—-filtered-coordinates-and-entries')
            scatter.scroll_into_view_if_needed()
            for canvas in scatter.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
            expect(scatter.locator('.fw-chart-summary-value')).to_have_text(['$15.75', '$22.50', '$4.00', '$5.00', '$40.00', '$60.00', '8'])
            scatter.get_by_role('button', name='iOS', exact=True).click()
            expect(scatter.locator('.fw-chart-summary-value')).to_have_text(['$10.50', '$15.00', '$4.00', '$5.00', '$20.00', '$30.00', '4'])
            expect(marketing.locator('.fw-mini-val').first).to_have_text('840')
            expect(funnels.locator('.fw-chart-summary-value')).to_have_text(['20', '180', '3'])
            page.get_by_role('link', name='Heatmap summaries', exact=True).click()
            heatmaps = page.locator('#sec-heatmap-summaries-—-filtered-cell-reducers-and-count')
            heatmaps.scroll_into_view_if_needed()
            for canvas in heatmaps.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
            expect(heatmaps.locator('.fw-chart-summary-value')).to_have_text(['540', '135', '0.00', '270', '4'])
            heatmaps.get_by_role('button', name='iOS', exact=True).click()
            expect(heatmaps.locator('.fw-chart-summary-value')).to_have_text(['180', '45', '0.00', '90', '4'])
            page.get_by_role('link', name='Stacked summaries', exact=True).click()
            stack_summaries = page.locator('#sec-stacked-summaries-—-selected-reducers')
            stack_summaries.scroll_into_view_if_needed()
            for canvas in stack_summaries.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
            expect(stack_summaries.locator('.fw-chart-summary-value')).to_have_text(['90', '45', '30', '60', '60'])
            stack_summaries.get_by_role('button', name='iOS', exact=True).click()
            expect(stack_summaries.locator('.fw-chart-summary-value')).to_have_text(['30', '15', '10', '20', '20'])
            expect(heatmaps.locator('.fw-chart-summary-value').first).to_have_text('180')
            page.get_by_role('link', name='Treemap summaries', exact=True).click()
            treemaps = page.locator('#sec-treemap-summaries-—-filtered-leaf-reducers-and-count')
            treemaps.scroll_into_view_if_needed()
            for canvas in treemaps.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
            expect(treemaps.locator('.fw-chart-summary-value')).to_have_text(['$900.00', '4', '$225.00', '$90.00', '$360.00'])
            treemaps.get_by_role('button', name='iOS', exact=True).click()
            expect(treemaps.locator('.fw-chart-summary-value')).to_have_text(['$300.00', '4', '$75.00', '$30.00', '$120.00'])
            expect(stack_summaries.locator('.fw-chart-summary-value').first).to_have_text('30')
            page.get_by_role('link', name='Sankey summaries', exact=True).click()
            sankey_summaries = page.locator('#sec-sankey-summaries-—-filtered-complete-paths')
            sankey_summaries.scroll_into_view_if_needed()
            for canvas in sankey_summaries.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
            expect(sankey_summaries.locator('.fw-chart-summary-value')).to_have_text(['1.0k', '260', '8'])
            sankey_summaries.get_by_role('button', name='Android', exact=True).click()
            expect(sankey_summaries.locator('.fw-chart-summary-value')).to_have_text(['500', '220', '4'])
            expect(treemaps.locator('.fw-chart-summary-value').first).to_have_text('$300.00')
            expect(scatter.locator('.fw-chart-summary-value').last).to_have_text('4')
            page.get_by_role('link', name='Combo summaries', exact=True).click()
            combos = page.locator('#sec-combo-summaries-—-axis-identity-and-reducers')
            combos.scroll_into_view_if_needed()
            for canvas in combos.locator('canvas').all():
                canvas.scroll_into_view_if_needed()
            expect(combos.locator('.fw-combo-summary-value')).to_have_text(
                ['$90.00', '$45.00', '$30.00', '$60.00', '$60.00', '27', '14', '$18.00', '180'])
            combos.get_by_role('button', name='iOS', exact=True).click()
            expect(combos.locator('.fw-combo-summary-value')).to_have_text(
                ['$30.00', '$15.00', '$10.00', '$20.00', '$20.00', '9', '5', '$6.00', '60'])
            expect(sankey_summaries.locator('.fw-chart-summary-value').first).to_have_text('500')
            page.get_by_role('link', name='keyboard controls', exact=True).click()
            keyboard = page.locator('#sec-keyboard-—-table-sorting-and-control-focus')
            amount = keyboard.get_by_role('columnheader', name='Amount')
            amount.focus()
            page.keyboard.press('Space')
            expect(amount).to_be_focused()
            expect(amount).to_have_attribute('aria-sort', 'descending')
            expect(keyboard.locator('tbody tr').first.locator('td').first).to_have_text('Largest amount')
            expect(combos.locator('.fw-combo-summary-value').first).to_have_text('$30.00')
            expect(marketing.locator('.fw-mini-val').first).to_have_text('840')
            page.get_by_role('link', name='Period comparison', exact=True).click()
            periods = page.locator('#sec-period-comparison')
            periods.get_by_role('button', name='iOS', exact=True).click()
            page.wait_for_function("fw.filterEngine.getFiltered('showcase_period_comparison')[0].A === 7192")
            periods.get_by_role('tab', name='Grouped comparison', exact=True).click()
            expect(periods.get_by_role('tab', name='Grouped comparison', exact=True)).to_have_attribute('aria-selected', 'true')
            periods.get_by_role('tab', name='Original dates on', exact=True).click()
            expect(periods.get_by_text('A: 2026-10-01 through 2026-10-31 inclusive · B: 2026-09-01 through 2026-09-30 inclusive · UTC calendar activity.', exact=True)).to_be_visible()
            expect(combos.locator('.fw-combo-summary-value').first).to_have_text('$30.00')
            expect(marketing.locator('.fw-mini-val').first).to_have_text('840')
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="pivot tables", exact=True).click()
            pivot = page.locator('.fw-section').filter(has=page.get_by_role(
                "heading", name="Pivot tables — revenue by title and platform", exact=True))
            pivot.get_by_role("button", name="Organic", exact=True).click()
            assert pivot.locator('tbody tr').count() == 2
            assert pivot.locator('tfoot td').last.inner_text() == "$5.7k"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="hierarchical tables", exact=True).click()
            hierarchy = page.locator('[id="sec-hierarchy-—-complete,-collapsed"]')
            tree = hierarchy.locator('.fw-hierarchy')
            expect(tree.locator('tfoot td').last).to_have_text('$17,850.00')
            tree.get_by_role('button', name='Paid search, 4 matching descendant facts', exact=True).click()
            expect(tree.locator('tbody tr')).to_have_count(5)
            expect(tree.locator('tfoot td').last).to_have_text('$17,850.00')
            with page.expect_download() as hierarchy_download:
                tree.locator('[data-leaf-csv]').click()
            with open(hierarchy_download.value.path(), encoding='utf-8', newline='') as file:
                hierarchy_rows = list(csv.DictReader(file))
            assert len(hierarchy_rows) == len({r['record_id'] for r in hierarchy_rows}) == 12
            assert all(r['row_type'] == 'leaf_fact' for r in hierarchy_rows)
            hierarchy.get_by_role('button', name='iOS', exact=True).click()
            expect(tree.locator('tfoot td').last).to_have_text('$10,250.00')
            hierarchy.get_by_role('button', name='Web', exact=True).click()
            expect(tree.locator('tfoot')).to_have_count(0)
            expect(tree).to_contain_text('No leaf facts match this selection.')
            hierarchy.get_by_role('button', name='All', exact=True).click()
            expect(tree.locator('tbody tr')).to_have_count(5)
            expect(tree.locator('tfoot td').last).to_have_text('$17,850.00')
            assert pivot.locator('tfoot td').last.inner_text() == "$5.7k"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="cohort tables", exact=True).click()
            cohort_section = page.locator('#sec-cohort-tables-—-supplied-snapshot-and-forecasts')
            cohorts = cohort_section.locator('.fw-cohort').first
            expect(cohorts.locator('tbody td')).to_have_count(30)
            expect(cohorts.locator('tbody tr').first.locator('td').nth(1)).to_have_text('0.00%')
            expect(cohorts.locator('[data-state="unavailable"]')).to_have_count(1)
            cohort_section.get_by_role('button', name='iOS', exact=True).click()
            expect(cohorts.locator('tbody td')).to_have_count(12)
            with page.expect_download() as cohort_download:
                cohorts.get_by_role('button', name='Download CSV', exact=True).click()
            with open(cohort_download.value.path(), encoding='utf-8', newline='') as file:
                cohort_rows = list(csv.DictReader(file))
            assert len(cohort_rows) == 12
            assert {r['cohort_month'] for r in cohort_rows} == {'2025-10', '2025-12'}
            assert any(r['display_state'] == 'forecast' for r in cohort_rows)
            cohort_section.get_by_role('button', name='Web', exact=True).click()
            expect(cohorts.locator('.fw-cohort-empty')).to_contain_text('No cohorts match')
            cohort_section.get_by_role('button', name='All', exact=True).click()
            expect(cohorts.locator('tbody td')).to_have_count(30)
            expect(tree.locator('tfoot td').last).to_have_text('$17,850.00')
            assert pivot.locator('tfoot td').last.inner_text() == "$5.7k"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='Sankey paths', exact=True).click()
            sankey_section = page.locator('#sec-sankey-—-complete-weekly-paths')
            sankey = sankey_section.locator('.fw-sankey')
            expect(sankey).to_have_attribute('data-sankey-state', 'ready')
            expect(sankey.locator('.fw-sankey-population')).to_contain_text('1000 players')
            sankey_section.get_by_role('button', name='iOS', exact=True).click()
            expect(sankey.locator('.fw-sankey-population')).to_contain_text('500 players')
            with page.expect_download() as sankey_download:
                sankey.get_by_role('button', name='Download CSV', exact=True).click()
            with open(sankey_download.value.path(), encoding='utf-8', newline='') as file:
                sankey_rows = list(csv.DictReader(file))
            assert len(sankey_rows) == 4 and sum(int(r['weight']) for r in sankey_rows) == 500
            sankey_section.get_by_role('button', name='All', exact=True).click()
            expect(sankey.locator('.fw-sankey-population')).to_contain_text('1000 players')
            expect(cohorts.locator('tbody td')).to_have_count(30)
            expect(tree.locator('tfoot td').last).to_have_text('$17,850.00')
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='Overview composition', exact=True).click()
            overview = page.locator('#sec-overview-—-synthetic-acquisition-cohorts')
            expect(overview.locator('.fw-kpi-value')).to_have_count(7)
            trend = overview.locator('canvas[data-fw-title="Daily installs"]')
            trend.scroll_into_view_if_needed()
            trend_id = trend.get_attribute('id')
            page.wait_for_function("id => window._chartInstances[id]?.data.datasets[0].data[0] === 849", arg=trend_id)
            assert round(trend.bounding_box()['height']) == 160
            overview.get_by_role('button', name='iOS', exact=True).click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 381", arg=trend_id)
            assert page.evaluate("window._fwFilterEngine.getFiltered('showcase_overview_daily').length") == 84
            expect(sankey.locator('.fw-sankey-population')).to_contain_text('1000 players')
            assert live.locator('.fw-kpi-value').first.inner_text() == '$18.4k'
            overview.get_by_role('button', name='All', exact=True).first.click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 849", arg=trend_id)
            page.get_by_role("link", name="line styles", exact=True).click()
            density = page.locator('[data-tab-id="showcase_line_density"]')
            density.get_by_role('tab', name='Sparse: 12 daily observations', exact=True).click()
            sparse = page.locator('canvas[data-fw-title="Sparse: 12 daily observations"]')
            sparse.scroll_into_view_if_needed()
            page.wait_for_function("id => window._chartInstances[id]?.getDatasetMeta(0).data.length === 12",
                                   arg=sparse.get_attribute('id'))
            assert page.evaluate("id => window._chartInstances[id].getDatasetMeta(0).data.every(p => p.options.radius === 2)",
                                 sparse.get_attribute('id'))
            page.get_by_role("link", name="combo charts", exact=True).click()
            combo_section = page.locator('#sec-combo-charts-—-daily-channel-observations')
            combo = combo_section.locator('canvas[data-fw-title="Currency bars and count lines · live"]')
            combo.scroll_into_view_if_needed()
            page.wait_for_function("id => !!window._chartInstances[id]", arg=combo.get_attribute('id'))
            combo_section.get_by_role('button', name='Direct', exact=True).click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 900",
                                   arg=combo.get_attribute('id'))
            legend = page.locator(f"#{combo.get_attribute('id')}-combo-context")
            assert legend.locator('[data-axis="y"] button').count() == 2
            assert legend.locator('[data-axis="y1"] button').count() == 2
            legend.locator('[data-axis="y1"]').get_by_role('button', name='Orders', exact=True).click()
            assert page.evaluate("id => window._chartInstances[id].data.datasets.map((d,i) => window._chartInstances[id].isDatasetVisible(i))",
                                 combo.get_attribute('id')) == [True, True, False, True]
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="heatmaps", exact=True).click()
            heatmap_section = page.locator('#sec-heatmaps-—-live-platform-quantities')
            heatmap = heatmap_section.locator('canvas')
            heatmap.scroll_into_view_if_needed()
            page.wait_for_function("id => !!window._chartInstances[id]", arg=heatmap.get_attribute('id'))
            heatmap_section.get_by_role('button', name='Android', exact=True).click()
            page.wait_for_function("""id => window._chartInstances[id].data.datasets[0].data.some(
                d => d.x === 'One' && d.y === 'Regular' && d.v === 75)""",
                arg=heatmap.get_attribute('id'))
            assert "Global linear" in heatmap_section.locator('.fw-heatmap-key').inner_text()
            sparse_heatmap = page.locator('#sec-heatmaps-—-zero-and-absent-coordinates canvas')
            sparse_heatmap.scroll_into_view_if_needed()
            page.wait_for_function("id => !!window._chartInstances[id]", arg=sparse_heatmap.get_attribute('id'))
            assert page.evaluate("id => window._chartInstances[id].data.datasets[0].data.map(d => d.v)",
                                 sparse_heatmap.get_attribute('id')) == [0, 40, 80]
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="treemaps", exact=True).click()
            treemap_section = page.locator('#sec-treemaps-—-filter-the-complete-population')
            treemap = treemap_section.locator('canvas')
            treemap.scroll_into_view_if_needed()
            page.wait_for_function("id => !!window._chartInstances[id]", arg=treemap.get_attribute('id'))
            treemap_section.locator('summary').click()
            treemap_section.get_by_role('button', name='Phone', exact=True).click()
            expect(treemap_section.locator('tbody tr')).to_have_count(6)
            assert treemap_section.locator('details').evaluate('el => el.open')
            assert treemap_section.locator('tbody tr').evaluate_all(
                'rows => rows.reduce((sum, row) => sum + Number(row.dataset.value), 0)') == 35015
            assert treemap_section.locator('tbody tr').first.get_attribute('data-path') == '["North","Store"]'
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="status text", exact=True).click()
            status_section = page.locator('#sec-status-text-with-declared-metric-directions')
            status_section.locator('tbody tr').first.wait_for()
            status_section.get_by_role('button', name='Context', exact=True).click()
            expect(status_section.locator('tbody tr')).to_have_count(2)
            assert status_section.locator('tbody tr').last.locator('td').last.inner_text() == '—\nNo prior period'
            page.locator('#fwThemeSelect').select_option('trellum light')
            positive = page.locator('#sec-readable-small-status-text .fw-kpi-delta.up').first
            assert positive.evaluate('el => getComputedStyle(el).color') == 'rgb(9, 109, 76)'
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='bar values and stack labels', exact=True).click()
            bar_section = page.locator('#sec-bar-values-—-category-and-platform-observations')
            bar = bar_section.locator('canvas[data-fw-title="Horizontal defaults · values automatic"]')
            bar.scroll_into_view_if_needed()
            bar_section.get_by_role('button', name='Mobile', exact=True).click()
            page.wait_for_function("""id => window._chartInstances[id]?._fwBarLabelPaint?.some(
                p => p.text === '$16.4k')""", arg=bar.get_attribute('id'))
            stack_section = page.locator('#sec-stack-labels-—-daily-source-and-platform-observations')
            stack = stack_section.locator('canvas[data-fw-title="Totals and fitting percentages · switch to Percentage"]')
            stack.scroll_into_view_if_needed()
            stack_section.get_by_role('button', name='Mobile', exact=True).click()
            stack_id = stack.get_attribute('id')
            page.locator(f'[data-normalize-toggle="{stack_id}"]').get_by_role('button', name='Percentage', exact=True).click()
            page.wait_for_function("""id => window._chartInstances[id]?._fwBarLabelPaint?.some(
                p => p.kind === 'percentage')""", arg=stack_id)
            assert page.evaluate("""id => window._chartInstances[id]._fwBarLabelPaint
                .filter(p => p.kind === 'total' && p.index === 0).map(p => p.text)""", stack_id) == ['$15.6k']
            assert page.locator(f'[data-stack-label-context="{stack_id}"]').count() == 1
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='KPI states and comparisons', exact=True).click()
            state_section = page.locator('#sec-kpi-states-—-supplied-observation-completeness')
            state_section.get_by_role('button', name='Missing', exact=True).click()
            expect(state_section.locator('.fw-kpi-value').nth(1)).to_have_text('—')
            assert state_section.locator('.fw-kpi-value').nth(3).inner_text() == '1'
            assert state_section.locator('.fw-kpi-state-note').filter(has_text='Complete revenue:').count() == 1
            cohort_section = page.locator('#sec-kpi-states-—-declared-cohort-age-and-horizon')
            cohort_section.get_by_role('button', name='Mature', exact=True).click()
            expect(cohort_section.locator('.fw-kpi-value').first).to_have_text('14.00%')
            cohort_section.get_by_role('button', name='Young', exact=True).click()
            expect(cohort_section.locator('.fw-kpi-value').first).to_contain_text('Age 3d · <7d')
            assert cohort_section.locator('.fw-kpi-forecast').first.inner_text() == 'Observed D3 · 8.00%'
            assert cohort_section.locator('.fw-kpi-sub').first.inner_text() == 'Forecast D7 · 15.00%'
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='chart context', exact=True).click()
            context_section = page.locator('#sec-chart-header-context-—-filtered-revenue-and-summary')
            context_canvas = context_section.locator('canvas')
            context_canvas.scroll_into_view_if_needed()
            context_section.get_by_role('button', name='Partner', exact=True).click()
            page.wait_for_function("id => window._chartInstances[id]?.data.datasets[0].data[0] === 52",
                                   arg=context_canvas.get_attribute('id'))
            expect(context_section.locator('.fw-chart-summary-value')).to_have_text('372')
            assert context_section.locator('.fw-chart-header-context').inner_text() == 'Orders; sum of matching completed orders'
            assert context_canvas.get_attribute('aria-describedby').split().count(
                context_canvas.get_attribute('id') + '-header-context') == 1
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='chart summaries', exact=True).click()
            summary_bar_section = page.locator('#sec-chart-summaries-—-filtered-horizontal-categories')
            summary_bar_section.get_by_role('button', name='Partner', exact=True).click()
            expect(summary_bar_section.locator('.fw-chart-summary-value')).to_have_text('70')
            summary_area_section = page.locator('#sec-chart-summaries-—-selected-area-band')
            summary_area_section.get_by_role('button', name='Direct', exact=True).click()
            expect(summary_area_section.locator('.fw-chart-summary-value')).to_have_text('4')
            expect(summary_bar_section.locator('.fw-chart-summary-value')).to_have_text('70')
            assert context_section.locator('.fw-chart-summary-value').inner_text() == '372'
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='table shading', exact=True).click()
            shading_section = page.locator('#sec-table-shading-—-filterable-cohort-metrics')
            shading_section.get_by_role('button', name='iOS', exact=True).click()
            expect(shading_section.locator('tbody tr')).to_have_count(3)
            assert shading_section.locator('tbody tr td:nth-child(4)').evaluate_all(
                "cells => cells.map(c => c.classList.contains('fw-table-shading-meets'))") == [True, True, False]
            shading_section.locator('input[data-table-search]').fill('launch')
            expect(shading_section.locator('.fw-table-note')).to_contain_text('2 matching')
            assert shading_section.locator('.fw-table-shading-key').count() == 1
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='category colors', exact=True).click()
            category_spend = page.locator('canvas[data-fw-title="Spend declared"]')
            category_revenue = page.locator('canvas[data-fw-title="Revenue declared"]')
            for chart in (category_spend, category_revenue):
                chart.scroll_into_view_if_needed()
                page.wait_for_function('id => !!window._chartInstances[id]', arg=chart.get_attribute('id'))
                assert page.evaluate("""id => Object.fromEntries(window._chartInstances[id].data.datasets
                    .map(d => [d.label, d.borderColor]))""", chart.get_attribute('id')) == {
                        'UA': '#0d9488', 'Retargeting': '#c9861b'}
            color_ring = page.locator('canvas[data-fw-title="Live declared"]')
            color_ring.scroll_into_view_if_needed()
            page.locator('#sec-category-colors-—-snapshot-and-filtered-rings').get_by_role(
                'button', name='North', exact=True).click()
            page.wait_for_function("id => window._chartInstances[id]?.data.datasets[0].data[0] === 323",
                                   arg=color_ring.get_attribute('id'))
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='line forecasts', exact=True).click()
            forecast_section = page.locator('#sec-line-forecasts-—-filtered-complete-historical-comparison')
            forecast_canvas = forecast_section.locator('canvas')
            forecast_canvas.scroll_into_view_if_needed()
            forecast_section.get_by_role('button', name='Mobile', exact=True).click()
            forecast_id = forecast_canvas.get_attribute('id')
            page.wait_for_function("id => window._chartInstances[id]?.data.datasets[1].data[0] === 49",
                                   arg=forecast_id)
            assert page.evaluate("id => window._chartInstances[id].data.datasets[1].borderDash", forecast_id) == [7, 4]
            assert forecast_section.locator('.fw-forecast-key').count() == 1
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='dated events', exact=True).click()
            event_canvas = page.locator('canvas[data-fw-title="Synthetic daily · grouped events"]')
            event_canvas.scroll_into_view_if_needed()
            event_id = event_canvas.get_attribute('id')
            event_details = page.locator(f'#{event_id}-event-details')
            expect(event_details.locator('summary')).to_contain_text('13 events')
            event_details.locator('summary').click()
            assert '2026-07-28 – 2026-08-05' in event_details.inner_text()
            event_section = page.locator('#sec-annotations-—-synthetic-independent-date-selection')
            event_window = event_section.locator('.fw-date-range')
            event_window.locator('.fw-date-trigger').click()
            event_window.locator('input[data-edge="min"]').fill('2026-08-18')
            event_window.locator('input[data-edge="min"]').dispatch_event('change')
            event_window.locator('input[data-edge="max"]').fill('2026-08-20')
            event_window.locator('input[data-edge="max"]').dispatch_event('change')
            expect(event_section.locator('.fw-event-details summary')).to_contain_text('1 event · 1 group')
            event_window.locator('.fw-date-trigger').click()
            event_section.get_by_role('button', name='Partner', exact=True).click()
            event_live_id = event_section.locator('canvas').get_attribute('id')
            page.wait_for_function("id => window._chartInstances[id]?.data.datasets[0].data[0] === 50",
                                   arg=event_live_id)
            expect(event_details.locator('summary')).to_contain_text('13 events')
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='month context', exact=True).click()
            month_static = page.locator('canvas[data-fw-title="Daily line · month context"]')
            month_static.scroll_into_view_if_needed()
            month_static_id = month_static.get_attribute('id')
            expect(page.locator(f'#{month_static_id}-month-context .fw-month-span')).to_have_count(3)
            month_section = page.locator('#sec-month-context-—-independent-daily-selection')
            month_line = month_section.locator('canvas[data-fw-title="Selected daily line"]')
            month_line.scroll_into_view_if_needed()
            month_id = month_line.get_attribute('id')
            month_section.get_by_role('button', name='Web', exact=True).click()
            month_section.get_by_role('button', name='Partner', exact=True).click()
            month_window = month_section.locator('.fw-date-range')
            month_window.locator('.fw-date-trigger').click()
            month_window.locator('input[data-edge="min"]').fill('2026-10-01')
            month_window.locator('input[data-edge="min"]').dispatch_event('change')
            month_window.locator('input[data-edge="max"]').fill('2026-10-03')
            month_window.locator('input[data-edge="max"]').dispatch_event('change')
            page.wait_for_function("""id => window._chartInstances[id]?.data.labels.length === 3
                && window._chartInstances[id]?.data.datasets[0].data[0] === 43""", arg=month_id)
            expect(page.locator(f'#{month_id}-month-context .fw-month-accessible')).to_have_text(['October 2026'])
            expect(page.locator(f'#{month_static_id}-month-context .fw-month-span')).to_have_count(3)
            month_window.locator('.fw-date-trigger').click()
            expect(event_details.locator('summary')).to_contain_text('13 events')
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='line availability', exact=True).click()
            availability_section = page.locator('#sec-line-availability-—-selected-supplied-observations')
            availability_canvas = availability_section.locator('canvas[data-fw-title="Complete selected orders"]')
            availability_canvas.scroll_into_view_if_needed()
            availability_id = availability_canvas.get_attribute('id')
            expect(page.locator(f'#{availability_id}-availability')).to_contain_text('2026-10-01')
            assert page.evaluate('id => window._chartInstances[id].data.datasets[0].data', availability_id) == [29, 0, None, 74]
            availability_section.get_by_role('button', name='Web', exact=True).click()
            expect(page.locator(f'#{availability_id}-availability')).to_have_count(0)
            assert page.evaluate('id => window._chartInstances[id].data.datasets[0].data', availability_id) == [12, 0, 24, 31]
            availability_section.get_by_role('button', name='App', exact=True).click()
            expect(page.locator(f'#{availability_id}-availability')).to_have_count(1)
            assert page.evaluate('id => window._chartInstances[id].data.datasets[0].data', availability_id) == [17, 0, None, 43]
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='monthly averages', exact=True).click()
            average_static = page.locator('canvas[data-fw-title="Daily orders · monthly averages"]')
            average_static.scroll_into_view_if_needed()
            average_static_id = average_static.get_attribute('id')
            expect(page.locator(f'#{average_static_id}-monthly-average tbody tr')).to_have_count(3)
            average_section = page.locator('#sec-monthly-averages-—-complete-selected-platform-contributions')
            average_canvas = average_section.locator('canvas')
            average_canvas.scroll_into_view_if_needed()
            average_id = average_canvas.get_attribute('id')
            page.wait_for_function('id => !!window._chartInstances[id]?._fwMonthlyAverage', arg=average_id)
            assert page.evaluate('id => window._chartInstances[id]._fwMonthlyAverage.rows.map(r=>r.mean)', average_id) == [10, 59]
            average_section.get_by_role('button', name='Web', exact=True).click()
            page.wait_for_function('id => window._chartInstances[id]?._fwMonthlyAverage.rows[1].mean === 35', arg=average_id)
            average_window = average_section.locator('.fw-date-range')
            average_window.locator('.fw-date-trigger').click()
            average_window.locator('input[data-edge="min"]').fill('2028-02-01')
            average_window.locator('input[data-edge="min"]').dispatch_event('change')
            average_window.locator('input[data-edge="max"]').fill('2028-02-02')
            average_window.locator('input[data-edge="max"]').dispatch_event('change')
            average_window.locator('.fw-date-trigger').click()
            expect(page.locator(f'#{average_id}-monthly-average tbody tr')).to_have_count(1)
            expect(page.locator(f'#{average_id}-monthly-average')).to_contain_text('2 / 2 days; 0 missing')
            average_bar = page.locator('canvas[data-fw-title="Complete selected daily bars"]')
            average_bar.scroll_into_view_if_needed()
            average_bar_section = page.locator('#sec-monthly-averages-—-native-daily-bars')
            average_bar_section.get_by_role('button', name='Direct', exact=True).click()
            page.wait_for_function('id => window._chartInstances[id]?._fwMonthlyAverage.rows[0].mean === 1', arg=average_bar.get_attribute('id'))
            assert page.evaluate('id => window._chartInstances[id]._fwMonthlyAverage.rows.map(r=>r.mean)', average_static_id) == [6, 12, 20]
            expect(page.locator(f'#{average_id}-monthly-average tbody tr')).to_have_count(1)
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='table row limits', exact=True).click()
            row_section = page.locator('#sec-datatable-row-limits-—-filtered')
            rows_choice = row_section.get_by_role('combobox', name='Rows', exact=True)
            row_metrics = row_section.locator('.fw-kpi-value')
            assert row_metrics.nth(1).inner_text() == '120'
            expect(row_section.locator('tbody tr')).to_have_count(10)
            rows_choice.select_option('all')
            expect(row_section.locator('tbody tr')).to_have_count(120)
            assert row_metrics.nth(1).inner_text() == '120'
            row_section.get_by_role('button', name='Android', exact=True).click()
            expect(row_section.locator('tbody tr')).to_have_count(60)
            assert row_metrics.nth(1).inner_text() == '60'
            row_section.locator('input[data-table-search]').fill('Campaign 120')
            expect(row_section.locator('tbody tr')).to_have_count(1)
            assert row_metrics.nth(1).inner_text() == '60'
            assert rows_choice.input_value() == 'all'
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role('link', name='table totals', exact=True).click()
            total_snapshot = page.locator('#sec-table-totals-—-exact-campaign-snapshot-off-and-on')
            expect(total_snapshot.locator('tfoot tr')).to_have_text('Total$100.1030$120.121.20x120.0%')
            total_events = page.locator('#sec-table-totals-—-all-matching-contributors')
            expect(total_events.locator('tbody tr')).to_have_count(10)
            expect(total_events.locator('.fw-table-totals-note')).to_contain_text('36 matching rows')
            expect(total_events.locator('.fw-kpi-value')).to_have_text('$876.00')
            page.locator('#sec-table-totals-—-delivered-event-filters').get_by_role(
                'button', name='Android', exact=True).click()
            expect(total_events.locator('.fw-table-totals-note')).to_contain_text('18 matching rows')
            expect(total_events.locator('.fw-kpi-value')).to_have_text('$456.00')
            total_events.locator('[data-table-search]').fill('Contributor 36')
            expect(total_events.locator('tbody tr')).to_have_count(1)
            expect(total_events.locator('tfoot tr')).to_have_text('Total$36.000$36.001.00x')
            expect(total_events.locator('.fw-kpi-value')).to_have_text('$456.00')
            expect(total_events.locator('.fw-table-totals-note')).to_contain_text('1 matching rows')
            assert row_metrics.nth(1).inner_text() == '60'
            assert live.locator('.fw-kpi-value').first.inner_text() == '$18.4k'
            page.get_by_role('link', name='table pinning', exact=True).click()
            pin_snapshot = page.locator('#sec-datatable-pinning-—-static-snapshots')
            for pin_table in pin_snapshot.locator('.fw-table-scroll').all():
                pin_table.scroll_into_view_if_needed()
            expect(pin_snapshot.locator('.fw-data-table-pinned')).to_have_count(3)
            pin_visits = page.locator('#sec-datatable-pinning-—-filterable-observations')
            pin_scroll = pin_visits.get_by_role('region', name='Pinned visit contributions')
            expect(pin_scroll.locator('tbody tr')).to_have_count(10)
            expect(pin_visits.locator('.fw-table-totals-note')).to_contain_text('42 matching rows')
            pin_visits.get_by_role('button', name='Android', exact=True).click()
            expect(pin_visits.locator('.fw-table-totals-note')).to_contain_text('21 matching rows')
            expect(pin_visits.locator('.fw-kpi-value')).to_have_text('$462.00')
            pin_visits.locator('[data-table-search]').fill('Visit 42')
            expect(pin_scroll.locator('tbody tr')).to_have_count(1)
            expect(pin_visits.locator('.fw-table-totals-note')).to_contain_text('1 matching rows')
            expect(pin_visits.locator('.fw-kpi-value')).to_have_text('$462.00')
            assert pin_scroll.evaluate('e=>e.clientHeight < 320')
            expect(total_events.locator('.fw-kpi-value')).to_have_text('$456.00')
            assert live.locator('.fw-kpi-value').first.inner_text() == '$18.4k'
            page.get_by_role('link', name='row drill-down', exact=True).click()
            drill_sources = page.locator('#sec-table-drill-down-—-source-observations')
            drill_campaigns = page.locator('#sec-table-drill-down-—-campaign-observations')
            drill_detail = page.locator('#sec-table-drill-down-—-selected-detail')
            drill_sources.scroll_into_view_if_needed()
            drill_sources.locator('[data-fw-drill-action]').first.click()
            drill_campaigns.scroll_into_view_if_needed()
            expect(drill_sources.locator('.fw-table-totals-note')).to_contain_text('56 matching rows')
            expect(drill_campaigns.locator('.fw-table-totals-note')).to_contain_text('16 matching rows')
            drill_campaigns.locator('[data-fw-drill-action]').first.click()
            drill_detail.scroll_into_view_if_needed()
            expect(drill_detail.locator('.fw-kpi-value')).to_have_text('192')
            expect(drill_campaigns.locator('.fw-table-totals-note')).to_contain_text('16 matching rows')
            drill_chart = drill_detail.locator('canvas')
            drill_chart.scroll_into_view_if_needed()
            page.wait_for_function('id => JSON.stringify(window._chartInstances[id]?.data.datasets[0].data) === "[45,47,49,51]"',
                                   arg=drill_chart.get_attribute('id'))
            drill_sources.get_by_role('button', name='Clear Source', exact=True).click()
            expect(drill_campaigns.locator('.fw-table-totals-note')).to_contain_text('56 matching rows')
            expect(drill_campaigns.get_by_role('button', name='Clear Campaign', exact=True)).to_have_count(0)
            expect(pin_visits.locator('.fw-kpi-value')).to_have_text('$462.00')
            expect(total_events.locator('.fw-kpi-value')).to_have_text('$456.00')
            assert live.locator('.fw-kpi-value').first.inner_text() == '$18.4k'
            # Every visible chart completes after reveal. Empty/zero Sankeys
            # intentionally have no geometry or Chart.js instance.
            for canvas in page.locator("canvas:visible").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("""id => !!window._chartInstances[id] ||
                    ['empty','zero'].includes(document.getElementById(id).closest('.fw-sankey')?.dataset.sankeyState)""",
                                       arg=canvas.get_attribute("id"))
                box = canvas.bounding_box()
                assert box["width"] > 0 and box["height"] > 0
            assert not errors, errors
        finally:
            browser.close()
