"""Independent UTC arithmetic oracle and native projection admission."""
import importlib.util
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from trellum.assets import load_js
from trellum.components.period_comparison import validate_definition, validate_source
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report
from trellum.validation.checks.period_comparison import check_period_comparison

ROOT = Path(__file__).resolve().parents[2]


def example_context(output):
    spec = importlib.util.spec_from_file_location('examples_period', ROOT/'trellum/demo/reports/component-showcase/examples_period_comparison.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={'name': 'Period comparison', 'studio': 'test', 'category': 'Test',
        'description': 'Complete independent synthetic UTC daily activity, explicit inclusive periods and selected platforms.'},
        slug='period-comparison', output_dir=str(output))
    module.add_sections(ctx)
    return ctx, module


def config(module):
    v = module.projection()
    return {'dataset_id': v.id, 'parent_id': v.parent, 'toggle_id': v.toggle_id, 'default': v.default,
            'date_column': v.date_column, 'presets': v.presets, 'groups': v.groups, 'metrics': v.metrics,
            'source_columns': v.source_columns, 'outputs': [o.id for o in v.outputs]}


def node(body):
    script = '''global.window=global;global.document={documentElement:{},getElementById:()=>({}),querySelectorAll:()=>[]};
        global.MutationObserver=class{observe(){} disconnect(){}};global._fwRenderers={};global.requestIdleCallback=fn=>fn();'''
    script += load_js('runtime/filter_engine.js') + load_js('components/metric_views.js') + load_js('components/period_comparison.js')
    output = subprocess.run(['node', '-e', script + body], text=True, capture_output=True, check=True)
    return json.loads(output.stdout)


def oracle(start, end, platform=None):
    """Enumerate independent formulas, never consume component metric declarations."""
    totals = dict(cost=0, installs=0, revenue=0, clicks=0)
    day, end = date.fromisoformat(start), date.fromisoformat(end)
    count = (end-day).days+1
    while day <= end:
        current = day.year == 2026 and day.month == 10
        u = (100 if current else 80) + day.day
        if platform != 'Android':
            totals['cost'] += 2*u
            totals['installs'] += u
            totals['revenue'] += (3 if current else 2.5)*u
            totals['clicks'] += (5 if current else 6)*u
        if platform != 'iOS':
            totals['cost'] += 2*u
            totals['installs'] += 2*u
            totals['revenue'] += (4 if current else 3)*u
            totals['clicks'] += (8 if current else 10)*u
        day += timedelta(days=1)
    return {'cost': totals['cost'], 'daily_cost': totals['cost']/count, 'cpi': totals['cost']/totals['installs'],
            'conversions': totals['installs'], 'daily_conversions': totals['installs']/count,
            'revenue': totals['revenue'], 'daily_revenue': totals['revenue']/count,
            'recovery': totals['revenue']/totals['cost'], 'click_conversion': totals['installs']/totals['clicks']}


@pytest.mark.parametrize('platform', [None, 'iOS', 'Android'])
@pytest.mark.parametrize('preset', ['WoW', 'MoM', 'YoY'])
def test_all_metrics_deltas_daily_vectors(tmp_path, platform, preset):
    _, m = example_context(tmp_path)
    cfg = config(m)
    rows = m.facts().to_dict('records')
    if platform:
        rows = [r for r in rows if r['platform'] == platform]
    out = node('console.log(JSON.stringify(_fwPeriodComparison.derive('+json.dumps(rows)+','+json.dumps(cfg)+','+json.dumps(preset)+',"")));')
    a, b = [oracle(*m.PRESETS[preset][side], platform) for side in ('A', 'B')]
    for row in out[m.RESULT]:
        key = row['metric']
        assert row['A'] == pytest.approx(a[key])
        assert row['B'] == pytest.approx(b[key])
        expected = (a[key]-b[key])*100 if key == 'click_conversion' else (a[key]-b[key])/b[key]
        assert row['change'] == pytest.approx(expected)
        assert not row['A_reason'] and not row['B_reason'] and not row['change_reason']
    assert [len(out[m.RESULT+'_group_'+g]) for g in m.GROUPS] == [3, 2, 2, 2]
    daily = out[m.RESULT+'_daily']
    for i, row in enumerate(daily):
        assert row['day'] == i+1
        for side in ('A', 'B'):
            start, end = map(date.fromisoformat, m.PRESETS[preset][side])
            original = start + timedelta(days=i)
            for key in ('cost', 'conversions', 'revenue', 'recovery'):
                if original > end:
                    assert row[key+'_'+side] is None and row[key+'_'+side+'_state'] == 'outside-period'
                    assert row[side+'_date'] is None
                else:
                    assert row[side+'_date'] == original.isoformat()
                    assert row[key+'_'+side] == pytest.approx(oracle(original.isoformat(), original.isoformat(), platform)[key])
    if preset == 'MoM' and platform is None:
        assert [a[k] for k in a] == pytest.approx([14384,464,4/3,10788,348,25172,812,1.75,3/13])
        assert [b[k] for k in b] == pytest.approx([11460,382,4/3,8595,286.5,15757.5,525.25,1.375,3/16])


