"""Strict snapshot definitions and real-engine lifecycle/reducer checks."""
from __future__ import annotations
import inspect
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import types

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import DataSource, ScopedDataSource, FilterBar, MiniKpi, RenderContext
from trellum.components.metric_views import MetricView, validate_source
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME
from trellum.validation.checks.metric_views import check_metric_views
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]
GROUPS = [{'id': 'all', 'label': 'All'}, {'id': 'uk', 'label': 'UK', 'where': {'column': 'country', 'values': ['GB']}}]
METRICS = {'cost': {'agg': 'sum', 'column': 'cost'}, 'count': {'agg': 'count'},
           'cpi': {'agg': 'ratio', 'numerator': 'cost', 'denominator': 'installs'}}


def view(**overrides):
    return MetricView(**{'id': 'result', 'parent': 'facts', 'groups': GROUPS, 'metrics': METRICS, **overrides})


def node(body):
    setup = '''global.window=global;global.document={documentElement:{},getElementById:()=>({})};
    global.MutationObserver=class {observe(){}};global._fwRenderers={};
    global.requestIdleCallback=fn=>fn();'''
    script = setup + load_js('runtime/filter_engine.js') + load_js('components/metric_views.js') + body
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('kwargs', [
    {'id': ''}, {'parent': 'result'}, {'groups': []}, {'groups': GROUPS * 17},
    {'groups': [GROUPS[0], GROUPS[0]]}, {'groups': [{'id': 'a', 'label': ''}]},
    {'groups': [{'id': 'a', 'label': 'A', 'where': None}]},
    {'groups': [{'id': 'a', 'label': 'A', 'where': {'column': 'country', 'values': [1]}}]},
    {'groups': [{'id': 'a', 'label': 'A', 'where': {'column': 'country', 'values': ['x'] * 33}}]},
    {'metrics': {}}, {'metrics': {'Notes': {'agg': 'count'}}},
    {'metrics': {'x': {'agg': 'mean', 'column': 'cost'}}},
    {'metrics': {'x': {'agg': 'count', 'column': 'cost'}}},
    {'metrics': {'x': {'agg': 'sum', 'column': 'cost'}, 'x_status': {'agg': 'count'}}},
    {'metrics': {'__proto__': {'agg': 'count'}}}, {'metrics': {'bad-key': {'agg': 'count'}}},
    {'metrics': {'x': {'agg': []}}},
])
def test_malformed_definition(kwargs):
    with pytest.raises(ValueError, match='MetricView'):
        view(**kwargs)


@pytest.mark.parametrize('value', [Decimal('1.1'), True, '1', 2**53])
def test_precision_and_incompatible_inputs(value):
    with pytest.raises(ValueError, match='native|unsafe'):
        validate_source([{'country': 'GB', 'cost': value, 'installs': 1}], ['country', 'cost', 'installs'], GROUPS, METRICS)


def test_missing_column():
    with pytest.raises(ValueError, match='missing'):
        validate_source([], ['cost'], GROUPS, METRICS)


@pytest.mark.heavy
def test_complete_source_resource_bounds():
    rows = [{'country': 'GB', 'cost': 1, 'installs': 1}] * 10000
    validate_source(rows, rows[0], GROUPS, METRICS)
    with pytest.raises(ValueError, match='10,000'):
        validate_source(rows + rows[:1], rows[0], GROUPS, METRICS)
    rows = [{'country': 'x' * 500, 'cost': 1, 'installs': 1}] * 9000
    with pytest.raises(ValueError, match='4 MiB'):
        validate_source(rows, rows[0], GROUPS, METRICS)
    output = node('''const e=_fwFilterEngine;
        e.init('facts',Array.from({length:10001},()=>({country:'GB',cost:1,installs:1})));
        _fwRenderers.metric_view('view',CFG);
        console.log(JSON.stringify({status:_fwMetricViews.status('result'),rows:e.getFiltered('result')}));'''.replace('CFG', json.dumps({'dataset_id':'result','parent_id':'facts','groups':GROUPS,'metrics':METRICS})))
    assert not output['status']['ready'] and '10,000' in output['status']['reason']
    assert output['rows'][0]['cost'] is None
    output = node('''const e=_fwFilterEngine;
        e.init('facts',Array.from({length:9000},()=>({country:'x'.repeat(500),cost:1,installs:1})));
        _fwRenderers.metric_view('view',CFG);
        console.log(JSON.stringify(_fwMetricViews.status('result')));'''.replace('CFG', json.dumps({'dataset_id':'result','parent_id':'facts','groups':GROUPS,'metrics':METRICS})))
    assert not output['ready'] and '4 MiB' in output['reason']


