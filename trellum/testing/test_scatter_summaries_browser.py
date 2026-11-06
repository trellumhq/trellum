"""Native generated scatter reports: plot parity, population, ownership and export."""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import subprocess
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image

from trellum.components import BarChart, DataSource, FilterBar, ScatterChart
from trellum.components.charts._scatter_summary import scatter_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _ready, _route
from trellum.testing.test_doughnut_summaries_browser import _csv, _day, _settle
from trellum.testing.test_scatter_summaries import (
    BASELINE,
    ROOT,
    SUMMARY,
    baseline_class,
    example_context,
    frame,
)
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_SCATTER_SUMMARY_EVIDENCE', '/tmp/trellum-scatter-summaries'))


def _diagnose(ctx, output, name):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / (name + '-diagnostics.json')).write_text(json.dumps(
        {'validation': result.to_dict(), 'details': details}, default=str, indent=2))
    assert [c.message for c in result.checks if c.level in ('warn', 'fail')] == []
    from trellum.validation.walk import _walk_components
    for section in ctx.sections:
        for component, _ in _walk_components(section['components'], section_title=section['title']):
            if isinstance(component, ScatterChart):
                assert scatter_option_problems(component) == []
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    result.write(str(output))


def _build(output):
    ctx = example_context(output)
    # A native ordinary chart loads the existing shared CSV delegate. Scatter's
    # retained schema has only headers because its Chart.js labels are empty.
    df = frame()
    ctx.add_section('Scatter CSV delegate and axis formats', [
        BarChart(df, 'group', 'x', 'Native CSV delegate', static=True),
        *[ScatterChart(df, 'x', 'y', title='Format ' + fmt, static=True, x_format=fmt,
                       header_summary=SUMMARY) for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain')],
        *[ScatterChart(df, 'x', 'y', title='Override ' + fmt, static=True, x_format='currency',
                       header_summary={**SUMMARY, 'format': fmt}) for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain')],
    ])
    edge = pd.DataFrame([
        {'case': case, 'x': x, 'y': y, 'group': group}
        for case, points in (
            ('Signed', [(-4, 0, 'a'), (4, 0, 'b')]),
            ('Missing', [(4, None, 'a'), (6, 7, None)]),
            ('String', [(4, '5', 'a'), (6, 7, 'b')]),
            ('Boolean', [(4, True, 'a'), (6, 7, 'b')]),
            ('Duplicate', [(4, 5, 'a'), (4, 5, 'a')]),
        ) for x, y, group in points
    ], dtype=object)
    ctx.add_section('Scatter bound raw eligibility', [DataSource('scatter_summary_edges', edge),
        FilterBar('scatter_summary_edges', edge, filters=[{'column': 'case', 'type': 'toggle'}]),
        *[ScatterChart(edge, 'x', 'y', color_by='group', title='Edge ' + stat, dataset_id='scatter_summary_edges',
                       header_summary={'stat': stat, 'scope': 'point_entries', **({'axis': 'x'} if stat != 'count' else {})})
          for stat in ('min', 'max', 'mean', 'count')]])
    _diagnose(ctx, output, 'native')


def _id(page, title):
    return page.get_by_text(title, exact=True).last.evaluate(
        "el=>{let n=el.closest('.fw-chart-head')||el;n=n.nextElementSibling;"
        "while(n&&!n.matches('.fw-chart-container'))n=n.nextElementSibling;return n.querySelector('canvas').id}")


def _box(page, cid):
    return page.locator(f'[data-chart-summary="{cid}"]')


def _values(page, ids):
    return [_box(page, cid).locator('.fw-chart-summary-value').inner_text() for cid in ids]


def _container(page, cid):
    return page.locator('#' + cid).locator('..')


def _section(page, cid):
    return page.locator('#' + cid).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')


def _plot(page, cid):
    return page.evaluate('''id=>{const c=_chartInstances[id];return {type:c.config.type,
        datasets:c.data.datasets, width:c.canvas.width, height:c.canvas.height,
        boxHeight:c.canvas.parentElement.getBoundingClientRect().height,
        scales:['x','y'].map(a=>({min:c.scales[a].min,max:c.scales[a].max,
            title:c.options.scales[a].title.text,ticks:c.scales[a].ticks.map(t=>t.label)})),
        legend:c.options.plugins.legend.display,fwLegend:c.options.plugins.fwLegend.display,
        zoom:JSON.stringify(c.config.options.plugins.zoom),
        tooltips:c.data.datasets.map(ds=>ds.data.map(p=>c.options.plugins.tooltip.callbacks.label({dataset:ds,raw:p}))),
        dots:c.data.datasets.map((ds,i)=>c.getDatasetMeta(i).data.map(p=>({x:p.x,y:p.y,r:p.options.radius})))}}''', cid)


def _source_csv(page, path):
    cid = page.evaluate("Object.keys(_reportData.components).find(id=>{const c=_reportData.components[id];"
                        "return c.type==='table'&&c.dataset_id==='showcase_scatter_summary_coordinates'})")
    button = page.locator(f'[data-csv-target="{cid}"]')
    with page.expect_download() as download:
        button.click()
    download.value.save_as(str(path))
    return list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_scatter_summaries_filters_lifecycle_and_exports(tmp_path, width):
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
            page.goto('http://scatter-summary.test/index.html', wait_until='networkidle')
            _ready(page)
            _settle(page)
            titles = ['Filtered ' + s for s in ('mean x', 'mean y', 'min x', 'min y', 'max x', 'max y', 'count entries')]
            ids = [_id(page, title) for title in titles]
            independent = _id(page, 'Bubble context and summary')
            section = _section(page, ids[0])
            assert _values(page, ids) == ['$15.75', '$22.50', '$4.00', '$5.00', '$40.00', '$60.00', '8']
            assert _values(page, [independent]) == ['$22.50']
            assert _box(page, ids[0]).locator('.fw-scatter-summary-reason').count() == 0
            assert 'coordinate pairs' in _box(page, ids[0]).get_attribute('title').lower()
            assert 'coordinate completeness' in _box(page, ids[-1]).inner_text()
            off, on = [_id(page, 'Scatter summary ' + state) for state in ('off', 'on')]
            assert _box(page, off).count() == 0
            # Static titles are dataset labels only when unsplit; these split
            # charts have identical plot/tooltip/legend/axis geometry and pixels.
            assert _plot(page, off) == _plot(page, on)
            # Read actual chart canvas pixels so a sticky report header cannot
            # obscure one stacked phone plot while photographing the other.
            images = [base64.b64decode(page.locator('#' + cid).evaluate(
                "el=>el.toDataURL('image/png').split(',')[1]")) for cid in (off, on)]
            assert Image.open(BytesIO(images[0])).tobytes() == Image.open(BytesIO(images[1])).tobytes()
            off_csv = _csv(page, _container(page, off), EVIDENCE / f'off-{width}.csv')
            assert off_csv == _csv(page, _container(page, on), EVIDENCE / f'on-{width}.csv')
            assert off_csv.decode('utf-8-sig').splitlines() == ['label,Organic,UA']
            source_rows = _source_csv(page, EVIDENCE / f'source-all-{width}.csv')
            assert len(source_rows) == 8
            assert sum(float(r['spend']) for r in source_rows) == 126
            assert sum(float(r['revenue']) for r in source_rows) == 180
            assert sum(float(r['supplied_size']) for r in source_rows) == 1260
            # Every supported coordinate format follows actual legacy axes;
            # richer formats apply only to the explicit summary override.
            formats = ('number', 'currency', 'percent', 'ratio', 'chips', 'plain')
            assert _values(page, [_id(page, 'Format ' + fmt) for fmt in formats]) == ['16', '$16.00', '1600.00%', '16', '16', '16']
            assert _values(page, [_id(page, 'Override ' + fmt) for fmt in formats]) == ['16', '$16.00', '1600.00%', '16.00x', '16 chips', '16']
            assert _values(page, [_id(page, f'Scatter {case} {stat}') for case in ('Duplicates', 'Missing', 'Zero') for stat in ('mean', 'count')]) == ['4', '2', '—', '2', '0.00', '2']
            _day(section, '2026-10-01')
            section.get_by_role('button', name='iOS', exact=True).click()
            assert _values(page, ids) == ['$7.00', '$10.00', '$4.00', '$5.00', '$10.00', '$15.00', '2']
            assert _values(page, [independent]) == ['$22.50']
            _day(section, '2026-10-02')
            section.get_by_role('button', name='Android', exact=True).click()
            expected = ['$28.00', '$40.00', '$16.00', '$20.00', '$40.00', '$60.00', '2']
            assert _values(page, ids) == expected
            source_rows = _source_csv(page, EVIDENCE / f'source-filtered-{width}.csv')
            assert len(source_rows) == 2 and {r['platform'] for r in source_rows} == {'Android'}
            assert {r['day'] for r in source_rows} == {'2026-10-02'}
            assert sorted((float(r['spend']), float(r['revenue']), float(r['supplied_size'])) for r in source_rows) == [(16, 20, 160), (40, 60, 400)]
            # Actual native legend clicks and zoom do not redefine the population.
            _container(page, ids[0]).locator('..').locator('.fw-chart-legend .cl').first.click()
            assert page.evaluate('id=>!_chartInstances[id].isDatasetVisible(0)', ids[0])
            page.evaluate('''id=>{const c=_chartInstances[id];c.zoomScale('x',{min:17,max:20},'none');c.update('none')}''', ids[0])
            assert _values(page, ids) == expected
            assert _csv(page, _container(page, ids[0]), EVIDENCE / f'filtered-{width}.csv').decode('utf-8-sig').splitlines() == ['label,UA,Organic']
            page.locator('#' + ids[0]).dblclick()
            assert page.evaluate('id=>!_chartInstances[id].isZoomedOrPanned()', ids[0])
            # Invalid raw coordinate pairs withhold all coordinate reducers;
            # bound missing group keys keep legacy Other grouping and ordering.
            edge = [_id(page, 'Edge ' + stat) for stat in ('min', 'max', 'mean', 'count')]
            edge_section = _section(page, edge[0])
            for case, values in (('Missing', ['—', '—', '—', '2']), ('String', ['—', '—', '—', '2']),
                                 ('Boolean', ['—', '—', '—', '2']), ('Signed', ['-4', '4', '0.00', '2']),
                                 ('Duplicate', ['4', '4', '4', '2'])):
                edge_section.get_by_role('button', name=case, exact=True).click()
                assert _values(page, edge) == values
                if case == 'Missing':
                    assert [ds['label'] for ds in _plot(page, edge[0])['datasets']] == ['a', 'Other']
            page.evaluate("fw.filterEngine.setFilter('scatter_summary_edges','empty','case','equals','Absent')")
            assert _values(page, edge) == ['—', '—', '—', '0']
            page.evaluate("fw.filterEngine.clearFilter('scatter_summary_edges','empty')")
            eligibility = _section(page, _id(page, 'Scatter Duplicates mean'))
            eligibility.get_by_role('tab', name='Reveal scatter', exact=True).click()
            revealed = _id(page, 'Revealed scatter with a long wrapping title')
            page.locator('#' + revealed).scroll_into_view_if_needed()
            page.wait_for_function('id=>!!_chartInstances[id]', arg=revealed)
            assert _values(page, [revealed]) == ['0.00']
            assert _box(page, revealed).locator('safe').count() == 0
            assert '<safe>' in _box(page, revealed).inner_text()
            for theme in ('trellum light', 'trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                _ready(page)
                _settle(page)
                assert _values(page, ids) == expected
                assert _values(page, [independent]) == ['$22.50']
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                for loc, label in ((section, 'filtered'), (_section(page, off), 'pair'),
                                   (eligibility, 'eligibility'), (_box(page, revealed), 'long-label'),
                                   (_section(page, independent), 'bubble')):
                    loc.screenshot(path=str(EVIDENCE / f'{width}-{theme.split()[-1]}-{label}.png'))
            page.evaluate('renderAll();renderAll()')
            _ready(page)
            assert _values(page, ids) == expected
            lifecycle = page.evaluate('''id=>{
                const cfg=_reportData.components[id], canvas=document.getElementById(id), summary=cfg.header_summary;
                canvas.setAttribute('aria-describedby',canvas.getAttribute('aria-describedby')+' unrelated');
                canvas.replaceWith(canvas.cloneNode(true));cfg.dataset_id='showcase_scatter_summary_coordinates';
                _fwRenderers.scatter(id,cfg);
                const read=()=>document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent;
                const replacement=read();cfg.dataset_id='showcase_scatter_summary_context';_fwRenderers.scatter(id,cfg);
                const independent=read();delete cfg.header_summary;_fwRenderers.scatter(id,cfg);
                const removed=document.querySelectorAll('[data-chart-summary="'+id+'"]').length;
                const described=document.getElementById(id).getAttribute('aria-describedby');
                cfg.header_summary=summary;_fwRenderers.scatter(id,cfg);_fwRenderers.scatter(id,cfg);
                let reset=0;_chartInstances[id].resetZoom=()=>reset++;
                document.getElementById(id).dispatchEvent(new MouseEvent('dblclick'));
                const subscriptions=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                return {replacement,independent,removed,described,restored:read(),subscriptions,reset,
                    descriptions:document.querySelectorAll('#'+id+'-scatter-summary-scope').length};}''', independent)
            assert lifecycle == {'replacement': '$40.00', 'independent': '$22.50', 'removed': 0,
                'described': independent + '-header-context unrelated', 'restored': '$22.50', 'subscriptions': 1, 'descriptions': 1, 'reset': 1}
            # Observe actual listener registration/removal during repeated
            # adoption; all operations still call the native event methods.
            ownership = page.evaluate('''id=>{const canvas=document.getElementById(id),cfg=_reportData.components[id];
                const add=window.addEventListener,remove=window.removeEventListener;
                const cadd=canvas.addEventListener,cremove=canvas.removeEventListener;
                const themes=new Set(),resets=new Set();
                window.addEventListener=function(t,f,o){if(t==='fw-theme-change')themes.add(f);return add.call(this,t,f,o)};
                window.removeEventListener=function(t,f,o){if(t==='fw-theme-change')themes.delete(f);return remove.call(this,t,f,o)};
                canvas.addEventListener=function(t,f,o){if(t==='dblclick')resets.add(f);return cadd.call(this,t,f,o)};
                canvas.removeEventListener=function(t,f,o){if(t==='dblclick')resets.delete(f);return cremove.call(this,t,f,o)};
                try{for(let i=0;i<3;i++)_fwRenderers.scatter(id,cfg);return {themes:themes.size,resets:resets.size}}
                finally{window.addEventListener=add;window.removeEventListener=remove;
                    canvas.addEventListener=cadd;canvas.removeEventListener=cremove;}}''', independent)
            assert ownership == {'themes': 1, 'resets': 1}
            # Actual mixed-report CSV bytes stay identical for on/off/on/off.
            csv_before = _csv(page, _container(page, independent), EVIDENCE / f'bound-on-{width}.csv')
            for enabled in (False, True, False):
                page.evaluate('''({id,enabled})=>{const cfg={..._reportData.components[id]};
                    if(!enabled)delete cfg.header_summary;_fwRenderers.scatter(id,cfg)}''', {'id': independent, 'enabled': enabled})
                assert _csv(page, _container(page, independent), EVIDENCE / f'bound-{enabled}-{width}.csv') == csv_before
            page.evaluate('id=>_fwRenderers.scatter(id,_reportData.components[id])', independent)
            # Bound -> static -> bound removes only the previous owned binding.
            assert page.evaluate('''id=>{const old=_reportData.components[id],cfg={...old};
                delete cfg.dataset_id;cfg.datasets=[{label:'Data',data:[{x:0,y:0}]}];_fwRenderers.scatter(id,cfg);
                const n=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                _fwRenderers.scatter(id,old);return n;}''', independent) == 0
            plots_before_capture = [_plot(page, cid) for cid in ids + [independent]]
            snapshot = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE / f'native-section-{width}.json').write_text(json.dumps(snapshot, indent=2))
            (EVIDENCE / f'native-section-{width}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
            assert _values(page, ids) == expected
            assert [_plot(page, cid) for cid in ids + [independent]] == plots_before_capture
            if width == 1280:
                for extension, fn in (('png', 'exportPNG'), ('pdf', 'exportPDF')):
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f'fw.{fn}()')
                    download.value.save_as(str(EVIDENCE / f'native-report.{extension}'))
                assert _values(page, ids) == expected
                assert [_plot(page, cid) for cid in ids + [independent]] == plots_before_capture
            # Workspace queue ownership remains with LiveWrap's existing lease.
            page.evaluate('''id=>{window._fwMetricsWorkspace=true;_fwRenderers.scatter(id,_reportData.components[id]);
                _fwRenderers.scatter(id,_reportData.components[id]);}''', independent)
            assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length', independent) == 1
            detached = ids[-1]
            page.evaluate('id=>document.getElementById(id).remove()', detached)
            page.wait_for_function('id=>!_fwScatterBindings.has(id)', arg=detached)
            assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length', detached) == 0
            assert _box(page, detached).count() == 0
            page.evaluate('id=>document.getElementById(id).remove()', revealed)
            page.wait_for_function('id=>!_fwScatterBindings.has(id)', arg=revealed)
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
        cls = baseline_class() if name == 'baseline' else ScatterChart
        df = frame()
        ctx = ReportContext(config={'name': 'Scatter baseline', 'studio': 'test', 'category': 'Test',
            'description': 'Default supplied static and bound coordinate pairs with unchanged ordinary and bubble dots.'}, slug='baseline', output_dir=str(output))
        ctx.add_section('Default scatter plots', [DataSource('baseline_source', df),
            FilterBar('baseline_source', df, filters=[{'column': 'group', 'type': 'toggle'}]),
            cls(df, 'x', 'y', color_by='group', title='Static', static=True),
            cls(df, 'x', 'y', size='size', color_by='group', title='Bound', dataset_id='baseline_source'),
            BarChart(df, 'group', 'x', 'CSV delegate', static=True)])
        _diagnose(ctx, output, name)
        if name == 'baseline':
            replacements = [((ROOT / 'trellum/static/js/components/scatter.js').read_text(),
                subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/components/scatter.js'], cwd=ROOT, text=True))]
            # Baseline class already supplies the historical CSS; JS is looked
            # up from disk and must explicitly be replaced with the real source.
            for current, historical in replacements:
                replaced = False
                for file in [output / 'index.html', *output.rglob('*.js'), *output.rglob('*.css')]:
                    text = file.read_text()
                    if current in text:
                        file.write_text(text.replace(current, historical))
                        replaced = True
                assert replaced
        roots.append(output)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            results = {}
            for name, output in zip(('baseline', 'current'), roots):
                page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
                _route(page, output)
                page.goto('http://scatter-baseline.test/index.html', wait_until='networkidle')
                _ready(page)
                for theme in ('trellum light', 'trellum dark'):
                    page.locator('#fwThemeSelect').select_option(theme)
                    _ready(page)
                    _settle(page)
                    page.mouse.move(0, 0)
                    geometry = [_plot(page, _id(page, title)) for title in ('Static', 'Bound')]
                    page.locator('.fw-section').screenshot(path=str(EVIDENCE / f'opt-out-{name}-{width}-{theme.split()[-1]}.png'), animations='disabled')
                    exports = [_csv(page, _container(page, _id(page, title)), EVIDENCE / f'baseline-{name}-{title}-{width}-{theme.split()[-1]}.csv') for title in ('Static', 'Bound')]
                    # The section screenshot is retained for inspection. Compare
                    # actual plot pixels without transient sticky-filter hover.
                    pixels = [page.locator('#' + _id(page, title)).evaluate(
                        "el=>el.toDataURL('image/png')") for title in ('Static', 'Bound')]
                    results[name, theme] = (geometry, pixels, exports)
                page.close()
            for theme in ('trellum light', 'trellum dark'):
                assert results['baseline', theme] == results['current', theme]
        finally:
            browser.close()