def test_native_diagnostics_and_schemas(tmp_path):
    ctx, m = example_context(tmp_path)
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert 'period-comparison' in result.groups_run
    assert [(c.id, c.message) for c in result.checks if c.level in {'warn', 'fail'}] == []
    assert details['totals']['total_rows'] == 184
    derived = [d for d in details['datasets'] if d.get('kind') == 'derived_period_comparison']
    assert len(derived) == 6 and all(d['rows'] == d['size_bytes_inline'] == 0 for d in derived)
    assert all(f['status'] == 'active' for row in details['filter_matrix'] if (row['dataset_id'] or '').startswith(m.RESULT) for f in row['filters'])


@pytest.mark.parametrize('window', [['2026-02-29','2026-03-01'], ['2026-10-02','2026-10-01'],
    ['2026-01-01','2027-01-02'], ['2026-01-01T00:00:00Z','2026-01-02']])
def test_strict_daily_windows(tmp_path, window):
    _, m = example_context(tmp_path)
    v = m.projection()
    v.presets = {'Bad': {'A': window, 'B': window}}
    v.default='Bad'
    with pytest.raises(ValueError):
        validate_definition(v)


def test_leap_window_and_unavailable_siblings(tmp_path):
    _, m = example_context(tmp_path)
    cfg = config(m)
    cfg['presets'] = {'Leap': {'A':['2024-02-28','2024-02-29'], 'B':['2023-02-28','2023-03-01']}}
    cfg['default'] = 'Leap'
    rows = [{'date': d, 'cost': 0 if d.startswith('2023') else 2, 'installs': 1, 'revenue': None, 'clicks': 2}
            for d in ['2024-02-28','2024-02-29','2023-02-28','2023-03-01']]
    out = node('console.log(JSON.stringify(_fwPeriodComparison.derive('+json.dumps(rows)+','+json.dumps(cfg)+',"Leap","")));')
    metrics = {r['metric']:r for r in out[m.RESULT]}
    assert metrics['cost']['B'] == 0 and metrics['cost']['change'] is None and 'Zero baseline' in metrics['cost']['change_reason']
    assert metrics['revenue']['A'] is None and metrics['conversions']['A'] == 2
    rows.pop()
    out = node('console.log(JSON.stringify(_fwPeriodComparison.derive('+json.dumps(rows)+','+json.dumps(cfg)+',"Leap","")));')
    assert all(r['B'] is None and 'Incomplete period' in r['B_reason'] for r in out[m.RESULT])


@pytest.mark.heavy
def test_complete_source_bounds(tmp_path):
    _, m = example_context(tmp_path)
    v = m.projection()
    r = m.facts().to_dict('records')[0]
    validate_source(v, [r]*10000, r)
    with pytest.raises(ValueError, match='10,000'):
        validate_source(v, [r]*10001, r)
    v.date_column = 'date'
    rows = [{**r, 'date':'2026-10-01', 'cost': 1}]*10000
    # A long declared numeric-column name contributes bytes to relevant payload too.
    v.metrics = {'wide': {**v.metrics['cost'], 'definition':{'agg':'sum', 'column':'x'*128}}}
    rows = [{'date':'2026-10-01', 'x'*128:1}]*10000
    validate_source(v, rows, rows[0])
    v.metrics = {f'm{i}': {**v.metrics['wide'], 'definition': {'agg':'sum', 'column':str(i).zfill(3)+'x'*125}} for i in range(32)}
    row = {'date':'2026-10-01', **{str(i).zfill(3)+'x'*125:1 for i in range(32)}}
    with pytest.raises(ValueError, match='4 MiB'):
        validate_source(v, [row]*1100, row)


