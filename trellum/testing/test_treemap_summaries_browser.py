"""Generated native reports with real vendored treemap leaves and exports."""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from trellum.components import BarChart, DataSource, FilterBar, ScopedDataSource, TreemapChart
from trellum.components.charts._treemap_summary import treemap_summary_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _ready, _route
from trellum.testing.test_doughnut_summaries_browser import _csv, _day, _settle
from trellum.testing.test_treemap_summaries import (
    BASELINE,
    ROOT,
    SUMMARY,
    baseline_class,
    example_context,
    frame,
)
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_TREEMAP_SUMMARY_EVIDENCE', '/tmp/trellum-treemap-summaries'))


def _diagnose(ctx, output, name):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    from trellum.validation.walk import _walk_components
    hooks = []
    for section in ctx.sections:
        for comp, _ in _walk_components(section['components'], section_title=section['title']):
            if isinstance(comp, TreemapChart):
                hooks.extend(treemap_summary_option_problems(comp))
    (EVIDENCE / (name + '-diagnostics.json')).write_text(json.dumps(
        {'validation': result.to_dict(), 'summary_options': hooks, 'details': details}, default=str, indent=2))
    assert [c.message for c in result.checks if c.level in ('warn', 'fail')] == []
    assert hooks == []
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    result.write(str(output))


def _build(output):
    ctx = example_context(output)
    df = frame()
    ctx.add_section('Native treemap CSV delegate and display formats', [
        BarChart(df, 'leaf', 'amount', 'Native CSV delegate', static=True),
        *[TreemapChart(df, ['parent', 'leaf'], 'amount', 'Format ' + fmt,
             value_format=fmt, static=True, header_summary={'stat': 'mean', 'scope': 'leaf_entries'})
          for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain')],
        *[TreemapChart(df, ['parent', 'leaf'], 'amount', 'Override ' + fmt,
             static=True, header_summary={'stat': 'mean', 'scope': 'leaf_entries', 'format': fmt})
          for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain')],
    ])
    ctx.add_section('Scoped treemap lifecycle', [DataSource('treemap_summary_parent', df),
        ScopedDataSource('treemap_summary_scope', parent='treemap_summary_parent'),
        FilterBar('treemap_summary_scope', df, filters=[{'column': 'platform', 'type': 'toggle'}]),
        TreemapChart(df, ['parent', 'leaf'], 'amount', 'Scoped treemap',
                     dataset_id='treemap_summary_scope', header_summary=SUMMARY)])
    _diagnose(ctx, output, 'native')


def _id(page, title):
    return page.evaluate('''title=>Object.keys(_reportData.components).find(id=>{
        const cfg=_reportData.components[id];return cfg.type==='treemap'&&cfg.title===title;})''', title)


def _root(page, cid):
    return page.locator('#' + cid).locator('xpath=ancestor::div[@class="fw-treemap"][1]')


def _box(page, cid):
    return page.locator(f'[data-chart-summary="{cid}"]')


def _values(page, ids):
    return [_box(page, cid).locator('.fw-chart-summary-value').inner_text() for cid in ids]


def _section(page, cid):
    return page.locator('#' + cid).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')


def _plot(page, cid):
    return page.evaluate('''id=>{const c=_chartInstances[id], ds=c.data.datasets[0];return {
        height:c.height,width:c.width, tree:ds.tree,
        nodes:ds.data.map(r=>({x:r.x,y:r.y,w:r.w,h:r.h,v:r.v,l:r.l,g:r.g})),
        elements:c.getDatasetMeta(0).data.map(e=>({x:e.x,y:e.y,w:e.width,h:e.height,color:e.options.backgroundColor})),
        labels:ds.data.map(raw=>ds.labels.formatter({raw})),
        details:document.getElementById(id).closest('.fw-treemap').querySelector('[data-treemap-context]').textContent,
        tooltips:ds.data.map(raw=>c.options.plugins.tooltip.callbacks.label({raw}))};}''', cid)


def _leaves(page, cid):
    return page.evaluate('''id=>{const ds=_chartInstances[id].data.datasets[0];return ds.data
        .filter(r=>r.l===ds.groups.length-1).map(r=>({value:r.v,
            path:ds.groups.map(k=>r._data.children[0][k])}));}''', cid)


def _source_csv(page, path):
    cid = page.evaluate("Object.keys(_reportData.components).find(id=>{const c=_reportData.components[id];"
        "return c.type==='table'&&c.dataset_id==='showcase_treemap_summary_leaves'})")
    with page.expect_download() as download:
        page.locator(f'[data-csv-target="{cid}"]').click()
    download.value.save_as(str(path))
    return list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))


