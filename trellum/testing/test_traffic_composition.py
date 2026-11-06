"""StackedBar shared-control API, baseline compatibility and teaching oracle."""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from trellum.components import LineChart, RenderContext, StackedBar, Toggle
from trellum.components.charts.bar import stack_option_problems
from trellum.themes import THEME_REGISTRY
from trellum.validation.checks.visibility import _check_toggle_visible
from trellum.validation.checks.chart_options import _check_chart_options
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '1c0c49710c5d073a78f83ab069fd517cd49c1632'


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def examples():
    return module(ROOT / 'trellum/demo/reports/component-showcase/examples_traffic.py', 'traffic_teaching')


def render(chart):
    ctx = RenderContext(THEME_REGISTRY['trellum light'])
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def chart(**kwargs):
    return StackedBar(pd.DataFrame({'day': ['2026-08-01'], 'value': [3]}), 'day', ['value'], 'Traffic', **kwargs)


def test_strict_keyword_only_api_and_actual_baseline_default_markup_payload(tmp_path):
    for name in ('show_controls', 'height'):
        assert inspect.signature(StackedBar).parameters[name].kind == inspect.Parameter.KEYWORD_ONLY
    source = tmp_path / 'baseline_bar.py'
    source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/bar.py'], cwd=ROOT))
    old = module(source, 'traffic_baseline_bar')
    for options in ({'static': True}, {'dataset_id': 'events'},
                    {'dataset_id': 'events', 'stack_by_options': {'Day': 'day'}, 'normalize_toggle': True},
                    {'dataset_id': 'events', 'stack_by_options': {'Day': 'day'}, 'normalize_toggle': True,
                     'stack_toggle_id': 'external', 'normalize_toggle_id': 'mode'}):
        current = chart(**options)
        historical = old.StackedBar(current.df, 'day', ['value'], 'Traffic', **options)
        assert render(current) == render(historical)
        assert render(chart(show_controls=True, height=None, **options)) == render(historical)
        result = ValidationResult(slug='historical')
        _check_chart_options([(historical, 'Traffic')], result)
        assert result.summary['fail'] == 0


@pytest.mark.parametrize('value', [True, False, 0, 127, 1201, 220.0, '220', [], {}])
def test_height_strict_and_mutation_diagnostic(value):
    with pytest.raises(ValueError, match='height'):
        chart(height=value)
    changed = chart()
    changed.height = value
    assert stack_option_problems(changed)[0][0] == 'stacked-bar-height-invalid'
    result = ValidationResult(slug='height')
    _check_chart_options([(changed, 'Traffic')], result)
    assert [(c.id, c.level, c.section) for c in result.checks] == [
        ('stacked-bar-height-invalid', 'fail', 'Traffic')]
    with pytest.raises(ValueError, match='height'):
        render(changed)


@pytest.mark.parametrize('value', [None, 0, 1, 'false', [], {}])
def test_controls_strict_and_mutation_diagnostic(value):
    with pytest.raises(ValueError, match='show_controls'):
        chart(show_controls=value)
    changed = chart()
    changed.show_controls = value
    result = ValidationResult(slug='mutated')
    _check_toggle_visible([(changed, 'Mutated')], result)
    assert any(c.id == 'stacked-bar-controls-invalid' for c in result.checks)


def test_shared_controls_omit_markup_keep_config_and_size_only_plot():
    options = {'dataset_id': 'events', 'stack_by_options': {'Day': 'day'}, 'normalize_toggle': True,
               'stack_toggle_id': 'breakdown', 'normalize_toggle_id': 'mode'}
    html, cfg = render(chart(**options, show_controls=False, height=220))
    assert 'fw-toggle-group' not in html
    assert cfg['stack_toggle_id'] == 'breakdown' and cfg['normalize_toggle_id'] == 'mode'
    assert cfg['stacked_bar_controls'] is True
    assert 'fw-chart-container fw-stacked-height' in html and '--fw-stacked-height:220px' in html
    assert '.fw-chart-container.fw-stacked-height' in StackedBar.css()
    for name in ('stack_toggle_id', 'normalize_toggle_id'):
        for value in (None, '', ' ', ' bad '):
            with pytest.raises(ValueError, match=name):
                chart(**{**options, name: value}, show_controls=False)
    # Static wide charts have no active controls, as before.
    html, cfg = render(chart(static=True, show_controls=False, normalize_toggle=True, height=128))
    assert 'fw-toggle-group' not in html and 'stacked_bar_controls' not in cfg


