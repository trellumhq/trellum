"""Declarations, independent leaf oracles and genuine native report diagnostics."""
from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
import types
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import RenderContext, TreemapChart
from trellum.components.charts._treemap_summary import (
    treemap_summary_config,
    treemap_summary_option_problems,
)
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'c519ebe960fa6c405108558678cedcb3ded3b6c4'
SUMMARY = {'stat': 'sum', 'scope': 'leaf_entries'}


def examples():
    spec = importlib.util.spec_from_file_location('examples_treemap_summaries',
        ROOT / 'trellum/demo/reports/component-showcase/examples_treemap_summaries.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame():
    return examples().facts()


def render(chart):
    ctx = RenderContext(DEFAULT_THEME)
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/treemap.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_treemap_summary_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, 'baseline/treemap.py', 'exec'), module.__dict__)  # noqa: S102 - Trusted local Git source for exact historical compatibility proof.
    return module.TreemapChart


def example_context(output):
    ctx = ReportContext(config={'name': 'Treemap summaries', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic additive USD source facts by date platform and full hierarchy path; final leaf-entry reducers with independent filters and conservative eligibility.'},
        slug='treemap-summaries', output_dir=str(output))
    examples().add_sections(ctx)
    return ctx


@pytest.mark.parametrize('dataset', [None, 'bound'])
@pytest.mark.parametrize('colors', [None, 'source_color', 'numeric_color'])
def test_opt_out_exact_baseline_payload_markup_and_positions(dataset, colors):
    args = (frame(), ['parent', 'leaf'], 'amount', 'Title', colors, 'currency', dataset, True)
    for shares in (False, True):
        for context in (None, '<context>'):
            options = {'show_percentages': shares, 'header_context': context}
            assert render(TreemapChart(*args, **options)) == render(baseline_class()(*args, **options))
            result = ValidationResult(slug='historical-treemap')
            _check_chart_options([(baseline_class()(*args, **options), 'Treemaps')], result)
            assert result.checks == []
    params = inspect.signature(TreemapChart).parameters
    assert params['header_summary'].kind == inspect.Parameter.KEYWORD_ONLY
    assert [n for n, p in params.items() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD] == [
        'df', 'group_cols', 'value', 'title', 'color_col', 'value_format', 'dataset_id', 'static']


@pytest.mark.parametrize('declaration', [
    {}, False, True, [], (), 'mean', 1,
    *[{**SUMMARY, 'stat': v} for v in ([], {}, pd.NA, True, 'last', 'weighted_mean')],
    *[{**SUMMARY, 'scope': v} for v in (None, [], {}, pd.NA, True, 'all_categories', 'source_rows')],
    *[{**SUMMARY, k: v} for k, v in (('axis', 'x'), ('series', 0), ('label', ''), ('label', ' '), ('label', 4), ('label', 'x' * 61))],
    *[{**SUMMARY, 'format': v} for v in (None, [], {}, pd.NA, True, 'unknown', 'percent', 'ratio')],
    *[{'stat': 'count', 'scope': 'leaf_entries', 'format': v} for v in ('currency', 'percent', 'chips', 'ratio')],
])
def test_invalid_construction_render_mutation_and_dispatch(declaration):
    with pytest.raises(ValueError, match='header_summary'):
        TreemapChart(frame(), ['parent', 'leaf'], 'amount', header_summary=declaration)
    chart = TreemapChart(frame(), ['parent', 'leaf'], 'amount')
    chart.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        render(chart)
    assert [code for code, _ in treemap_summary_option_problems(chart)] == ['chart-summary-invalid']
    result = ValidationResult(slug='mutated-treemap')
    _check_chart_options([(chart, 'Treemaps')], result)
    assert [(c.id, c.level, c.component, c.section) for c in result.checks] == [
        ('chart-summary-invalid', 'fail', 'TreemapChart', 'Treemaps')]


@pytest.mark.parametrize('stat,label', [('sum', 'Leaf total'), ('count', 'Leaf entries'),
    ('mean', 'Mean leaf'), ('min', 'Minimum leaf'), ('max', 'Maximum leaf')])
def test_defaults_header_and_format_fallback(stat, label):
    for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain', None, [], pd.NA):
        options = {'stat': stat, 'scope': 'leaf_entries'}
        if stat == 'sum' and isinstance(fmt, str) and fmt in ('percent', 'ratio'):
            with pytest.raises(ValueError, match='additive'):
                TreemapChart(frame(), ['parent', 'leaf'], 'amount', value_format=fmt, header_summary=options)
            continue
        chart = TreemapChart(frame(), ['parent', 'leaf'], 'amount', title='Once',
            value_format=fmt, header_context='<safe>', header_summary=options)
        expected = fmt if stat != 'count' and isinstance(fmt, str) and fmt in ('number', 'currency', 'percent') else 'number'
        html, data = render(chart)
        assert data['header_summary'] == {**options, 'label': label, 'format': expected}
        assert html.count('Once') == 1 and '&lt;safe&gt;' in html
        assert 'data-chart-summary' in html and treemap_summary_option_problems(chart) == []
        assert options == {'stat': stat, 'scope': 'leaf_entries'}
    for fmt in ('number', 'currency', 'percent', 'ratio', 'chips', 'plain'):
        if stat == 'sum' and fmt in ('percent', 'ratio') or stat == 'count' and fmt not in ('number', 'plain'):
            continue
        chart.header_summary = {**options, 'format': fmt, 'label': '<escaped> label'}
        assert treemap_summary_config(chart)['header_summary']['format'] == fmt


@pytest.mark.parametrize('value,valid', [(0, True), (1.25, True), (9007199254740991, True),
    (None, False), (pd.NA, False), (Decimal('1.25'), False), (True, False), ('1.25', False),
    (float('inf'), False), (float('nan'), False), (-1, False), (9007199254740992, False), (10**400, False)])
def test_static_guard_preserves_pre_serialization_types(value, valid):
    df = pd.DataFrame({'parent': ['A'], 'leaf': ['B'], 'amount': [value]}, dtype=object)
    _, cfg = render(TreemapChart(df, ['parent', 'leaf'], 'amount', header_summary=SUMMARY))
    assert cfg['summary_guard'] == {'values_valid': valid}
    _, off = render(TreemapChart(df, ['parent', 'leaf'], 'amount'))
    assert {k: v for k, v in cfg.items() if k not in ('header_summary', 'summary_guard')} == off
    _, bound = render(TreemapChart(df, ['parent', 'leaf'], 'amount', dataset_id='raw', header_summary=SUMMARY))
    assert 'summary_guard' not in bound and 'rows' not in bound


def test_fixture_oracle_and_native_validation(tmp_path):
    df = frame()
    assert len(df) == 20
    leaves = df.groupby(['parent', 'leaf'])['amount'].sum()
    assert sorted(leaves.tolist()) == [90, 180, 270, 360]
    assert (leaves.sum(), len(leaves), leaves.mean(), leaves.min(), leaves.max()) == (900, 4, 225, 90, 360)
    filtered = df[(df.day == '2026-08-01') & (df.platform == 'iOS')]
    assert sorted(filtered.groupby(['parent', 'leaf']).amount.sum()) == [10, 20, 30, 40]
    ctx = example_context(tmp_path)
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert [c.to_dict() for c in result.checks if c.level in ('warn', 'fail')] == []


def test_real_javascript_reducer_membership_precision_and_count():
    # Independent synthetic plugin records test the pure arithmetic in real JS;
    # browser tests separately exercise genuine bundled-plugin leaf results.
    source = (ROOT / 'trellum/static/js/components/treemap.js').read_text()
    script = source.split('function _fwTreemapSummary(canvas', 1)[0] + '''
const cfg={group_cols:['p','l'],value_col:'v',dataset_id:'raw',header_summary:{stat:'sum'}};
const rows=[{v:4},{v:6},{v:20}],tree=[{p:'A',l:'X'},{p:'A',l:'X'},{p:'B',l:'X'}];
const leaves=[{v:10,_data:{children:tree.slice(0,2)}},{v:20,_data:{children:tree.slice(2)}}];
const results={};
for(const stat of ['sum','count','min','max','mean']){cfg.header_summary.stat=stat;results[stat]=_fwTreemapSummaryResult(leaves,tree,rows,cfg);}
cfg.header_summary.stat='min';results.missing=_fwTreemapSummaryResult(leaves,tree,[{v:null}],cfg);
results.invalids=[true,'4',null,undefined,NaN,Infinity,-1,Number.MAX_SAFE_INTEGER+1].map(v=>_fwTreemapSummaryResult(leaves,tree,[{v}],cfg));
results.blank=_fwTreemapSummaryResult(leaves,[{p:' ',l:'X'}],rows,cfg);
results.staticGuard=_fwTreemapSummaryResult(leaves,tree,rows,{...cfg,dataset_id:null,summary_guard:{values_valid:false}});
results.coverage=_fwTreemapSummaryResult(leaves.slice(0,1),tree,rows,cfg);
results.empty=_fwTreemapSummaryResult([],[],[],cfg);
const huge=[{v:Number.MAX_SAFE_INTEGER,_data:{children:[tree[0]]}},{v:Number.MAX_SAFE_INTEGER,_data:{children:[tree[1]]}}];
for(const stat of ['min','max','mean','sum']){cfg.header_summary.stat=stat;results['huge_'+stat]=_fwTreemapSummaryResult(huge,tree.slice(0,2),[{v:1},{v:1}],cfg);}
cfg.header_summary.stat='count';results.invalidCount=_fwTreemapSummaryResult(leaves,tree,[{v:null}],cfg);
results.emptyCount=_fwTreemapSummaryResult([],[],[],cfg);results.incomplete=_fwTreemapSummaryResult(null,[],[],cfg);
console.log(JSON.stringify(results));'''
    results = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    assert {s: results[s]['value'] for s in ('sum', 'count', 'min', 'max', 'mean')} == {'sum': 30, 'count': 2, 'min': 10, 'max': 20, 'mean': 15}
    for key in ('missing', 'coverage', 'empty', 'huge_sum', 'incomplete', 'blank', 'staticGuard'):
        assert results[key]['value'] is None and results[key]['reason']
    assert all(r['value'] is None and 'Every supplied value' in r['reason'] for r in results['invalids'])
    for stat in ('min', 'max', 'mean'):
        assert results['huge_' + stat] == {'value': 9007199254740991, 'reason': ''}
    assert results['invalidCount'] == {'value': 2, 'reason': ''}
    assert results['emptyCount'] == {'value': 0, 'reason': ''}
