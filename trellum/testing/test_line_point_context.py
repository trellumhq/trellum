"""Opt-in point context keeps legacy contracts and rejects ambiguous identities."""
import inspect
import json
import subprocess

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import LineChart, RenderContext
from trellum.themes import DEFAULT_THEME


def chart(**kwargs):
    return LineChart(pd.DataFrame({'day':['Day 1','Day 2'], 'a':[1,2], 'b':[2,3], 'date':['2026-10-22','2026-10-23'], 'window':['A','A']}),
                     'day', ['a','b'], **kwargs)


def test_default_config_and_keyword_contract():
    assert inspect.signature(LineChart).parameters['point_context'].kind is inspect.Parameter.KEYWORD_ONLY
    ctx = RenderContext(theme=DEFAULT_THEME); chart().render_html(ctx)
    assert 'point_context' not in next(iter(ctx.component_data.values()))


@pytest.mark.parametrize('kwargs', [
    {'point_context':{}}, {'point_context':[{'series':True, 'records':[]}]},
    {'point_context':[{'series':0,'date_column':'missing','window_column':'window'}], 'dataset_id':'facts'},
    {'point_context':[{'series':0,'date_column':'date','window_column':'window','unknown':'x'}], 'dataset_id':'facts'},
    {'point_context':[{'series':0,'records':[{'date':'2026-02-29','window':'A'},None]}]},
    {'point_context':[{'series':0,'records':[{'date':'2026-10-22','window':'A\nB'},None]}]},
    {'point_context':[{'series':0,'records':[None,None]}], 'stack_by':'group'},
    {'point_context':[{'series':'same','records':[None,None]}], 'y_labels':['same','same']},
])
def test_strict_point_context(kwargs):
    with pytest.raises(ValueError): chart(**kwargs)


def test_static_context_and_literal_tooltip():
    c = chart(point_context=[{'series':0,'records':[{'date':'2026-10-22','window':'<literal & text>'},None]}])
    ctx=RenderContext(theme=DEFAULT_THEME); c.render_html(ctx)
    cfg=next(iter(ctx.component_data.values()))
    script='global.window=global;'+load_js('runtime/point_context.js')+'''
        const cfg=CFG;
        const spec=_fwLinePointContextSpec(cfg,null,{labels:['Day 1','Day 2'],datasets:[]});
        const original={callbacks:{label:()=> 'preserved'}};
        const tt=_fwLinePointContextTooltip(spec.linePointContext,original);
        console.log(JSON.stringify([tt.callbacks.title([{datasetIndex:0,dataIndex:0,label:'Day 1',chart:{width:600}}]),
            tt.callbacks.label({datasetIndex:0,dataIndex:0,chart:{width:600}}),Object.keys(original.callbacks)]));'''.replace('CFG',json.dumps(cfg))
    result=json.loads(subprocess.check_output(['node','-e',script],text=True))
    assert result==['2026-10-22',['preserved','<literal & text>'],['label']]


def test_shared_chart_assets_remain_single_and_context_default_is_absent():
    from trellum.components import BarChart
    ctx=RenderContext(theme=DEFAULT_THEME)
    ctx.render_child(chart())
    ctx.render_child(BarChart(pd.DataFrame({'x':['one'],'v':[1]}),'x','v','Existing bar'))
    _,js=ctx.collect_assets()
    assert js.count(load_js('components/chart_base.js'))==1
    assert '_fwLinePointContextTooltip = function' not in js
    ctx.render_child(chart(point_context=[{'series':0,'records':[None,None]}]))
    _,js=ctx.collect_assets()
    assert js.count(load_js('components/chart_base.js'))==1
    assert js.count('_fwLinePointContextTooltip = function')==1


def test_bound_context_rejects_ambiguous_source_at_render():
    from trellum.components import DataSource
    ctx=RenderContext(theme=DEFAULT_THEME)
    c=chart(dataset_id='facts',point_context=[{'series':0,'date_column':'date','window_column':'window'}])
    frame=c.df.copy();frame['day']='same'
    ctx.render_child(DataSource('facts',frame))
    with pytest.raises(ValueError,match='unique x'):
        ctx.render_child(c)
