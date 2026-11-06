"""Strict Combo declarations, historical opt-out parity and native diagnostics."""
from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import ComboChart, RenderContext
from trellum.components.charts._combo_summary import (
    combo_summary_config,
    combo_summary_input_reason,
    combo_summary_option_problems,
)
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DefaultTheme
from trellum.validation import validate_report
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult
from trellum.validation.walk import _all_components

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '316171b17a1631a62c393ea5739952cfc16fec11'
SUMMARY = {'stat': 'sum', 'scope': 'axis_series', 'axis': 'left', 'series': 'Daily total'}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def examples():
    return module(ROOT / 'trellum/demo/reports/component-showcase/examples_combo_summaries.py', 'combo_teaching')


def chart(**options):
    return ComboChart(pd.DataFrame({'day': ['2026-08-02', '2026-08-01'], 'spend': [60, 30], 'orders': [18, 9]}),
        'day', ['spend'], ['orders'], 'Legacy title', bar_labels=['Daily total'], line_labels=['Daily total'], **options)


def render(comp):
    ctx = RenderContext(DefaultTheme)
    return comp.render_html(ctx), next(iter(ctx.component_data.values()))


def context(output):
    ctx = ReportContext(config={'name': 'Combo summaries', 'studio': 'test', 'category': 'Components',
        'description': 'Complete additive synthetic spend, fees, orders and visits by day and platform; explicit axis-series summaries.'},
        slug='combo-summaries', output_dir=str(output))
    examples().add_sections(ctx)
    return ctx


def validate(ctx):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    issues = [(c.id, c.level, c.section, c.message) for c in result.checks if c.level in {'warn', 'fail'} and not c.suppressed]
    assert not issues, issues
    return details, result


def test_keyword_only_subclass_and_actual_baseline(tmp_path):
    @dataclass
    class Child(ComboChart):
        extra: str = 'child'
    assert inspect.signature(ComboChart).parameters['header_summary'].kind is inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(Child).parameters['extra'].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    source = tmp_path / 'baseline_bar.py'
    source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/bar.py'], cwd=ROOT))
    old = module(source, 'combo_summary_baseline')
    for opts in ({}, {'dataset_id': 'source', 'static': True}, {'height': 220, 'header_context': 'Context', 'month_context': True}):
        comp = chart(**opts)
        legacy = old.ComboChart(comp.df, comp.x, comp.bar_cols, comp.line_cols, comp.title,
            bar_labels=comp.bar_labels, line_labels=comp.line_labels, **opts)
        result = ValidationResult(slug='historical-combo')
        _check_chart_options([(legacy, 'Combo')], result)
        assert not result.checks
        assert render(comp) == render(legacy)
        assert 'combo_summary' not in render(comp)[1] and 'data-combo-summary=' not in render(comp)[0]
    html, cfg = render(chart(header_summary=SUMMARY))
    assert html.count('Legacy title') == 1 and 'data-combo-summary=' in html
    assert cfg.pop('combo_summary')['format'] == 'number'
    assert cfg.pop('combo_summary_input_reason') == ''
    assert cfg == render(chart())[1]


BAD = [False, True, [], 'sum', {}, {'stat': 'sum'}, {**SUMMARY, 'extra': 0}]
for name, values in {'stat': [[], {}, True, None, 'median'], 'scope': [[], {}, True, None, 'plotted_series'],
    'axis': [[], {}, True, None, 'y'], 'series': [[], {}, True, 0, None, '', ' ', 'x' * 121],
    'format': [[], {}, True, None, 'decimal1'], 'label': [[], {}, True, None, '', ' ', 'x' * 61]}.items():
    BAD.extend({**SUMMARY, name: value} for value in values)


@pytest.mark.parametrize('bad', BAD)
def test_construction_render_and_pure_mutation_validation(bad):
    with pytest.raises(ValueError, match='ComboChart header_summary'):
        chart(header_summary=bad)
    comp = chart()
    comp.header_summary = bad
    assert combo_summary_option_problems(comp)[0][0] == 'chart-summary-invalid'
    result = ValidationResult(slug='mutated-combo')
    _check_chart_options([(comp, 'Combo')], result)
    assert [(c.id, c.level, c.component, c.section) for c in result.checks] == [
        ('chart-summary-invalid', 'fail', 'ComboChart', 'Combo')]
    with pytest.raises(ValueError, match='ComboChart header_summary'):
        render(comp)


