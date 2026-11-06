"""Native generated doughnut reports, delivered populations and capture parity."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image

from trellum.components import DataSource, DoughnutChart, FilterBar, TabGroup
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _ready, _route
from trellum.testing.test_doughnut_summaries import (
    BASELINE,
    ROOT,
    SUMMARY,
    baseline_class,
    example_context,
    frame,
)
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_DOUGHNUT_SUMMARY_EVIDENCE', '/tmp/trellum-doughnut-summaries'))


def _build(output):
    ctx = example_context(output)
    # Test-only edge data still uses native components, data binding and filters.
    edge = pd.DataFrame([
        {'case': 'Complete', 'category': 'UA', 'value': 10},
        {'case': 'Missing', 'category': 'UA', 'value': None},
        {'case': 'Missing', 'category': 'UA', 'value': 10},
        {'case': 'Signed', 'category': 'UA', 'value': -10},
        {'case': 'Signed', 'category': 'UA', 'value': 20},
        {'case': 'Overflow', 'category': 'UA', 'value': 1e308},
        {'case': 'Overflow', 'category': 'RT', 'value': 1e308},
        {'case': 'Grouped overflow', 'category': 'UA', 'value': 1e308},
        {'case': 'Grouped overflow', 'category': 'UA', 'value': 1e308},
    ])
    ctx.add_section('Guarded inputs', [DataSource('summary_edge', edge),
        FilterBar('summary_edge', edge, filters=[{'column': 'case', 'type': 'toggle'}]),
        *[DoughnutChart(edge, 'category', 'value', f'Edge {stat}', dataset_id='summary_edge',
                       header_summary={'stat': stat, 'scope': 'all_categories'}) for stat in ('sum', 'max', 'count')],
        TabGroup([{'label': 'Start', 'content': []}, {'label': 'Reveal doughnut', 'content': [
            DoughnutChart(frame(), 'category', 'value', 'Revealed', static=True,
                          header_summary={**SUMMARY, 'label': 'Long summary <safe> ' + 'x' * 35},
                          header_context='Independent <context>')]}]),
    ])
    result = validate_report(ctx, details=compute_details(ctx))
    issues = [(c.id, c.level, c.section, c.message) for c in result.checks if c.level in {'warn', 'fail'}]
    assert issues == [], issues
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    result.write(str(output))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / 'diagnostics.json').write_text(json.dumps({'validation': result.to_dict(), 'details': compute_details(ctx)}, default=str, indent=2))


def _id(page, title):
    return page.evaluate("title=>Object.keys(_reportData.components).find(id=>_reportData.components[id].type==='doughnut'&&_reportData.components[id].title===title)", title)


def _root(page, cid):
    return page.locator('#' + cid).locator('xpath=ancestor::div[@class="fw-doughnut"][1]')


def _values(page, ids):
    return [_root(page, cid).locator('.fw-doughnut-summary-value').inner_text() for cid in ids]


def _settle(page):
    page.evaluate("""async()=>{await document.fonts.ready;
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
        Object.values(_chartInstances).forEach(c=>{c.stop();c.update('none')});
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));} """)


def _csv(page, root, path):
    with page.expect_download() as download:
        root.get_by_title('Download CSV').focus()
        root.get_by_title('Download CSV').press('Enter')
    download.value.save_as(str(path))
    return path.read_bytes()


def _day(section, day):
    section.locator('.fw-date-trigger').click()
    # Native date inputs commit on change, without a custom adapter or Apply.
    section.locator('input[data-edge="min"]').fill(day)
    section.locator('input[data-edge="min"]').dispatch_event('change')
    section.locator('input[data-edge="max"]').fill(day)
    section.locator('input[data-edge="max"]').dispatch_event('change')
    section.locator('.fw-date-trigger').click()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_doughnut_summary_lifecycle_filters_and_exports(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
            page.set_default_timeout(10000)
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            _route(page, tmp_path)
            page.goto('http://doughnut-summary.test/index.html', wait_until='networkidle')
            _ready(page)
            source = 'showcase_doughnut_summary_amounts'
            ids = [_id(page, f'{source}-{stat}') for stat in ('sum', 'max', 'count')]
            other = [_id(page, f'showcase_doughnut_summary_options-{stat}') for stat in ('sum', 'max', 'count')]
            assert _values(page, ids) == ['120', '90', '3']
            assert _values(page, other) == ['120', '90', '3']
            assert _values(page, [_id(page, 'Duplicate ' + stat) for stat in ('sum', 'max', 'count')]) == ['60', '30', '3']
            off, on = [_root(page, _id(page, title)) for title in ('Summary off', 'Summary on')]
            assert off.locator('.fw-doughnut-summary').count() == 0
            assert off.locator('.fw-doughnut-center').is_hidden() and on.locator('.fw-doughnut-center').is_hidden()
            for cls in ('.fw-doughnut-ring', '.fw-doughnut-legend'):
                a, b = off.locator(cls).bounding_box(), on.locator(cls).bounding_box()
                assert (a['width'], a['height']) == (b['width'], b['height'])
            assert _csv(page, off, EVIDENCE / f'off-{width}.csv') == _csv(page, on, EVIDENCE / f'on-{width}.csv')
            section = _root(page, ids[0]).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
            section.get_by_role('button', name='iOS', exact=True).click()
            _day(section, '2026-10-01')
            page.wait_for_function("id=>document.getElementById(id).closest('.fw-doughnut').querySelector('.fw-doughnut-summary-value').textContent==='20'", arg=ids[0])
            assert _values(page, ids) == ['20', '12', '3']
            section.get_by_role('button', name='Android', exact=True).click()
            _day(section, '2026-10-02')
            assert _values(page, ids) == ['40', '36', '3']
            assert _values(page, other) == ['120', '90', '3']
            section.get_by_role('button', name='RT', exact=True).click()
            assert _values(page, ids) == ['0', '0', '1']
            # Clear acquisition only through its native toggle control.
            section.locator('.fw-toggle-group').last.get_by_role('button', name='All', exact=True).click()
            assert _values(page, ids) == ['40', '36', '3']
            root = _root(page, ids[0])
            csv = _csv(page, root, EVIDENCE / f'filtered-{width}.csv')
            assert csv.decode('utf-8-sig').splitlines() == ['label,value', 'Organic,4', 'RT,0', 'UA,36']
            for entry in root.locator('.fw-doughnut-entry').all():
                entry.click()
            assert _values(page, ids) == ['40', '36', '3']
            assert _csv(page, root, EVIDENCE / f'hidden-{width}.csv') == csv
            root.get_by_role('button', name='Restore all').click()
            assert root.locator('.fw-doughnut-entry[aria-pressed=true]').count() == 3
            edge = [_id(page, 'Edge ' + stat) for stat in ('sum', 'max', 'count')]
            edge_section = _root(page, edge[0]).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
            for case, expected in [('Missing', ['—', '—', '1']), ('Signed', ['—', '—', '1']),
                                   ('Overflow', ['—', '1e+308', '2']), ('Grouped overflow', ['—', '—', '1'])]:
                edge_section.get_by_role('button', name=case, exact=True).click()
                values = _values(page, edge)
                assert values[0] == expected[0] and values[2] == expected[2]
                if case == 'Overflow':
                    assert '—' != values[1] and _root(page, edge[1]).locator('.fw-doughnut-summary-reason').inner_text() == ''
                else:
                    assert values[1] == expected[1]
            page.evaluate("fw.filterEngine.setFilter('summary_edge','empty','case','equals','Absent')")
            assert _values(page, edge) == ['—', '—', '0']
            page.evaluate("fw.filterEngine.clearFilter('summary_edge','empty')")
            edge_section.get_by_role('tab', name='Reveal doughnut', exact=True).click()
            reveal = _id(page, 'Revealed')
            page.locator('#' + reveal).scroll_into_view_if_needed()
            page.wait_for_function('id=>!!_chartInstances[id]', arg=reveal)
            assert _values(page, [reveal]) == ['60']
            assert _root(page, reveal).locator('.fw-doughnut-summary-label').inner_text().startswith('Long summary <safe>')
            assert _root(page, reveal).locator('safe').count() == 0
            for theme in ('trellum light', 'trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                _ready(page)
                _settle(page)
                assert _values(page, ids) == ['40', '36', '3']
                assert _values(page, other) == ['120', '90', '3']
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                for loc, label in ((section, 'filtered'), (off.locator('xpath=ancestor::div[contains(@class,"fw-section")][1]'), 'pair'),
                                    (_root(page, reveal), 'long-label')):
                    loc.screenshot(path=str(EVIDENCE / f'{width}-{theme.split()[-1]}-{label}.png'))
            page.evaluate('renderAll();renderAll()')
            _ready(page)
            assert _values(page, ids) == ['40', '36', '3']
            # Re-entry, canvas/source replacement and option removal preserve unrelated description tokens.
            page.evaluate("""id=>{const cfg=_reportData.components[id];cfg.value_format='currency';cfg.precision=3;_fwRenderers.doughnut(id,cfg)}""", other[2])
            assert _values(page, [other[2]]) == ['3']
            lifecycle = page.evaluate("""id=>{
                const cfg=_reportData.components[id],canvas=document.getElementById(id),summary=cfg.header_summary;
                canvas.replaceWith(canvas.cloneNode(true));
                cfg.dataset_id='showcase_doughnut_summary_amounts';
                _fwRenderers.doughnut(id,cfg);
                const read=()=>document.getElementById(id).closest('.fw-doughnut').querySelector('.fw-doughnut-summary-value').textContent;
                const replacement=read();cfg.dataset_id='showcase_doughnut_summary_options';_fwRenderers.doughnut(id,cfg);
                const other=read();delete cfg.header_summary;_fwRenderers.doughnut(id,cfg);
                const c=document.getElementById(id),root=c.closest('.fw-doughnut');
                const removed=root.querySelectorAll('.fw-doughnut-summary').length,described=c.getAttribute('aria-describedby');
                cfg.header_summary=summary;_fwRenderers.doughnut(id,cfg);_fwRenderers.doughnut(id,cfg);
                return {replacement,other,removed,described,restored:read(),nodes:root.querySelectorAll('.fw-doughnut-summary').length};
            }""", other[0])
            assert lifecycle == {'replacement': '40', 'other': '120', 'removed': 0,
                                 'described': other[0] + '-header-context', 'restored': '120', 'nodes': 1}
            # Native capture removes ordinary sticky controls through the existing capture path.
            snapshot = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE / f'native-section-{width}.json').write_text(json.dumps(snapshot, indent=2))
            (EVIDENCE / f'native-section-{width}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
            if width == 1280:
                # Ordinary report exports keep the delivered Android/day2 state.
                for ext, fn in [('png', 'exportPNG'), ('pdf', 'exportPDF')]:
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f'fw.{fn}()')
                    download.value.save_as(str(EVIDENCE / f'native-report.{ext}'))
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_actual_baseline_opt_out_pixels_geometry_data_csv(tmp_path, width):
    from playwright.sync_api import sync_playwright

    roots = []
    for name in ('baseline', 'current'):
        output = tmp_path / name
        output.mkdir()
        cls = baseline_class() if name == 'baseline' else DoughnutChart
        ctx = ReportContext(config={'name': 'Doughnut baseline', 'studio': 'test', 'category': 'Test',
            'description': 'Baseline comparison of static duplicate and bound category entries.'}, slug='baseline', output_dir=str(output))
        df = frame()
        ctx.add_section('Defaults', [DataSource('baseline_source', df),
            FilterBar('baseline_source', df, filters=[{'column': 'category', 'type': 'toggle'}]),
            cls(df, 'category', 'value', 'Static', static=True),
            cls(df, 'category', 'value', 'Bound', dataset_id='baseline_source')])
        result = validate_report(ctx, details=compute_details(ctx))
        assert result.summary['warn'] == result.summary['fail'] == 0, result.to_dict()
        render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
        if name == 'baseline':
            for relative in ('trellum/static/js/components/doughnut.js', 'trellum/static/css/components/doughnut.css'):
                current = (ROOT / relative).read_text()
                historical = subprocess.check_output(['git', 'show', f'{BASELINE}:{relative}'], cwd=ROOT, text=True)
                replaced = False
                for file in [output / 'index.html', *output.rglob('*.js'), *output.rglob('*.css')]:
                    text = file.read_text()
                    if current in text:
                        file.write_text(text.replace(current, historical))
                        replaced = True
                assert replaced, relative
        roots.append(output)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            results = {}
            for name, output in zip(('baseline', 'current'), roots):
                page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
                _route(page, output)
                page.goto('http://baseline.test/index.html', wait_until='networkidle')
                _ready(page)
                for theme in ('trellum light', 'trellum dark'):
                    page.locator('#fwThemeSelect').select_option(theme)
                    _ready(page)
                    _settle(page)
                    page.mouse.move(0, 0)
                    geometry = page.evaluate("""()=>Object.values(_chartInstances).map(c=>({
                        data:c.data.labels.map((l,i)=>[l,c.data.datasets[0].data[i]]),
                        ring:c.canvas.getBoundingClientRect().toJSON(),
                        legend:c.canvas.closest('.fw-doughnut').querySelector('.fw-doughnut-legend').getBoundingClientRect().toJSON(),
                        inner:c.getDatasetMeta(0).data[0].innerRadius,outer:c.getDatasetMeta(0).data[0].outerRadius}))""")
                    image = page.locator('.fw-section').screenshot(path=str(EVIDENCE / f'opt-out-{name}-{width}-{theme.split()[-1]}.png'), animations='disabled')
                    csv = [_csv(page, _root(page, _id(page, title)), EVIDENCE / f'baseline-{name}-{title}-{width}-{theme.split()[-1]}.csv')
                           for title in ('Static', 'Bound')]
                    results[name, theme] = (geometry, Image.open(BytesIO(image)).convert('RGBA').tobytes(), csv)
                page.close()
            for theme in ('trellum light', 'trellum dark'):
                assert results['baseline', theme] == results['current', theme]
        finally:
            browser.close()