def validate_controls(owners, **options):
    comp = chart(dataset_id='events', stack_by_options={'Day': 'day'}, normalize_toggle=True,
                 stack_toggle_id='breakdown', normalize_toggle_id='mode', show_controls=False)
    for name, value in options.items():
        setattr(comp, name, value)
    result = ValidationResult(slug='controls')
    _check_toggle_visible([(c, 'Independent scope/pair') for c in [*owners, comp]], result)
    return result


def test_exact_native_owners_options_defaults_and_usage():
    owners = [Toggle('breakdown', ['Day']), Toggle('mode', ['Absolute', 'Percentage'])]
    result = validate_controls(owners)
    assert result.summary['fail'] == 0
    assert not any(c.id == 'toggle-unused' for c in result.checks)
    for bad in ([], owners + [Toggle('mode', ['Absolute', 'Percentage'])],
                [owners[0], Toggle('mode', ['Percentage', 'Absolute'])],
                [owners[0], Toggle('mode', [])],
                [owners[0], Toggle('mode', ['Absolute', 'Percentage'], default='Missing')],
                [Toggle('breakdown', ['Other']), owners[1]],
                owners + [chart(dataset_id='events', normalize_toggle=True, normalize_toggle_id='mode')]):
        result = validate_controls(bad)
        assert result.summary['fail'] > 0
    # The explicit opt-in restriction names inactive scopes rather than implying
    # a new global duplicate constraint for all Toggle/Visible reports.
    result = validate_controls(owners + [Toggle('mode', ['Absolute', 'Percentage'])])
    assert any('including inactive report scopes' in c.message for c in result.checks)
    result = ValidationResult(slug='legacy')
    _check_toggle_visible([(Toggle('legacy', ['A']), 'First'), (Toggle('legacy', ['A']), 'Second')], result)
    assert result.summary['fail'] == 0


def test_shared_controls_coexist_with_lines_and_reject_a_line_local_owner():
    owners = [Toggle('breakdown', ['Day']), Toggle('mode', ['Absolute', 'Percentage'])]
    line = LineChart(pd.DataFrame({'day': ['2026-08-01'], 'value': [3]}),
                     'day', ['value'], dataset_id='events')
    assert validate_controls([*owners, line]).summary['fail'] == 0
    line.stack_by_options = {'Day': 'day'}
    line.stack_toggle_id = 'breakdown'
    result = validate_controls([*owners, line])
    assert [c.id for c in result.checks if c.level == 'fail'] == [
        'stacked-bar-control-owner-invalid']


def test_complete_event_fixture_partition_and_independent_denominators():
    frame = examples().facts()
    assert len(frame) == 48 and not frame.isna().any().any()
    assert not frame.duplicated(['day', 'platform', 'channel', 'inactivity']).any()
    assert list(frame[['events', 'd7_revenue']].sum()) == [408, 480]
    assert frame.groupby('day')[['events', 'd7_revenue']].sum().values.tolist() == [[204, 240], [204, 240]]
    by_channel = frame.groupby('channel')[['events', 'd7_revenue']].sum().loc[['UA', 'RT', 'Organic']]
    assert by_channel.events.tolist() == [102, 90, 216]
    assert by_channel.d7_revenue.tolist() == [102, 270, 108]
    assert (by_channel.events / 408 * 100).tolist() == pytest.approx([25, 22.0588235294, 52.9411764706])
    assert (by_channel.d7_revenue / 480 * 100).tolist() == pytest.approx([21.25, 56.25, 22.5])
    assert frame[(frame.platform == 'iOS') & (frame.day == '2026-08-01')][['events', 'd7_revenue']].sum().tolist() == [68, 80]
    paid, organic = frame.channel.isin(['UA', 'RT']), frame.channel.eq('Organic')
    assert (paid ^ organic).all()
    assert frame[paid][['events', 'd7_revenue']].sum().tolist() == [192, 372]
    assert frame[organic][['events', 'd7_revenue']].sum().tolist() == [216, 108]
