"""Opt-in supplied LineChart validity and native vendored-browser presentation."""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import importlib.util
import inspect
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import orjson
import pandas as pd
import pytest

from trellum.assets import load_css, load_js
from trellum.components import LineChart, RenderContext
from trellum.components.charts.line_area import line_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '3ceae68a756eaf77bacdc9401f92dbe084af10e3'


def frame():
    return pd.DataFrame({'day': ['A', 'A', 'C'], 'value': [0, 3, 5], 'other': [7, 11, 13]})


def config(chart):
    ctx = RenderContext(theme=THEME_REGISTRY['trellum light'])
    chart.render_html(ctx)
    return next(iter(ctx.component_data.values()))


def rule(series=0, **extra):
    return {'series': series, 'required_columns': ['value'], **extra}


def test_keyword_only_legacy_position_subclass_and_copy():
    @dataclasses.dataclass
    class Child(LineChart):
        extra: str = 'child'
    assert inspect.signature(Child).parameters['series_availability'].kind == inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(Child).parameters['extra'].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert Child(frame(), 'day', 'value', 'Title').title == 'Title'
    assert [f.name for f in dataclasses.fields(LineChart) if f.init and not f.kw_only][-2:] == ['height', 'header_summary']
    authored = [rule(required_columns=['value', 'other'])]
    chart = Child(frame(), 'day', 'value', series_availability=authored)
    authored[0]['required_columns'].append('ignored')
    authored[0]['series'] = 4
    assert chart.series_availability == [rule(required_columns=['value', 'other'])]
    chart.series_availability[0]['required_columns'].clear()
    with pytest.raises(ValueError, match='required_columns'):
        config(chart)
    assert any(key == 'line-chart-availability-invalid' for key, _ in line_option_problems(chart))


@pytest.mark.parametrize('value', [True, {}, 'value', [None], [0], [dict(rule(), extra=True)],
    [{'required_columns': ['value']}], [rule(True)], [rule(-1)], [rule(4)], [rule('missing')],
    [rule(required_columns=[])], [rule(required_columns=['value', 'value'])],
    [rule(required_columns=[''])], [rule(required_columns=[False])], [rule(required_columns='value')],
    [rule(state_column=None)], [rule(state_column=' ')], [rule(reason='')], [rule(reason=True)],
    [rule(reason='x' * 241)], [rule(reason='control\x00')], [rule(), rule('value')],
    [rule(required_columns=['other'])]])
def test_malformed_and_mutated_declarations(value):
    with pytest.raises(ValueError, match='series_availability'):
        LineChart(frame(), 'day', 'value', series_availability=value)
    chart = LineChart(frame(), 'day', 'value')
    chart.series_availability = value
    with pytest.raises(ValueError, match='series_availability'):
        config(chart)


@pytest.mark.parametrize('value', [None, [], ()])
def test_empty_emits_nothing_and_keeps_dynamic_combinations(value):
    for extra in ({'static': True}, {'dataset_id': 'd', 'stack_by': 'day'},
                  {'dataset_id': 'd', 'stack_by_options': {'Day': 'day'}}):
        assert 'series_availability' not in config(LineChart(frame(), 'day', 'value', series_availability=value, **extra))


@pytest.mark.parametrize('extra', [{'stack_by': 'day'}, {'stack_by_options': {'Day': 'day'}}])
def test_dynamic_series_rejected(extra):
    with pytest.raises(ValueError, match='split series are dynamic'):
        LineChart(frame(), 'day', 'value', series_availability=[rule()], **extra)


