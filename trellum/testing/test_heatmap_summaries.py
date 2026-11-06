"""Heatmap-local summary API, original-input guards and native arithmetic."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys
import types

import pandas as pd
import pytest

from trellum.components import HeatmapChart, RenderContext
from trellum.components.charts._heatmap_summary import heatmap_summary_config, heatmap_option_problems, heatmap_input_reason
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'c9ca7bc258a6d5887f0a0861f7fc41a88541dad6'
SUMMARY = {'stat': 'sum', 'scope': 'current_cells'}


def frame():
    return pd.DataFrame({'x': ['B', 'A', 'B', 'C'], 'y': ['one'] * 4, 'v': [12, 10, 18, 0]})


def render(chart):
    ctx = RenderContext(DEFAULT_THEME)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/heatmap.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_heatmap_summary_baseline'); sys.modules[module.__name__] = module
    exec(compile(source, 'baseline/heatmap.py', 'exec'), module.__dict__)
    return module.HeatmapChart


def examples():
    path = ROOT / 'trellum/demo/reports/component-showcase/examples_heatmap_summaries.py'
    spec = importlib.util.spec_from_file_location('examples_heatmap_summaries', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def example_context(output):
    ctx = ReportContext(config={'name': 'Heatmap summaries', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic quantities by observation date and platform, aggregate cell reducers, raw units and input eligibility.'},
        slug='heatmap-summaries', output_dir=str(output))
    examples().add_sections(ctx)
    return ctx


def walk(components):
    for component in components:
        yield component
        yield from walk(getattr(component, 'children', []))
        for tab in getattr(component, 'tabs', []):
            yield from walk(tab.get('content', []))


@pytest.mark.parametrize('dataset', [None, 'bound'])
def test_opt_out_exact_baseline_and_keyword_only_subclass_positions(dataset):
    args = (frame(), 'x', 'y', 'v', 'Title', ('#fff', '#000'), False, 'row', True, 'currency', dataset, True)
    assert render(HeatmapChart(*args)) == render(baseline_class()(*args))
    assert inspect.signature(HeatmapChart).parameters['header_summary'].kind == inspect.Parameter.KEYWORD_ONLY
    @dataclass
    class Child(HeatmapChart):
        extra: str = 'extra'
    assert Child(*args, 'child', header_summary=SUMMARY).extra == 'child'


@pytest.mark.parametrize('declaration', [
    {}, [], (), 'sum', 1, False, True,
    *[{**SUMMARY, 'stat': v} for v in ([], {}, None, False, 'last', 'rate', 'weighted_mean')],
    *[{**SUMMARY, 'scope': v} for v in ([], {}, None, False, pd.NA, 'all_cells')],
    *[{**SUMMARY, 'format': v} for v in ([], {}, None, False, pd.NA, 'bad')],
    *[{**SUMMARY, 'label': v} for v in ([], {}, None, False, '', ' ', 'x' * 61)],
    {**SUMMARY, 'row': 'Mon'}, {**SUMMARY, 'column': '09'}, {**SUMMARY, 'series': 0},
    {**SUMMARY, 'format': 'percent'}, {**SUMMARY, 'format': 'ratio'},
    {'stat': 'count', 'scope': 'current_cells', 'format': 'chips'},
])
def test_malformed_constructor_render_and_mutation_validation(declaration):
    with pytest.raises(ValueError, match='header_summary'):
        HeatmapChart(frame(), 'x', 'y', 'v', header_summary=declaration)
    chart = HeatmapChart(frame(), 'x', 'y', 'v'); chart.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        render(chart)
    assert [code for code, _ in heatmap_option_problems(chart)] == ['chart-summary-invalid']


@pytest.mark.parametrize('value_format', ['number', 'currency', 'percent', 'chips', 'ratio', 'plain', 'unsupported'])
def test_default_matches_actual_cell_formatter_and_sum_rates_rejected(value_format):
    chart = HeatmapChart(frame(), 'x', 'y', 'v', value_format=value_format,
                         header_summary={'stat': 'mean', 'scope': 'current_cells'})
    expected = value_format if value_format in ('number', 'currency', 'percent') else 'number'
    assert heatmap_summary_config(chart)['header_summary']['format'] == expected
    chart.header_summary = SUMMARY
    if value_format in ('percent', 'ratio'):
        assert heatmap_option_problems(chart)
    else:
        assert heatmap_option_problems(chart) == []


@pytest.mark.parametrize('stat,label', [('count','Cell entries'), ('sum','Cell total'), ('mean','Mean cell'), ('min','Minimum cell'), ('max','Maximum cell')])
def test_opt_in_preserves_cells_and_carries_only_compact_original_guard(stat, label):
    chart = HeatmapChart(frame(), 'x', 'y', 'v', 'Title', header_context='<units>',
                         header_summary={'stat': stat, 'scope': 'current_cells'})
    html, cfg = render(chart)
    assert cfg['header_summary']['label'] == label
    assert html.count('Title') == 1 and '&lt;units&gt;' in html
    assert cfg['cells'] == [{'x': 'B', 'y': 'one', 'v': 30}, {'x': 'A', 'y': 'one', 'v': 10}, {'x': 'C', 'y': 'one', 'v': 0}]
    assert cfg.get('summary_input_reason', '') == ''
    assert ('summary_input_reason' in cfg) == (stat != 'count')
    chart.header_summary = {**chart.header_summary, 'label': '<safe> & text', 'format': 'plain'}
    assert render(chart)[1]['header_summary']['label'] == '<safe> & text'
    assert 'inputs' not in cfg


@pytest.mark.parametrize('bad', [None, True, False, '2', 'bad', pd.NA, float('nan'), float('inf'), -float('inf'), 2**53, Decimal('2'), pd.Timestamp('2026-08-01')])
def test_static_guard_before_legacy_coercion_and_drop_empty(bad):
    df = pd.DataFrame({'x': ['A', 'B'], 'y': ['one', 'two'], 'v': [bad, 10]}, dtype=object)
    chart = HeatmapChart(df, 'x', 'y', 'v', drop_empty=True, header_summary=SUMMARY)
    _, on = render(chart); _, off = render(baseline_class()(df, 'x', 'y', 'v', drop_empty=True))
    assert on['summary_input_reason']
    assert on['cells'] == off['cells']
    assert heatmap_input_reason([bad])


def test_native_examples_diagnostics_and_independent_expected_arithmetic(tmp_path):
    df = examples().facts()
    assert len(df) == 20
    cells = df.groupby(['x', 'y'])['units'].sum()
    assert sorted(cells.tolist()) == [0, 90, 180, 270]
    assert [cells.sum(), cells.mean(), cells.min(), cells.max(), cells.count()] == [540, 135, 0, 270, 4]
    one = df[(df.platform == 'iOS') & (df.day == '2026-08-01')].groupby(['x', 'y'])['units'].sum()
    assert [one.sum(), one.mean(), one.min(), one.max(), one.count()] == [60, 15, 0, 30, 4]
    ctx = example_context(tmp_path)
    details = compute_details(ctx); result = validate_report(ctx, details=details)
    assert [(c.id, c.level, c.section, c.message) for c in result.checks if c.level in {'warn', 'fail'}] == []
    for section in ctx.sections:
        for chart in walk(section['components']):
            if isinstance(chart, HeatmapChart):
                assert heatmap_option_problems(chart) == []
    # Direct hook until common dispatch is integrated by the lead.
    chart.header_summary = {}
    assert heatmap_option_problems(chart)[0][0] == 'chart-summary-invalid'


def test_actual_javascript_reducers_precision_signed_zero_and_eligibility():
    source = (ROOT / 'trellum/static/js/components/heatmap.js').read_text().replace('\x00', r'\u0000')
    script = 'global.window={_fwRenderers:{}};\n' + source + r'''
const stats=['sum','mean','min','max','count'];
const run=(values,reason='')=>stats.map(stat=>_fwHeatmapSummaryResult(values.map(v=>({v})),{stat},reason));
const invalid=[null,undefined,'2',true,false,NaN,Infinity,-Infinity,Number.MAX_SAFE_INTEGER+1];
process.stdout.write(JSON.stringify({full:run([0,90,180,270]),one:run([0,10,20,30]),
  empty:run([]),zero:run([0]),signed:run([-20,10,-5]),rates:run([.1,.3]),
  cancel:run([Number.MAX_SAFE_INTEGER,1,-Number.MAX_SAFE_INTEGER]),
  tinyCancellation:run([Number.MAX_SAFE_INTEGER,1e-310,-Number.MAX_SAFE_INTEGER]),
  aggregateUnsafe:run([Number.MAX_SAFE_INTEGER+1]),sumUnsafe:run([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER]),
  overflow:run([Infinity]),
  guards:invalid.map(v=>{const reason=_fwHeatmapInputReason([{v}], 'v');return {reason,result:run([0,10],reason)}}),
  valid:_fwHeatmapInputReason([{v:-3},{v:0},{v:.5}], 'v')}));
'''
    out = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    values = lambda key: [r['value'] for r in out[key]]
    assert values('full') == [540,135,0,270,4]
    assert values('one') == [60,15,0,30,4]
    assert values('empty') == [None,None,None,None,0]
    assert values('zero') == [0,0,0,0,1]
    assert values('signed') == [-15,-5,-20,10,3]
    assert values('rates') == pytest.approx([.4,.2,.1,.3,2])
    assert values('cancel')[:2] == pytest.approx([1,1/3])
    assert values('tinyCancellation')[0] == 1e-310
    assert values('tinyCancellation')[1] == 1e-310 / 3
    assert values('aggregateUnsafe') == [None,None,None,None,1]
    assert values('sumUnsafe') == [None,2**53-1,2**53-1,2**53-1,2]
    assert values('overflow') == [None,None,None,None,1]
    for guard in out['guards']:
        assert guard['reason'] and [r['value'] for r in guard['result']] == [None,None,None,None,2]
    assert out['valid'] == ''
