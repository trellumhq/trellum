"""Doughnut authoring compatibility, eligibility and real report interactions."""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import DataSource, DoughnutChart, FilterBar
from trellum.components.base import RenderContext
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report


def _examples():
    path = Path(__file__).parents[1] / 'demo/reports/component-showcase/examples_doughnuts.py'
    spec = importlib.util.spec_from_file_location('doughnut_examples', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_report(output):
    ctx = ReportContext(
        config={'name': 'Doughnut review', 'studio': 'test', 'category': 'Test',
                'description': 'Deterministic doughnut category values, shares, filtering and visibility fixtures.'},
        slug='doughnuts', output_dir=str(output))
    _examples().add_sections(ctx)
    frame = pd.DataFrame([
        {'case': 'Complete', 'category': 'Organic', 'value': 10},
        {'case': 'Incomplete', 'category': 'Organic', 'value': 10},
        {'case': 'Incomplete', 'category': 'Organic', 'value': None},
        {'case': 'Signed', 'category': 'Organic', 'value': 20},
        {'case': 'Signed', 'category': 'Organic', 'value': -10},
    ])
    ctx.add_section('Live eligibility', [
        DataSource('doughnut_eligibility', frame),
        FilterBar('doughnut_eligibility', frame, filters=[
            {'column': 'case', 'type': 'toggle'},
        ]),
        DoughnutChart(frame, 'category', 'value', 'Live eligibility',
                      dataset_id='doughnut_eligibility', center_total=True),
    ])
    from trellum.reporting.diagnostics import compute_details
    details = compute_details(ctx)
    validation = validate_report(ctx, details=details)
    assert validation.summary['warn'] == validation.summary['fail'] == 0, validation.to_dict()
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    validation.write(str(output))
    return ctx


def test_constructor_preserves_positions_static_grain_and_new_options():
    positions = [name for name, p in inspect.signature(DoughnutChart).parameters.items()
                 if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
    assert positions == ['df', 'label', 'value', 'title', 'dataset_id', 'static', 'cross_filter', 'show_labels']
    frame = pd.DataFrame({'category': ['A', 'A'], 'value': [1, 2]})
    for dataset in (None, 'bound'):
        ctx = RenderContext(DEFAULT_THEME)
        html = DoughnutChart(frame, 'category', 'value', 'Title', dataset, True, True, True,
                             center_total=True, center_label='<Revenue>',
                             value_format='currency', precision=3).render_html(ctx)
        cfg = next(iter(ctx._component_data.values()))
        assert cfg['show_labels'] is True
        assert cfg['center_total'] is True
        assert cfg['center_label'] == '<Revenue>'
        assert cfg['precision'] == 3
        assert '<Revenue>' not in html
        if dataset:
            assert cfg['crossFilter'] == 'category'
        else:
            assert cfg['labels'] == ['A', 'A'] and cfg['data'] == [1, 2]
    assert '.fw-doughnut ' in DoughnutChart.css()
    nullable = pd.DataFrame({'category': ['A', 'B'], 'value': pd.Series([1, pd.NA], dtype='Int64')})
    ctx = RenderContext(DEFAULT_THEME)
    DoughnutChart(nullable, 'category', 'value', 'Nullable').render_html(ctx)
    cfg = next(iter(ctx._component_data.values()))
    assert cfg['data'] == [1, None] and cfg['supplied_values'] == ['1', 'null']


@pytest.mark.parametrize('options', [
    {'value_format': 'unknown'}, {'precision': -1}, {'precision': 13},
    {'precision': True}, {'precision': 1.2}, {'center_total': 'yes'}, {'center_label': None},
])
def test_invalid_options_are_rejected(options):
    with pytest.raises(ValueError):
        DoughnutChart(pd.DataFrame(), 'category', 'value', 'Title', **options)


@pytest.fixture
def report_page(tmp_path):
    from playwright.sync_api import sync_playwright
    build_report(tmp_path)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.set_default_timeout(8000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: msg.type == 'error' and errors.append(msg.text))
            # Observe listener admission without replacing browser behavior.
            page.add_init_script('''window._doughnutThemeAdds=0;window._doughnutThemeRemoves=0;
                const add = window.addEventListener;
                window.addEventListener = function(type, fn, options) {
                    if (type === 'fw-theme-change') window._doughnutThemeAdds++;
                    return add.call(this, type, fn, options);
                };
                const remove = window.removeEventListener;
                window.removeEventListener = function(type, fn, options) {
                    if(type === 'fw-theme-change') window._doughnutThemeRemoves++;
                    return remove.call(this,type,fn,options);
                };''')
            page.goto(f'http://127.0.0.1:{server.server_port}/index.html', wait_until='networkidle')
            for cid in page.locator('.fw-doughnut canvas').evaluate_all('(cs)=>cs.map(c=>c.id)'):
                page.locator('#' + cid).scroll_into_view_if_needed()
                page.wait_for_function('(id)=>!!window._chartInstances[id]', arg=cid)
            page.evaluate('Object.values(window._chartInstances).forEach(c=>{c.stop();c.update("none")})')
            yield page
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def _root(page, title):
    return page.locator('#' + _cid(page, title)).locator('xpath=ancestor::div[@class="fw-doughnut"][1]')


def _cid(page, title):
    return page.evaluate('(title)=>Object.keys(window._reportData.components).find(id=>window._reportData.components[id].type==="doughnut" && window._reportData.components[id].title===title)', title)


def _capture(page, name):
    directory = os.environ.get('TRELLUM_SCREENSHOT_DIR')
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.evaluate('''async()=>{
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            Object.values(window._chartInstances).forEach(c=>{c.stop();c.update('none')});
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        }''')
        geometry = page.evaluate('''()=>Object.values(window._chartInstances).map(c=>({
            id:c.canvas.id, width:c.width,height:c.height,buffer:[c.canvas.width,c.canvas.height],
            css:c.canvas.style.cssText,box:c.canvas.getBoundingClientRect().toJSON(),
            arc:c.getDatasetMeta(0).data[0] && {x:c.getDatasetMeta(0).data[0].x,
                y:c.getDatasetMeta(0).data[0].y,outer:c.getDatasetMeta(0).data[0].outerRadius}
        }))''')
        (Path(directory) / name.replace('.png', '.json')).write_text(json.dumps(geometry, indent=2))
        page.screenshot(path=str(Path(directory) / name), full_page=True)
        page.locator('.fw-section').first.screenshot(
            path=str(Path(directory) / name.replace('.png', '-center-options.png')))


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_real_ring_layout_visibility_focus_and_raw_csv(report_page, width):
    page = report_page
    page.set_viewport_size({'width': width, 'height': 900})
    default, optional = _root(page, 'Default ring'), _root(page, 'Optional total')
    default.scroll_into_view_if_needed()
    cid = _cid(page, 'Default ring')
    geometry = page.evaluate('''id => {
        const c=window._chartInstances[id]; c.stop();c.update('none');
        const arc=c.getDatasetMeta(0).data[0];
        const pixels=c.ctx.getImageData(0,0,c.canvas.width,c.canvas.height).data;
        let painted=0; for(let i=3;i<pixels.length;i+=4) if(pixels[i]) painted++;
        return {inner:arc.innerRadius,outer:arc.outerRadius,painted};
    }''', cid)
    assert geometry['outer'] > 70 and .73 < geometry['inner'] / geometry['outer'] < .75
    assert geometry['painted'] > 2000
    assert default.locator('.fw-doughnut-ring').bounding_box()['height'] == 170
    assert default.locator('.fw-doughnut-center').is_hidden()
    assert optional.locator('.fw-doughnut-center-value').inner_text() == '$100,000.00'
    assert default.bounding_box()['height'] == optional.bounding_box()['height']
    assert default.locator('.fw-doughnut-value').all_text_contents() == ['$48,000.00', '$30,000.00', '$14,000.00', '$6,000.00', '$2,000.00']
    assert default.locator('.fw-doughnut-share').all_text_contents() == ['48.0%', '30.0%', '14.0%', '6.0%', '2.0%']
    assert 'Share of all categories' in default.inner_text()
    assert default.locator('.fw-doughnut-value').first.get_attribute('title') == 'Supplied values: 48000; chart value: 48000'
    entry = default.locator('.fw-doughnut-entry').first
    entry.focus()
    entry.press('Space')
    assert entry.get_attribute('aria-pressed') == 'false'
    assert entry.evaluate('(el)=>document.activeElement===el')
    assert 'Hidden' in entry.inner_text()
    assert 'Hiding changes only the ring' in default.inner_text()
    assert default.locator('.fw-doughnut-share').first.inner_text() == '48.0%'
    assert page.evaluate('(id)=>window._chartInstances[id].getDataVisibility(0)', cid) is False
    # Geometry renormalizes visible arcs; the original-result percentages do not.
    assert page.evaluate('''id=>{
        const c=window._chartInstances[id]; c.stop();c.update('none');
        return c.getDatasetMeta(0).total;
    }''', cid) == 52000
    # A mixed-chart report may also load the ordinary chart CSV delegate.
    # Doughnut's local button must still download exactly once.
    from trellum.assets import load_js
    page.evaluate('(js)=>eval(js)', load_js('components/chart_base.js'))
    page.evaluate('''()=>{
        const download=window._fwDownloadCsv;window._doughnutDownloads=0;
        window._fwDownloadCsv=function(...args){window._doughnutDownloads++;return download(...args)};
    }''')
    with page.expect_download() as download:
        default.get_by_title('Download CSV').click(force=True)
    csv = Path(download.value.path()).read_text(encoding='utf-8-sig')
    assert csv.splitlines() == ['label,value', 'Organic,48000', 'Paid social,30000', 'Paid search,14000', 'Partnerships,6000', 'Other,2000']
    assert page.evaluate('window._doughnutDownloads') == 1
    for i in range(1, 5):
        default.locator('.fw-doughnut-entry').nth(i).click()
    assert 'All categories hidden' in default.inner_text()
    assert default.locator('.is-empty').count() == 1
    default.get_by_role('button', name='Restore all').click()
    assert default.locator('.fw-doughnut-entry[aria-pressed=true]').count() == 5
    assert entry.evaluate('(el)=>document.activeElement===el')
    assert default.get_by_role('button', name='Restore all').is_hidden()
    long = _root(page, 'Long names and tiny values')
    assert long.locator('.fw-doughnut-entry').count() == 7
    assert long.locator('.fw-doughnut-value').last.inner_text() == '$100.00'
    assert long.locator('.fw-doughnut-share').last.inner_text() == '0.1%'
    # Label threshold and formatter are identical for static/live options.
    labels = page.evaluate('''id=>{
        const c=window._chartInstances[id], p=c.config.options.plugins.datalabels;
        return {display:c.data.datasets[0].data.map((v,i)=>p.display({dataset:c.data.datasets[0],dataIndex:i})),
                percent:p.formatter(58000)};
    }''', _cid(page, 'Long names and tiny values'))
    assert labels == {'display': [True, True, True, True, False, False, False], 'percent': '58.0%'}
    duplicate = _root(page, 'Static duplicate grain')
    assert duplicate.locator('.fw-doughnut-name').all_text_contents() == ['Organic', 'Organic', 'Paid']
    duplicate.locator('.fw-doughnut-entry').nth(1).click()
    assert duplicate.locator('.fw-doughnut-entry').nth(0).get_attribute('aria-pressed') == 'true'
    assert duplicate.locator('.fw-doughnut-entry').nth(1).get_attribute('aria-pressed') == 'false'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # On a wide viewport this deliberately narrow panel still stacks its legend.
    page.evaluate('''id=>{
        const root=document.getElementById(id).closest('.fw-doughnut');root.style.maxWidth='280px';
    }''', _cid(page, 'Long names and tiny values'))
    assert long.locator('.fw-doughnut-legend').bounding_box()['y'] > long.locator('.fw-doughnut-ring').bounding_box()['y'] + 160
    assert long.locator('.fw-doughnut-name').first.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    page.evaluate('document.querySelectorAll(".fw-doughnut").forEach(el=>el.style.maxWidth="")')
    page.evaluate('scrollTo(0,0)')
    _capture(page, f'doughnut-current-{width}.png')


def test_live_filters_arc_bridge_theme_and_recreation(report_page):
    page = report_page
    title = 'Filterable acquisition revenue'
    root = _root(page, title)
    cid = _cid(page, title)
    section = page.locator('#sec-doughnuts-—-report-filters-and-ring-visibility')
    section.get_by_role('button', name='iOS', exact=True).click()
    page.wait_for_function('(id)=>document.getElementById(id).closest(".fw-doughnut").querySelector(".fw-doughnut-center-value").textContent === "$60,000.00"', arg=cid)
    assert root.locator('.fw-doughnut-share').first.inner_text() == '48.0%'
    assert page.evaluate('(id)=>!!window._chartInstances[id].config.options.plugins.datalabels.display', cid)
    root.locator('.fw-doughnut-entry').first.click()
    assert root.locator('.fw-doughnut-center-value').inner_text() == '$60,000.00'
    with page.expect_download() as download:
        root.get_by_title('Download CSV').click(force=True)
    csv = Path(download.value.path()).read_text(encoding='utf-8-sig')
    assert len(csv.splitlines()) == 6 and 'Organic,28800' in csv
    root.locator('.fw-doughnut-entry').first.click()
    page.locator('#' + cid).scroll_into_view_if_needed()
    point = page.evaluate('''id=>{
        const c=window._chartInstances[id];c.stop();c.update('none');
        const arc=c.getDatasetMeta(0).data[0], a=(arc.startAngle+arc.endAngle)/2;
        const r=(arc.innerRadius+arc.outerRadius)/2, box=c.canvas.getBoundingClientRect();
        return {x:box.x+arc.x+Math.cos(a)*r,y:box.y+arc.y+Math.sin(a)*r};
    }''', cid)
    page.mouse.click(point['x'], point['y'])
    page.wait_for_function('(id)=>window._chartInstances[id].data.labels.length===1', arg=cid)
    assert root.locator('.fw-doughnut-name').all_text_contents() == ['Organic']
    assert root.locator('.fw-doughnut-center-value').inner_text() == '$28,800.00'
    # Clear using the existing FilterBar dropdown, retaining the platform.
    section.locator('.ss-value-delete').click()
    page.wait_for_function('(id)=>window._chartInstances[id].data.labels.length===5', arg=cid)
    assert root.locator('.fw-doughnut-center-value').inner_text() == '$60,000.00'
    before = page.evaluate('window._doughnutThemeAdds-window._doughnutThemeRemoves')
    page.evaluate('''id=>{
        const cfg=window._reportData.components[id];
        for(let i=0;i<3;i++)window._fwRenderers.doughnut(id,cfg);
        const canvas=document.getElementById(id);canvas.replaceWith(canvas.cloneNode(true));
        window._fwRenderers.doughnut(id,cfg);
    }''', cid)
    assert page.evaluate('window._doughnutThemeAdds-window._doughnutThemeRemoves') == before
    assert page.evaluate('(id)=>window._fwFilterEngine._subscribers.showcase_doughnuts.filter(s=>s.id===id).length', cid) == 1
    assert root.locator('.fw-doughnut-entry').count() == 5
    for theme in ('dark', 'money', 'light'):
        page.evaluate('(theme)=>switchTheme(theme)', theme)
        for target in (cid, _cid(page, 'Default ring')):
            page.locator('#' + target).scroll_into_view_if_needed()
            page.wait_for_function('''id=>{
                const c=window._chartInstances[id],root=c.canvas.closest('.fw-doughnut');
                const swatch=root.querySelector('.fw-doughnut-swatch');
                const probe=document.createElement('span');probe.style.backgroundColor=c.data.datasets[0].backgroundColor[0];
                return c.data.datasets[0].backgroundColor[0]===getThemeColors().chart_colors[0] &&
                    swatch.style.backgroundColor===probe.style.backgroundColor;
            }''', arg=target)
    root.locator('.fw-doughnut-entry').first.click()
    assert root.locator('.fw-doughnut-entry').first.get_attribute('aria-pressed') == 'false'
    section.get_by_role('button', name='All', exact=True).click()
    page.wait_for_function('(id)=>document.getElementById(id).closest(".fw-doughnut").querySelector(".fw-doughnut-center-value").textContent === "$100,000.00"', arg=cid)
    assert root.locator('.fw-doughnut-entry').first.get_attribute('aria-pressed') == 'true'


def test_invalid_populations_raw_values_and_legacy_markup(report_page):
    page = report_page
    # Original rows must determine eligibility even when grouped values look valid.
    live = _root(page, 'Live eligibility')
    section = page.locator('#sec-live-eligibility')
    for case, total, reason in [('Complete', '10', ''), ('Incomplete', '—', 'incomplete'), ('Signed', '—', 'signed')]:
        section.get_by_role('button', name=case, exact=True).click()
        assert live.locator('.fw-doughnut-center-value').inner_text() == total
        assert reason in live.locator('.fw-doughnut-context').inner_text()
    section.get_by_role('button', name='Incomplete', exact=True).click()
    assert live.locator('.fw-doughnut-value').inner_text() == '10, null'
    assert live.locator('.fw-doughnut-share').inner_text() == '—'
    assert _root(page, 'Measured zeros').locator('.fw-doughnut-center-value').inner_text() == '$0.00'
    assert 'Shares undefined' in _root(page, 'Measured zeros').inner_text()
    assert _root(page, 'Empty result').locator('.fw-doughnut-center-value').inner_text() == '—'
    assert _root(page, 'Empty result').locator('.fw-doughnut-entry').count() == 0
    assert 'No categories' in _root(page, 'Empty result').inner_text()
    assert _root(page, 'Incomplete supplied values').locator('.fw-doughnut-value').nth(1).inner_text() == 'nan'
    result = page.evaluate('''()=>{
        const values=[[],[0,0],[1,null],[1,NaN],[1,Infinity],[1,'2'],[1,-1],[1e308,1e308],[1,2]];
        return values.map(v=>_fwDoughnutPopulation(v));
    }''')
    assert [r['total'] for r in result] == [None, 0, None, None, None, None, None, None, 3]
    assert 'finite range' in result[7]['reason']
    assert page.evaluate("_fwDoughnutFormat(.1234,{value_format:'percent'})") == '12.34%'
    assert page.evaluate("_fwDoughnutFormat(1.2345,{value_format:'ratio',precision:3})") == '1.235x'
    assert page.evaluate("_fwDoughnutFormat(.0000001,{value_format:'number',precision:8})") == '0.00000010'
    assert page.evaluate("_fwDoughnutFormat(1e308,{value_format:'percent'})") == '1.00e+310%'
    assert page.evaluate("_fwDoughnutScientific(1e20,{value_format:'currency'})") == '$1.00e+20'
    # Render new runtime against old, metadata-free component markup.
    result = page.evaluate('''()=>{
        const holder=document.createElement('div'); holder.className='fw-chart-container';
        holder.innerHTML='<canvas id="legacy-doughnut"></canvas>';document.body.append(holder);
        _fwRenderers.doughnut('legacy-doughnut',{labels:['A','A'],data:[1,2]});
        const chart=_chartInstances['legacy-doughnut'];chart.stop();chart.update('none');
        const result={legend:chart.options.plugins.legend.display,labels:chart.legend.legendItems.length};
        chart.destroy();delete _chartInstances['legacy-doughnut'];holder.remove();return result;
    }''')
    assert result == {'legend': True, 'labels': 2}


def test_ordinary_png_capture_includes_html_legend_and_center(report_page, tmp_path):
    page = report_page
    # Keep this ordinary report capture small; the first section has both
    # default and opt-in totals, with the actual component markup unchanged.
    page.evaluate('''()=>{
        document.querySelectorAll('.fw-section').forEach((section,i)=>{if(i)section.remove()});
        scrollTo(0,0);
        Object.values(window._chartInstances).forEach(c=>{c.stop();c.update('none')});
    }''')
    with page.expect_download() as download:
        page.evaluate('fw.exportPNG()')
    destination = Path(os.environ.get('TRELLUM_SCREENSHOT_DIR', tmp_path)) / 'doughnut-report-export.png'
    destination.parent.mkdir(parents=True, exist_ok=True)
    download.value.save_as(destination)
    assert destination.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert destination.stat().st_size > 10000
    assert page.locator('.fw-doughnut-entry').count() == 10
    assert page.locator('.fw-doughnut-center-value').inner_text() == '$100,000.00'