def test_actual_captions_ratio_requirements_and_explicit_binding():
    assert config(LineChart(frame(), 'day', ['value', 'other'], y_labels=[''], dataset_id='d', static=True,
        forecast_series=['other'], series_availability=[rule('other', required_columns=['other'])]))['series_availability'][0]['series'] == 1
    assert config(LineChart(frame(), 'day', 'value', y_labels=[''], static=True,
        series_availability=[rule('Series 1')]))['series_availability'][0]['series'] == 0
    with pytest.raises(ValueError, match='exactly one'):
        LineChart(frame(), 'day', ['value', 'other'], y_labels=['Only'], static=True,
                  series_availability=[rule(1)])
    for target in (0, 'Same'):
        with pytest.raises(ValueError, match='shared displayed caption|exactly one'):
            LineChart(frame(), 'day', ['value', 'other'], y_labels=['Same', 'Same'], series_availability=[rule(target)])
    ratios = [{'label': 'Rate', 'numerator': 'value', 'denominator': 'other'}]
    with pytest.raises(ValueError, match='underlying columns'):
        LineChart(frame(), 'day', ratios=ratios, dataset_id='d', series_availability=[rule('Rate')])
    cfg = config(LineChart(frame(), 'day', ratios=ratios, dataset_id='d', static=True,
        series_availability=[rule('Rate', required_columns=['value', 'other'])]))
    assert cfg['dataset_id'] == 'd' and 'datasets' not in cfg
    # A ratio declaration without binding retains ordinary static y semantics.
    cfg = config(LineChart(frame(), 'day', 'value', ratios=ratios, static=True, series_availability=[rule()]))
    assert cfg['datasets'][0]['data'] == [0, 3, 5]


def test_static_strict_inputs_positions_missing_fields_and_state():
    data = pd.DataFrame({'day': ['same'] * 10, 'value': pd.Series([0, 3, '4', True, None, float('inf'), float('nan'), 9, 11, 13], dtype=object),
                         'state': ['available'] * 7 + ['unavailable', None, pd.NA]})
    cfg = config(LineChart(data, 'day', 'value', static=True,
        series_availability=[rule(state_column='state', reason='<img src=x> & literal')]))
    assert cfg['series_availability'][0]['unavailable'] == list(range(2, 10))
    assert cfg['labels'] == ['same'] * 10
    missing = config(LineChart(frame(), 'day', 'absent', static=True,
        series_availability=[rule(required_columns=['absent'])]))
    assert missing['datasets'][0]['data'] == [None] * 3
    assert missing['series_availability'][0]['unavailable'] == [0, 1, 2]
    extra = config(LineChart(frame(), 'day', 'value', static=True,
        series_availability=[rule(required_columns=['value', 'absent'])]))
    assert extra['series_availability'][0]['unavailable'] == [0, 1, 2]


def test_static_nullable_observations_reach_the_runtime_as_json():
    data = pd.DataFrame({'day': ['2026-10-01', '2026-10-02', '2026-10-03'],
                         'value': pd.Series([0, pd.NA, 5], dtype='Int64')})
    cfg = config(LineChart(data, 'day', 'value', static=True,
        series_availability=[rule()]))
    payload = orjson.loads(orjson.dumps(cfg, option=orjson.OPT_SERIALIZE_NUMPY))
    assert payload['datasets'][0]['data'] == [0, None, 5]
    assert payload['series_availability'][0]['unavailable'] == [1]


