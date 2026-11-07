"""Native generated funnels: delivered entries, unchanged plots, downloads and captures."""
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

from trellum.components import BarChart, DataSource, FilterBar, FunnelChart, TabGroup
from trellum.components.charts._funnel_summary import funnel_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _ready, _route
from trellum.testing.test_doughnut_summaries_browser import _csv, _day, _settle
from trellum.testing.test_funnel_summaries import (
    BASELINE,
    ROOT,
    baseline_class,
    example_context,
    frame,
)
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_FUNNEL_SUMMARY_EVIDENCE', '/tmp/trellum-funnel-summaries'))


def _diagnose(ctx, output):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    issues = [(c.id, c.level, c.section, c.message) for c in result.checks if c.level in {'warn', 'fail'}]
    assert issues == [], issues
    # Exercise the family hook as well as the shared report validation above.
    def walk(components):
        for component in components:
            if isinstance(component, FunnelChart):
                assert funnel_option_problems(component) == []
            walk(getattr(component, 'children', []))
    for section in ctx.sections:
        walk(section['components'])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    result.write(str(output))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / 'diagnostics.json').write_text(json.dumps({'validation': result.to_dict(), 'details': details}, default=str, indent=2))


def _build(output):
    ctx = example_context(output)
    edge = pd.DataFrame([
        {'case': case, 'stage': stage, 'amount': value}
        for case, entries in (
            ('Missing selected', [('Completed', None), ('Completed', 10), ('Opened', 50)]),
            ('Missing other', [('Completed', 20), ('Opened', None)]),
            ('Signed selected', [('Completed', -10), ('Completed', 20), ('Opened', 50)]),
            ('String selected', [('Completed', '2'), ('Opened', 50)]),
            ('Boolean selected', [('Completed', True), ('Opened', 50)]),
            ('Cross-stage overflow', [('Completed', 1e308), ('Opened', 1e308)]),
            ('Grouped overflow', [('Completed', 1e308), ('Completed', 1e308)]),
        ) for stage, value in entries
    ])
    ctx.add_section('Funnel guarded contributions', [DataSource('funnel_summary_edges', edge),
        FilterBar('funnel_summary_edges', edge, filters=[{'column': 'case', 'type': 'toggle'}]),
        *[FunnelChart(edge, 'stage', 'amount', f'Edge {stat}', dataset_id='funnel_summary_edges',
                     header_summary={'stat': stat, 'scope': 'current_stages', **({'stage': 'Completed'} if stat == 'stage' else {})})
          for stat in ('stage', 'max', 'count')],
        TabGroup([{'label': 'Start', 'content': []}, {'label': 'Reveal funnel', 'content': [
            FunnelChart(frame(), 'stage', 'amount', 'Revealed funnel', static=True,
                        header_context='Independent <context>',
                        header_summary={'stat': 'max', 'scope': 'current_stages', 'label': '<safe> ' + 'x' * 50})]}]),
    ])
    _diagnose(ctx, output)


def _id(page, title):
    return page.get_by_text(title, exact=True).last.evaluate(
        "el=>{let n=el.closest('.fw-chart-head')||el;n=n.nextElementSibling;"
        "while(n&&!n.matches('.fw-chart-container'))n=n.nextElementSibling;return n.querySelector('canvas').id}")


def _bound_ids(page, source):
    return page.evaluate("source=>['stage','max','count'].map(stat=>Object.keys(_reportData.components).find(id=>{"
                         "const c=_reportData.components[id];return c.type==='funnel'&&c.dataset_id===source&&c.header_summary.stat===stat}))", source)


def _box(page, cid):
    return page.locator(f'[data-chart-summary="{cid}"]')


def _values(page, ids):
    return [_box(page, cid).locator('.fw-chart-summary-value').inner_text() for cid in ids]


def _container(page, cid):
    return page.locator('#' + cid).locator('..')


def _section(page, cid):
    return page.locator('#' + cid).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')


