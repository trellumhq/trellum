"""Public options and genuine native live-result teaching report builds."""
from __future__ import annotations

import importlib.util
import inspect
import json
import sqlite3
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from trellum.assets import load_css, load_js
from trellum.components import (
    ComparisonTable,
    DataSource,
    DataTable,
    FilterBar,
    KpiRow,
    LiveDataSource,
    Panel,
    ScopedDataSource,
    Section,
)
from trellum.components.base import RenderContext
from trellum.data.query import disable_cache, enable_cache
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import resolve_theme
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '98d96192564f87568252c6779481c6234b5b848a'
EXAMPLE = ROOT / 'trellum/demo/reports/live-result-examples/generator.py'
spec = importlib.util.spec_from_file_location('native_live_result_examples', EXAMPLE)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def build_native(output, *, opt_out=False, historical=False, extra=True):
    """Use a real named SQLite source, declaration, diagnostics and validation."""
    output.mkdir(parents=True, exist_ok=True)
    database = output / 'synthetic.sqlite'
    sqlite3.connect(database).close()
    config = {
        'name': 'Live Result Examples', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic platform/day observations, returned USD values, native selection and local scope.',
        'data_sources': [{'name': 'demo_db', 'type': 'sqlite', 'path': str(database)}],
    }
    ctx = ReportContext(config=config, slug='live-result-examples', output_dir=str(output))
    disable_cache()
    try:
        example.LiveResultExamplesReport().generate(ctx)
        if extra:
            frame = ctx.declare_live_query('independent_result', example.SYNTHETIC_SQL,
                datasource='demo_db', params=[{'name': 'selection', 'type': 'enum', 'required': True,
                    'values': ['Current', 'Alternative', 'Empty']}], snapshot_params={'selection': 'Current'})
            source = LiveDataSource('independent_result', query='independent_result', df=frame,
                bindings=[{'column': 'selection', 'param': 'selection', 'filter_type': 'toggle'}])
            # The second source has no FilterBar; propagation drives its own query.
            controls = next(s for s in ctx.sections if any(isinstance(c, FilterBar) and c.dataset_id == 'live_result_rows' for c in s['components']))
            controls['components'].insert(0, source)
            next(c for c in controls['components'] if isinstance(c, FilterBar)).propagate_to = {'independent_result': {'selection': 'selection'}}
            ctx.add_section('Independent observations', [
                KpiRow([{'label': 'Independent USD', 'column': 'value', 'agg': 'sum'}], dataset_id='independent_result'),
                DataTable(frame, 'Independent rows', dataset_id='independent_result'),
            ])
            ctx.add_section('Nested owners', [Panel([
                KpiRow([{'label': 'Panel USD', 'column': 'value', 'agg': 'sum'}], dataset_id='live_result_rows'),
                DataTable(frame, 'Panel rows', dataset_id='live_result_rows'),
                Section('Inner section', [
                    KpiRow([{'label': 'Inner USD', 'column': 'value', 'agg': 'sum'}], dataset_id='live_result_rows'),
                    DataTable(frame, 'Inner rows', dataset_id='live_result_rows'),
                ]),
            ], title='Nested panel')])
            ctx.add_section('Lazy table owner', [
                DataTable(frame, 'Lazy rows', dataset_id='live_result_rows'),
                KpiRow([{'label': 'Lazy USD', 'column': 'value', 'agg': 'sum'}], dataset_id='live_result_rows'),
            ])
            ctx.add_section('Compiled rows', [
                DataSource('compiled', frame), ScopedDataSource('compiled_child', parent='compiled'),
                FilterBar('compiled_child', frame, filters=[{'type': 'toggle', 'column': 'platform', 'default': 'iOS'}]),
                KpiRow([{'label': 'Compiled USD', 'column': 'value', 'agg': 'sum'}], dataset_id='compiled_child'),
                DataTable(frame, 'Compiled rows', dataset_id='compiled_child'),
            ])
        def visit(components):
            for component in components:
                if isinstance(component, (KpiRow, DataTable)):
                    component.live_context = not opt_out
                visit(getattr(component, 'children', []))
        for section in ctx.sections:
            visit(section['components'])
        details = compute_details(ctx)
        validation = validate_report(ctx, details=details)
        (output / 'diagnostics.json').write_text(json.dumps(details, indent=2, default=str))
        (output / 'validation.json').write_text(json.dumps(validation.to_dict(), indent=2))
        assert validation.summary['warn'] == validation.summary['fail'] == 0, validation.to_dict()
        with ExitStack() as stack:
            if historical:
                js_names = ['components/kpi_row.js', 'components/data_table.js']
                before_js = {name: subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/{name}'], cwd=ROOT, text=True) for name in js_names}
                def before_css(name):
                    return '' if name == 'components/live_result_context.css' else load_css(name)
                def historical_js(name):
                    return '' if name == 'components/live_result_context.js' else before_js.get(name, load_js(name))
                for module in ['trellum.components.kpis', 'trellum.components.tables']:
                    stack.enter_context(patch(module + '.load_js', historical_js))
                    stack.enter_context(patch(module + '.load_css', before_css))
            render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
        return ctx.live_queries
    finally:
        ctx.close_connections()
        enable_cache()


def test_live_context_is_strict_keyword_only_and_legacy_payload_absent():
    for cls, kwargs in [(KpiRow, {'kpis': [{'label': 'Value', 'agg': 'sum', 'column': 'value'}]}),
                        (DataTable, {'df': pd.DataFrame({'value': [0]}), 'title': 'Value'})]:
        assert inspect.signature(cls).parameters['live_context'].kind is inspect.Parameter.KEYWORD_ONLY
        for option in [True, False]:
            component = cls(**kwargs, dataset_id='example', live_context=option)
            ctx = RenderContext(resolve_theme('trellum light'))
            component.render_html(ctx)
            assert ('live_context' in next(iter(ctx.component_data.values()))) is option
        for invalid in [1, None, 'true', []]:
            with pytest.raises(ValueError, match='live_context must be bool'):
                cls(**kwargs, live_context=invalid).render_html(RenderContext(resolve_theme('trellum light')))
        ctx = RenderContext(resolve_theme('trellum light'))
        cls(**kwargs, dataset_id='example', static=True).render_html(ctx)
        assert all('live_context' not in cfg for cfg in ctx.component_data.values())
    # No binding means no context marker; the ordinary static rendering is kept.
    ctx = RenderContext(resolve_theme('trellum light'))
    DataTable(pd.DataFrame({'value': [0]}), 'Static').render_html(ctx)
    assert all('live_context' not in cfg for cfg in ctx.component_data.values())
    ctx = RenderContext(resolve_theme('trellum light'))
    ComparisonTable(pd.DataFrame({'label': ['x'], 'value': [0]}), 'Comparison',
                    value_col='value', compare_cols=[], dataset_id='example').render_html(ctx)
    assert all('live_context' not in cfg for cfg in ctx.component_data.values())


def test_native_example_validates_and_executes_all_named_sqlite_selections(tmp_path):
    queries = build_native(tmp_path / 'example', extra=False)
    manifest = queries['live_result_example']
    conn = sqlite3.connect(tmp_path / 'example/synthetic.sqlite')
    try:
        for selection, values in [('Current', [10, 25, 0]), ('Alternative', [40, 60, 20]), ('Empty', [])]:
            cursor = conn.execute(manifest['sql'], {'selection': selection})
            rows = cursor.fetchall()
            assert [row[3] for row in rows] == values
            assert [description[0] for description in cursor.description] == ['selection', 'day', 'platform', 'value']
    finally:
        conn.close()
    data = json.loads((tmp_path / 'example/data.json').read_text())
    assert data['_ds_live_result_rows']['_data']
    assert 'synthetic WHERE' not in (tmp_path / 'example/index.html').read_text()