def _capture(page, locator, path):
    # Document coordinates keep sticky report/filter chrome above the captured
    # section instead of covering a plot midway through a phone screenshot.
    page.evaluate('window.scrollTo(0,0)')
    clip = locator.evaluate('''el=>{const r=el.getBoundingClientRect();return {
        x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height};}''')
    page.screenshot(path=str(path), clip=clip, full_page=True, animations='disabled')


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_summaries_filters_details_lifecycle_and_exports(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            _route(page, tmp_path)
            page.goto('http://treemap-summary.test/index.html', wait_until='networkidle')
            _ready(page)
            _settle(page)
            ids = [_id(page, 'Filtered treemap ' + stat) for stat in ('sum', 'count', 'mean', 'min', 'max')]
            section = _section(page, ids[0])
            independent = _id(page, 'Treemap categorical context')
            assert _values(page, ids) == ['$900.00', '4', '$225.00', '$90.00', '$360.00']
            assert sorted(r['value'] for r in _leaves(page, ids[0])) == [90, 180, 270, 360]
            assert _box(page, ids[0]).locator('.fw-treemap-summary-reason').count() == 0
            assert 'Count independent of numeric eligibility' in _box(page, ids[1]).inner_text()
            off, on = [_id(page, 'Treemap summary ' + state) for state in ('off', 'on')]
            assert _box(page, off).count() == 0
            assert _plot(page, off) == _plot(page, on)
            assert [page.locator('#' + cid).evaluate("el=>el.toDataURL()") for cid in (off, on)][0] == page.locator('#' + on).evaluate("el=>el.toDataURL()")
            off_csv = _csv(page, _root(page, off), EVIDENCE / f'off-{width}.csv')
            assert off_csv == _csv(page, _root(page, on), EVIDENCE / f'on-{width}.csv')
            assert off_csv.decode('utf-8-sig').splitlines() == ['label,value']
            source = _source_csv(page, EVIDENCE / f'source-all-{width}.csv')
            assert len(source) == 20 and sum(float(r['amount']) for r in source) == 900
            formats = ('number', 'currency', 'percent', 'ratio', 'chips', 'plain')
            assert _values(page, [_id(page, 'Format ' + fmt) for fmt in formats]) == ['225', '$225.00', '22500.00%', '225', '225', '225']
            assert _values(page, [_id(page, 'Override ' + fmt) for fmt in formats]) == ['225', '$225.00', '22500.00%', '225.00x', '225 chips', '225']
            root = _root(page, ids[0])
            root.locator('summary').click()
            root.locator('summary').focus()
            _day(section, '2026-08-01')
            section.get_by_role('button', name='iOS', exact=True).click()
            expected = ['$100.00', '4', '$25.00', '$10.00', '$40.00']
            assert _values(page, ids) == expected
            assert _values(page, [independent]) == ['$225.00']
            assert root.locator('details').evaluate('el=>el.open')
            assert sorted(r['value'] for r in _leaves(page, ids[0])) == [10, 20, 30, 40]
            source = _source_csv(page, EVIDENCE / f'source-filtered-{width}.csv')
            assert len(source) == 5 and sum(float(r['amount']) for r in source) == 100
            assert {r['platform'] for r in source} == {'iOS'} and {r['day'] for r in source} == {'2026-08-01'}
            page.wait_for_function("location.search.includes('iOS')")
            selected_url = page.url
            page.reload(wait_until='networkidle')
            _ready(page)
            _settle(page)
            # Existing shared URL writer scans all date bars for presets: the
            # independent bar's All preset supersedes this custom range. Keep
            # this retained limitation explicit, then test supported range URL
            # restoration independently of that writer (no shared-code fix).
            assert page.url == selected_url and _values(page, ids) == ['$300.00', '4', '$75.00', '$30.00', '$120.00']
            from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
            parts = urlsplit(selected_url)
            params = dict(parse_qsl(parts.query))
            params['showcase_treemap_summary_leaves.day'] = '2026-08-01..2026-08-01'
            page.goto(urlunsplit(parts._replace(query=urlencode(params))), wait_until='networkidle')
            _ready(page)
            _settle(page)
            assert _values(page, ids) == expected
            edge = [_id(page, 'Treemap eligibility ' + stat) for stat in ('min', 'mean', 'count')]
            edge_section = _section(page, edge[0])
            for case, numeric in (('Zero', ['$0.00', '$10.00']), ('Null', ['—', '—']),
                                  ('String', ['—', '—']), ('Blank', ['—', '—']),
                                  ('Signed', ['—', '—']), ('All zero', ['$0.00', '$0.00']),
                                  ('Duplicate', ['$5.00', '$21.00'])):
                edge_section.get_by_role('button', name=case, exact=True).click()
                assert _values(page, edge[:2]) == numeric, (case, _values(page, edge))
                assert _values(page, edge[2:]) == [str(len(_leaves(page, edge[2])))]
                if case in ('Null', 'String', 'Signed'):
                    assert 'Every supplied value' in _box(page, edge[0]).inner_text()
                elif case == 'Blank':
                    assert 'Blank or suppressed' in _box(page, edge[0]).inner_text()
                elif case == 'All zero':
                    # This plugin keeps zero leaves despite NaN rectangle
                    # geometry. Entry eligibility never certifies drawing.
                    assert len(_leaves(page, edge[0])) == 2
                    assert _box(page, edge[0]).locator('.fw-treemap-summary-reason').count() == 0
            paths = [r['path'] for r in _leaves(page, edge[0])]
            assert ['A.B', 'C'] in paths and ['A', 'B.C'] in paths
            assert ['P', 'Same'] in paths and ['Q', 'Same'] in paths and len(paths) == 5
            assert _root(page, edge[0]).locator('safe').count() == 0
            page.evaluate("fw.filterEngine.setFilter('showcase_treemap_summary_eligibility','empty','case','equals','Absent')")
            assert _values(page, edge) == ['—', '—', '0']
            page.evaluate("fw.filterEngine.clearFilter('showcase_treemap_summary_eligibility','empty')")
            edge_section.get_by_role('tab', name='Reveal treemap', exact=True).click()
            _ready(page)
            _settle(page)
            revealed = _id(page, 'Revealed treemap with long wrapping title')
            assert _values(page, [revealed]) == ['$900.00']
            assert '<safe>' in _box(page, revealed).inner_text() and _box(page, revealed).locator('safe').count() == 0
            assert _values(page, [_id(page, 'Empty treemap numeric'), _id(page, 'Empty treemap count')]) == ['—', '0']
            for theme in ('trellum light', 'trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                _ready(page)
                _settle(page)
                assert _values(page, ids) == expected and _values(page, [independent]) == ['$225.00']
                assert _plot(page, off) == _plot(page, on)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                for loc, label in ((section, 'filtered'), (_section(page, off), 'pair'),
                    (edge_section, 'eligibility'), (_section(page, independent), 'context'), (_box(page, revealed), 'long-label')):
                    _capture(page, loc, EVIDENCE / f'{width}-{theme.split()[-1]}-{label}.png')
            page.evaluate('renderAll();renderAll()')
            _ready(page)
            assert _values(page, ids) == expected
            lifecycle = page.evaluate('''id=>{const cfg=_reportData.components[id],s=cfg.header_summary;
                const canvas=document.getElementById(id);canvas.setAttribute('aria-describedby',canvas.getAttribute('aria-describedby')+' unrelated');
                canvas.replaceWith(canvas.cloneNode(true));cfg.dataset_id='showcase_treemap_summary_leaves';_fwRenderers.treemap(id,cfg);
                const read=()=>document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent;
                const replacement=read();cfg.dataset_id='showcase_treemap_summary_context';_fwRenderers.treemap(id,cfg);
                const independent=read();const root=document.getElementById(id).closest('.fw-treemap');
                root.querySelector('details').open=true;root.querySelector('summary').focus();
                delete cfg.header_summary;_fwRenderers.treemap(id,cfg);
                const removed=root.querySelectorAll('[data-chart-summary]').length,described=document.getElementById(id).getAttribute('aria-describedby');
                const focus=document.activeElement===root.querySelector('summary');cfg.header_summary=s;
                for(let i=0;i<3;i++)_fwRenderers.treemap(id,cfg);
                return {replacement,independent,removed,described,focus,restored:read(),
                    open:root.querySelector('details').open,subs:Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length};}''', independent)
            assert lifecycle == {'replacement': '$25.00', 'independent': '$225.00', 'removed': 0,
                'described': independent + '-header-context unrelated', 'focus': True, 'restored': '$225.00', 'open': True, 'subs': 1}
            # Source replacement/removal/re-entry and scoped-parent updates.
            scoped = _id(page, 'Scoped treemap')
            states = page.evaluate('''id=>{const engine=_fwFilterEngine;
                const rows=engine.getFiltered('treemap_summary_parent');
                const read=()=>document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent;
                engine.init('treemap_summary_parent',[{parent:'New',leaf:'One',amount:12}]);const replaced=read();
                engine.removeDataset('treemap_summary_parent');const removed=read();
                engine.init('treemap_summary_parent',rows);const restored=read();
                engine.removeDataset('treemap_summary_scope');engine.addScopedChild('treemap_summary_scope','treemap_summary_parent');
                return {replaced,removed,restored,reentry:read(),subs:engine._subscribers.treemap_summary_scope.filter(s=>s.id===id).length};}''', scoped)
            assert states == {'replaced': '12', 'removed': '—', 'restored': '900', 'reentry': '900', 'subs': 1}
            before = _csv(page, _root(page, independent), EVIDENCE / f'bound-on-{width}.csv')
            for enabled in (False, True, False):
                page.evaluate('''({id,enabled})=>{const cfg={..._reportData.components[id]};if(!enabled)delete cfg.header_summary;
                    _fwRenderers.treemap(id,cfg);}''', {'id': independent, 'enabled': enabled})
                assert _csv(page, _root(page, independent), EVIDENCE / f'bound-{enabled}-{width}.csv') == before
            page.evaluate('id=>_fwRenderers.treemap(id,_reportData.components[id])', independent)
            # A view window, hover or details disclosure cannot redefine leaves.
            root = _root(page, ids[0])
            root.locator('summary').click()
            assert _values(page, ids) == expected
            root.locator('canvas').scroll_into_view_if_needed()
            point = page.evaluate('''id=>{const c=_chartInstances[id];
                const i=c.data.datasets[0].data.findIndex(r=>r.l===1&&r.g==='UA');
                return c.getDatasetMeta(0).data[i].getCenterPoint();}''', ids[0])
            rectangle = root.locator('canvas').bounding_box()
            page.mouse.move(rectangle['x'] + point['x'], rectangle['y'] + point['y'])
            page.wait_for_function("id=>_chartInstances[id].tooltip.title[0]==='Paid › UA'", arg=ids[0])
            assert _values(page, ids) == expected
            page.mouse.move(0, 0)
            plot_before = _plot(page, ids[0])
            canvas_image = page.locator('#' + ids[0]).evaluate("el=>el.toDataURL().split(',')[1]")
            (EVIDENCE / f'raw-canvas-{width}.png').write_bytes(base64.b64decode(canvas_image))
            snapshot = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE / f'native-section-{width}.json').write_text(json.dumps(snapshot, indent=2))
            (EVIDENCE / f'native-section-{width}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
            assert _plot(page, ids[0]) == plot_before and _values(page, ids) == expected
            if width == 1280:
                for extension, function in (('png', 'exportPNG'), ('pdf', 'exportPDF')):
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f'fw.{function}()')
                    download.value.save_as(str(EVIDENCE / f'native-report.{extension}'))
                assert _values(page, ids) == expected
            # LiveWrap keeps workspace draw leases and their disposal ownership.
            page.evaluate('''id=>{window._fwMetricsWorkspace=true;_fwRenderers.treemap(id,_reportData.components[id]);
                _fwRenderers.treemap(id,_reportData.components[id]);}''', independent)
            assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length', independent) == 1
            page.evaluate('id=>document.getElementById(id).remove()', independent)
            page.wait_for_function('id=>!_fwTreemapBindings[id]', arg=independent)
            assert _box(page, independent).count() == 0
            assert page.locator('#' + independent + '-header-context').count() == 1
            assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length', independent) == 0
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.heavy
def test_owned_resources_stay_bounded_across_replacements_and_detach(tmp_path):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            _route(page, tmp_path)
            page.goto('http://treemap-resources.test/index.html', wait_until='networkidle')
            _ready(page)
            cid = _id(page, 'Treemap categorical context')
            resources = page.evaluate('''id=>{
                const cfg=_reportData.components[id],canvas=document.getElementById(id);
                const add=window.addEventListener,remove=window.removeEventListener,themes=new Set();
                const lifeBefore=_fwFilterEngine._lifecycleListeners.size, chartsBefore=Object.keys(_chartInstances).length;
                window.addEventListener=function(t,f,o){if(t==='fw-theme-change')themes.add(f);return add.call(this,t,f,o)};
                window.removeEventListener=function(t,f,o){if(t==='fw-theme-change')themes.delete(f);return remove.call(this,t,f,o)};
                try {
                    const original=JSON.stringify(_chartInstances[Object.keys(_chartInstances)[0]].data.datasets[0].data);
                    for(let i=0;i<60;i++) {
                        const next={...cfg};if(i%3===0)delete next.header_summary;
                        if(i%5===0){delete next.dataset_id;next.rows=[{parent:'A',leaf:'B',amount:3}];}
                        _fwRenderers.treemap(id,next);
                    }
                    _fwRenderers.treemap(id,cfg);
                    const read=()=>({themes:themes.size,lifecycle:_fwFilterEngine._lifecycleListeners.size-lifeBefore,
                        subs:Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length,
                        charts:Object.keys(_chartInstances).length-chartsBefore,
                        summaries:document.querySelectorAll('[data-chart-summary="'+id+'"]').length});
                    const plain=read();window._fwMetricsWorkspace=true;
                    for(let i=0;i<60;i++)_fwRenderers.treemap(id,cfg);
                    const workspace=read();
                    _fwTreemapBindings[id]();
                    return {plain,workspace,disposed:read(),lease:!!_fwMetricDrawLeases[id],
                        otherUnchanged:JSON.stringify(_chartInstances[Object.keys(_chartInstances)[0]].data.datasets[0].data)===original};
                } finally {window.addEventListener=add;window.removeEventListener=remove;}
            }''', cid)
            assert resources == {'plain': {'themes': 1, 'lifecycle': 0, 'subs': 1, 'charts': 0, 'summaries': 1},
                'workspace': {'themes': 1, 'lifecycle': 0, 'subs': 1, 'charts': 0, 'summaries': 1},
                'disposed': {'themes': 0, 'lifecycle': -1, 'subs': 0, 'charts': 0, 'summaries': 1},
                'lease': False, 'otherUnchanged': True}
            # A separate DOM detach must retire actual chart and owned markup.
            page.evaluate('id=>{_fwRenderers.treemap(id,_reportData.components[id]);document.getElementById(id).remove()}', cid)
            page.wait_for_function('id=>!_fwTreemapBindings[id]&&!_chartInstances[id]', arg=cid)
            assert _box(page, cid).count() == 0
        finally:
            browser.close()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_actual_baseline_off_pixels_geometry_details_colors_and_csv(tmp_path, width):
    from playwright.sync_api import sync_playwright

    outputs = []
    for name in ('baseline', 'current'):
        output = tmp_path / name
        output.mkdir()
        cls = baseline_class() if name == 'baseline' else TreemapChart
        df = frame()
        ctx = ReportContext(config={'name': 'Treemap baseline', 'studio': 'test', 'category': 'Test',
            'description': 'Synthetic additive USD source facts with native hierarchy colors labels details and filters; optional summaries disabled.'},
            slug='treemap-baseline', output_dir=str(output))
        ctx.add_section('Default treemap plots', [DataSource('baseline_source', df),
            FilterBar('baseline_source', df, filters=[{'column': 'platform', 'type': 'toggle'}]),
            cls(df, ['parent', 'leaf'], 'amount', 'Static', value_format='currency', static=True),
            cls(df, ['parent', 'leaf'], 'amount', 'Bound', color_col='numeric_color',
                value_format='currency', dataset_id='baseline_source', show_percentages=True),
            BarChart(df, 'leaf', 'amount', 'CSV delegate', static=True)])
        _diagnose(ctx, output, name)
        if name == 'baseline':
            # The historical class loads assets from today's tree. Replace
            # both with their actual Git baseline bytes in the native build.
            for asset in ('trellum/static/js/components/treemap.js', 'trellum/static/css/components/treemap.css'):
                current = (ROOT / asset).read_text()
                historical = subprocess.check_output(['git', 'show', f'{BASELINE}:{asset}'], cwd=ROOT, text=True)
                replaced = False
                for file in [output / 'index.html', *output.rglob('*.js'), *output.rglob('*.css')]:
                    text = file.read_text()
                    if current in text:
                        file.write_text(text.replace(current, historical))
                        replaced = True
                assert replaced
        outputs.append(output)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            results = {}
            for name, output in zip(('baseline', 'current'), outputs):
                page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
                _route(page, output)
                page.goto('http://treemap-baseline.test/index.html', wait_until='networkidle')
                _ready(page)
                for theme in ('trellum light', 'trellum dark'):
                    page.locator('#fwThemeSelect').select_option(theme)
                    _ready(page)
                    _settle(page)
                    page.mouse.move(0, 0)
                    ids = [_id(page, title) for title in ('Static', 'Bound')]
                    plots = [_plot(page, cid) for cid in ids]
                    pixels = [page.locator('#' + cid).evaluate('el=>el.toDataURL()') for cid in ids]
                    exports = [_csv(page, _root(page, cid), EVIDENCE / f'baseline-{name}-{title}-{width}-{theme.split()[-1]}.csv')
                               for title, cid in zip(('Static', 'Bound'), ids)]
                    _capture(page, page.locator('.fw-section'), EVIDENCE / f'opt-out-{name}-{width}-{theme.split()[-1]}.png')
                    results[name, theme] = (plots, pixels, exports)
                page.close()
            for theme in ('trellum light', 'trellum dark'):
                assert results['baseline', theme] == results['current', theme]
        finally:
            browser.close()
