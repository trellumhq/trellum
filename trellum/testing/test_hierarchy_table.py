"""Hierarchy admission, source scope and generated-report diagnostics."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, HierarchyTable, ScopedDataSource
from trellum.components.base import RenderContext
from trellum.components.hierarchy_table import typed_dimension
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report
from trellum.validation.checks.hierarchy_table import check_hierarchy_tables
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]


def examples():
    spec = importlib.util.spec_from_file_location('examples_hierarchies', ROOT / 'trellum/demo/reports/component-showcase/examples_hierarchies.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame():
    return pd.DataFrame({'id':['a','b','c'], 'source':['A','A','B'], 'platform':['iOS','Android','iOS'],
                         'month':['Sep','Oct','Sep'], 'value':pd.Series([0.1,0.2,0], dtype=object)})


def table(df=None, **kwargs):
    return HierarchyTable(frame() if df is None else df, rows=['source','platform'], value='value', record_id='id', column='month', **kwargs)


def context(path):
    return ReportContext({'name':'Hierarchy tables', 'studio':'test', 'category':'Components',
                          'description':'Synthetic additive spend leaf facts by source, platform and month show complete parent totals, measured zero and explicit unavailable input policies.'},
                         'hierarchy-tables', str(path))


def diagnostics(ctx):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    return details, result


def test_central_validator_refuses_source_codec_identity_collisions(tmp_path):
    df = pd.DataFrame({
        "id": [str(i) for i in range(20)],
        "source": pd.Series([True, 1] * 10, dtype=object),
        "value": [1] * 20,
    })
    hierarchy = HierarchyTable(df, rows=["source"], value="value",
                               record_id="id", dataset_id="typed_source")
    ctx = context(tmp_path)
    ctx.add_section("Typed source", [
        DataSource("typed_source", df),
        FilterBar("typed_source", df, filters=[]),
        hierarchy,
    ])
    result = validate_report(ctx)
    assert any(c.id == "hierarchy-table-invalid" and "typed dimension identity" in c.message
               for c in result.checks)
    # The explicitly typed static path does not use the lossy source dictionary.
    static = HierarchyTable(df, rows=["source"], value="value", record_id="id", static=True)
    assert static.prepare(df)[0][0]["path"] != static.prepare(df)[0][1]["path"]
    df["source"] = pd.Series(["true", "1"] * 10, dtype=object)
    valid = ValidationResult("hierarchy")
    check_hierarchy_tables([(hierarchy, "Typed source")],
                           {"typed_source": DataSource("typed_source", df)}, valid)
    assert valid.summary["fail"] == 0


def test_native_examples_diagnostics_and_vendored_build(tmp_path):
    ctx = context(tmp_path)
    examples().add_sections(ctx)
    details, result = diagnostics(ctx)
    assert result.summary['warn'] == result.summary['fail'] == 0, result.to_dict()
    render_report(ctx, auto_refresh=False, validation=result, details=details)
    data = json.loads((tmp_path/'data.json').read_text())
    assert sum(c['type']=='hierarchy_table' for c in data['components'].values()) == 4
    assert details and (tmp_path/'index.html').is_file()


def test_keyword_contract_and_exact_typed_static_values():
    df = frame()
    df['value'] = pd.Series([Decimal('0.100000000000000001'), 2**53+1, None], dtype=object)
    t = table(df, expanded_paths=[['A']])
    ctx = RenderContext(DEFAULT_THEME)
    t.render_html(ctx)
    cfg = next(iter(ctx.component_data.values()))
    assert [f['input'] for f in cfg['facts']] == ['0.100000000000000001','9007199254740993',None]
    assert cfg['expanded'] == [[['string','A']]]
    with pytest.raises(TypeError):
        HierarchyTable(df, 'Title', ['source'])


@pytest.mark.parametrize('kwargs', [
    {'rows':[]}, {'rows':['source']*2}, {'rows':'source'}, {'rows':['source']*9},
    {'value_format':'ratio'}, {'value_format':'percent'}, {'value_format':'chips'},
    {'precision':True}, {'precision':7}, {'precision':-1}, {'known_subtotals':1},
    {'rows':['value']}, {'column':'source'}, {'expanded_paths':['A']},
    {'expanded_paths':[['Unknown']]}, {'expanded_paths':[['A','iOS','Sep']]},
])
def test_invalid_declarations(kwargs):
    options = {'rows':['source','platform'], 'value':'value', 'record_id':'id', 'column':'month'}
    options.update(kwargs)
    with pytest.raises(ValueError, match='HierarchyTable'):
        HierarchyTable(frame(), **options)


@pytest.mark.parametrize('value', [None, '', 12, False, ['x']])
def test_invalid_record_identity(value):
    df = frame(); df['id'] = df['id'].astype(object); df.at[0,'id'] = value
    with pytest.raises(ValueError, match='record_id'):
        table(df)


def test_duplicate_ids_reject_but_repeated_coordinate_facts_are_distinct():
    df = frame()
    df.loc[1,['source','platform','month']] = df.loc[0,['source','platform','month']].values
    assert len(table(df).prepare(df)[0]) == 3
    df.at[1,'id'] = 'a'
    with pytest.raises(ValueError, match='duplicate record_id'):
        table(df)


@pytest.mark.parametrize('value', [float('inf'), float('-inf'), 2**53, ['a'], {'a':1}, Decimal('1.2')])
def test_invalid_dimension_identity(value):
    with pytest.raises(ValueError, match='HierarchyTable'):
        typed_dimension(value)


def test_structural_paths_and_missing_identity():
    values = [None,'',True,'true',1,'1','__proto__','a|b','Missing','Empty']
    keys = [json.dumps(typed_dimension(v)) for v in values]
    assert len(set(keys)) == len(values)
    assert typed_dimension(float('nan')) == ['null',None]
    assert typed_dimension(1.0) == typed_dimension(1)


@pytest.mark.parametrize('scoped', [False, True])
def test_effective_source_checks_full_shape_ids_and_live_ancestry(scoped):
    from trellum.components import LiveDataSource
    df = frame()
    t = table(dataset_id='child' if scoped else 'root')
    def check(source, extras=()):
        sources = {'root':source}
        if scoped: sources['child'] = ScopedDataSource('child', parent='root')
        result = ValidationResult('hierarchy')
        check_hierarchy_tables([(t,'Hierarchy'),*extras], sources, result)
        return result
    assert check(DataSource('root',df)).summary['fail'] == 0
    for bad in (df.drop(columns='platform'), pd.concat([df,df.iloc[:1]])):
        assert check(DataSource('root',bad)).summary['fail'] == 1
    assert check(DataSource('root',df,chunk_by='month')).summary['fail'] == 1
    assert check(LiveDataSource('root',query='q',df=df)).summary['fail'] == 1
    live = LiveDataSource('live',query='q',df=df)
    sources = {'root':DataSource('root',df), 'live':live, 'mid':DataSource('mid',df)}
    if scoped: sources['child'] = ScopedDataSource('child',parent='root')
    comps = [(t,'Hierarchy'), (FilterBar('live',df,filters=[],propagate_to={'mid':{'source':'source'}}),'Live'),
             (FilterBar('mid',df,filters=[],propagate_to={'root':{'source':'source'}}),'Mid')]
    result = ValidationResult('hierarchy'); check_hierarchy_tables(comps,sources,result)
    assert result.summary['fail'] == 1 and 'live ancestry' in str(result.to_dict())


@pytest.mark.heavy
def test_source_bounds_are_admitted_before_tree_expansion():
    df = frame().iloc[[0]].copy()
    cases = []
    facts = pd.concat([df]*50001,ignore_index=True); facts['id'] = [str(i) for i in range(len(facts))]
    cases.append((facts,'50,000'))
    nodes = pd.concat([df]*2000,ignore_index=True); nodes['id'] = [str(i) for i in range(len(nodes))]; nodes['source'] = nodes['id']
    cases.append((nodes,'2,000'))
    columns = pd.concat([df]*33,ignore_index=True); columns['id'] = [str(i) for i in range(len(columns))]; columns['month'] = columns['id']
    cases.append((columns,'32 column'))
    cells = pd.concat([df]*1940,ignore_index=True); cells['id'] = [str(i) for i in range(len(cells))]
    cells['platform'] = cells['id']; cells['month'] = [str(i%32) for i in range(len(cells))]
    cases.append((cells,'64,000'))
    payload = pd.concat([df]*10000,ignore_index=True); payload['id'] = [str(i) for i in range(len(payload))]; payload['source'] = 'x'*1024
    cases.append((payload,'8 MiB'))
    for population, reason in cases:
        with pytest.raises(ValueError,match=reason): table(population)


@pytest.mark.parametrize('value',[Decimal('1.23'),10**100,-(2**64)])
def test_bound_values_outside_existing_source_serializer_refuse_with_static_guidance(value):
    df = frame(); df['value'] = pd.Series([value,0,0],dtype=object)
    t = table(df,dataset_id='root')
    result = ValidationResult('hierarchy')
    check_hierarchy_tables([(t,'Hierarchy')],{'root':DataSource('root',df)},result)
    assert result.summary['fail'] == 1
    assert 'static typed facts' in str(result.to_dict())
    # The same value remains a supported exact static hierarchy input.
    assert table(df,static=True).prepare(df)[0][0]['input'] == str(value)