@pytest.mark.parametrize('axis', ['left', 'right'])
@pytest.mark.parametrize('bound', [False, True])
@pytest.mark.parametrize('declared', ['number', 'currency', 'percent', 'ratio', 'chips', 'plain', 'unknown', [], None])
def test_actual_formats_and_declared_sum_guard(axis, bound, declared):
    opts = {'bar_format' if axis == 'left' else 'line_format': declared, 'dataset_id': 'source' if bound else None}
    comp = chart(**opts, header_summary={**SUMMARY, 'axis': axis, 'stat': 'mean'})
    fallback = 'currency' if axis == 'left' else 'number'
    actual = (declared if isinstance(declared, str) and declared in ('number', 'currency', 'percent') else fallback) if bound else ('number' if axis == 'left' else 'currency')
    assert combo_summary_config(comp)['combo_summary']['format'] == actual
    for fmt in ('number', 'currency', 'percent', 'chips', 'ratio', 'plain'):
        option = {**SUMMARY, 'axis': axis, 'format': fmt}
        if fmt in ('percent', 'ratio') or declared in ('percent', 'ratio'):
            with pytest.raises(ValueError, match='sum is undefined'):
                replace(comp, header_summary=option)
        else:
            assert combo_summary_config(replace(comp, header_summary=option))['combo_summary']['format'] == fmt


@pytest.mark.parametrize('value,valid', [(0, True), (-1, True), (.25, True), (None, False), ('10', False), (True, False),
    (float('nan'), False), (float('inf'), False), (2 ** 53, False), (2 ** 53 - 1, True), (-(2 ** 53), False)])
def test_raw_static_selected_guard(value, valid):
    comp = chart(header_summary=SUMMARY)
    comp.df['spend'] = pd.Series([value, 30], dtype=object)
    assert bool(combo_summary_input_reason(comp)) is not valid
    comp.header_summary = {**SUMMARY, 'axis': 'right'}
    assert combo_summary_input_reason(comp) == ''


def test_static_final_caption_mapping_truncation_ambiguity_and_escaping():
    comp = chart(header_summary=SUMMARY)
    comp.df['other'] = [None, None]
    comp.bar_cols.append('other')
    assert combo_summary_input_reason(comp) == ''  # zip truncates unrelated source
    comp.bar_labels = ['Other']
    assert combo_summary_input_reason(comp) == ''  # unavailable identity, diagnosed later
    comp.bar_labels = ['Daily total', 'Daily total']
    assert combo_summary_input_reason(comp) == ''  # ambiguous, never silently first
    comp.header_summary = {**SUMMARY, 'label': '<img src=x onerror=alert(1)>', 'series': '<b>Missing</b>'}
    html, cfg = render(comp)
    assert '<img' not in html and '<b>Missing' not in html
    assert cfg['combo_summary']['series'] == '<b>Missing</b>'
    assert 'combo_summary_input_reason' not in render(replace(comp, dataset_id='explicit', static=True))[1]


def test_native_examples_oracle_and_zero_diagnostics(tmp_path):
    teaching = examples()
    frame = teaching.facts()
    assert len(frame) == 4
    grouped = frame.groupby('day').sum(numeric_only=True)
    assert grouped.spend.tolist() == [30, 60] and grouped.orders.tolist() == [9, 18]
    assert grouped.fees.sum() == 18 and grouped.visits.sum() == 180
    one = frame[frame.platform.eq('iOS') & frame.day.eq('2026-08-01')].iloc[0]
    assert one.spend == 10 and one.orders == 3
    ctx = context(tmp_path)
    assert len(ctx.sections) == 4
    assert len({comp.dataset_id for comp, _ in _all_components(ctx) if type(comp).__name__ == 'FilterBar'}) == 4
    validate(ctx)
    ctx.close_connections()
