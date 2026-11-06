"""Ordinary line result API and production classification logic."""
from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import AreaChart, LineChart, RenderContext
from trellum.components.charts.line_area import line_option_problems
from trellum.themes import THEME_REGISTRY

BASELINE = '98d96192564f87568252c6779481c6234b5b848a'
ROOT = Path(__file__).resolve().parents[2]


def render(chart):
    ctx = RenderContext(THEME_REGISTRY['trellum light'])
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def test_api_strict_keyword_only_and_actual_baseline_payload(tmp_path):
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/line_area.py'], cwd=ROOT)
    path = tmp_path / 'baseline_line.py'
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location('line_results_baseline', path)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    assert inspect.signature(LineChart).parameters['result_presentation'].kind == inspect.Parameter.KEYWORD_ONLY
    for frame in (pd.DataFrame({'day': ['2026-10-01', '2026-10-02'], 'value': [0, 0]}),
                  pd.DataFrame({'day': ['2026-10-01', '2026-10-02'], 'value': [2, 7]})):
        for binding in ({'static': True}, {'dataset_id': 'orders'}):
            before = render(old.LineChart(frame, 'day', 'value', 'Supplied', **binding))
            assert render(LineChart(frame, 'day', 'value', 'Supplied', result_presentation=False, **binding)) == before
            html, cfg = render(LineChart(frame, 'day', 'value', 'Supplied', **binding))
            assert cfg.pop('line_result_presentation') is True
            assert (html, cfg) == before
    assert 'line_result_presentation' not in render(AreaChart(frame, 'day', 'value'))[1]
    assert '.fw-line-result-overlay' in LineChart.css()


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_bad_option_and_mutation_diagnostic(value):
    frame = pd.DataFrame({'day': ['2026-10-01'], 'value': [0]})
    with pytest.raises(ValueError, match='result_presentation must be bool'):
        LineChart(frame, 'day', 'value', result_presentation=value)
    chart = LineChart(frame, 'day', 'value')
    chart.result_presentation = value
    assert ('line-chart-result-presentation-invalid', 'LineChart result_presentation must be bool.') in line_option_problems(chart)
    with pytest.raises(ValueError, match='result_presentation must be bool'):
        render(chart)


def test_production_classification_scope_guard_and_unhidden_vectors():
    script = "const window={_fwRenderers:{}}; const document={addEventListener(){}};\n" + load_js('components/chart_base.js') + r'''
    const assert=require('node:assert/strict');
    const cfg={dataset_id:'child'}, source={id:'orders',subset:true};
    const engine={_scopes:{child:{parent:'orders'}},_columnar:{orders:{}},
      isLiveDataset:id=>id==='orders',getDatasetRowCount:()=>3};
    const spec=v=>({labels:v.map((_,i)=>i),datasets:[{data:v,hidden:true}]});
    const ready={phase:'ready',origin:'snapshot',has_result:true,requested_params:{id:1},displayed_params:{id:1}};
    const text=(values,state=ready,rows=values.map(v=>({value:v})))=>_fwLineResultText(cfg,spec(values),rows,source,state,engine);
    assert.equal(text([0,null,0]).overlay,'');
    assert.equal(text([]).overlay,'No matching data');
    assert.equal(text([],{...ready,phase:'pending',requested_params:{id:2}}).overlay,'Loading requested data\nRequested: id=2');
    assert.match(text([],{...ready,phase:'error',requested_params:{id:2}}).overlay,/Could not load/);
    assert.match(text([],{...ready,phase:'pending'}).overlay,/Loading requested data/);
    assert.match(text([],{...ready,phase:'error'}).overlay,/Could not load requested data/);
    assert.match(text([12],{...ready,phase:'pending',requested_params:{id:2},displayed_params:null}).context,/Displayed snapshot: unknown/);
    assert.match(text([12],{...ready,phase:'error',requested_params:{id:2}}).context,/Local subset.*orders/);
    assert.equal(text([12],{...ready,phase:'error'}).context,'');
    assert.equal(text([],{...ready,phase:'unavailable',has_result:false}).overlay,'Result unavailable');
    assert.equal(text([],null).overlay,'Result unavailable');
    assert.match(text([null]).overlay,/No usable observations/);
    const unavailable={...spec([null]),availability:[{reason:'Awaiting <source>'}]};
    assert.equal(_fwLineResultText(cfg,unavailable,[{}],source,ready,engine).overlay,'Observations unavailable\nAwaiting <source>');
    assert.deepEqual(_fwLineResultSource(engine,'child'),source);
    engine._scopes.orders={parent:'child'};
    assert.equal(_fwLineResultSource(engine,'child'),null);
    engine._scopes={};for(let i=0;i<70;i++)engine._scopes['c'+i]={parent:'c'+(i+1)};
    assert.equal(_fwLineResultSource(engine,'c0'),null);
    const ordinary={...engine,_columnar:{static:{}},isLiveDataset:()=>false,getDatasetRowCount:()=>0};
    assert.equal(_fwLineResultText({dataset_id:'static'},spec([]),[],{id:'static',subset:false},null,ordinary).overlay,'No data supplied');
    assert.equal(_fwLineResultText({dataset_id:'missing'},spec([]),[],{id:'missing',subset:false},null,ordinary).overlay,'Data not ready');
    console.log('production result classification passed');
    '''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    assert 'classification passed' in result.stdout
