"""Explicit daily averages: author contract, selected scope, native paint/capture."""

from __future__ import annotations

import base64
import calendar
import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.assets import load_css, load_js
from trellum.components import BarChart, LineChart, RenderContext
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'c61104a119ef9961a73dd085581331f1d2f20eeb'


def declaration(**changes):
    return {
        'metric': 'daily_additive',
        'aggregation': 'unique',
        'start': '2028-01-30',
        'end': '2028-03-02',
        'unit': 'orders',
        **changes,
    }


def frame():
    return pd.DataFrame({'day': ['2028-01-30', '2028-02-01'], 'value': [0, 12], 'other': [7, 19]})


def chart(cls=LineChart, **kwargs):
    return cls(frame(), 'day', 'value', 'Daily orders', **kwargs)


def payload(component):
    ctx = RenderContext(theme=THEME_REGISTRY['trellum light'])
    html = component.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


@pytest.mark.parametrize('cls', [LineChart, BarChart])
def test_keyword_only_subclass_none_copy_and_render_mutation(cls):
    @dataclasses.dataclass
    class Child(cls):
        extra: str = 'child'

    assert (
        inspect.signature(Child).parameters['monthly_average'].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert (
        inspect.signature(Child).parameters['extra'].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    )
    component = chart(Child)
    positional = [
        getattr(component, f.name)
        for f in dataclasses.fields(component)
        if f.init and not f.kw_only
    ]
    assert Child(*positional).extra == 'child'
    assert payload(component) == payload(chart(Child, monthly_average=None))
    authored = declaration()
    component = chart(Child, monthly_average=authored)
    authored['unit'] = 'changed'
    assert component.monthly_average['unit'] == 'orders'
    component.monthly_average['start'] = '2028-02-30'
    with pytest.raises(ValueError, match='monthly_average'):
        payload(component)


@pytest.mark.parametrize(
    'bad',
    [
        True,
        False,
        {},
        [],
        'auto',
        1,
        declaration(extra=True),
        {k: v for k, v in declaration().items() if k != 'metric'},
        declaration(metric='rate'),
        declaration(metric=True),
        declaration(aggregation='avg'),
        declaration(aggregation=False),
        declaration(start='2028-2-01'),
        declaration(start='2028-02-30'),
        declaration(start='0000-01-01'),
        declaration(start='2028-01-30T00:00:00'),
        declaration(start='2028-W01'),
        declaration(start=True),
        declaration(end='2027-01-01'),
        declaration(start='1900-01-01', end='2000-01-01'),
        declaration(unit=''),
        declaration(unit=' '),
        declaration(unit='a' * 61),
        declaration(unit='<b>orders</b>'),
        declaration(unit='a\nb'),
        declaration(unit=True),
    ],
)
@pytest.mark.parametrize('cls', [LineChart, BarChart])
def test_schema_rejects_malformed_at_construction_and_render(cls, bad):
    with pytest.raises(ValueError, match='monthly_average'):
        chart(cls, monthly_average=bad)
    component = chart(cls)
    component.monthly_average = bad
    with pytest.raises(ValueError, match='monthly_average'):
        payload(component)


@pytest.mark.parametrize(
    'cls,extra',
    [
        (LineChart, {'y_format': 'percent'}),
        (LineChart, {'y_format': 'ratio'}),
        (LineChart, {'ratios': [{'numerator': 'value', 'denominator': 'other', 'label': 'Rate'}]}),
        (LineChart, {'forecast_series': [0]}),
        (LineChart, {'stack_by': 'platform'}),
        (LineChart, {'stack_by_options': {'Platform': 'platform'}}),
        (BarChart, {'horizontal': True}),
        (BarChart, {'sort': ''}),
        (BarChart, {'top_n': 0}),
        (BarChart, {'stacked': True}),
        (BarChart, {'net_color': True}),
        (BarChart, {'y_format': 'percent'}),
        (BarChart, {'ratios': [{'numerator': 'value', 'denominator': 'other', 'label': 'Rate'}]}),
    ],
)
def test_applicability_is_only_opt_in(cls, extra):
    payload(chart(cls, **extra))
    with pytest.raises(ValueError, match='monthly_average'):
        chart(cls, monthly_average=declaration(), **extra)


@pytest.mark.parametrize('cls', [LineChart, BarChart])
def test_binding_precedence_static_unique_and_actual_series_count(cls):
    opts = {'y': ['value', 'other']} if cls is LineChart else {'y_cols': ['value', 'other']}

    def make(**extra):
        args = {'df': frame(), 'x': 'day', 'y': 'value', 'title': 'Daily', **opts, **extra}
        return cls(**args)

    with pytest.raises(ValueError, match='monthly_average'):
        make(monthly_average=declaration())
    assert len(payload(make(y_labels=['Only'], monthly_average=declaration()))[1]['datasets']) == 1
    with pytest.raises(ValueError, match='monthly_average'):
        make(y_labels=['Only'], dataset_id='d', monthly_average=declaration())
    with pytest.raises(ValueError, match='monthly_average'):
        chart(cls, static=True, monthly_average=declaration(aggregation='sum_complete'))
    cfg = payload(
        chart(
            cls,
            dataset_id='d',
            static=True,
            monthly_average=declaration(aggregation='sum_complete'),
        )
    )[1]
    assert (
        cfg['dataset_id'] == 'd'
        and 'inputs' not in cfg['monthly_average']
        and 'datasets' not in cfg
    )
    assert 'month_context' not in cfg
    assert payload(chart(cls, monthly_average=declaration(start='1900-01-01', end='1999-12-31')))[1]


@pytest.mark.parametrize('cls', [LineChart, BarChart])
def test_static_raw_input_evidence_preserves_plot_and_dataframe(cls):
    df = pd.DataFrame(
        {
            'day': ['2028-02-01'] * 7,
            'value': pd.Series([0, None, pd.NA, '12', True, float('inf'), -7], dtype=object),
        }
    )
    before = df.copy(deep=True)
    component = cls(df, 'day', 'value', 'Inputs', static=True, monthly_average=declaration())
    cfg = payload(component)[1]
    assert [p['kind'] for p in cfg['monthly_average']['inputs']] == [
        'finite',
        'unknown',
        'unknown',
        'invalid',
        'invalid',
        'invalid',
        'finite',
    ]
    assert (
        cfg['datasets'][0]['data']
        == payload(cls(df, 'day', 'value', 'Inputs', static=True))[1]['datasets'][0]['data']
    )
    pd.testing.assert_frame_equal(df, before)


@pytest.fixture
def runtime_page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<html><body></body></html>')
        for name in ['month_context', 'filter_engine', 'monthly_averages']:
            page.add_script_tag(content=load_js('runtime/' + name + '.js'))
        yield page
        browser.close()


def compute(
    page,
    labels,
    values,
    *,
    rows=None,
    decl=None,
    filters=None,
    parent_filters=None,
    ready=True,
    live=False,
):
    cfg = {'monthly_average': decl or declaration(), 'x': 'day', 'value_cols': ['value']}
    if rows is not None:
        cfg['dataset_id'] = 'daily-test'
    else:
        cfg['monthly_average'] = {
            **cfg['monthly_average'],
            'inputs': [
                {'kind': 'unknown'}
                if v is None
                else {'kind': 'finite', 'value': v}
                if type(v) in (int, float)
                else {'kind': 'invalid'}
                for v in values
            ],
        }
    return page.evaluate(
        '''args=>{
        const {cfg,labels,values,rows,filters,parent_filters,ready,live}=args, e=window._fwFilterEngine;
        e.removeDataset('daily-test'); e.removeDataset('daily-parent');
        if(rows!==null) {
          if(ready) e.init('daily-parent',rows);
          e.addScopedChild('daily-test','daily-parent');
          for(const [source,list] of [['daily-parent',parent_filters],['daily-test',filters]])
            for(const [i,f] of (list||[]).entries()) e.setFilter(source,'range-'+i,f.column||'day',f.mode||'range',f.value);
          if(live) e._liveDatasets['daily-parent']={};
        }
        const spec={labels,datasets:[{label:'Orders',data:values}]};
        return window._fwMonthlyAverageSpec(cfg,rows,spec).monthlyAverage;
    }''',
        {
            'cfg': cfg,
            'labels': labels,
            'values': values,
            'rows': rows,
            'filters': filters,
            'parent_filters': parent_filters,
            'ready': ready,
            'live': live,
        },
    )


def expected_rows(start, end, observations):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    year, month = start.year, start.month
    result = []
    while (year, month) <= (end.year, end.month):
        first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        lo, hi = max(start, first), min(end, last)
        values = [
            v
            for d, v in observations.items()
            if lo <= date.fromisoformat(d) <= hi and v is not None
        ]
        result.append(
            {
                'key': f'{year:04}-{month:02}',
                'start': lo.isoformat(),
                'end': hi.isoformat(),
                'selected': (hi - lo).days + 1,
                'full': lo == first and hi == last,
                'observed': len(values),
                'mean': sum(values) / len(values) if values else None,
            }
        )
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


@pytest.mark.parametrize(
    'start,end,observations',
    [
        (
            '2028-01-30',
            '2028-03-02',
            {
                '2028-01-30': 0,
                '2028-01-31': 12,
                '2028-02-01': 8,
                '2028-02-02': None,
                '2028-02-29': 16,
                '2028-03-02': 20,
            },
        ),
        ('2027-12-31', '2028-03-01', {'2027-12-31': 14, '2028-02-29': None, '2028-03-01': 0}),
        ('1900-02-01', '1900-02-28', {}),
        ('2000-02-01', '2000-02-29', {'2000-02-01': None}),
        ('2028-01-30', '2028-01-31', {'2028-01-30': -12, '2028-01-31': 4}),
    ],
)
def test_python_calendar_oracle(runtime_page, start, end, observations):
    actual = compute(
        runtime_page,
        list(observations),
        list(observations.values()),
        decl=declaration(start=start, end=end),
    )
    assert actual['status'] == 'eligible'
    assert actual['rows'] == expected_rows(start, end, observations)


@pytest.mark.parametrize(
    'labels,values,reason',
    [
        (['2028-02-01', '2028-01-30'], [1, 2], 'ascending'),
        (['2028-02-01', '2028-02-01'], [1, 2], 'unique'),
        (['2028-02-30'], [1], 'valid'),
        (['2028-02'], [1], 'valid'),
        (['2028-02-01T00:00:00'], [1], 'valid'),
        (['2028-01-29'], [1], 'outside'),
        (['2028-02-01'], ['12'], 'finite'),
        (['2028-02-01'], [True], 'finite'),
    ],
)
def test_invalid_static_dates_and_scalars(runtime_page, labels, values, reason):
    result = compute(runtime_page, labels, values)
    assert result['status'] == 'unavailable' and reason in result['reason'] and not result['rows']


@pytest.mark.parametrize(
    'raw,plotted,aggregation,reason,observed,mean',
    [
        ([2, 6], [8], 'unique', 'duplicate', None, None),
        ([2, 6], [8], 'sum_complete', None, 1, 8),
        ([2, None], [2], 'sum_complete', 'incomplete', None, None),
        ([None], [0], 'unique', 'incomplete', None, None),
        ([2, None], [None], 'sum_complete', None, 0, None),
        ([9], [None], 'unique', None, 0, None),
        ([0], [0], 'unique', None, 1, 0),
        ([-8, 3], [-5], 'sum_complete', None, 1, -5),
        (['2'], [2], 'unique', 'incomplete', None, None),
        ([True], [1], 'unique', 'incomplete', None, None),
        ([4], [5], 'unique', 'disagree', None, None),
        ([float('inf')], [1], 'unique', 'incomplete', None, None),
        ([float('nan')], [None], 'unique', None, 0, None),
    ],
)
def test_contributors_and_final_gap_contract(
    runtime_page, raw, plotted, aggregation, reason, observed, mean
):
    result = compute(
        runtime_page,
        ['2028-02-01'],
        plotted,
        rows=[{'day': '2028-02-01', 'value': v} for v in raw],
        decl=declaration(aggregation=aggregation),
    )
    if reason:
        assert result['status'] == 'unavailable' and reason in result['reason']
    else:
        assert result['status'] == 'eligible'
        row = next(r for r in result['rows'] if r['key'] == '2028-02')
        assert (row['observed'], row['mean']) == (observed, mean)


@pytest.mark.parametrize(
    'filters,parent_filters,start,end,reason',
    [
        (
            [{'value': {'min': '2028-02-01', 'max': '2028-02-29'}}],
            None,
            '2028-02-01',
            '2028-02-29',
            None,
        ),
        (
            [{'value': {'min': '2028-02-02'}}],
            [{'value': {'max': '2028-02-28'}}],
            '2028-02-02',
            '2028-02-28',
            None,
        ),
        ([{'value': {'min': '2029-01-01'}}], None, None, None, 'empty intersection'),
        ([{'value': {'min': '2028-02-01T00:00:00'}}], None, None, None, 'canonical'),
        ([{'mode': 'equals', 'value': '2028-02-01'}], None, None, None, 'date-range'),
        (
            [
                {'mode': 'equals', 'value': '__all__'},
                {'mode': 'in', 'value': []},
                {'value': {'min': None, 'max': ''}},
            ],
            None,
            '2028-01-30',
            '2028-03-02',
            None,
        ),
        (
            [{'column': 'platform', 'mode': 'equals', 'value': 'Web'}],
            None,
            '2028-01-30',
            '2028-03-02',
            None,
        ),
        (None, [{'value': {'min': '2028-02-30'}}], None, None, 'canonical'),
        (
            [{'value': {'min': '2028-02-12', 'max': '2028-02-10'}}],
            None,
            None,
            None,
            'empty intersection',
        ),
    ],
)
def test_inherited_selected_interval(runtime_page, filters, parent_filters, start, end, reason):
    result = compute(runtime_page, [], [], rows=[], filters=filters, parent_filters=parent_filters)
    if reason:
        assert result['status'] == 'unavailable' and reason in result['reason']
    else:
        assert result['rows'] == expected_rows(start, end, {})


@pytest.mark.parametrize(
    'ready,live,reason', [(False, False, 'not available yet'), (True, True, 'Live-query')]
)
def test_loading_and_live_ancestor(runtime_page, ready, live, reason):
    result = compute(runtime_page, [], [], rows=[], ready=ready, live=live)
    assert result['status'] == 'unavailable' and reason in result['reason'] and not result['rows']


def test_raw_dates_and_overflow_and_safe_mean(runtime_page):
    for day, reason in [('2028-02-30', 'invalid daily'), ('2028-01-29', 'outside')]:
        result = compute(runtime_page, ['2028-02-01'], [4], rows=[{'day': day, 'value': 4}])
        assert reason in result['reason']
    result = compute(
        runtime_page,
        ['2028-02-01'],
        [1e308],
        rows=[{'day': '2028-02-01', 'value': 1e308}] * 2,
        decl=declaration(aggregation='sum_complete'),
    )
    assert 'incomplete' in result['reason']
    result = compute(runtime_page, ['2028-02-01', '2028-02-02'], [1e308, 1e308])
    assert result['rows'][1]['mean'] == 1e308
    result = runtime_page.evaluate('''()=>window._fwMonthlyAverageSpec({monthly_average:{start:'2028-02-01',end:'2028-02-02',unit:'orders',inputs:[{kind:'invalid'}]}},null,
        {labels:['2028-02-01'],datasets:[{data:[Infinity]}]}).monthlyAverage''')
    assert result['status'] == 'unavailable'


def test_safe_signed_cancellation_and_blank_static_caption(runtime_page):
    result = compute(runtime_page, ['2028-02-01', '2028-02-02', '2028-02-03'], [1e308, 1, -1e308])
    assert result['rows'][1]['mean'] == pytest.approx(1 / 3)
    result = runtime_page.evaluate("""()=>window._fwMonthlyAverageSpec({monthly_average:{start:'2028-02-01',end:'2028-02-01',unit:'orders',inputs:[{kind:'finite',value:2}]}},null,
        {labels:['2028-02-01'],datasets:[{label:'',data:[2]}]}).monthlyAverage""")
    assert result['metric'] == 'Series 1'


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _historical(path):
    try:
        return subprocess.check_output(
            ['git', 'show', f'{BASELINE}:{path}'], cwd=ROOT, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return None


def _build(output, baseline=False):
    output.mkdir(parents=True, exist_ok=True)
    module = _module(
        ROOT / 'trellum/demo/reports/component-showcase/examples_monthly_averages.py',
        'monthly_examples',
    )
    historical = baseline and _historical('trellum/components/charts/line_area.py') is not None
    if baseline:
        (output / 'baseline-reference.txt').write_text(
            'Actual dependency baseline ' + BASELINE
            if historical
            else 'Current opt-out reference (history unavailable)'
        )
        for file, name in [('line_area', 'LineChart'), ('bar', 'BarChart')]:
            if historical:
                path = output / f'baseline_{file}.py'
                path.write_bytes(_historical(f'trellum/components/charts/{file}.py'))
                cls = getattr(_module(path, 'monthly_baseline_' + file), name)
            else:
                cls = LineChart if name == 'LineChart' else BarChart

            def factory(*args, _cls=cls, **kwargs):
                kwargs.pop('monthly_average', None)
                return _cls(*args, **kwargs)

            setattr(module, name, factory)
    ctx = ReportContext(
        config={
            'name': 'Synthetic observed-day monthly averages',
            'studio': 'shared',
            'category': 'Test',
            'description': 'Explicit daily quantities and observed-day monthly averages with independent platform/channel selection.',
            'theme': 'trellum light',
            'annotations': {
                'events': [
                    {'date': '2028-02-01', 'type': 'release', 'label': 'Synthetic reporting event'}
                ]
            },
        },
        slug='monthly-averages',
        output_dir=str(output),
        theme=THEME_REGISTRY['trellum light'],
    )
    module.add_sections(ctx)
    # Monthly guide comparisons isolate the annotation mode from the later
    # adaptive-event default, which has separate interaction/paint coverage.
    from trellum.validation.walk import _all_components
    for component, _ in _all_components(ctx):
        if type(component).__name__ == 'LineChart':
            component.event_labels = 'grouped'
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary['warn'] == result.summary['fail'] == 0, [
        (c.id, c.message) for c in result.checks if c.level in ('warn', 'fail')
    ]
    result.write(str(output))
    from trellum.runner.events import local_annotation_events

    render_report(
        ctx, str(output), data={'_events': local_annotation_events(ctx.config)}, auto_refresh=False
    )
    copy_vendor_tree(output)
    if historical:
        html = (output / 'index.html').read_text()
        for path, current in [
            ('trellum/static/js/components/chart_base.js', load_js('components/chart_base.js')),
            ('trellum/static/css/components/chart_base.css', load_css('components/chart_base.css')),
            ('trellum/static/js/runtime/month_context.js', load_js('runtime/month_context.js')),
            ('trellum/static/js/runtime/export.js', load_js('runtime/export.js')),
        ]:
            assert current in html
            html = html.replace(current, _historical(path).decode())
        html = html.replace(load_js('runtime/monthly_averages.js'), '')
        (output / 'index.html').write_text(html)
    return ctx


def test_genuine_module_full_validation(tmp_path):
    assert len(_build(tmp_path).sections) == 4


def _open(page, output):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip('/')
        if path.is_file():
            route.fulfill(
                body=path.read_bytes(),
                content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream',
            )
        else:
            route.fulfill(status=404, body='Missing fixture')

    page.route('**/*', serve)
    page.goto('http://monthly.test/index.html', wait_until='networkidle')
    page.evaluate('document.fonts.ready')
    for cid in page.locator('canvas[data-fw-kind]').evaluate_all('els=>els.map(e=>e.id)'):
        page.locator('#' + cid).scroll_into_view_if_needed()
        page.wait_for_function('id=>!!window._chartInstances[id]', arg=cid)


STATE = '''id=>{const c=window._chartInstances[id];return JSON.parse(JSON.stringify({height:c.height,width:c.width,
 area:{left:c.chartArea.left,right:c.chartArea.right,top:c.chartArea.top,bottom:c.chartArea.bottom},
 labels:c.data.labels,datasets:c.data.datasets.map((d,i)=>({label:d.label,data:d.data,color:d.borderColor,width:d.borderWidth,
 elements:c.getDatasetMeta(i).data.map(p=>({x:p.x,y:p.y,width:p.width,height:p.height,base:p.base,radius:p.options.radius}))})),
 scales:Object.fromEntries(Object.entries(c.scales).map(([k,s])=>[k,{min:s.min,max:s.max,ticks:s.ticks.map(t=>t.label)}]))}));}'''


def _browser(tmp_path, width):
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright

    current, baseline = tmp_path / 'current', tmp_path / 'baseline'
    _build(current)
    _build(baseline, baseline=True)
    evidence = Path(os.environ.get('TRELLUM_MONTHLY_EVIDENCE', str(tmp_path / 'evidence')))
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
        old = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        _open(page, current)
        _open(old, baseline)
        ids = page.locator('canvas[data-fw-kind]').evaluate_all('els=>els.map(e=>e.id)')
        for cid in ids:
            page.locator('#' + cid).scroll_into_view_if_needed()
            old.locator('#' + cid).scroll_into_view_if_needed()
            assert page.evaluate(STATE, cid) == old.evaluate(STATE, cid), (
                cid,
                page.evaluate(STATE, cid),
                old.evaluate(STATE, cid),
            )
            if not page.evaluate('id=>!!window._reportData.components[id].monthly_average', cid):
                assert page.locator('#' + cid + '-monthly-average').count() == 0
                assert page.locator('#' + cid).evaluate('el=>el.toDataURL()') == old.locator(
                    '#' + cid
                ).evaluate('el=>el.toDataURL()')
        opted = page.evaluate(
            "()=>Object.entries(window._reportData.components).filter(([id,c])=>c.monthly_average).map(([id])=>id)"
        )
        assert len(opted) == 5
        sid, lid, static_bar, bid, sparse = opted

        def state(cid):
            page.locator('#' + cid).scroll_into_view_if_needed()
            page.wait_for_function('id=>!!window._chartInstances[id]?._fwMonthlyAverage', arg=cid)
            return page.evaluate('id=>window._chartInstances[id]._fwMonthlyAverage', cid)

        assert [(r['observed'], r['selected'], r['mean']) for r in state(sid)['rows']] == [
            (2, 2, 6),
            (2, 29, 12),
            (1, 2, 20),
        ]
        assert [r['mean'] for r in state(lid)['rows']] == [10, 59]
        assert [r['mean'] for r in state(bid)['rows']] == [8, 21]
        assert [(r['observed'], r['selected'], r['mean']) for r in state(sparse)['rows']] == [
            (1, 1, 14),
            (0, 31, None),
            (0, 29, None),
            (1, 1, 0),
        ]
        for view, name in [(old, 'baseline'), (page, 'enabled')]:
            view.evaluate('window.scrollTo(0,0)')
            full = evidence / f'averages-{width}-{name}-initial.png'
            view.screenshot(path=str(full), full_page=True)
            comparison = (
                view.locator('#' + sid)
                .locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
                .bounding_box()
            )
            Image.open(full).crop(
                (
                    comparison['x'],
                    comparison['y'],
                    comparison['x'] + comparison['width'],
                    comparison['y'] + comparison['height'],
                )
            ).save(evidence / f'averages-{width}-{name}-comparison.png')
        # Native guide paint differs from the actual same-chart baseline.
        page.locator('#' + sid).screenshot(path=str(evidence / f'averages-{width}-plot.png'))
        old.locator('#' + sid).screenshot(
            path=str(evidence / f'averages-{width}-baseline-plot.png')
        )
        difference = ImageChops.difference(
            Image.open(evidence / f'averages-{width}-plot.png'),
            Image.open(evidence / f'averages-{width}-baseline-plot.png'),
        )
        assert difference.getbbox()
        # Paint differences occur along the guide itself, away from its label.
        guide = next(g for g in state(sid)['guides'] if g['key'] == '2028-02')
        strip = difference.crop(
            (
                int(guide['left'] + 25),
                int(guide['y'] - 1),
                int(guide['right'] - 2),
                int(guide['y'] + 2),
            )
        )
        assert (
            sum(
                any(strip.getpixel((x, y))[:3])
                for x in range(strip.width)
                for y in range(strip.height)
            )
            > 5
        )
        assert guide['label'] == '12'

        for cid in opted:
            s = state(cid)
            assert s['status'] == 'eligible'
            geometry = page.evaluate(STATE, cid)['area']
            for guide in s['guides']:
                assert geometry['left'] <= guide['left'] < guide['right'] <= geometry['right']
                assert geometry['top'] <= guide['y'] <= geometry['bottom']
        # Pointer-controlled category selection changes observations, not base dates.
        section = page.locator('#' + lid).locator(
            'xpath=ancestor::*[contains(@class,"fw-section")][1]'
        )
        section.get_by_role('button', name='Web', exact=True).click()
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.rows[1].observed===2', arg=lid
        )
        assert [r['mean'] for r in state(lid)['rows']] == [5, 35]
        assert [r['selected'] for r in state(lid)['rows']] == [2, 2]
        # Daily range uses the real native inputs and filter handler.
        section.locator('.fw-date-trigger').click()
        dates = section.locator('input[type=date]')
        dates.nth(0).fill('2028-02-01')
        dates.nth(0).dispatch_event('change')
        dates.nth(1).fill('2028-02-02')
        dates.nth(1).dispatch_event('change')
        page.keyboard.press('Escape')
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.rows.length===1', arg=lid
        )
        assert state(lid)['rows'][0]['mean'] == 35
        page.evaluate(
            "fw.filterEngine.setFilter('monthly_average_platform_orders','unsupported','day','equals','2028-02-01')"
        )
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.status==="unavailable"', arg=lid
        )
        assert 'date-range' in state(lid)['reason']
        page.evaluate(
            "fw.filterEngine.clearFilter('monthly_average_platform_orders','unsupported')"
        )
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.status==="eligible"', arg=lid
        )
        page.evaluate(
            "fw.filterEngine.setFilter('monthly_average_platform_orders','no-results','platform','equals','Absent')"
        )
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.rows.every(r=>r.observed===0)',
            arg=lid,
        )
        assert state(lid)['rows'][0]['selected'] == 2 and state(lid)['rows'][0]['mean'] is None
        assert not state(lid)['guides']
        assert (
            '0 / 2 days; 2 missing' in page.locator('#' + lid + '-monthly-average').text_content()
        )
        page.locator('#' + lid + '-monthly-average').screenshot(
            path=str(evidence / f'averages-{width}-no-observations.png')
        )
        page.evaluate("fw.filterEngine.clearFilter('monthly_average_platform_orders','no-results')")
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.rows[0].observed===2', arg=lid
        )
        page.evaluate(
            "fw.filterEngine.setFilter('monthly_average_bar_orders','invalid-window','day','equals','2028-02-28')"
        )
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.status==="unavailable"', arg=bid
        )
        page.locator('#' + bid + '-monthly-average').screenshot(
            path=str(evidence / f'averages-{width}-unsupported.png')
        )
        page.evaluate("fw.filterEngine.clearFilter('monthly_average_bar_orders','invalid-window')")
        page.wait_for_function(
            'id=>window._chartInstances[id]._fwMonthlyAverage.status==="eligible"', arg=bid
        )
        # Final state/extra-input availability masks exclude finite metrics too.
        page.evaluate(
            '''id=>{const cfg=window._reportData.components[id];window.savedRules=cfg.series_availability;
          cfg.series_availability=[{series:0,required_columns:['orders','verification'],state_column:'verified'}];
          window._fwRenderers.chart(id,cfg);}''',
            lid,
        )
        assert all(r['observed'] == 0 for r in state(lid)['rows'])
        assert not state(lid)['guides']
        assert page.locator('#' + lid + '-availability').count() == 1
        assert (
            page.locator('#' + lid + '-availability').evaluate('el=>el.nextElementSibling.id')
            == lid + '-monthly-average'
        )
        page.evaluate(
            'id=>{let cfg=window._reportData.components[id];cfg.series_availability=window.savedRules;window._fwRenderers.chart(id,cfg)}',
            lid,
        )
        assert state(lid)['rows'][0]['mean'] == 35
        # CSV remains raw daily values and labels, with no synthetic monthly rows.
        downloads = {}
        for view, label in [(page, 'enabled'), (old, 'baseline')]:
            with view.expect_download() as download:
                view.locator('[data-csv-chart="' + sid + '"]').click()
            downloads[label] = download.value.path().read_text()
        assert (
            downloads['enabled'] == downloads['baseline']
            and 'monthly' not in downloads['enabled'].lower()
        )
        # Zoom clips guide paint without recomputing the selected coverage.
        before = state(sid)['rows']
        page.evaluate(
            'id=>{let c=window._chartInstances[id];c.zoomScale("x",{min:2,max:4},"none");c.update("none")}',
            sid,
        )
        assert state(sid)['rows'] == before
        assert all(g['key'] == '2028-02' for g in state(sid)['guides'])
        page.locator('#' + sid).dblclick()
        page.evaluate('id=>window._chartInstances[id].resetZoom("none")', sid)
        # Native theme switch, redraw, legend visibility and aria ownership.
        page.locator('#fwThemeSelect').select_option('trellum dark')
        page.wait_for_function(
            "document.documentElement.getAttribute('data-theme')==='trellum dark'"
        )
        assert page.locator('#fwThemeSelect').input_value() == 'trellum dark'
        page.locator('#' + sid).scroll_into_view_if_needed()
        page.mouse.move(0, 0)
        page.evaluate('window.scrollTo(0,0)')
        dark_full = evidence / f'averages-{width}-dark-full.png'
        page.screenshot(path=str(dark_full), full_page=True)
        dark_box = (
            page.locator('#' + sid)
            .locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
            .bounding_box()
        )
        Image.open(dark_full).crop(
            (
                dark_box['x'],
                dark_box['y'],
                dark_box['x'] + dark_box['width'],
                dark_box['y'] + dark_box['height'],
            )
        ).save(evidence / f'averages-{width}-dark.png')

        page.evaluate(
            'id=>{let c=window._chartInstances[id];c.setDatasetVisibility(0,false);c.update("none")}',
            sid,
        )
        assert page.locator('#' + sid + '-monthly-average').count() == 0
        page.evaluate(
            'id=>{let c=window._chartInstances[id];c.setDatasetVisibility(0,true);c.update("none")}',
            sid,
        )
        assert page.locator('#' + sid + '-monthly-average').count() == 1
        page.locator('#fwThemeSelect').select_option('trellum light')
        # Coverage follows month/event/availability; changing options keeps event focus.
        desc = page.locator('#' + sid).get_attribute('aria-describedby')
        assert sid + '-monthly-average' in desc and sid + '-month-context' in desc
        event = page.locator('#' + sid + '-event-details summary')
        if event.count():
            event.focus()
            page.evaluate(
                'id=>window._fwRenderers.chart(id,window._reportData.components[id])', sid
            )
            assert event.evaluate('el=>document.activeElement===el')
        # Default-off -> on adoption, repeated renderAll, replacement and removal.
        for cid in (lid, bid):
            page.evaluate(
                '''id=>{const cfg=window._reportData.components[id];window.savedMonthly=cfg.monthly_average;
              delete cfg.monthly_average;window._fwRenderers.chart(id,cfg);window.renderAll();cfg.monthly_average=window.savedMonthly;
              window._fwRenderers.chart(id,cfg);window.renderAll();window.renderAll();}''',
                cid,
            )
            page.locator('#' + cid).scroll_into_view_if_needed()
            page.wait_for_function('id=>!!window._chartInstances[id]?._fwMonthlyAverage', arg=cid)
            assert page.locator('#' + cid + '-monthly-average').count() == 1
            ds = page.evaluate('id=>window._reportData.components[id].dataset_id', cid)
            assert (
                page.evaluate(
                    'a=>window._fwFilterEngine._subscribers[a.ds].filter(s=>s.id===a.cid).length',
                    {'ds': ds, 'cid': cid},
                )
                == 1
            )
        # A newly attached ordinary bound bar starts scoped even with no option.
        page.evaluate(
            '''id=>{let cfg=window._reportData.components[id],old=document.getElementById(id);
          window.freshMonthly=cfg.monthly_average;delete cfg.monthly_average;
          old.replaceWith(old.cloneNode());window._fwRenderers.chart(id,cfg);}''',
            bid,
        )
        assert page.evaluate('id=>window._fwCategoryChartBindings.has(id)', bid)
        assert page.locator('#' + bid + '-monthly-average').count() == 0
        page.evaluate(
            'id=>{let cfg=window._reportData.components[id];cfg.monthly_average=window.freshMonthly;window._fwRenderers.chart(id,cfg)}',
            bid,
        )
        assert state(bid)['status'] == 'eligible'
        # Hiding/revealing the owning region preserves the fixed plot height.
        before_height = page.evaluate(STATE, bid)['height']
        page.locator('#' + bid).locator(
            'xpath=ancestor::*[contains(@class,"fw-section")][1]'
        ).evaluate('el=>{el.style.display="none"}')
        page.locator('#' + bid).locator(
            'xpath=ancestor::*[contains(@class,"fw-section")][1]'
        ).evaluate('el=>{el.style.display=""}')
        page.evaluate('id=>window._chartInstances[id].resize()', bid)
        assert page.evaluate(STATE, bid)['height'] == before_height
        page.evaluate(
            '''id=>{let cfg=window._reportData.components[id];window.savedMonthly=cfg.monthly_average;
          delete cfg.monthly_average;window._fwRenderers.chart(id,cfg);let c=document.getElementById(id);c.replaceWith(c.cloneNode());
          window._fwRenderers.chart(id,cfg);}''',
            sid,
        )
        assert page.locator('#' + sid + '-monthly-average').count() == 0
        assert sid + '-monthly-average' not in (
            page.locator('#' + sid).get_attribute('aria-describedby') or ''
        )
        page.evaluate(
            'id=>{let cfg=window._reportData.components[id];cfg.monthly_average=window.savedMonthly;window._fwRenderers.chart(id,cfg)}',
            sid,
        )
        page.set_viewport_size({'width': width, 'height': 840})
        page.wait_for_timeout(250)
        assert page.locator('#' + sid + '-monthly-average').count() == 1
        # Full native section capture, never a canvas surrogate.
        section = page.locator('#' + sid).locator(
            'xpath=ancestor::*[contains(@class,"fw-section")][1]'
        )
        section.scroll_into_view_if_needed()
        capture = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
        capture_path = evidence / f'averages-{width}-section-capture.png'
        capture_path.write_bytes(base64.b64decode(capture['image']['data_base64']))
        box = section.bounding_box()
        assert Image.open(capture_path).height >= int(box['height'] * 2) - 2
        for selector in ['#' + sid, '#' + sid + ' canvas']:
            target = (
                page.locator(selector) if selector.endswith('canvas') else page.locator(selector)
            )
            if not target.count():
                target = page.locator('#' + sid)
            error = target.evaluate(
                'el=>fw.captureElementSnapshotForAnalysis(el).then(()=>"unexpected",e=>e.message)'
            )
            assert error == 'Capture the report section to include monthly-average coverage.'
        plot = page.locator('#' + sid).locator('..')
        assert (
            plot.evaluate(
                'el=>fw.captureElementSnapshotForAnalysis(el).then(()=>"unexpected",e=>e.message)'
            )
            == 'Capture the report section to include monthly-average coverage.'
        )
        # An ordinary native plot remains capturable.
        ordinary = page.locator('#' + ids[0]).locator('..')
        assert (
            ordinary.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')['format']
            == 'trellum-analysis-capture'
        )
        # Guard the declaration before lazy initialization, and recheck after
        # final rendering if opt-in arrives during an asynchronous capture.
        late = page.evaluate(
            '''id=>{const cfg=window._reportData.components[id],saved=cfg.monthly_average;
          window._chartInstances[id].destroy();delete window._chartInstances[id];
          return fw.captureElementSnapshotForAnalysis(document.getElementById(id)).then(()=>"unexpected",e=>e.message);
        }''',
            sid,
        )
        assert late == 'Capture the report section to include monthly-average coverage.'
        late = page.evaluate(
            '''id=>{const cfg=window._reportData.components[id],saved=cfg.monthly_average;
          delete cfg.monthly_average;window._fwRenderers.chart(id,cfg);
          const pending=fw.captureElementSnapshotForAnalysis(document.getElementById(id).parentElement);
          cfg.monthly_average=saved;return pending.then(()=>"unexpected",e=>e.message);
        }''',
            sid,
        )
        assert late == 'Capture the report section to include monthly-average coverage.'
        page.evaluate('id=>window._fwRenderers.chart(id,window._reportData.components[id])', sid)
        assert state(sid)['status'] == 'eligible'
        # Isolated opted-in plots are excluded from keyboard analysis targets.
        page.evaluate('()=>{fw.captureForAnalysis();}')
        assert 'fw-analysis-selectable' not in (plot.get_attribute('class') or '')
        assert 'fw-analysis-selectable' in section.get_attribute('class')
        page.keyboard.press('Escape')
        for view, name in [(old, 'baseline'), (page, 'enabled')]:
            view.evaluate('window.scrollTo(0,0)')
            view.screenshot(path=str(evidence / f'averages-{width}-{name}.png'), full_page=True)
        assert page.locator('.fw-monthly-average').evaluate_all(
            'els=>els.every(e=>e.scrollHeight<=e.clientHeight+1)'
        )
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors, errors
        (evidence / f'averages-{width}-verified.json').write_text(
            json.dumps(
                {
                    'baseline': BASELINE,
                    'width': width,
                    'states': {cid: state(cid) for cid in opted},
                    'csv': downloads['enabled'],
                    'capture': str(capture_path),
                },
                indent=2,
            )
        )
        browser.close()


def test_monthly_averages_chromium_desktop(tmp_path):
    _browser(tmp_path, 1365)


@pytest.mark.heavy
def test_monthly_averages_chromium_phone(tmp_path):
    _browser(tmp_path, 390)
