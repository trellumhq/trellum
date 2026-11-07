"""Exact trend declarations and independent numerical/population oracles."""
from __future__ import annotations

import importlib.util
import inspect
import json
import random
import subprocess
from decimal import Decimal, localcontext
from pathlib import Path

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import LineChart, RenderContext, ScatterChart
from trellum.components.charts._trends import trend_config
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
TREND = {"method": "linear", "series": "each"}


def js(expression):
    source = "global.window=global;" + load_js("components/chart_trends.js")
    source += '\nprocess.stdout.write(JSON.stringify((' + expression + ')));'
    return json.loads(subprocess.check_output(['node', '-e', source], text=True))


def frame():
    return pd.DataFrame({'day': ['2026-10-01', '2026-10-02', '2026-10-03'],
                         'x': [0, 1, 2], 'y': [0, 2, 4], 'group': ['A'] * 3})


@pytest.mark.parametrize('cls,args', [(LineChart, ('day', 'y')), (ScatterChart, ('x', 'y'))])
def test_keyword_only_opt_out_and_defensive_copy(cls, args):
    assert inspect.signature(cls).parameters['trend_line'].kind == inspect.Parameter.KEYWORD_ONLY
    chart = cls(frame(), *args)
    assert trend_config(chart) == {}
    ctx = RenderContext(DEFAULT_THEME)
    chart.render_html(ctx)
    assert all('trend_line' not in c for c in ctx.component_data.values())
    declaration = {'method': 'linear', 'series': [{'column': 'y'}]}
    chart = cls(frame(), *args, trend_line=declaration)
    resolved = trend_config(chart)['trend_line']
    resolved['series'][0]['column'] = 'changed'
    assert declaration['series'] == [{'column': 'y'}]


@pytest.mark.parametrize('value', [False, True, {}, [], 'linear', {'method': 'quadratic', 'series': 'each'},
    {'method': 'linear'}, {**TREND, 'weights': 'size'}, {**TREND, 'details': 1},
    {**TREND, 'series': []}, {**TREND, 'series': [0]}, {**TREND, 'series': ['y']},
    {**TREND, 'series': [{'column': 'absent'}]},
    {**TREND, 'series': [{'column': 'y'}, {'column': 'y'}]},
    {**TREND, 'series': [{'column': 'y', 'group': 'A'}]},
])
@pytest.mark.parametrize('cls,args', [(LineChart, ('day', 'y')), (ScatterChart, ('x', 'y'))])
def test_all_options_validated_at_construction_and_render(cls, args, value):
    with pytest.raises(ValueError, match='trend_line'):
        cls(frame(), *args, trend_line=value)
    chart = cls(frame(), *args)
    chart.trend_line = value
    with pytest.raises(ValueError, match='trend_line'):
        chart.render_html(RenderContext(DEFAULT_THEME))


def oracle(pairs):
    with localcontext() as ctx:
        ctx.prec = 70
        x = [Decimal.from_float(float(p['x'])) for p in pairs]
        y = [Decimal.from_float(float(p['y'])) for p in pairs]
        mx, my = sum(x) / len(x), sum(y) / len(y)
        xx = sum((v - mx) ** 2 for v in x)
        xy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        slope = xy / xx
        yy = sum((v - my) ** 2 for v in y)
        residual = sum((b - my - slope * (a - mx)) ** 2 for a, b in zip(x, y))
        return {'slope': float(slope), 'valueAtOrigin': float(my + slope * (min(x) - mx)),
                'end': float(my + slope * (max(x) - mx)), 'r2': None if not yy else float(1 - residual / yy)}


@pytest.mark.parametrize('pairs', [
    [{'x': x, 'y': y} for x, y in [(0, 0), (1, 2), (2, 4)]],
    [{'x': x, 'y': y} for x, y in [(1, 5), (1, 3), (2, 1), (4, -3)]],
    [{'x': x + 1700000000000, 'y': y} for x, y in [(0, 9), (1, 7), (2, 4), (5, -1)]],
    [{'x': x, 'y': 1e120 + y * 1e110} for x, y in [(0, 3), (1, -7), (2, 4), (5, 1)]],
    [{'x': x, 'y': -3} for x in [1, 2, 3]],
])
def test_fit_matches_high_precision_independent_oracle(pairs):
    actual = js('_fwTrends.fit(' + json.dumps(pairs) + ', 2)')
    assert actual['reason'] == ''
    assert actual['n'] == len(pairs) and actual['excluded'] == 2
    for k, expected in oracle(pairs).items():
        assert actual[k] == (None if expected is None else pytest.approx(expected, rel=1e-11, abs=1e-12))


