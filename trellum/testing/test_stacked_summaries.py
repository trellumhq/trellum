"""Strict StackedBar declarations, original eligibility and native examples."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import inspect
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from trellum.components import RenderContext, StackedBar
from trellum.components.charts._stacked_summary import (
    stacked_summary_config, stacked_summary_input_reason, stacked_summary_option_problems,
)
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DefaultTheme
from trellum.validation import validate_report
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult
from trellum.validation.walk import _all_components

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'c519ebe960fa6c405108558678cedcb3ded3b6c4'
SUMMARY = {'stat': 'sum', 'scope': 'absolute_series', 'series': 'UA'}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def examples():
    return module(ROOT / 'trellum/demo/reports/component-showcase/examples_stacked_summaries.py', 'stacked_teaching')


def chart(**options):
    return StackedBar(pd.DataFrame({'day': ['B', 'A'], 'UA': [30, 60]}), 'day', ['UA'], 'Legacy title', **options)


def render(comp):
    ctx = RenderContext(DefaultTheme)
    return comp.render_html(ctx), next(iter(ctx.component_data.values()))


def context(output):
    ctx = ReportContext(config={'name': 'Stacked summaries', 'studio': 'test', 'category': 'Components',
        'description': 'Complete synthetic delivered additive event facts by date, platform and channel; original-unit selected bar summaries.'},
        slug='stacked-summaries', output_dir=str(output))
    examples().add_sections(ctx)
    return ctx


def validate(ctx):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    issues = [(c.id, c.level, c.section, c.message) for c in result.checks if c.level in {'warn', 'fail'}]
    assert not issues, issues
    return details, result


def test_keyword_only_actual_baseline_html_payload(tmp_path):
    assert inspect.signature(StackedBar).parameters['header_summary'].kind is inspect.Parameter.KEYWORD_ONLY
    source = tmp_path / 'baseline_bar.py'
    source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/bar.py'], cwd=ROOT))
    old = module(source, 'stacked_summary_baseline')
    for options in ({}, {'dataset_id': 'source', 'stack_by': 'UA', 'normalize_toggle': True},
                    {'height': 220, 'header_context': 'Context'}):
        comp = chart(**options)
        assert render(comp) == render(old.StackedBar(comp.df, comp.x, comp.y_cols, comp.title, **options))
        result = ValidationResult(slug='historical-stacks')
        _check_chart_options([(old.StackedBar(comp.df, comp.x, comp.y_cols, comp.title, **options), 'Stacks')], result)
        assert result.checks == []
        assert 'stacked_summary' not in render(comp)[1]
        assert 'data-chart-summary=' not in render(comp)[0]
    html, cfg = render(chart(header_summary=SUMMARY))
    assert 'Legacy title' not in html  # Preserve the legacy static omitted title.
    assert cfg['stacked_summary_input_reason'] == ''
    summary = cfg.pop('stacked_summary'); cfg.pop('stacked_summary_input_reason')
    assert cfg == render(chart())[1]
    assert summary['label'] == 'Total · UA'
    assert 'header_summary' not in cfg


BAD = [False, True, [], 'sum', {}, {'stat': 'sum'}, {**SUMMARY, 'extra': 0}]
for name, values in {'stat': [[], {}, True, None, 'median'], 'scope': [[], {}, True, None, 'plotted_series'],
                     'series': [[], {}, True, 0, None, '', ' ', 'x' * 121],
                     'format': [[], {}, True, None, 'decimal1'], 'label': [[], {}, True, None, '', ' ', 'x' * 61]}.items():
    BAD.extend({**SUMMARY, name: value} for value in values)


@pytest.mark.parametrize('bad', BAD)
def test_strict_construction_render_and_pure_mutation_diagnostics(bad):
    with pytest.raises(ValueError, match='StackedBar header_summary'):
        chart(header_summary=bad)
    comp = chart(); comp.header_summary = bad
    assert stacked_summary_option_problems(comp)[0][0] == 'chart-summary-invalid'
    result = ValidationResult(slug='stacked-summary')
    _check_chart_options([(comp, 'Stacks')], result)
    assert [(c.id, c.level, c.component, c.section) for c in result.checks] == [
        ('chart-summary-invalid', 'fail', 'StackedBar', 'Stacks')]
    with pytest.raises(ValueError, match='StackedBar header_summary'):
        render(comp)


@pytest.mark.parametrize('declared', ['number', 'currency', 'percent', 'chips', 'ratio', 'plain', 'unknown', [], None])
def test_formats_defaults_and_sum_guard(declared):
    comp = chart(value_format=declared, header_summary={**SUMMARY, 'stat': 'mean'})
    assert stacked_summary_config(comp)['stacked_summary']['format'] == (declared if isinstance(declared, str) and declared in ('number', 'currency', 'percent', 'chips', 'ratio', 'plain') else 'number')
    for fmt in ('number', 'currency', 'percent', 'chips', 'ratio', 'plain'):
        option = {**SUMMARY, 'format': fmt}
        if fmt in ('percent', 'ratio') or declared in ('percent', 'ratio'):
            with pytest.raises(ValueError, match='sum is undefined'):
                replace(comp, header_summary=option)
        else:
            assert stacked_summary_config(replace(comp, header_summary=option))['stacked_summary']['format'] == fmt


@pytest.mark.parametrize('value,valid', [(0, True), (-1, True), (.25, True), (None, False),
    ('10', False), (True, False), (float('nan'), False), (float('inf'), False),
    (2 ** 53, False), (2 ** 53 - 1, True), (-(2 ** 53), False)])
def test_static_original_values_before_legacy_cleaning(value, valid):
    comp = chart(header_summary=SUMMARY)
    comp.df = pd.DataFrame({'day': ['A'], 'UA': pd.Series([value], dtype=object)})
    assert bool(stacked_summary_input_reason(comp)) is not valid
    assert render(comp)[1]['datasets'][0]['data'] == ([None] if isinstance(value, float) and value != value else [value])


def test_exact_captions_escaping_and_no_premature_identity_guess():
    option = {**SUMMARY, 'series': '<b>missing</b>', 'label': '<img src=x onerror=alert(1)>'}
    comp = chart(header_summary=option)
    html, cfg = render(comp)
    assert '<img' not in html
    assert cfg['stacked_summary']['series'] == option['series']
    assert not stacked_summary_option_problems(comp)  # Missing/ambiguous final identity is a runtime dash.
    assert 'stacked_summary_input_reason' not in render(replace(comp, dataset_id='raw'))[1]


def test_static_summary_only_guards_its_selected_series():
    comp = chart(header_summary=SUMMARY)
    comp.df['Other'] = pd.Series([None, '2'], dtype=object)
    comp.y_cols.append('Other')
    assert stacked_summary_input_reason(comp) == ''
    assert render(comp)[1]['stacked_summary_input_reason'] == ''
    comp.header_summary = {**SUMMARY, 'series': 'Other'}
    assert stacked_summary_input_reason(comp)


def test_native_examples_oracle_and_zero_diagnostics(tmp_path):
    teaching = examples(); frame = teaching.facts()
    assert len(frame) == 12 and frame.events.sum() == 540
    assert frame.groupby('channel').events.sum().to_dict() == {'Organic': 270, 'RT': 180, 'UA': 90}
    assert frame[frame.channel.eq('UA')].groupby('day').events.sum().tolist() == [30, 60]
    ctx = context(tmp_path)
    assert len(ctx.sections) == 4
    assert len({comp.dataset_id for comp, _ in _all_components(ctx) if type(comp).__name__ == 'FilterBar'}) == 4
    validate(ctx)
    ctx.close_connections()
