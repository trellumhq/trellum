"""Doughnut category-entry declarations, runtime guards and baseline parity."""
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

from trellum.components import DoughnutChart, RenderContext
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '98d96192564f87568252c6779481c6234b5b848a'
SUMMARY = {'stat': 'sum', 'scope': 'all_categories'}


def frame():
    return pd.DataFrame({'category': ['UA', 'UA', 'Organic'], 'value': [10, 20, 30]})


def render(chart):
    ctx = RenderContext(DEFAULT_THEME)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/doughnut.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_doughnut_summary_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, 'baseline/doughnut.py', 'exec'), module.__dict__)  # noqa: S102 - Trusted local Git source for exact historical compatibility proof.
    return module.DoughnutChart


def example_context(output):
    path = ROOT / 'trellum/demo/reports/component-showcase/examples_doughnut_summaries.py'
    spec = importlib.util.spec_from_file_location('examples_doughnut_summaries', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={'name': 'Doughnut summaries', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic complete additive category entries, independent filters and static duplicate grain.'},
        slug='doughnut-summaries', output_dir=str(output))
    module.add_sections(ctx)
    return ctx


@pytest.mark.parametrize('dataset', [None, 'bound'])
def test_opt_out_exact_baseline_payload_markup_and_positions(dataset):
    args = (frame(), 'category', 'value', 'Title', dataset, True, True, True)
    assert render(DoughnutChart(*args)) == render(baseline_class()(*args))
    parameters = inspect.signature(DoughnutChart).parameters
    assert parameters['header_summary'].kind == inspect.Parameter.KEYWORD_ONLY
    assert [n for n, p in parameters.items() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD] == [
        'df', 'label', 'value', 'title', 'dataset_id', 'static', 'cross_filter', 'show_labels']


@pytest.mark.parametrize('declaration', [
    {}, False, [], 'sum', {'stat': ['sum'], 'scope': 'all_categories'},
    {'stat': 'mean', 'scope': 'all_categories'}, {'stat': 'sum'},
    {**SUMMARY, 'scope': []}, {**SUMMARY, 'scope': pd.NA},
    {**SUMMARY, 'format': None}, {**SUMMARY, 'format': pd.NA},
    {'stat': 'sum', 'scope': 'plotted_series'}, {**SUMMARY, 'series': 0},
    {**SUMMARY, 'label': ''}, {**SUMMARY, 'label': ' '}, {**SUMMARY, 'label': 3},
    {**SUMMARY, 'label': 'a' * 61}, {**SUMMARY, 'format': []}, {**SUMMARY, 'format': 'unknown'},
    {'stat': 'count', 'scope': 'all_categories', 'format': 'currency'},
    {'stat': 'count', 'scope': 'all_categories', 'format': 'percent'},
    {'stat': 'count', 'scope': 'all_categories', 'format': 'ratio'},
    {'stat': 'count', 'scope': 'all_categories', 'format': 'chips'},
])
def test_invalid_declarations_construction_render_and_mutation_validation(declaration):
    with pytest.raises(ValueError, match='header_summary'):
        DoughnutChart(frame(), 'category', 'value', 'Title', header_summary=declaration)
    chart = DoughnutChart(frame(), 'category', 'value', 'Title')
    chart.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        render(chart)
    result = ValidationResult(slug='mutation')
    _check_chart_options([(chart, 'Mutation')], result)
    assert [(c.id, c.section) for c in result.checks] == [('chart-summary-invalid', 'Mutation')]


@pytest.mark.parametrize('chart_format,override', [('percent', 'number'), ('ratio', 'currency'),
                                                  ('number', 'percent'), ('currency', 'ratio')])
def test_sum_unit_guard_cannot_be_bypassed(chart_format, override):
    with pytest.raises(ValueError, match='percent/ratio'):
        DoughnutChart(frame(), 'category', 'value', 'Title', value_format=chart_format,
                      header_summary={**SUMMARY, 'format': override})


def test_resolved_formats_labels_and_independent_context_center():
    for stat, label in [('sum', 'Total'), ('max', 'Largest category'), ('count', 'Category entries')]:
        html, cfg = render(DoughnutChart(frame(), 'category', 'value', 'Title', value_format='currency',
            precision=3, center_total=True, header_context='<Context>',
            header_summary={'stat': stat, 'scope': 'all_categories'}))
        assert cfg['header_summary'] == {'stat': stat, 'scope': 'all_categories', 'label': label,
                                         'format': 'number' if stat == 'count' else 'currency'}
        assert cfg['center_total'] and cfg['precision'] == 3
        assert '&lt;Context&gt;' in html and '<Context>' not in html
    html, cfg = render(DoughnutChart(frame(), 'category', 'value', 'Title',
        header_summary={**SUMMARY, 'label': '<img src=x onerror=alert(1)>'}))
    assert '<img' not in html
    assert cfg['header_summary']['label'] == '<img src=x onerror=alert(1)>'


def test_native_examples_zero_warn_fail_and_report_mutation(tmp_path):
    ctx = example_context(tmp_path)
    result = validate_report(ctx, details=compute_details(ctx))
    assert [(c.id, c.message) for c in result.checks if c.level in {'warn', 'fail'}] == []
    chart = DoughnutChart(frame(), 'category', 'value', 'Mutated', static=True)
    ctx.add_section('Mutation', [chart])
    chart.header_summary = {}
    result = validate_report(ctx, details=compute_details(ctx))
    assert any(c.id == 'chart-summary-invalid' and c.level == 'fail' for c in result.checks)


def test_runtime_entry_arithmetic_and_original_input_guards():
    source = (ROOT / 'trellum/static/js/components/doughnut.js').read_text()
    # Run the actual reducer without substituting a report renderer.
    script = "global.window={_fwRenderers:{}};\n" + source + r'''
const probe=(data,original=data)=>['sum','max','count'].map(stat=>_fwDoughnutSummaryResult(data,original,{stat}));
const out={duplicates:probe([10,20,30]),zero:probe([0,0,0]),empty:probe([]),
  missing:probe([10],[10,null]),absent:probe([10],[10,undefined]),
  strings:probe([10],[10,'2']),boolean:probe([1],[true]),signed:probe([10],[20,-10]),
  nonfinite:probe([10],[Infinity]),nan:probe([10],[NaN]),
  overflow:probe([1e308,1e308]),groupOverflow:probe([Infinity],[1e308,1e308])};
process.stdout.write(JSON.stringify(out));
'''
    out = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    assert [v['value'] for v in out['duplicates']] == [60, 30, 3]
    assert [v['value'] for v in out['zero']] == [0, 0, 3]
    assert [v['value'] for v in out['empty']] == [None, None, 0]
    for case in ('missing', 'absent', 'strings', 'boolean', 'signed', 'nonfinite', 'nan', 'groupOverflow'):
        assert [v['value'] for v in out[case]] == [None, None, 1]
        assert out[case][0]['reason'] and out[case][1]['reason']
    assert [v['value'] for v in out['overflow']] == [None, 1e308, 2]