def test_many_noisy_fits_match_independent_oracle():
    rng = random.Random(731)
    cases = [[{'x': 10**12 + rng.randrange(5000), 'y': rng.uniform(-800, 1000)} for _ in range(50)] for _ in range(20)]
    actual = js(json.dumps(cases) + '.map(p=>_fwTrends.fit(p,0))')
    for pairs, result in zip(cases, actual):
        for k, expected in oracle(pairs).items():
            assert result[k] == pytest.approx(expected, rel=1e-10, abs=1e-10)


@pytest.mark.parametrize('expression,reason', [
    ('[{x:0,y:1},{x:1,y:2}]', 'three'),
    ('[{x:1,y:1},{x:1,y:2},{x:1,y:3}]', 'distinct'),
    ('[{x:0,y:null},{x:1,y:2},{x:2,y:3}]', 'finite numeric'),
    ('[{x:0,y:Infinity},{x:1,y:2},{x:2,y:3}]', 'finite numeric'),
    ('[{x:-1e308,y:0},{x:0,y:1},{x:1e308,y:2}]', 'range'),
    ('[{x:0,y:-1e308},{x:1,y:0},{x:2,y:1e308}]', 'range'),
])
def test_explicit_numerical_refusals(expression, reason):
    assert reason in js('_fwTrends.fit(' + expression + ',0)')['reason']


def test_population_excludes_raw_missing_before_zero_fill_ratio_and_forecast():
    actual = js('''(()=>{
        window._reportData={components:{}};window._fwFilterEngine={_columnar:{d:{}},_scopes:{},_liveDatasets:{}};
        const cfg={dataset_id:'d',x:'day',value_cols:['v','den'],trend_line:{...%s,columns:['v']}};
        const labels=Array.from({length:6},(_,i)=>'2026-10-0'+(i+1));
        const rows=labels.flatMap((day,i)=>[{day,v:i,den:i===4?0:2},{day,v:i===2?null:i,den:i===4?0:2}]);
        const spec={labels,datasets:[{label:'v',data:[0,2,2,6,8,10]}]};
        const sum=_fwTrends.line(cfg,rows,spec).trends.series[0].fit;
        cfg.ratios=[{numerator:'v',denominator:'den',label:'R'}];
        const ratio=_fwTrends.line(cfg,rows,{labels,datasets:[{label:'R',data:[0,.5,.5,1.5,null,2.5]}]}).trends.series[0].fit;
        cfg.forecast_series=[0];const forecast=_fwTrends.line(cfg,rows,spec).trends.series[0].fit;
        return {sum,ratio,forecast,source:spec.datasets[0].data};})()''' % json.dumps(TREND))
    assert actual['sum']['n'] == 5 and actual['sum']['excluded'] == 1
    assert actual['sum']['slope'] == pytest.approx(2)
    assert actual['ratio']['n'] == 4 and actual['ratio']['excluded'] == 2
    assert actual['ratio']['slope'] == pytest.approx(.5)
    assert actual['forecast']['n'] == 0 and 'forecast' in actual['forecast']['reason']
    assert actual['source'] == [0, 2, 2, 6, 8, 10]


def test_daily_grid_and_runtime_unsupported_population_guards():
    result = js('''(()=>{
        window._reportData={components:{},_chunk_manifests:{}};
        window._fwFilterEngine={_columnar:{d:{}},_scopes:{child:{parent:'d'}},_liveDatasets:{}};
        const cfg={dataset_id:'child',x:'day',trend_line:{...%s,columns:['v']}};
        const spec={labels:['2026-10-01','2026-10-03','2026-10-04'],datasets:[{label:'v',data:[1,2,3]}]};
        const irregular=_fwTrends.line(cfg,[],spec).trends.reason;
        _reportData._chunk_manifests.d={};const chunk=_fwTrends.line(cfg,[],spec).trends.reason;
        delete _reportData._chunk_manifests.d;_fwFilterEngine._liveDatasets.d={};
        const live=_fwTrends.line(cfg,[],spec).trends.reason;
        return {irregular,chunk,live,invalid:_fwTrends.day('2026-02-30')};})()''' % json.dumps(TREND))
    assert 'regular daily' in result['irregular'] and 'chunked' in result['chunk'] and 'live-connected' in result['live']
    assert result['invalid'] is None