def _plot(page, cid):
    return page.evaluate('''id=>{const c=_chartInstances[id];return {labels:c.data.labels,
        values:c.data.datasets[0].data, height:c.canvas.parentElement.getBoundingClientRect().height,
        legend:c.options.plugins.legend.display, fwLegend:c.options.plugins.fwLegend.display,
        percentages:c.options.plugins.datalabels.display,
        ticks:c.data.datasets[0].data.map((v,i)=>c.config.options.plugins.datalabels.formatter(v,{dataIndex:i})),
        bars:c.getDatasetMeta(0).data.map(b=>({x:b.x,y:b.y,width:b.width,height:b.height}))}}''', cid)


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_funnel_summaries_filters_lifecycle_and_exports(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            _route(page, tmp_path)
            page.goto('http://funnel-summary.test/index.html', wait_until='networkidle')
            _ready(page)
            source = 'showcase_funnel_summary_stages'
            ids = _bound_ids(page, source)
            other = _bound_ids(page, 'showcase_funnel_summary_context')
            assert _values(page, ids) == ['60', '540', '3']
            assert _values(page, other) == ['60', '540', '3']
            off, on = [_id(page, 'Funnel summary ' + state) for state in ('off', 'on')]
            assert _box(page, off).count() == 0
            assert _plot(page, off) == _plot(page, on)
            assert _plot(page, on)['ticks'] == ['540', '300 (55.6%)', '60 (20.0%)']
            assert _plot(page, on)['height'] == 250 and not _plot(page, on)['legend']
            assert _csv(page, _container(page, off), EVIDENCE / f'off-{width}.csv') == _csv(page, _container(page, on), EVIDENCE / f'on-{width}.csv')
            assert _values(page, [_id(page, f'Funnel Duplicates {s}') for s in ('stage', 'max', 'count')]) == ['—', '50', '3']
            assert _values(page, [_id(page, 'Funnel Missing stage'), _id(page, 'Funnel Missing count'), _id(page, 'Funnel Zero stage')]) == ['—', '2', '0.00']
            section = _section(page, ids[0])
            section.get_by_role('button', name='iOS', exact=True).click()
            _day(section, '2026-10-01')
            assert _values(page, ids) == ['20', '100', '3']
            section.get_by_role('button', name='Android', exact=True).click()
            _day(section, '2026-10-02')
            assert _values(page, ids) == ['0.00', '160', '3']
            assert _plot(page, ids[0])['labels'] == ['Opened', 'Started', 'Completed']
            assert _plot(page, ids[0])['values'] == [160, 80, 0]
            assert _values(page, other) == ['60', '540', '3']
            section.get_by_role('button', name='Opened', exact=True).click()
            assert _values(page, ids) == ['—', '160', '1']
            assert 'absent' in _box(page, ids[0]).locator('.fw-funnel-summary-reason').inner_text()
            section.locator('.fw-toggle-group').last.get_by_role('button', name='All', exact=True).click()
            csv = _csv(page, _container(page, ids[0]), EVIDENCE / f'filtered-{width}.csv')
            assert csv.decode('utf-8-sig').splitlines() == ['label,value', 'Opened,160', 'Started,80', 'Completed,0']
            assert _csv(page, _container(page, ids[1]), EVIDENCE / f'filtered-max-{width}.csv') == csv
            # Current entries include hidden entries; zoom is not a summary filter.
            page.evaluate('''id=>{const c=_chartInstances[id];c.toggleDataVisibility(2);
                c.options.scales.y.min=0;c.options.scales.y.max=1;c.update('none')}''', ids[0])
            assert _values(page, ids) == ['0.00', '160', '3']
            assert _csv(page, _container(page, ids[0]), EVIDENCE / f'hidden-{width}.csv') == csv
            page.evaluate('id=>_fwRenderers.funnel(id,_reportData.components[id])', ids[0])
            edge = [_id(page, 'Edge ' + stat) for stat in ('stage', 'max', 'count')]
            edge_section = _section(page, edge[0])
            for case, expected in (
                ('Missing selected', ['—', '—', '2']), ('Missing other', ['20', '—', '2']),
                ('Signed selected', ['—', '—', '2']), ('String selected', ['—', '—', '2']),
                ('Boolean selected', ['—', '—', '2']), ('Grouped overflow', ['—', '—', '1']),
            ):
                edge_section.get_by_role('button', name=case, exact=True).click()
                assert _values(page, edge) == expected
            edge_section.get_by_role('button', name='Cross-stage overflow', exact=True).click()
            assert '—' not in _values(page, edge)
            page.evaluate("fw.filterEngine.setFilter('funnel_summary_edges','empty','case','equals','Absent')")
            assert _values(page, edge) == ['—', '—', '0']
            page.evaluate("fw.filterEngine.clearFilter('funnel_summary_edges','empty')")
            edge_section.get_by_role('button', name='Missing other', exact=True).click()
            edge_section.get_by_role('tab', name='Reveal funnel', exact=True).click()
            revealed = _id(page, 'Revealed funnel')
            page.locator('#' + revealed).scroll_into_view_if_needed()
            page.wait_for_function('id=>!!_chartInstances[id]', arg=revealed)
            assert _values(page, [revealed]) == ['50']
            assert _box(page, revealed).locator('safe').count() == 0
            for theme in ('trellum light', 'trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                _ready(page)
                _settle(page)
                assert _values(page, ids) == ['0.00', '160', '3']
                assert _values(page, other) == ['60', '540', '3']
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                for loc, label in ((section, 'filtered'), (_section(page, off), 'pair'),
                                   (_section(page, _id(page, 'Funnel Duplicates stage')), 'eligibility'),
                                   (_box(page, revealed), 'long-label')):
                    loc.screenshot(path=str(EVIDENCE / f'{width}-{theme.split()[-1]}-{label}.png'))
            page.evaluate('renderAll();renderAll()')
            _ready(page)
            assert _values(page, ids) == ['0.00', '160', '3']
            # Re-entry and replacement leave one callback/description and preserve unrelated tokens.
            lifecycle = page.evaluate('''id=>{
                const cfg=_reportData.components[id], c=document.getElementById(id), summary=cfg.header_summary;
                c.setAttribute('aria-describedby',c.getAttribute('aria-describedby')+' unrelated');
                c.replaceWith(c.cloneNode(true));cfg.dataset_id='showcase_funnel_summary_stages';
                _fwRenderers.funnel(id,cfg);
                const read=()=>document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent;
                const replacement=read();cfg.dataset_id='showcase_funnel_summary_context';_fwRenderers.funnel(id,cfg);
                const independent=read();delete cfg.header_summary;_fwRenderers.funnel(id,cfg);
                const removed=document.querySelectorAll('[data-chart-summary="'+id+'"]').length;
                const described=document.getElementById(id).getAttribute('aria-describedby');
                cfg.header_summary=summary;_fwRenderers.funnel(id,cfg);_fwRenderers.funnel(id,cfg);
                const subscriptions=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                return {replacement,independent,removed,described,restored:read(),subscriptions,
                    descriptions:document.querySelectorAll('#'+id+'-funnel-summary-scope').length};}''', other[0])
            assert lifecycle == {'replacement': '0.00', 'independent': '60', 'removed': 0,
                'described': other[0] + '-header-context unrelated', 'restored': '60', 'subscriptions': 1, 'descriptions': 1}
            # Static/bound transitions dispose old scoped subscriptions too.
            count = page.evaluate('''id=>{const cfg=_reportData.components[id],source=cfg.dataset_id;
                delete cfg.dataset_id;cfg.labels=['Completed'];cfg.values=[0];_fwRenderers.funnel(id,cfg);
                const n=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                cfg.dataset_id=source;_fwRenderers.funnel(id,cfg);return n;}''', other[0])
            assert count == 0
            snapshot = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE / f'native-section-{width}.json').write_text(json.dumps(snapshot, indent=2))
            (EVIDENCE / f'native-section-{width}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
            if width == 1280:
                for extension, fn in (('png', 'exportPNG'), ('pdf', 'exportPDF')):
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f'fw.{fn}()')
                    download.value.save_as(str(EVIDENCE / f'native-report.{extension}'))
            # Detached ownership retires without disturbing unrelated charts.
            detached = ids[-1]
            page.evaluate('id=>document.getElementById(id).remove()', detached)
            page.wait_for_function('id=>!_fwFunnelBindings.has(id)', arg=detached)
            assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length', detached) == 0
            assert _box(page, detached).count() == 0
            page.evaluate('id=>document.getElementById(id).remove()', revealed)
            page.wait_for_function('id=>!_fwFunnelBindings.has(id)', arg=revealed)
            assert _box(page, revealed).count() == 0
            assert page.locator('#' + revealed + '-header-context').count() == 1
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_actual_baseline_off_pixels_geometry_data_csv(tmp_path, width):
    from playwright.sync_api import sync_playwright

    roots = []
    for name in ('baseline', 'current'):
        output = tmp_path / name
        output.mkdir()
        cls = baseline_class() if name == 'baseline' else FunnelChart
        df = frame()
        ctx = ReportContext(config={'name': 'Funnel baseline', 'studio': 'test', 'category': 'Test',
            'description': 'Default static duplicate and bound stage entries at supplied snapshot and grouped grain.'}, slug='baseline', output_dir=str(output))
        ctx.add_section('Default funnels', [DataSource('baseline_source', df),
            FilterBar('baseline_source', df, filters=[{'column': 'stage', 'type': 'toggle'}]),
            cls(df, 'stage', 'amount', 'Static', static=True),
            cls(df, 'stage', 'amount', 'Bound', dataset_id='baseline_source'),
            # Baseline funnel CSV uses the shared ordinary-chart delegate.
            BarChart(df, 'stage', 'amount', 'CSV delegate', static=True)])
        result = validate_report(ctx, details=compute_details(ctx))
        assert result.summary['warn'] == result.summary['fail'] == 0, result.to_dict()
        render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
        if name == 'baseline':
            replacements = [((ROOT / 'trellum/static/js/components/funnel.js').read_text(),
                 subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/components/funnel.js'], cwd=ROOT, text=True))]
            for current, historical in replacements:
                replaced = False
                for file in [output / 'index.html', *output.rglob('*.js'), *output.rglob('*.css')]:
                    text = file.read_text()
                    if current in text:
                        file.write_text(text.replace(current, historical))
                        replaced = True
                assert replaced
        roots.append(output)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            results = {}
            for name, output in zip(('baseline', 'current'), roots):
                page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
                _route(page, output)
                page.goto('http://funnel-baseline.test/index.html', wait_until='networkidle')
                _ready(page)
                for theme in ('trellum light', 'trellum dark'):
                    page.locator('#fwThemeSelect').select_option(theme)
                    _ready(page)
                    _settle(page)
                    page.mouse.move(0, 0)
                    geometry = [_plot(page, _id(page, title)) for title in ('Static', 'Bound')]
                    image = page.locator('.fw-section').screenshot(path=str(EVIDENCE / f'opt-out-{name}-{width}-{theme.split()[-1]}.png'), animations='disabled')
                    csv = [_csv(page, _container(page, _id(page, title)), EVIDENCE / f'baseline-{name}-{title}-{width}-{theme.split()[-1]}.csv') for title in ('Static', 'Bound')]
                    results[name, theme] = (geometry, Image.open(BytesIO(image)).convert('RGBA').tobytes(), csv)
                page.close()
            for theme in ('trellum light', 'trellum dark'):
                assert results['baseline', theme] == results['current', theme]
        finally:
            browser.close()