@pytest.mark.parametrize('binding,value', [({}, None), ({'dataset_id': 'result', 'group': 'all', 'metric': 'cost', 'x': 1}, None),
    ({'dataset_id': 'result', 'group': 'all', 'metric': 'cost'}, 0), ({'dataset_id': '', 'group': 'all', 'metric': 'cost'}, None)])
def test_binding_rejects_ambiguity(binding, value):
    with pytest.raises(ValueError):
        MiniKpi('Cost', value, binding=binding).render_html(RenderContext(DEFAULT_THEME))


def test_static_mini_exact_baseline_and_keyword_only():
    source = subprocess.check_output(['git', 'show', '1c0c49710c5d073a78f83ab069fd517cd49c1632:trellum/components/kpis.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_metric_mini_baseline'); sys.modules[module.__name__] = module
    exec(source, module.__dict__)
    for stacked in (False, True):
        a, b = RenderContext(DEFAULT_THEME), RenderContext(DEFAULT_THEME)
        args = ('Cost', 1234.5, 'currency', None)
        assert MiniKpi(*args, stacked=stacked).render_html(a) == module.MiniKpi(*args, stacked=stacked).render_html(b)
        assert a.component_data == b.component_data
        assert MiniKpi.css() == module.MiniKpi.css()
    assert inspect.signature(MiniKpi).parameters['binding'].kind == inspect.Parameter.KEYWORD_ONLY
    assert [k for k,p in inspect.signature(MiniKpi).parameters.items() if p.kind == p.POSITIONAL_OR_KEYWORD] == ['label', 'value', 'format', 'metric']


@pytest.mark.parametrize('kind', ['missing', 'chained', 'chunked', 'scope-chain', 'later', 'bad-column', 'live', 'propagated-live', 'late-propagated-live'])
def test_validator_rejects_parent_contract(kind, tmp_path):
    from trellum.components import LiveDataSource
    frame = pd.DataFrame([{'country': 'GB', 'cost': 1, 'installs': 1}])
    source = DataSource('facts', frame)
    metric = view()
    components = [source, metric]
    if kind == 'missing': metric.parent = 'absent'
    if kind == 'chained': components += [view(id='next', parent='result')]
    if kind == 'chunked': source.chunk_by = 'month'
    if kind == 'scope-chain': components = [source, ScopedDataSource('a','facts'),ScopedDataSource('b','a'),view(parent='b')]
    if kind == 'later': components.reverse()
    if kind == 'bad-column': source.df = frame.drop(columns=['cost'])
    if kind in ('live', 'propagated-live', 'late-propagated-live'):
        live = LiveDataSource('live', 'q', frame)
        fb = FilterBar('live',frame,filters=[],propagate_to={'facts':{'country':'country'}})
        components = [live, view(parent='live')] if kind == 'live' else [live, source, fb, metric]
        if kind == 'late-propagated-live': components = [source, metric, live, fb]
    ctx = ReportContext(config={'name':'Test'}, slug='test', output_dir=str(tmp_path))
    ctx.add_section('Test',components)
    result = ValidationResult(slug='test'); check_metric_views(ctx,result)
    assert any(c.id == 'metric-view-invalid' for c in result.checks)


def test_pure_reducer_missing_zero_and_independent_siblings():
    output = node('''const r=_fwMetricViews.reduceMetric;
    const defs={sum:{agg:'sum',column:'x'},count:{agg:'count'},ratio:{agg:'ratio',numerator:'x',denominator:'n'}};
    const cases=[[],[{x:0,n:2}],[{x:2,n:0}],[{x:null,n:2}],[{x:2,n:1},{x:8,n:4}],[{x:Infinity,n:1}],[{x:MAX,n:1},{x:1,n:1}]];
    console.log(JSON.stringify(cases.map(rows=>Object.values(defs).map(d=>r(rows,d)))));'''.replace('MAX',str(2**53-1)))
    assert [v['value'] for v in output[0]] == [None,0,None]
    assert [v['value'] for v in output[1]] == [0,1,0]
    assert [v['value'] for v in output[2]] == [2,1,None]
    assert [v['value'] for v in output[3]] == [None,1,None]
    assert [v['value'] for v in output[4]] == [10,2,2]
    assert output[5][0]['value'] is None and output[6][0]['value'] is None


def test_real_engine_lifecycle_contract_and_owned_reentry():
    cfg = {'dataset_id':'result','parent_id':'facts','groups':GROUPS,'metrics':METRICS}
    output = node('''const e=_fwFilterEngine; let ordinary=0, events=[];
    e.subscribe('facts','existing',()=>ordinary++);
    e.initColumnar('facts',null);e.init('facts',[{country:'GB',cost:4,installs:2}]);
    const unchanged=ordinary; e.notify('facts');
    let off=e.subscribeLifecycle(event=>events.push(event));
    e.init('other',[{x:1}]); off();off();e.removeDataset('other');
    _fwRenderers.metric_view('v',CFG);let publications=0;
    e.subscribe('result','probe',()=>publications++);
    e.setFilter('facts','country','country','equals','GB');
    let filtered=publications;
    e.init('facts',[{country:'GB',cost:12,installs:3}]);
    let replaced=e.getFiltered('result')[0].cpi;
    e.removeDataset('facts');let removed=e.getFiltered('result')[0].cost;
    e.init('facts',[{country:'GB',cost:20,installs:2}]);
    let restored=e.getFiltered('result')[0].cpi;
    let before=e._lifecycleListeners.size; _fwMetricViews.dispose('v');
    let after=e._lifecycleListeners.size;
    _fwRenderers.metric_view('v',CFG);_fwRenderers.metric_view('v',CFG);
    let reentry=e._lifecycleListeners.size;
    let rowListeners=e._subscribers.facts.filter(s=>s.id==='v:metric-view').length;
    console.log(JSON.stringify({unchanged,ordinary,events,filtered,replaced,removed,restored,publications,before,after,reentry,rowListeners}));'''.replace('CFG',json.dumps(cfg)))
    assert output['unchanged'] == 0 and output['ordinary'] == 2  # explicit notify + filter only
    assert output['events'] == [{'type':'init','dataset_id':'other'}]
    assert output['filtered'] == 1 and output['publications'] == 5 # filter/init/remove/reinit/dispose
    assert (output['replaced'],output['removed'],output['restored']) == (4,None,10)
    assert (output['before'],output['after'],output['reentry'],output['rowListeners']) == (1,0,1,1)


def test_scoped_replacement_bypasses_stale_cache_without_filter_writes():
    cfg = {'dataset_id':'result','parent_id':'scoped','groups':GROUPS,'metrics':METRICS}
    output = node('''const e=_fwFilterEngine;
    e.init('facts',[{country:'GB',cost:4,installs:2}]);e.addScopedChild('scoped','facts');
    e.setFilter('scoped','local','country','equals','GB');e.getFiltered('scoped');
    _fwRenderers.metric_view('v',CFG);
    let hits=0;e.subscribe('result','probe',()=>hits++);
    e.init('facts',[{country:'GB',cost:12,installs:3}]);let replaced=e.getFiltered('result')[0].cpi;
    e.init('other',[{country:'GB',cost:30,installs:3}]);e.addScopedChild('scoped','other');
    let redirected=e.getFiltered('result')[0].cpi;
    e.removeDataset('scoped');let unavailable=e.getFiltered('result')[0].cost;
    e.addScopedChild('scoped','facts');e.setFilter('scoped','local','country','equals','GB');
    console.log(JSON.stringify({hits,replaced,redirected,unavailable,restored:e.getFiltered('result')[0].cpi,state:e.getFilterState('scoped')}));'''.replace('CFG',json.dumps(cfg)))
    assert (output['replaced'],output['redirected'],output['unavailable'],output['restored']) == (4,10,None,4)
    assert output['hits'] == 5
    assert output['state']['local']['value'] == 'GB'


@pytest.mark.parametrize('target,group,metric', [('missing','all','cost'),('facts','all','cost'),('result','absent','cost'),('result','all','unknown')])
def test_binding_reference_validator(target,group,metric,tmp_path):
    frame=pd.DataFrame([{'country':'GB','cost':2,'installs':1}])
    ctx=ReportContext(config={'name':'Test'},slug='test',output_dir=str(tmp_path))
    ctx.add_section('Binding',[DataSource('facts',frame),view(),MiniKpi('Cost',binding={'dataset_id':target,'group':group,'metric':metric})])
    result=ValidationResult(slug='test');check_metric_views(ctx,result)
    assert [c.id for c in result.checks]==['mini-kpi-binding-invalid']


def test_mutated_definition_and_conflicting_source_schema(tmp_path):
    ctx=ReportContext(config={'name':'Test'},slug='test',output_dir=str(tmp_path))
    source=DataSource('facts',pd.DataFrame([[1,2]],columns=['cost','cost']))
    metric=view();ctx.add_section('Schema',[source,metric])
    result=ValidationResult(slug='test');check_metric_views(ctx,result)
    assert 'unique' in result.checks[0].message
    source.df=pd.DataFrame([{'country':'GB','cost':2,'installs':1}])
    metric.metrics={'x':{'agg':'ratio','numerator':'cost','denominator':'installs','expression':'x'}}
    result=ValidationResult(slug='test');check_metric_views(ctx,result)
    assert 'exact' in result.checks[0].message


def test_lifecycle_null_branch_consistent_events_and_unrelated_change_counts():
    output=node('''const e=_fwFilterEngine;let calls=0;let values=[];
    const off=e.subscribeLifecycle(v=>values.push({type:v.type,id:v.dataset_id,ready:e.isReady(v.dataset_id),rows:e.getDatasetRowCount(v.dataset_id)}));
    e.subscribe('facts','old',()=>calls++);e.initColumnar('facts',null);e.notify('facts');e.removeDataset('facts');e.init('facts',[{x:2}]);
    off();off();e.removeDataset('facts');
    console.log(JSON.stringify({calls,values,count:e._lifecycleListeners.size}));''')
    assert output=={'calls':1,'count':0,'values':[
        {'type':'init','id':'facts','ready':True,'rows':0},
        {'type':'remove','id':'facts','ready':False,'rows':0},
        {'type':'init','id':'facts','ready':True,'rows':1}]}


def test_lifecycle_duplicate_callback_ownership():
    output=node('''const e=_fwFilterEngine;let count=0;const fn=()=>count++;
    let a=e.subscribeLifecycle(fn),b=e.subscribeLifecycle(fn);
    e.initColumnar('facts',null);a();a();e.removeDataset('facts');b();
    console.log(JSON.stringify({count,listeners:e._lifecycleListeners.size}));''')
    assert output=={'count':3,'listeners':0}


def test_replacement_incompatible_metric_keeps_valid_siblings():
    cfg={'dataset_id':'result','parent_id':'facts','groups':GROUPS,'metrics':METRICS}
    output=node('''const e=_fwFilterEngine;
    e.init('facts',[{country:'GB',cost:4,installs:2}]);_fwRenderers.metric_view('view',CFG);
    e.init('facts',[{country:'GB',cost:'bad',installs:2}]);
    console.log(JSON.stringify(e.getFiltered('result')[0]));'''.replace('CFG',json.dumps(cfg)))
    assert output['cost'] is None and output['cpi'] is None and output['count']==1
    assert output['count_status']=='available' and 'incompatible' in output['cost_reason']