def test_raw_runtime_masking_before_summary_and_without_global_aggregation_changes():
    # Execute the shipped helper, with real aggregate output; no chart mocks.
    script = "const window={_fwRenderers:{}}; const document={addEventListener(){}};\n" + load_js('runtime/aggregate.js') + load_js('components/chart_base.js') + r'''
    const rows=[
      {x:'a',v:7,s:2,n:4,d:8,state:'available'},
      {x:'a',v:null,s:3,n:null,d:9,state:'available'},
      {x:'b',v:0,s:5,n:0,d:2,state:'available'},
      {x:'c',v:9,s:7,n:1,d:0,state:'available'},
      {x:'d',v:10,s:11,n:-2,d:-1,state:'available'},
      {x:'d',v:11,s:13,n:3,d:3,state:'available'},
      {x:'e',v:12,s:17,n:1,d:2,state:'unavailable'},
      {x:'f',v:Number.MAX_VALUE,s:19,n:1,d:Number.MAX_VALUE,state:'available'},
      {x:'f',v:Number.MAX_VALUE,s:23,n:1,d:Number.MAX_VALUE,state:'available'},
      {x:'g',v:'15',s:29,n:true,d:2,state:'available'},
      {x:'h',v:true,s:31,n:1,d:2,state:'available'},
      {x:'i',s:37,n:1,d:2,state:'available'},
      {x:'j',v:18,s:41,n:1,d:2},
      {x:'k',v:19,s:43,n:Number.MAX_VALUE,d:Number.MIN_VALUE,state:'available'}];
    function mask(rows,ratio=false) {
      const gr=window._fwAggregate.groupBy(rows,'x',['v','s','n','d']);
      const cfg={dataset_id:'d',x:'x',value_cols:['v','s'],series_availability:[
        {series:0,required_columns:ratio?['n','d']:['v'],state_column:'state'}]};
      if(ratio) cfg.ratios=[{numerator:'n',denominator:'d'}];
      const spec={labels:gr.labels,datasets:[{data:gr.labels.map(l=>ratio?
        (gr.map[l].d>0?gr.map[l].n/gr.map[l].d:null):gr.map[l].v||0)},
        {data:gr.labels.map(l=>gr.map[l].s)}]};
      return _fwApplyLineAvailability(cfg,rows,spec);
    }
    const sums=mask(rows),ratios=mask(rows,true),empty=mask([]);
    const staticCfg={series_availability:[{series:0,unavailable:[1]}]};
    const original={labels:['a','a','c'],datasets:[{data:[0,'7',9]}]};
    const stat=_fwApplyLineAvailability(staticCfg,null,original);
    console.log(JSON.stringify({sums,ratios,empty,stat,original,mean:_fwSummaryValue(sums.datasets[0].data,'mean')}));
    '''
    result = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    assert result['sums']['labels'] == list('abcdefghijk')  # no inferred missing categories
    assert result['sums']['datasets'][0]['data'] == [None, 0, 9, 21, None, None, None, None, None, None, 19]
    assert result['sums']['datasets'][1]['data'] == [5, 5, 7, 24, 17, 42, 29, 31, 37, 41, 43]
    assert result['ratios']['datasets'][0]['data'] == [None, 0, None, .5, None, None, None, .5, .5, None, None]
    assert [p['kind'] for p in result['ratios']['availability'][0]['points']] == [
        'inputs', 'denominator', 'inputs', 'denominator', 'inputs', 'inputs', 'aggregate']
    assert result['sums']['datasets'][0]['spanGaps'] is False
    assert result['empty']['datasets'][0]['data'] == [] and result['empty']['availability'] == []
    assert result['stat']['datasets'][0]['data'] == [0, None, 9]
    assert result['original']['datasets'][0]['data'] == [0, '7', 9]
    assert result['mean'] == 49 / 4


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _build(output, baseline=False):
    output.mkdir(parents=True, exist_ok=True)
    module = _module(ROOT / 'trellum/demo/reports/component-showcase/examples_line_availability.py', 'availability_examples')
    historical = False
    original = LineChart
    if baseline:
        try:
            source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/line_area.py'],
                                             cwd=ROOT, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            original = LineChart
        else:
            historical = True
            path = output / 'baseline_line_area.py'
            path.write_bytes(source)
            original = _module(path, 'availability_baseline').LineChart
        (output / 'baseline-reference.txt').write_text(
            'Actual dependency Git sources: ' + BASELINE if historical
            else 'Current opt-out control (dependency history unavailable)')
    def factory(*args, **kwargs):
        if baseline:
            kwargs.pop('series_availability', None)
        # Compare availability independently of the later adaptive-event default.
        kwargs.setdefault('event_labels', 'grouped')
        return original(*args, **kwargs)
    module.LineChart = factory
    ctx = ReportContext(config={'name': 'Supplied line observations', 'studio': 'shared', 'category': 'Test',
        'description': 'Synthetic daily platform and channel contributions, supplied input availability and selected rates.',
        'theme': 'trellum light', 'annotations': {'events': [
            {'date': '2026-10-01', 'type': 'release', 'label': 'Synthetic supplied checkpoint'}]}},
        slug='line-availability', output_dir=str(output), theme=THEME_REGISTRY['trellum light'])
    module.add_sections(ctx)
    if not baseline:
        result = validate_report(ctx, details=compute_details(ctx))
        assert not [(c.id, c.message) for c in result.checks if c.level in ('warn', 'fail')]
        result.write(str(output))
    from trellum.runner.events import local_annotation_events
    render_report(ctx, str(output), data={'_events': local_annotation_events(ctx.config)}, auto_refresh=False)
    copy_vendor_tree(output)
    if historical:
        html = (output / 'index.html').read_text()
        for path, current in [('js/components/chart_base.js', load_js('components/chart_base.js')),
                              ('css/components/chart_base.css', load_css('components/chart_base.css'))]:
            old = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/{path}'], cwd=ROOT).decode()
            assert current in html
            html = html.replace(current, old)
        (output / 'index.html').write_text(html)
    return ctx


