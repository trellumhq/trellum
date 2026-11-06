"""Schema, effective snapshot admission and native CohortTable payloads."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trellum.components.base import RenderContext
from trellum.components.cohort_table import CohortTable, MAX_BYTES
from trellum.components.filterable import DataSource, FilterBar, LiveDataSource, ScopedDataSource
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report
from trellum.validation.checks.cohort_table import check_cohort_tables
from trellum.validation.result import ValidationResult
from trellum.validation.walk import _all_components

EXAMPLES_PATH = Path(__file__).resolve().parents[1] / 'demo/reports/component-showcase/examples_cohorts.py'
spec = importlib.util.spec_from_file_location('cohort_examples', EXAMPLES_PATH)
examples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(examples)


def make(frame=None, title="", **kwargs):
    return examples.cohort_table(examples.cohort_frame() if frame is None else frame, title, **kwargs)


def diagnostic(ctx):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    comps = _all_components(ctx)
    ds_map = {c.id: c for c, _ in comps if isinstance(c, (DataSource, ScopedDataSource, LiveDataSource))}
    check_cohort_tables(comps, ds_map, result)
    result.ran('cohort tables (explicit pending integration hook)')
    return details, result


def test_native_examples_diagnostics_and_payload(tmp_path):
    ctx = ReportContext({'name': 'Cohort snapshots', 'studio': 'test', 'category': 'Components',
                         'description': 'Independent precomputed synthetic cohort observations and supplied forecasts.'},
                        'cohorts', str(tmp_path))
    examples.add_sections(ctx)
    details, result = diagnostic(ctx)
    assert result.summary['warn'] == result.summary['fail'] == 0, result.to_dict()
    assert any(r['chart_kind'] == 'CohortTable' for r in details['filter_matrix'])
    cfg = make(forecast='forecast', forecast_metadata=examples.FORECAST).snapshot(examples.cohort_frame())
    assert sum(r['state'] == 'observed' for r in cfg['data']) == 13
    assert cfg['data'][0]['observed'] == .34 and cfg['data'][0]['forecast'] == .37
    assert cfg['data'][6]['observed'] == 0
    assert cfg['data'][14]['observed'] is None and cfg['data'][14]['state'] == 'unavailable'
    assert cfg['data'][22]['observed'] is None and cfg['data'][22]['forecast'] is None
    assert json.loads(json.dumps(cfg, allow_nan=False)) == cfg


@pytest.mark.parametrize('value', [True, np.bool_(False), '0', '1.5', float('inf'), float('-inf'), float('nan'), None])
def test_observed_requires_genuine_finite_number(value):
    df = examples.cohort_frame().astype(object)
    df.at[0, 'observed'] = value
    with pytest.raises(ValueError, match='finite genuine number'):
        make(df)


@pytest.mark.parametrize('column,value,match', [
    ('cohort', '', 'cohort identity'), ('cohort', 12, 'cohort identity'),
    ('month', '2025-9', 'YYYY-MM'), ('month', '0000-01', 'YYYY-MM'),
    ('month', '2025-13', 'YYYY-MM'), ('day', True, 'integer day'),
    ('day', 7.0, 'integer day'), ('day', '7', 'integer day'), ('day', -1, 'integer day'),
    ('state', 'forecast', 'observed_state'), ('state', None, 'observed_state'),
    ('reason', 42, 'reason'), ('cohort', 'x' * 2049, '2048'),
])
def test_invalid_cells(column, value, match):
    df = examples.cohort_frame().astype(object)
    df.at[0, column] = value
    with pytest.raises(ValueError, match=match):
        make(df)


def test_valid_signed_zero_missing_special_identity_and_no_mutation():
    df = examples.cohort_frame().astype(object)
    df.at[0, 'observed'] = -.12345678901234
    df.at[0, 'cohort'] = '__proto__, <script>"\n'
    before = df.copy(deep=True)
    table = make(df)
    cfg = table.snapshot(df)
    assert cfg['data'][0]['observed'] == -.12345678901234
    assert cfg['data'][0]['cohort'] == '__proto__, <script>"\n'
    pd.testing.assert_frame_equal(df, before)
    # Distinct identities may share the same display month.
    twin = df.iloc[[0]].copy()
    twin['cohort'] = 'another identity'
    make(pd.concat([df, twin], ignore_index=True))


def test_duplicates_inconsistent_month_and_nonobserved_values():
    df = examples.cohort_frame()
    with pytest.raises(ValueError, match='duplicate'):
        make(pd.concat([df, df.iloc[[0]]], ignore_index=True))
    changed = df.copy()
    changed.at[1, 'month'] = '2026-01'
    with pytest.raises(ValueError, match='consistent'):
        make(changed)
    changed = df.copy()
    changed.at[0, 'state'] = 'not_mature'
    with pytest.raises(ValueError, match='require null'):
        make(changed)


@pytest.mark.parametrize('kwargs,match', [
    ({'forecast': 'forecast'}, 'forecast_metadata'),
    ({'forecast_metadata': examples.FORECAST}, 'forecast column'),
    ({'forecast': 'forecast', 'forecast_metadata': {**examples.FORECAST, 'issued_at': '2026-02-04'}}, 'offset-aware'),
    ({'forecast': 'forecast', 'forecast_metadata': {**examples.FORECAST, 'scope': ' '}}, 'scope'),
    ({'milestone': {'threshold': True, 'label': 'Test'}}, 'finite genuine'),
    ({'milestone': {'threshold': float('inf'), 'label': 'Test'}}, 'finite genuine'),
    ({'milestone': {'threshold': 1, 'label': ''}}, 'milestone.label'),
    ({'value_format': 'unknown'}, 'value_format'),
    ({'dataset_id': 'x', 'static': True}, 'static=True'),
])
def test_options(kwargs, match):
    with pytest.raises(ValueError, match=match):
        make(**kwargs)


@pytest.mark.parametrize('patch,match', [
    ({'label': ''}, 'population.label'), ({'as_of': '2026-02-04T00:00:00'}, 'offset-aware'),
    ({'source_time': '2026-02-04'}, 'offset-aware'), ({'timezone': 'not/a/zone'}, 'IANA'),
])
def test_population(patch, match):
    table = make()
    table.population = {**examples.POPULATION, **patch}
    with pytest.raises(ValueError, match=match):
        table.snapshot(table.df)


@pytest.mark.parametrize('kind', ['cohorts', 'horizons', 'records', 'bytes'])
def test_bounds(kind):
    if kind == 'records':
        df = pd.concat([examples.cohort_frame()] * 257, ignore_index=True)
        match = '7680'
    elif kind == 'bytes':
        df = examples.cohort_frame()
        for i in range(70):
            df[f'text{i}'] = 'x' * 2048
        match = '4 MiB'
    else:
        count = 121 if kind == 'cohorts' else 65
        df = pd.concat([examples.cohort_frame().iloc[[0]]] * count, ignore_index=True)
        if kind == 'cohorts':
            df['cohort'] = [str(i) for i in range(count)]
        else:
            df['day'] = range(count)
        match = '120 cohorts or 64 horizons'
    with pytest.raises(ValueError, match=match):
        make(df)


@pytest.mark.heavy
def test_exact_snapshot_count_bound():
    df = pd.DataFrame([{'cohort': str(c), 'month': '2026-01', 'day': d, 'observed': 0,
                        'state': 'observed', 'reason': ''} for c in range(120) for d in range(64)])
    assert len(make(df).snapshot(df)['data']) == 7680


def validate_pair(table, sources, filters=()):
    comps = [(c, 'Cohort section') for c in [*sources, *filters, table]]
    result = ValidationResult('test')
    check_cohort_tables(comps, {s.id: s for s in sources}, result)
    return result


def test_validator_reads_every_effective_source_record_and_mutations():
    source = examples.cohort_frame()
    table = make(source.iloc[:2], dataset_id='source')
    result = validate_pair(table, [DataSource('source', source)])
    assert result.summary['fail'] == 0
    source.at[29, 'state'] = 'invented'
    result = validate_pair(table, [DataSource('source', source)])
    assert result.summary['fail'] == 1 and 'observed_state' in result.checks[0].message
    table.df.at[0, 'day'] = -1
    assert validate_pair(table, [DataSource('source', source)]).summary['fail'] == 1


@pytest.mark.parametrize('column', ['day', 'observed', 'state', 'forecast', 'reason', 'varying'])
def test_slicing_filters_rejected_even_if_no_active_selection(column):
    df = examples.cohort_frame()
    df['varying'] = range(len(df))
    table = make(df, dataset_id='scoped')
    result = validate_pair(table, [DataSource('base', df), ScopedDataSource('scoped', parent='base')],
                           [FilterBar('base', df, filters=[{'column': column}])])
    assert result.summary['fail'] == 1, result.to_dict()


def test_scoped_constant_filters_and_snapshot_propagation():
    df = examples.cohort_frame()
    table = make(df, dataset_id='scoped')
    sources = [DataSource('base', df), ScopedDataSource('scoped', parent='base'), DataSource('other', df)]
    bars = [FilterBar('base', df, filters=[{'column': 'platform'}]),
            FilterBar('other', df, filters=[{'column': 'platform'}], propagate_to={'base': {'platform': 'platform'}})]
    assert validate_pair(table, sources, bars).summary['fail'] == 0
    bars[-1].propagate_to = {'base': {'platform': 'day'}}
    assert validate_pair(table, sources, bars).summary['fail'] == 1


@pytest.mark.parametrize('mode', ['direct', 'ancestry', 'propagation', 'outgoing'])
def test_live_rejected(mode):
    df = examples.cohort_frame()
    live = LiveDataSource('live', query='test', df=df)
    sources = [live, DataSource('base', df), ScopedDataSource('scoped', parent='live')]
    table = make(df, dataset_id='live' if mode == 'direct' else 'scoped' if mode == 'ancestry' else 'base')
    bars = []
    if mode == 'propagation':
        bars = [FilterBar('live', df, filters=[{'column': 'platform'}], propagate_to={'base': {'platform': 'platform'}})]
    if mode == 'outgoing':
        bars = [FilterBar('base', df, filters=[{'column': 'platform'}], propagate_to={'live': {'platform': 'platform'}})]
    assert validate_pair(table, sources, bars).summary['fail'] == 1


def test_source_binding_payload_uses_effective_snapshot_and_all_chunks():
    df = examples.cohort_frame()
    df['event_date'] = df['month'] + '-01'
    ctx = RenderContext(DEFAULT_THEME)
    DataSource('base', df, chunk_by='month', default_chunks=1).render_html(ctx)
    assert len(ctx._pending_chunks) == 4
    ScopedDataSource('scoped', parent='base').render_html(ctx)
    make(df.iloc[:1], dataset_id='scoped').render_html(ctx)
    cfg = list(ctx.component_data.values())[-1]
    assert len(cfg['data']) == len(df)
    assert {r['month'] for r in cfg['data']} == set(df['month'])
    assert cfg['dataset_id'] == 'scoped'
    assert len(json.dumps(cfg).encode()) < MAX_BYTES


def test_mutated_declaration_rechecked_at_render_and_missing_source():
    table = make()
    table.df.at[0, 'state'] = 'bad'
    with pytest.raises(ValueError, match='observed_state'):
        table.render_html(RenderContext(DEFAULT_THEME))
    with pytest.raises(ValueError, match='DataSource before'):
        make(dataset_id='missing').render_html(RenderContext(DEFAULT_THEME))


def test_empty_and_keyword_only():
    make(examples.cohort_frame().iloc[:0], static=True)
    with pytest.raises(TypeError):
        CohortTable(examples.cohort_frame(), '', 'cohort')


@pytest.mark.parametrize('value', [True, '0.25', float('inf'), float('-inf')])
def test_forecast_requires_genuine_finite_number(value):
    df = examples.cohort_frame().astype(object)
    df.at[0, 'forecast'] = value
    with pytest.raises(ValueError, match='finite genuine number'):
        make(df, forecast='forecast', forecast_metadata=examples.FORECAST)


def test_effective_missing_column_duplicate_and_oversize_fail():
    frame = examples.cohort_frame()
    table = make(frame.iloc[:1], dataset_id='source')
    for bad in (frame.drop(columns='observed'), pd.concat([frame, frame.iloc[[29]]]),
                pd.concat([frame] * 257)):
        result = validate_pair(table, [DataSource('source', bad)])
        assert result.summary['fail'] == 1, result.to_dict()


def test_numpy_milestone_and_snapshot_static_bound_parity():
    frame = examples.cohort_frame()
    static = make(frame, static=True, milestone={'threshold': np.int64(1), 'label': 'Sampled'})
    cfg = static.snapshot(frame)
    assert json.loads(json.dumps(cfg))['milestone']['threshold'] == 1
    ctx = RenderContext(DEFAULT_THEME)
    DataSource('same', frame).render_html(ctx)
    bound = make(frame, dataset_id='same', milestone={'threshold': 1, 'label': 'Sampled'})
    bound.render_html(ctx)
    cfg_bound = list(ctx.component_data.values())[-1]
    cfg_bound.pop('dataset_id')
    assert cfg_bound == cfg


@pytest.mark.parametrize('value', [2**53, 10**1000])
def test_unsafe_integer_precision_rejected_without_rounding(value):
    frame = examples.cohort_frame().astype(object)
    frame.at[0, 'observed'] = value
    with pytest.raises(ValueError, match='exact snapshot integer precision'):
        make(frame)


def test_month_can_also_be_the_cohort_identity():
    frame = examples.cohort_frame()
    table = CohortTable(frame, cohort='month', cohort_month='month', horizon='day',
                        observed='observed', observed_state='state', population=examples.POPULATION)
    assert len(table.snapshot(frame)['data']) == 30