def test_invalid_limits_group_partitions_schema_and_precision(tmp_path):
    import copy
    from decimal import Decimal
    _, m=example_context(tmp_path)
    for mutation in (
        lambda v:setattr(v,'presets',{str(i):v.presets['MoM'] for i in range(17)}),
        lambda v:setattr(v,'metrics',{f'm{i}':v.metrics['cost'] for i in range(33)}),
        lambda v:setattr(v,'groups',{f'g{i}':'Group' for i in range(9)}),
        lambda v:v.groups.update(unused='Unused'),
        lambda v:v.metrics['cost'].update(precision=True),
        lambda v:v.metrics['cost'].update(unknown='expression'),
        lambda v:v.metrics['cost']['definition'].update(agg='expression'),
    ):
        v=copy.deepcopy(m.projection())
        mutation(v)
        with pytest.raises(ValueError):
            validate_definition(v)
    v=m.projection()
    row=m.facts().to_dict('records')[0]
    for invalid in (Decimal('1.1'), '1', True, 2**53):
        with pytest.raises(ValueError,match='native|unsafe'):
            validate_source(v,[{**row,'cost':invalid}],row)
    with pytest.raises(ValueError,match='missing'):
        validate_source(v,[row],['date','cost'])


def test_native_order_collision_chaining_and_ambiguous_context(tmp_path):
    import pandas as pd

    from trellum.components import DataSource, LineChart, Toggle
    _,m=example_context(tmp_path)
    # Public dispatch must reject malformed native source/control declarations.
    for components in (
        [m.projection(),DataSource(m.SOURCE,m.facts())],
        [DataSource(m.SOURCE,m.facts()),Toggle(m.PERIOD,list(m.PRESETS),'WoW'),m.projection()],
        [DataSource(m.SOURCE,m.facts()),Toggle(m.PERIOD,list(m.PRESETS),'MoM'),m.projection(),DataSource(m.RESULT+'_daily',m.facts())],
    ):
        ctx=ReportContext(config={'name':'Admission'},slug='admission',output_dir=str(tmp_path))
        ctx.add_section('Admission',components)
        result=validate_report(ctx, details=compute_details(ctx))
        assert any(c.level=='fail' and c.id.startswith('period-comparison') for c in result.checks)
    frame=pd.DataFrame({'x':[1,1],'v':[1,2],'date':['2026-10-01','2026-10-02'],'window':['A','A']})
    line=LineChart(frame,'x','v',dataset_id='facts',point_context=[{'series':0,'date_column':'date','window_column':'window'}])
    ctx=ReportContext(config={'name':'Ambiguous'},slug='ambiguous',output_dir=str(tmp_path))
    ctx.add_section('Admission',[DataSource('facts',frame),line])
    result=validate_report(ctx, details=compute_details(ctx))
    assert any(c.id=='line-chart-point-context-invalid' for c in result.checks)


def test_owned_validation_leaves_unrelated_optional_control_ids_alone(tmp_path):
    from trellum.components import TabGroup
    from trellum.validation.result import ValidationResult
    ctx=ReportContext(config={'name':'Ordinary controls'},slug='ordinary-controls',output_dir=str(tmp_path))
    ctx.add_section('Controls',[TabGroup([]),TabGroup([])])
    result=ValidationResult('ordinary-controls')
    check_period_comparison(ctx,result)
    assert not result.checks


def test_oversized_serialized_source_rejected_before_decoding(tmp_path):
    from trellum.components import DataSource, RenderContext
    from trellum.themes import DEFAULT_THEME
    _, module = example_context(tmp_path)
    ctx = RenderContext(DEFAULT_THEME)
    source = DataSource(module.SOURCE, module.facts())
    source.render_html(ctx)
    class UnreadRows(list):
        def __iter__(self):
            pytest.fail('Oversized source must fail before decoding')
    ctx.raw_data_blocks['_ds_' + module.SOURCE]['_data'] = UnreadRows([None] * 10001)
    with pytest.raises(ValueError, match='10,000'):
        module.projection().render_html(ctx)