def test_report_validator_reports_mutated_availability(tmp_path):
    ctx = ReportContext(config={'name': 'Mutation check', 'studio': 'shared', 'category': 'Test',
        'description': 'Synthetic supplied observations for declaration validation.'},
        slug='line-availability-mutation', output_dir=str(tmp_path), theme=THEME_REGISTRY['trellum light'])
    chart = LineChart(frame(), 'day', 'value', static=True)
    ctx.add_section('Supplied observations', [chart])
    chart.series_availability = [rule(required_columns=[])]
    result = validate_report(ctx, details=compute_details(ctx))
    assert [(check.id, check.level) for check in result.checks if check.id.startswith('line-chart-')] == [
        ('line-chart-availability-invalid', 'fail')]


def test_genuine_module_full_validation(tmp_path):
    _build(tmp_path)


def _open(page, output):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip('/')
        if not path.is_file():
            route.fulfill(status=404, body='not found')
        else:
            route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
    page.route('**/*', serve)
    page.goto('http://availability.test/index.html', wait_until='networkidle')
    page.evaluate('document.fonts.ready')
    for cid in page.locator('canvas[data-fw-kind]').evaluate_all('els=>els.map(e=>e.id)'):
        page.locator('#' + cid).scroll_into_view_if_needed()
        page.wait_for_function('id=>!!window._chartInstances[id]', arg=cid)


_STATE = r'''title=>{
  const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
  const note=document.getElementById(c.canvas.id+'-availability');
  return {id:c.canvas.id,labels:c.data.labels, data:c.data.datasets.map(d=>d.data),
    height:c.height,area:{...c.chartArea},min:c.scales.y.min,max:c.scales.y.max,
    described:c.canvas.getAttribute('aria-describedby'), note:note?.textContent||'',
    summary:document.querySelector('[data-chart-summary="'+c.canvas.id+'"]')?.textContent,
    ds:c.data.datasets.map((d,i)=>({span:d.spanGaps,tension:d.tension,dash:d.borderDash,
      segments:c.getDatasetMeta(i).dataset.segments.map(s=>[s.start,s.end]),
      points:c.getDatasetMeta(i).data.map(p=>({skip:p.skip,radius:p.options.radius,x:p.x,y:p.y}))})),
    paint:c.canvas.toDataURL()};
}'''


