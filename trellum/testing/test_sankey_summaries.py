"""Declarations, historical opt-out and independent supplied-population oracles."""
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

from trellum.components import RenderContext, SankeyChart
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '1cc4eee8a83e335e97aa188d5a0a27451e1c01a5'
SUMMARY = {'stat': 'sum', 'scope': 'complete_paths'}


def examples():
    path = ROOT / 'trellum/demo/reports/component-showcase/examples_sankey_summaries.py'
    spec = importlib.util.spec_from_file_location('sankey_summary_examples', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chart(**options):
    e = examples()
    return e.paths(e.facts(), 'Supplied paths', **options)


def render(c):
    ctx = RenderContext(DEFAULT_THEME)
    html = c.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def baseline_class():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/sankey.py'], cwd=ROOT, text=True)
    module = types.ModuleType('_sankey_summary_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, 'baseline/sankey.py', 'exec'), module.__dict__)  # noqa: S102 - Trusted local Git source for exact historical compatibility proof.
    return module.SankeyChart


def example_context(output):
    ctx = ReportContext(config={'name': 'Sankey summaries', 'title': 'Declared supplied populations',
        'studio': 'demo', 'category': 'Components', 'theme': 'trellum light', 'annotations': False,
        'description': 'Synthetic complete path and independent adjacent transition observations with declared weight units, grouped-entry summaries and independent filters.'},
        slug='sankey-summaries', output_dir=str(output))
    examples().add_sections(ctx)
    return ctx


@pytest.mark.parametrize('mode', ['paths', 'edges'])
def test_off_historical_config_markup_and_keyword_signature(mode):
    e = examples()
    current = chart(static=True, header_context='<context>') if mode == 'paths' else e.edges(e.edge_facts(), 'Supplied edges', static=True)
    args = {name: getattr(current, name) for name, p in inspect.signature(baseline_class()).parameters.items()}
    old = baseline_class()(**args)
    assert render(current) == render(old)
    assert current.snapshot(current.df) == old.snapshot(old.df)
    del current.header_summary
    assert render(current) == render(old)
    assert inspect.signature(SankeyChart).parameters['header_summary'].kind == inspect.Parameter.KEYWORD_ONLY
    assert [n for n,p in inspect.signature(SankeyChart).parameters.items() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD] == ['df','title']


BAD = [False, True, [], (), 'sum', 1, {},
    *[{**SUMMARY,'stat': v} for v in (None, [], {}, pd.NA, True, 'mean', 'min')],
    *[{**SUMMARY,'scope': v} for v in (None, [], {}, pd.NA, True, 'adjacent_boundary', 'category')],
    *[{**SUMMARY,'format': v} for v in (None, [], {}, pd.NA, True, 'percent', 'ratio')],
    *[{**SUMMARY,'label': v} for v in (None, [], True, '', ' ', 'x'*61)],
    *[{**SUMMARY,k: None} for k in ('extra','from_stage','to_stage')],
    {'stat':'count','scope':'complete_paths','format':'currency'},
    {'stat':'count','scope':'complete_paths','format':'chips'}]


@pytest.mark.parametrize('declaration', BAD)
def test_reject_constructor_mutated_config_render_and_actual_shared_dispatch(declaration, tmp_path):
    with pytest.raises(ValueError, match='header_summary'):
        chart(header_summary=declaration)
    c = chart(static=True)
    c.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        c.config()
    with pytest.raises(ValueError, match='header_summary'):
        render(c)
    ctx = ReportContext(config={'name':'Mutated Sankey'},slug='bad-summary',output_dir=str(tmp_path))
    ctx.add_section('Declared summary', [c])
    result = validate_report(ctx)
    failures = [r for r in result.checks if r.id == 'sankey-invalid']
    assert len(failures) == 1 and failures[0].level == 'fail'
    assert 'header_summary' in failures[0].message


@pytest.mark.parametrize('extra', [
    {}, {'from_stage':'w1'}, {'to_stage':'w2'}, {'from_stage':'5 October','to_stage':'12 October'},
    *[{'from_stage': a,'to_stage': b} for a,b in [('w2','w1'),('w1','w3'),('w2','w2'),('bad','w2'),([], 'w2'),(True,'w2'),('w1',{}),(pd.NA,'w2')]],
])
def test_exact_ordered_boundary_keys(extra):
    e = examples()
    declaration = {'stat':'sum','scope':'adjacent_boundary',**extra}
    with pytest.raises(ValueError, match='header_summary'):
        e.edges(e.edge_facts(), 'Boundary', header_summary=declaration)
    c = e.edges(e.edge_facts(), 'Boundary')
    c.header_summary = declaration
    with pytest.raises(ValueError, match='header_summary'):
        c.config()
    with pytest.raises(ValueError, match='header_summary'):
        render(c)


@pytest.mark.parametrize('mode', ['paths','edges'])
@pytest.mark.parametrize('stat', ['sum','max','count'])
def test_defaults_formats_no_payload_or_graph_changes(mode, stat):
    e = examples()
    c = chart() if mode == 'paths' else e.edges(e.edge_facts(), 'Edges')
    off_html, off = render(c)
    original_graph = c.snapshot(c.df)
    declaration = e.declaration(stat, None if mode == 'paths' else 2)
    c.header_summary = declaration
    _, on = render(c)
    assert {k:v for k,v in on.items() if k != 'header_summary'} == off
    assert c.snapshot(c.df) == original_graph
    expected = ({'sum':'Path total','max':'Largest path','count':'Path entries'} if mode == 'paths' else
                {'sum':'Boundary total','max':'Largest edge','count':'Edge entries'})[stat]
    assert on['header_summary'] == {**declaration,'label':expected,'format':'number'}
    for fmt in (('number','plain') if stat == 'count' else ('number','currency','chips','plain')):
        c.header_summary = {**declaration,'format':fmt,'label':'<safe> "quoted"\nsummary'}
        assert c.config()['header_summary']['format'] == fmt
    assert declaration == e.declaration(stat,None if mode == 'paths' else 2)


def test_actual_runtime_graphs_match_independent_oracle_and_unchanged_admission():
    e = examples()
    cases = []
    for mode, df, column, selections in [('paths',e.facts(),'platform',[None,'iOS','Android','absent']),
                                        ('edges',e.edge_facts(),'channel',[None,'Store','Web','absent'])]:
        c = chart() if mode == 'paths' else e.edges(df,'Edges')
        for selection in selections:
            selected = df if selection is None else df[df[column] == selection]
            for stat in ('sum','max','count'):
                for boundary in ([None] if mode == 'paths' else [1,2,3]):
                    c.header_summary = e.declaration(stat,boundary)
                    cfg = render(c)[1]
                    cfg['rows'] = c.snapshot(selected)['rows']
                    # Independent oracle: group source facts by their declared identity.
                    grouped = {}
                    for r in selected.to_dict('records'):
                        if mode == 'edges' and r['from_week'] != f'w{boundary}':
                            continue
                        key = tuple(r[f'w{i}'] for i in (1,2,3,4)) if mode == 'paths' else (r['from_category'],r['to_category'])
                        grouped[key] = grouped.get(key,0) + r['players']
                    value = len(grouped) if stat == 'count' else None if not grouped else sum(grouped.values()) if stat == 'sum' else max(grouped.values())
                    cases.append({'cfg':cfg,'expected':value})
    source = (ROOT/'trellum/static/js/components/sankey.js').read_text().replace(
        'window._fwRenderers.sankey =', 'window.testModel=model; window.testSummary=summaryConfig; window._fwRenderers.sankey =')
    script = 'global.window={_fwRenderers:{}};\n' + source + '''
const cases=JSON.parse(require('fs').readFileSync(0,'utf8'));
const outputs=cases.map(({cfg})=>{const g=window.testModel(cfg,cfg.rows,true),s=window.testSummary(cfg);
 const entries=cfg.mode==='paths'?g.paths:g.edges.filter(e=>e.stage===s.boundary);
 return {graph:g,value:s.stat==='count'?entries.length:!entries.length?null:s.stat==='sum'?(cfg.mode==='paths'?g.total:g.boundaries[s.boundary]):Math.max(...entries.map(e=>cfg.mode==='paths'?e.weight:e.flow))};});
process.stdout.write(JSON.stringify(outputs));
'''
    out = json.loads(subprocess.check_output(['node','-e',script],input=json.dumps(cases),text=True))
    assert [r['value'] for r in out] == [c['expected'] for c in cases]
    assert out[0]['graph']['rowCount'] == 9 and len(out[0]['graph']['paths']) == 8
    # Bad data cannot be admitted even for a count declaration.
    c = chart(header_summary=e.declaration('count'))
    for value in (True,None,-1,float('inf'),2**53):
        bad = c.df.astype(object)
        bad.loc[0,'players'] = value
        with pytest.raises(ValueError):
            c.snapshot(bad)


def test_native_examples_diagnostics(tmp_path):
    ctx = example_context(tmp_path)
    details = compute_details(ctx)
    result = validate_report(ctx,details=details)
    assert [(c.id,c.message) for c in result.checks if c.level in {'warn','fail'}] == []
    assert len(ctx.sections) == 4
