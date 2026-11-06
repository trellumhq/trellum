"""Funnel-local declaration, arithmetic, compatibility and native diagnostics."""
from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import FunnelChart, RenderContext
from trellum.components.charts._funnel_summary import funnel_option_problems, funnel_summary_config
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '1c0c49710c5d073a78f83ab069fd517cd49c1632'
SUMMARY = {'stat': 'stage', 'scope': 'current_stages', 'stage': 'Completed'}


def frame():
    return pd.DataFrame({'stage': ['Completed', 'Completed', 'Opened'], 'amount': [10, 20, 50]})


def render(chart):
    ctx = RenderContext(DEFAULT_THEME)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/funnel.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_funnel_summary_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, 'baseline/funnel.py', 'exec'), module.__dict__)
    return module.FunnelChart


def example_context(output):
    path = ROOT / 'trellum/demo/reports/component-showcase/examples_funnel_summaries.py'
    spec = importlib.util.spec_from_file_location('examples_funnel_summaries', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={'name': 'Funnel summaries', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic supplied stage counts by date and platform; selected stage, largest stage and entry counts at current delivered grain.'},
        slug='funnel-summaries', output_dir=str(output))
    module.add_sections(ctx)
    return ctx


@pytest.mark.parametrize('dataset', [None, 'bound'])
def test_opt_out_exact_baseline_markup_payload_and_positions(dataset):
    args = (frame(), 'stage', 'amount', 'Title', True, dataset, True)
    assert render(FunnelChart(*args)) == render(baseline_class()(*args))
    params = inspect.signature(FunnelChart).parameters
    assert params['header_summary'].kind == inspect.Parameter.KEYWORD_ONLY
    assert [n for n, p in params.items() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD] == [
        'df', 'label', 'value', 'title', 'show_percentages', 'dataset_id', 'static']


@pytest.mark.parametrize('declaration', [
    {}, False, True, [], (), 'stage', 1,
    {'stat': ['max'], 'scope': 'current_stages'}, {'stat': True, 'scope': 'current_stages'},
    {**SUMMARY, 'scope': []}, {**SUMMARY, 'scope': pd.NA}, {**SUMMARY, 'scope': 'all_categories'},
    {**SUMMARY, 'stat': 'sum'}, {**SUMMARY, 'stat': 'mean'}, {**SUMMARY, 'stat': 'conversion'},
    {**SUMMARY, 'stat': 'percent'}, {'stat': 'stage', 'scope': 'current_stages'},
    {**SUMMARY, 'stage': None}, {**SUMMARY, 'stage': False}, {**SUMMARY, 'stage': []},
    {**SUMMARY, 'stage': ''}, {**SUMMARY, 'stage': ' '}, {**SUMMARY, 'stage': 'x' * 121},
    {**SUMMARY, 'stat': 'max'}, {**SUMMARY, 'stat': 'count'},
    {**SUMMARY, 'series': 0}, {**SUMMARY, 'label': ''}, {**SUMMARY, 'label': ' '},
    {**SUMMARY, 'label': 3}, {**SUMMARY, 'label': 'a' * 61},
    *[{**SUMMARY, 'format': fmt} for fmt in (None, [], pd.NA, False, 'currency', 'percent', 'ratio', 'chips')],
])
def test_invalid_declarations_construction_render_and_mutation_hook(declaration):
    with pytest.raises(ValueError, match='header_summary'):
        FunnelChart(frame(), 'stage', 'amount', header_summary=declaration)
    chart = FunnelChart(frame(), 'stage', 'amount')
    chart.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        render(chart)
    assert [code for code, _ in funnel_option_problems(chart)] == ['chart-summary-invalid']


def test_config_header_context_and_source_are_independent():
    df = frame()
    for stat, label in (('stage', 'Selected stage'), ('max', 'Largest stage'), ('count', 'Stage entries')):
        declaration = {'stat': stat, 'scope': 'current_stages', **({'stage': 'Completed'} if stat == 'stage' else {})}
        chart = FunnelChart(df, 'stage', 'amount', 'Single title', header_context='<Context>', header_summary=declaration)
        html, cfg = render(chart)
        assert cfg['header_summary'] == {**declaration, 'label': label, 'format': 'number'}
        assert declaration == {'stat': stat, 'scope': 'current_stages', **({'stage': 'Completed'} if stat == 'stage' else {})}
        assert html.count('Single title') == 1 and '&lt;Context&gt;' in html
        assert 'data-chart-summary' in html and 'aria-describedby' in html
        assert cfg['labels'] == ['Completed', 'Completed', 'Opened']
        assert cfg['values'] == [10, 20, 50]
        assert funnel_option_problems(chart) == []
    assert funnel_summary_config(FunnelChart(df, 'stage', 'amount')) == {}


def test_native_examples_diagnostics_and_mutation_validation(tmp_path):
    ctx = example_context(tmp_path)
    result = validate_report(ctx, details=compute_details(ctx))
    assert [(c.id, c.level, c.message) for c in result.checks if c.level in {'warn', 'fail'}] == []
    chart = FunnelChart(frame(), 'stage', 'amount', static=True)
    chart.header_summary = {}
    ctx.add_section('Invalid declaration', [chart])
    result = validate_report(ctx, details=compute_details(ctx))
    failures = [c for c in result.checks if c.id == 'chart-summary-invalid' and c.level == 'fail']
    assert len(failures) == 1
    assert failures[0].component == 'FunnelChart'
    assert failures[0].section == 'Invalid declaration'


def test_static_serialization_keeps_legacy_values_without_provenance_copy():
    import orjson

    df = pd.DataFrame({'stage': ['Completed', 'Missing', 'Infinite', 'Boolean', 'Text'],
                       'amount': [0, None, float('inf'), True, '2']}, dtype=object)
    _, off = render(FunnelChart(df, 'stage', 'amount'))
    _, on = render(FunnelChart(df, 'stage', 'amount', header_summary=SUMMARY))
    assert orjson.loads(orjson.dumps(on))['values'] == [0, None, None, True, '2']
    assert set(on) - set(off) == {'header_summary'}
    assert orjson.dumps({k: v for k, v in on.items() if k != 'header_summary'}) == orjson.dumps(off)


def test_declaration_bounds_plain_format_and_exact_stage_whitespace():
    declaration = {**SUMMARY, 'stage': ' x' + 'x' * 117 + ' ', 'label': 'L' * 60, 'format': 'plain'}
    chart = FunnelChart(frame(), 'stage', 'amount', header_summary=declaration)
    assert len(declaration['stage']) == 120
    assert render(chart)[1]['header_summary'] == declaration


def test_actual_runtime_stage_entry_eligibility_arithmetic_and_grouping():
    source = (ROOT / 'trellum/static/js/components/funnel.js').read_text()
    aggregate = (ROOT / 'trellum/static/js/runtime/aggregate.js').read_text()
    script = "global.window={_fwRenderers:{}};\n" + aggregate + source + r'''
const stats=['stage','max','count'];
const probe=(labels,values,original=values,groups)=>stats.map(stat=>_fwFunnelSummaryResult(labels,values,original,{stat,stage:'Completed'},groups));
const bound=rows=>{const gr=window._fwAggregate.groupBy(rows,'stage',['amount']);
  gr.labels.sort((a,b)=>(gr.map[b].amount||0)-(gr.map[a].amount||0));
  const groups=new Map();rows.forEach(r=>{let k=String(r.stage!=null?r.stage:'');if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r.amount)});
  return {labels:gr.labels,values:gr.labels.map(l=>gr.map[l].amount||0),
    result:probe(gr.labels,gr.labels.map(l=>gr.map[l].amount||0),rows.map(r=>r.amount),groups)}};
const out={duplicates:probe(['Completed','Completed','Opened'],[10,20,50]),
  valid:probe(['Opened','Completed'],[100,20]),zero:probe(['Completed','Opened'],[0,0]),empty:probe([],[]),
  absent:probe(['Opened'],[10]),unrelatedBad:probe(['Completed','Opened'],[20,null]),
  overflow:probe(['Completed','Opened'],[1e308,1e308]),
  grouped:bound([{stage:'Opened',amount:40},{stage:'Completed',amount:20},{stage:'Opened',amount:30}]),
  reordered:bound([{stage:'Opened',amount:1},{stage:'Completed',amount:20}]),
  invalids:[null,undefined,'2',true,false,NaN,Infinity,-Infinity,-1].map(bad=>bound([
    {stage:'Completed',amount:bad},{stage:'Completed',amount:10},{stage:'Opened',amount:50}])),
  unrelated:bound([{stage:'Completed',amount:20},{stage:'Opened',amount:null}]),
  groupedOverflow:bound([{stage:'Completed',amount:1e308},{stage:'Completed',amount:1e308}]),
  collision:bound([{stage:1,amount:10},{stage:'1',amount:20}])};
process.stdout.write(JSON.stringify(out));
'''
    out = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    values = lambda results: [v['value'] for v in results]
    assert values(out['duplicates']) == [None, 50, 3]
    assert 'ambiguous' in out['duplicates'][0]['reason']
    assert values(out['valid']) == [20, 100, 2]
    assert values(out['zero']) == [0, 0, 2]
    assert values(out['empty']) == [None, None, 0]
    assert values(out['absent']) == [None, 10, 1]
    assert values(out['unrelatedBad']) == [20, None, 2]
    assert values(out['overflow']) == [1e308, 1e308, 2]
    assert out['grouped']['labels'] == ['Opened', 'Completed']
    assert out['grouped']['values'] == [70, 20]
    assert values(out['grouped']['result']) == [20, 70, 2]
    assert out['reordered']['labels'] == ['Completed', 'Opened']
    assert values(out['reordered']['result']) == [20, 20, 2]
    for case in out['invalids']:
        assert values(case['result']) == [None, None, 2]
        assert case['result'][0]['reason'] and case['result'][1]['reason']
    assert values(out['unrelated']['result']) == [20, None, 2]
    assert values(out['groupedOverflow']['result']) == [None, None, 1]
    assert out['collision']['labels'] == ['1'] and out['collision']['values'] == [30]