def _browser(tmp_path, width):
    from playwright.sync_api import sync_playwright
    changed, baseline = tmp_path / 'changed', tmp_path / 'baseline'
    _build(changed)
    _build(baseline, baseline=True)
    evidence = Path(os.environ.get('TRELLUM_SCREENSHOT_DIR', str(tmp_path / 'evidence')))
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        options = {'viewport': {'width': width, 'height': 900}, 'device_scale_factor': 1}
        page = browser.new_page(**options)
        before = browser.new_page(**options)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        _open(before, baseline)
        _open(page, changed)
        def state(title, target=page):
            target.locator(f'canvas[data-fw-title="{title}"]').scroll_into_view_if_needed()
            return target.evaluate(_STATE, title)
        title = 'Complete selected orders'
        actual = state(title)
        assert actual['labels'] == ['2026-09-28', '2026-09-29', '2026-10-01', '2026-10-02']
        assert actual['data'] == [[29, 0, None, 74]]
        assert actual['ds'][0]['segments'] == [[0, 1], [3, 3]]
        assert actual['ds'][0]['span'] is False and actual['ds'][0]['points'][3]['radius'] == 2
        assert 'Orders: 1 unavailable observation (2026-10-01)' in actual['note']
        assert actual['height'] == 220 and (actual['min'], actual['max']) == (0, 100)
        # The undeclared plot matches actual dependency Python, JS and CSS paint.
        old = state('Legacy selected subtotal', before)
        off = state('Legacy selected subtotal')
        assert off['paint'] == old['paint'] and off['area'] == old['area']
        for target, name in ((before, 'baseline'), (page, 'changed')):
            target.locator(f'canvas[data-fw-title="{title}"]').scroll_into_view_if_needed()
            target.evaluate('window.scrollTo(0,0)')
            target.screenshot(path=str(evidence / f'line-availability-{name}-{width}.png'), full_page=True)
            target.locator(f'canvas[data-fw-title="{title}"]').locator('xpath=ancestor::*[contains(@class,"fw-section")][1]').screenshot(
                path=str(evidence / f'line-availability-orders-{name}-{width}.png'))
        total = state('Selected complete daily totals')
        assert total['data'] == [[54, None, 66, 72], [119, 129, None, 151]]
        rate = state('Complete supplied conversion rate')
        assert rate['data'] == [[18/84, None, None, 0, 34/114]]
        assert 'No valid positive denominator' in rate['note'] and 'Required observations are incomplete' in rate['note']
        assert 'Mean plotted daily rate' in rate['summary']
        expected_mean = (18 / 84 + 34 / 114) / 3
        assert page.evaluate('id=>_fwSummaryValue(window._chartInstances[id].data.datasets[0].data,"mean")', rate['id']) == expected_mean
        assert '2026-10-08, 2026-10-09' in rate['note']
        snapshot = state('Static supplied observations and forecast')
        assert snapshot['data'] == [[18, 0, None, 29, 34], [20, 23, 25, 31, 36]]
        assert snapshot['ds'][1]['dash'] == [7, 4] and snapshot['ds'][0]['tension'] == .25
        assert '<Source & status>' in snapshot['note']
        assert page.locator('.fw-line-availability img').count() == 0
        sid = snapshot['id']
        assert all(token in snapshot['described'] for token in (sid+'-header-context', sid+'-month-context', sid+'-event-details', sid+'-availability'))
        assert page.evaluate('''id=>{
          const plot=document.getElementById(id).parentElement;
          return [plot.nextElementSibling.id,plot.nextElementSibling.nextElementSibling.id,
            plot.nextElementSibling.nextElementSibling.nextElementSibling.id];
        }''', sid) == [sid+'-month-context', sid+'-event-details', sid+'-availability']
        # Focused native event DOM is retained through legend/zoom updates.
        summary = page.locator('#' + sid + '-event-details summary')
        summary.click()
        summary.focus()
        page.evaluate('id=>{const c=window._chartInstances[id];c.zoomScale("x",{min:1,max:3});c.update("none");}', sid)
        assert summary.evaluate('e=>e===document.activeElement')
        assert page.locator('#' + sid + '-availability').text_content() == snapshot['note']
        page.evaluate('id=>window._chartInstances[id].resetZoom()', sid)
        # Native HTML legend interaction controls availability context too.
        legend = page.locator('#' + sid).locator('..').locator('..').locator('.fw-chart-legend .cl').filter(has_text='Observed')
        legend.click()
        assert page.locator('#' + sid + '-availability').count() == 0
        assert sid+'-event-details' in page.locator('#'+sid).get_attribute('aria-describedby')
        legend.click()
        assert page.locator('#' + sid + '-availability').count() == 1
        # Multiple unavailable positions, a forecast target and hostile authored
        # text stay literal. The payload remains reusable after final masking.
        page.evaluate('''id=>{const cfg=window._reportData.components[id];window.originalRules=cfg.series_availability;
          cfg.series_availability=[{series:1,unavailable:[0,1,2,3,4],reason:'<img src=x onerror="window.availabilityXss=true"> & literal'}];
          window._fwRenderers.chart(id,cfg);}''', sid)
        multi = state('Static supplied observations and forecast')
        assert 'Supplied forecast: 5 unavailable observations (2026-09-29, 2026-09-30, 2026-10-01; 2 more)' in multi['note']
        assert '<img src=x onerror=' in multi['note']
        assert multi['data'][1] == [None] * 5 and multi['ds'][1]['dash'] == [7, 4]
        assert page.locator('.fw-line-availability img').count() == 0
        assert page.evaluate('window.availabilityXss') is None
        page.evaluate('''id=>{const cfg=window._reportData.components[id];cfg.series_availability=window.originalRules;
          window._fwRenderers.chart(id,cfg);}''', sid)
        # CSV receives full original units/order with nulls and no status columns.
        page.evaluate('window._fwDownloadCsv=(cols,rows,name)=>{window.availabilityCsv={cols,rows,name}}')
        page.locator(f'[data-csv-chart="{sid}"]').click()
        csv = page.evaluate('window.availabilityCsv')
        assert csv['cols'] == ['label', 'Observed', 'Supplied forecast']
        assert csv['rows'][2] == ['2026-10-01', None, 25] and len(csv['rows']) == 5
        # Actual shared filter control restores all-valid geometry and no note.
        page.get_by_role('button', name='Web', exact=True).first.click()
        before.get_by_role('button', name='Web', exact=True).first.click()
        complete = state(title)
        control = state(title, before)
        assert complete['data'] == [[12, 0, 24, 31]] and not complete['note']
        assert complete['paint'] == control['paint'] and complete['area'] == control['area']
        assert complete['id']+'-availability' not in (complete['described'] or '')
        page.get_by_role('button', name='App', exact=True).first.click()
        assert state(title)['data'] == [[17, 0, None, 43]]
        # A chart that started undeclared can adopt/remove rules without keeping
        # its previous draw callback or accumulating subscriptions/theme draws.
        legacy_id = off['id']
        adoption = page.evaluate('''id=>{
          const cfg=window._reportData.components[id];
          cfg.series_availability=[{series:0,required_columns:['orders']}];
          window._fwRenderers.chart(id,cfg);
          const masked=window._chartInstances[id].data.datasets[0].data[2];
          delete cfg.series_availability;window._fwRenderers.chart(id,cfg);
          return {masked,restored:window._chartInstances[id].data.datasets[0].data[2],
            subscriptions:window._fwFilterEngine._subscribers[cfg.dataset_id].filter(s=>s.id===id).length};
        }''', legacy_id)
        assert adoption == {'masked': None, 'restored': 0, 'subscriptions': 1}
        # Empty selections create no fictional unavailable point.
        cid = actual['id']
        page.evaluate('''id=>{
          window._fwFilterEngine.setFilter('line_availability_orders','probe','platform','equals','absent');
        }''', cid)
        assert state(title)['data'] == [[]] and not state(title)['note']
        page.evaluate("window._fwFilterEngine.clearFilter('line_availability_orders','probe')")
        # Bound config removal/re-add restores legacy partial sums, then masking.
        lifecycle = page.evaluate('''id=>{
          const cfg=window._reportData.components[id], saved=cfg.series_availability;
          const counts=[];
          for(let i=0;i<4;i++) {
            delete cfg.series_availability;window._fwRenderers.chart(id,cfg);
            counts.push(window._fwFilterEngine._subscribers[cfg.dataset_id].filter(s=>s.id===id).length);
            if(window._chartInstances[id].data.datasets[0].data[2]!==0) throw Error('legacy sum not restored');
            cfg.series_availability=saved;window._fwRenderers.chart(id,cfg);
            if(window._chartInstances[id].data.datasets[0].data[2]!==null) throw Error('mask not restored');
          }
          return counts;
        }''', cid)
        assert lifecycle == [1] * 4
        # Real drag zoom and the existing bound double-click reset leave notes
        # describing selected supplied data, even outside the viewport.
        current = state(title)
        canvas = page.locator('#'+cid)
        box = canvas.bounding_box()
        a = current['area']
        page.mouse.move(box['x'] + a['left'] + a['width'] * .3, box['y'] + a['bottom'] - 25)
        page.mouse.down()
        page.mouse.move(box['x'] + a['right'] - a['width'] * .2, box['y'] + a['bottom'] - 25, steps=10)
        page.mouse.up()
        assert page.evaluate('id=>window._chartInstances[id].isZoomedOrPanned()', cid)
        assert state(title)['note'] == current['note']
        canvas.dblclick(position={'x': a['left'] + 5, 'y': a['bottom'] - 20})
        assert page.evaluate('id=>window._chartInstances[id].scales.x.min', cid) == 0

        # Native theme switch and renderAll do not multiply DOM or bindings.
        theme = page.locator('#fwThemeSelect')
        theme.select_option('trellum dark')
        page.wait_for_function("document.documentElement.getAttribute('data-theme')==='trellum dark'")
        state('Static supplied observations and forecast')
        assert page.locator('#' + sid + '-availability').count() == 1
        theme.select_option('trellum light')
        page.evaluate('window.renderAll();window.renderAll()')
        state(title)
        state('Static supplied observations and forecast')
        assert page.locator('#' + sid + '-availability').count() == 1
        # Replacing/removing config masks only its final spec, not original data.
        page.evaluate('''id=>{const cfg=window._reportData.components[id];window.savedAvailability=cfg.series_availability;
          delete cfg.series_availability; window._fwRenderers.chart(id,cfg);}''', sid)
        assert not state('Static supplied observations and forecast')['note']
        assert state('Static supplied observations and forecast')['data'][0][2] == 26
        # A cloned canvas can inherit a stale token when its config changes.
        page.evaluate('''id=>{const old=document.getElementById(id);old.setAttribute('aria-describedby',
          old.getAttribute('aria-describedby')+' '+id+'-availability');
          old.replaceWith(old.cloneNode());window._fwRenderers.chart(id,window._reportData.components[id]);}''', sid)
        cleared = state('Static supplied observations and forecast')
        assert sid+'-availability' not in cleared['described']
        assert sid+'-header-context' in cleared['described'] and sid+'-month-context' in cleared['described']
        # All-valid static configuration has exact baseline geometry and paint.
        page.evaluate('''id=>{const cfg=window._reportData.components[id];
          cfg.series_availability=window.savedAvailability.map(r=>({...r,unavailable:[]}));
          window._fwRenderers.chart(id,cfg);}''', sid)
        all_valid_static = state('Static supplied observations and forecast')
        old_static = state('Static supplied observations and forecast', before)
        assert all_valid_static['paint'] == old_static['paint'] and all_valid_static['area'] == old_static['area']
        assert not all_valid_static['note']
        page.evaluate('''id=>{const cfg=window._reportData.components[id];cfg.series_availability=window.savedAvailability;
          window._fwRenderers.chart(id,cfg);}''', sid)
        assert state('Static supplied observations and forecast')['data'][0][2] is None
        # Resize/hide/reveal preserves fixed height and outside-note wrapping.
        page.evaluate('''id=>{const e=document.getElementById(id).parentElement;e.style.display='none';e.style.display='';
          window._chartInstances[id].resize();}''', sid)
        assert state('Static supplied observations and forecast')['height'] == 220
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.locator('#'+sid).scroll_into_view_if_needed()
        page.screenshot(path=str(evidence / f'line-availability-context-{width}.png'))
        section = page.locator('#'+sid).locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
        capture = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
        (evidence / f'line-availability-section-capture-{width}.png').write_bytes(
            base64.b64decode(capture['image']['data_base64']))
        (evidence / f'line-availability-verification-{width}.json').write_text(json.dumps({
            'baseline': (baseline / 'baseline-reference.txt').read_text(), 'viewport_width': width,
            'opt_out_paint_sha256': hashlib.sha256(off['paint'].encode()).hexdigest(),
            'all_valid_bound_paint_sha256': hashlib.sha256(complete['paint'].encode()).hexdigest(),
            'all_valid_static_paint_sha256': hashlib.sha256(all_valid_static['paint'].encode()).hexdigest(),
            'baseline_paint_equality': True, 'fixed_plot_height': actual['height'],
            'explicit_bounds': [actual['min'], actual['max']],
            'masked_orders': actual['data'], 'segments': actual['ds'][0]['segments'],
            'masked_rate': rate['data'], 'selected_data_note': snapshot['note'],
        }, indent=2))
        # The metrics workspace retains its existing leased queue/disposal path.
        page.evaluate('''id=>{
          const cfg=window._reportData.components[id];
          window._fwMetricsWorkspace={};
          window._fwRenderers.chart(id,cfg);window._fwRenderers.chart(id,cfg);
        }''', cid)
        assert page.evaluate('''id=>window._fwFilterEngine._subscribers.line_availability_orders.filter(s=>s.id===id).length''', cid) == 1
        page.evaluate("window._fwFilterEngine.clearAllFilters('line_availability_orders');window._fwFilterEngine.setFilter('line_availability_orders','workspace-probe','platform','equals','Web')")
        page.wait_for_function('id=>window._chartInstances[id].data.datasets[0].data[2]===24', arg=cid)
        assert not state(title)['note']
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        assert page.evaluate('''id=>window._fwFilterEngine._subscribers.line_availability_orders.filter(s=>s.id===id).length''', cid) == 0
        page.evaluate('''id=>{delete window._fwMetricsWorkspace;window._fwWorkspaceDisposed=false;
          delete document.getElementById(id)._fwBarLive;window._fwRenderers.chart(id,window._reportData.components[id]);}''', cid)
        # Replacement disposes only the old canvas and its own context.
        page.evaluate('''id=>{const old=document.getElementById(id), fresh=old.cloneNode();old.replaceWith(fresh);
          window._fwRenderers.chart(id,window._reportData.components[id]);}''', cid)
        assert state(title)['data'] == [[12, 0, 24, 31]]
        # The scoped binding releases its raw subscription/theme listener on detach.
        page.evaluate('id=>document.getElementById(id).remove()', cid)
        page.wait_for_function('id=>!window._fwCategoryChartBindings.has(id)', arg=cid)
        page.evaluate('id=>document.getElementById(id).remove()', legacy_id)
        page.wait_for_function('id=>!window._fwCategoryChartBindings.has(id)', arg=legacy_id)
        # Static charts release their own context when detached too.
        page.evaluate('id=>document.getElementById(id).remove()', sid)
        page.wait_for_function('id=>!document.getElementById(id+"-availability")', arg=sid)
        assert not errors
        browser.close()


def test_line_availability_chromium_desktop(tmp_path):
    _browser(tmp_path, 1280)


@pytest.mark.heavy
def test_line_availability_chromium_phone(tmp_path):
    _browser(tmp_path, 390)
