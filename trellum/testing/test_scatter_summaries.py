"""Scatter-local declarations, raw point arithmetic and native diagnostics."""
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

from trellum.components import RenderContext, ScatterChart
from trellum.components.charts._scatter_summary import scatter_option_problems, scatter_summary_config
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '6043ce4b9288b169bebbb6ce272884ff570b5c6a'
SUMMARY = {'stat': 'mean', 'scope': 'point_entries', 'axis': 'x'}


def frame():
    return pd.DataFrame({'x': [4, 4, 40], 'y': [5, 5, 60], 'size': [40, 40, 400],
                         'group': ['Organic', 'Organic', 'UA']})


def render(chart):
    ctx = RenderContext(DEFAULT_THEME)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/scatter.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_scatter_summary_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, 'baseline/scatter.py', 'exec'), module.__dict__)
    return module.ScatterChart


def example_context(output):
    path = ROOT / 'trellum/demo/reports/component-showcase/examples_scatter_summaries.py'
    spec = importlib.util.spec_from_file_location('examples_scatter_summaries', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={'name': 'Scatter summaries', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic supplied point observations by date and platform; explicit coordinate summaries and point-entry counts at current delivered grain.'},
        slug='scatter-summaries', output_dir=str(output))
    module.add_sections(ctx)
    return ctx


@pytest.mark.parametrize('dataset', [None, 'bound'])
@pytest.mark.parametrize('size', [None, 'size'])
def test_opt_out_exact_baseline_markup_payload_positions(dataset, size):
    args = (frame(), 'x', 'y', size, 'group', 'Title', 'currency', 'ratio', dataset, True)
    assert render(ScatterChart(*args)) == render(baseline_class()(*args))
    params = inspect.signature(ScatterChart).parameters
    assert params['header_summary'].kind == inspect.Parameter.KEYWORD_ONLY
    assert [n for n, p in params.items() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD] == [
        'df', 'x', 'y', 'size', 'color_by', 'title', 'x_format', 'y_format', 'dataset_id', 'static']


@pytest.mark.parametrize('declaration', [
    {}, False, True, [], (), 'mean', 1,
    *[{**SUMMARY, 'stat': value} for value in ([], {}, pd.NA, True, 'sum', 'rate', 'correlation', 'regression', 'weighted_mean')],
    *[{**SUMMARY, 'scope': value} for value in (None, [], pd.NA, True, 'visible_points', 'all_categories')],
    *[{**SUMMARY, 'axis': value} for value in (None, [], pd.NA, True, 'all', 'radius', 'size', 'z')],
    *[{**SUMMARY, key: value} for key, value in (('series', 0), ('extra', 1), ('label', ''), ('label', ' '), ('label', 3), ('label', 'a' * 61))],
    *[{**SUMMARY, 'format': value} for value in (None, [], pd.NA, True, 'unknown')],
    {'stat': 'min', 'scope': 'point_entries'},
    {'stat': 'count', 'scope': 'point_entries', 'axis': None},
    *[{'stat': 'count', 'scope': 'point_entries', 'format': value} for value in ('percent', 'currency', 'chips', 'ratio')],
])
def test_invalid_construction_render_mutation(declaration):
    with pytest.raises(ValueError, match='header_summary'):
        ScatterChart(frame(), 'x', 'y', header_summary=declaration)
    chart = ScatterChart(frame(), 'x', 'y')
    chart.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        render(chart)
    assert [code for code, _ in scatter_option_problems(chart)] == ['chart-summary-invalid']


@pytest.mark.parametrize('stat,axis,label', [('count', None, 'Point entries'), ('min', 'x', 'Minimum x'),
    ('min', 'y', 'Minimum y'), ('max', 'x', 'Maximum x'), ('max', 'y', 'Maximum y'), ('mean', 'x', 'Mean x'), ('mean', 'y', 'Mean y')])
def test_config_defaults_and_independent_header(stat, axis, label):
    cfg = {'stat': stat, 'scope': 'point_entries', **({'axis': axis} if axis else {})}
    chart = ScatterChart(frame(), 'x', 'y', title='Single title', x_format='currency', y_format='percent',
                         header_context='<Context>', header_summary=cfg)
    html, data = render(chart)
    assert cfg == {'stat': stat, 'scope': 'point_entries', **({'axis': axis} if axis else {})}
    assert data['header_summary'] == {**cfg, 'label': label, 'format': 'currency' if axis == 'x' else 'percent' if axis else 'number'}
    assert html.count('Single title') == 1 and '&lt;Context&gt;' in html and 'data-chart-summary' in html
    assert scatter_option_problems(chart) == []


@pytest.mark.parametrize('axis_format', ['number', 'currency', 'percent', 'ratio', 'chips', 'plain', 'unknown', None, [], pd.NA])
def test_actual_axis_fallback_and_explicit_format(axis_format):
    for axis in ('x', 'y'):
        chart = ScatterChart(frame(), 'x', 'y', **{axis + '_format': axis_format}, header_summary={**SUMMARY, 'axis': axis})
        expected = axis_format if isinstance(axis_format, str) and axis_format in ('number', 'currency', 'percent') else 'number'
        assert scatter_summary_config(chart)['header_summary']['format'] == expected
        for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain'):
            chart.header_summary = {**SUMMARY, 'axis': axis, 'format': fmt, 'label': '<safe> ' + 'x' * 53}
            assert scatter_summary_config(chart)['header_summary']['format'] == fmt
    assert scatter_summary_config(ScatterChart(frame(), 'x', 'y')) == {}


def test_static_payload_retains_raw_points_radii_and_group_omission():
    import orjson
    df = pd.DataFrame({'x': [0, None, True, '2', float('inf'), -4], 'y': [1, 2, 3, 4, 5, 0],
                       'g': ['a', 'a', 'b', 'b', 'c', None]}, dtype=object)
    for grouped in (None, 'g'):
        _, off = render(ScatterChart(df, 'x', 'y', color_by=grouped))
        _, on = render(ScatterChart(df, 'x', 'y', color_by=grouped, header_summary=SUMMARY))
        assert set(on) - set(off) == {'header_summary'}
        assert orjson.dumps({k: v for k, v in on.items() if k != 'header_summary'}) == orjson.dumps(off)
        points = [p for ds in on['datasets'] for p in ds['data']]
        assert len(points) == (5 if grouped else 6)
        assert points[:2] == [{'x': 0, 'y': 1}, {'x': None, 'y': 2}]
    _, off = render(ScatterChart(frame(), 'x', 'y', size='size', color_by='group'))
    _, on = render(ScatterChart(frame(), 'x', 'y', size='size', color_by='group', header_summary=SUMMARY))
    assert on['datasets'] == off['datasets']


def test_actual_runtime_signed_zero_empty_invalid_overflow_duplicates_and_size_independence():
    source = (ROOT / 'trellum/static/js/components/scatter.js').read_text()
    script = 'global.window={_fwRenderers:{}};\n' + source + r'''
const probe=points=>['min','max','mean','count'].map(stat=>_fwScatterSummaryResult([{data:points.slice(0,1)},{data:points.slice(1)}],{stat,axis:'x'}));
const pair=x=>({x,y:1});
process.stdout.write(JSON.stringify({valid:probe([-4,0,4].map(pair)), duplicate:probe([4,4,40].map(pair)),
  zero:probe([{x:0,y:0}]),tiny:probe([Number.MIN_VALUE,Number.MIN_VALUE].map(pair)),empty:probe([]),
  large:probe([1e308,1e308].map(pair)),cancel:probe([1e308,-1e308].map(pair)),
  max:probe([Number.MAX_VALUE,Number.MAX_VALUE].map(pair)),
  invalids:[null,undefined,'2',true,false,'2026-10-01',NaN,Infinity,-Infinity].map(v=>probe([{x:1,y:v},{x:2,y:3}])),
  sizes:probe([{x:4,y:5,r:NaN,_rawSize:null},{x:6,y:7,r:Infinity,_rawSize:'bad'}]),
  axes:['x','y'].map(axis=>['min','max','mean'].map(stat=>_fwScatterSummaryResult([{data:[{x:4,y:5},{x:40,y:60}]}],{stat,axis})))}));
'''
    out = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    values = lambda key: [r['value'] for r in out[key]]
    assert values('valid') == [-4, 4, 0, 3]
    assert values('duplicate') == [4, 40, 16, 3]
    assert values('zero') == [0, 0, 0, 1]
    assert values('tiny')[:3] == [5e-324] * 3
    assert values('empty') == [None, None, None, 0]
    assert values('large') == [1e308, 1e308, 1e308, 2]
    assert values('cancel') == [-1e308, 1e308, 0, 2]
    assert values('max') == [sys.float_info.max] * 3 + [2]
    assert values('sizes') == [4, 6, 5, 2]
    for case in out['invalids']:
        assert [r['value'] for r in case] == [None, None, None, 2]
        assert all(r['reason'] for r in case[:3])
    assert [[r['value'] for r in rs] for rs in out['axes']] == [[4, 40, 22], [5, 60, 32.5]]


def test_native_examples_zero_diagnostics(tmp_path):
    ctx = example_context(tmp_path)
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert [(c.id, c.level, c.message) for c in result.checks if c.level in {'warn', 'fail'}] == []


def test_pure_mutation_hook():
    chart = ScatterChart(frame(), 'x', 'y', header_summary=SUMMARY)
    before = dict(chart.__dict__)
    assert scatter_option_problems(chart) == [] and chart.__dict__ == before
    chart.header_summary = {}
    assert scatter_option_problems(chart)[0][0] == 'chart-summary-invalid'