def example_context(output):
    path = ROOT / 'trellum/demo/reports/component-showcase/examples_trends.py'
    spec = importlib.util.spec_from_file_location('examples_trends', path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    ctx = ReportContext(config={'name': 'Native linear trends', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic daily observations, ratios and numeric scatter pairs by platform; optional unweighted observed linear trends with complete snapshot filters.'},
        slug='native-trends', output_dir=str(output))
    example.add_sections(ctx)
    return ctx


def test_standalone_native_example_has_no_new_warn_fail_or_suppression(tmp_path):
    ctx = example_context(tmp_path)
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert [(c.id, c.message) for c in result.checks if c.level in ('warn', 'fail')] == []
    assert not ctx.config.get('validation', {}).get('suppress')
    (tmp_path / 'diagnostics.json').write_text(json.dumps({'validation': result.to_dict(), 'details': details}, default=str))


def test_timestamp_contract_checked_before_static_date_truncation():
    df = frame()
    df['day'] = pd.date_range('2026-10-01T12:00:00+02:00', periods=3)
    cfg = trend_config(LineChart(df, 'day', 'y', trend_line=TREND))['trend_line']
    assert 'nonmidnight' in cfg['unavailable']


def test_exact_group_identities_do_not_retarget_after_reordering_or_filtering():
    result = js('''(()=>{
        const cfg={trend_line:{method:'linear',series:[{column:'y',group:'A'}],columns:['y'],group_column:'g'}};
        const a={label:'A',data:[{x:0,y:0},{x:1,y:2},{x:2,y:4}]};
        const b={label:'B',data:[{x:0,y:4},{x:1,y:2},{x:2,y:0}]};
        const get=datasets=>_fwTrends.result(cfg,null,{datasets},true).series;
        return {first:get([a,b]),reordered:get([b,a]),absent:get([b])};})()''')
    assert result['first'][0]['fit']['slope'] == result['reordered'][0]['fit']['slope'] == 2
    assert result['first'][0]['index'] == 0 and result['reordered'][0]['index'] == 1
    assert 'absent' in result['absent'][0]['fit']['reason']


def test_bound_series_availability_gaps_and_nonnumeric_scatter_pairs():
    result = js('''(()=>{
        const cfg={trend_line:{method:'linear',series:'each',columns:['y']}};
        const scatter=_fwTrends.result(cfg,null,{datasets:[{label:'Y',data:[
            {x:0,y:0},{x:1,y:null},{x:2,y:'3'},{x:3,y:true},{x:4,y:Infinity},{x:5,y:10},{x:5,y:10}]}]},true);
        const line=_fwTrends.line(cfg,null,{labels:['2026-01-01','2026-01-02','2026-01-03','2026-01-04'],
            datasets:[{label:'Y',data:[0,null,4,6]}]}).trends;
        return {scatter:scatter.series[0].fit,line:line.series[0].fit};})()''')
    assert result['scatter']['n'] == 3 and result['scatter']['excluded'] == 4
    assert result['scatter']['slope'] == 2
    assert result['line']['n'] == 3 and result['line']['excluded'] == 1 and result['line']['slope'] == 2


def test_bound_source_validation_refuses_chunked_live_and_derived_without_chart_mutation():
    for source_type, reason in [('live_data_source', 'live'), ('period_comparison', 'derived')]:
        ctx = RenderContext(DEFAULT_THEME)
        ctx.register('source', {'type': source_type, 'dataset_id': 'd'})
        chart = LineChart(frame(), 'day', 'y', dataset_id='d', trend_line=TREND)
        cfg = trend_config(chart, ctx)['trend_line']
        assert reason in cfg['unavailable']
        assert chart.trend_line == TREND
    ctx = RenderContext(DEFAULT_THEME)
    ctx.register('source', {'type': 'data_source', 'dataset_id': 'd'})
    ctx._chunk_manifests = {'d': {}}
    # Empty/malformed manifests still denote chunk ownership, never completeness.
    ctx._chunk_manifests['d'] = {'chunks': {'a': {}}}
    assert 'chunked' in trend_config(LineChart(frame(), 'day', 'y', dataset_id='d', trend_line=TREND), ctx)['trend_line']['unavailable']


def test_candidate_contributor_and_series_budgets_are_explicit():
    result = js('''(()=>{
        window._reportData={components:{}};window._fwFilterEngine={_columnar:{d:{}},_scopes:{},_liveDatasets:{}};
        const cfg={dataset_id:'d',x:'day',trend_line:{method:'linear',series:'each',columns:['y']}};
        const spec={labels:['2026-01-01','2026-01-02','2026-01-03'],datasets:[{label:'Y',data:[1,2,3]}]};
        const raw=_fwTrends.line(cfg,new Array(50001),spec).trends;
        const many={datasets:Array.from({length:9},(_,i)=>({label:'g'+i,data:[{x:0,y:0},{x:1,y:2},{x:2,y:4}]}))};
        const nine=_fwTrends.result({...cfg,dataset_id:null},null,many,true);
        const invalid=_fwTrends.fit(new Array(50001),0);
        return {raw,nine,invalid};})()''')
    assert 'budget' in result['raw']['reason'] and result['raw']['series'] == []
    assert 'budget' in result['nine']['reason'] and result['nine']['series'] == []
    assert '50,000' in result['invalid']['reason']


def test_dispatch_hook_reports_mutated_scatter_trend_declarations():
    from trellum.components.charts._trends import trend_option_problems
    from trellum.validation.checks.chart_options import _check_chart_options
    from trellum.validation.result import ValidationResult
    chart = ScatterChart(frame(), 'x', 'y', trend_line=TREND)
    assert trend_option_problems(chart) == []
    chart.trend_line = {**TREND, 'method': 'auto'}
    assert trend_option_problems(chart)[0][0] == 'chart-trend-invalid'
    result = ValidationResult('trend-options')
    _check_chart_options([(chart, 'Trend example')], result)
    assert result.summary['fail'] == 1
    assert 'chart-trend-invalid' in str(result.to_dict())


def test_split_sum_identity_and_complete_multi_column_contributors():
    chart = LineChart(frame(), 'day', ['x', 'y'], dataset_id='d', stack_by='group',
                      trend_line={'method': 'linear', 'series': [{'group': 'A'}]})
    assert trend_config(chart)['trend_line']['series'] == [{'group': 'A'}]
    alias = LineChart(frame(), 'day', 'y', dataset_id='d', stack_by='group',
                      trend_line={'method': 'linear', 'series': [{'column': 'y', 'group': 'A'}]})
    assert trend_config(alias)['trend_line']['series'] == [{'group': 'A'}]
    actual = js('''(()=>{
        window._reportData={components:{}};window._fwFilterEngine={_columnar:{d:{}},_scopes:{},_liveDatasets:{}};
        const cfg={dataset_id:'d',x:'day',value_cols:['a','b'],trend_line:{method:'linear',series:[{group:'A'}],columns:['a','b']}};
        const labels=['2026-01-01','2026-01-02','2026-01-03','2026-01-04'];
        const rows=labels.flatMap((day,i)=>[{day,group:'A',a:i,b:i===1?null:i},{day,group:'B',a:10,b:10}]);
        const spec={labels,datasets:[{label:'B',_fwTrendSource:{group:'B'},data:[20,20,20,20]},
            {label:'A',_fwTrendSource:{group:'A'},data:[0,1,4,6]}]};
        return _fwTrends.line(cfg,rows,spec,'group').trends.series[0];})()''')
    assert actual['index'] == 1 and actual['identity'] == {'group': 'A'}
    assert actual['fit']['n'] == 3 and actual['fit']['excluded'] == 1
    assert actual['fit']['slope'] == pytest.approx(2)


def test_large_supplied_date_frame_cannot_skip_utc_eligibility():
    df = pd.DataFrame({'day': [pd.Timestamp('2026-10-01T00:00:00+02:00')] * 50001, 'y': [1] * 50001})
    assert 'UTC' in trend_config(LineChart(df, 'day', 'y', trend_line=TREND))['trend_line']['unavailable']
