"""Declared category-color authoring contracts and collision-safe resolution."""
from __future__ import annotations

import copy
import dataclasses
import inspect
import json

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import DataSource, DoughnutChart, FilterBar, LineChart, StackedBar
from trellum.components.base import RenderContext
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME

DECLARATION = {"domain": "acquisition", "column": "channel", "slots": {"UA": 0, "Retargeting": 2}}
FRAME = pd.DataFrame({"day": ["2026-09-01", "2026-09-02"], "channel": ["UA", "Retargeting"], "value": [21, 17]})


def chart(kind, **options):
    if kind is DoughnutChart:
        return kind(FRAME, "channel", "value", "Test", **options)
    if kind is LineChart:
        return kind(FRAME, "day", "value", "Test", **options)
    return kind(FRAME, "day", ["value"], "Test", **options)


def config(component, ctx=None):
    ctx = ctx or RenderContext(DEFAULT_THEME)
    html = component.render_html(ctx)
    return html, list(ctx.component_data.values())[-1]


@pytest.mark.parametrize("kind", [LineChart, StackedBar, DoughnutChart])
def test_keyword_only_subclasses_legacy_equivalence_and_no_mutation(kind):
    @dataclasses.dataclass
    class Child(kind):
        extra: str = "child"
    assert inspect.signature(kind).parameters['category_colors'].kind == inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(Child).parameters['extra'].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert config(chart(kind)) == config(chart(kind, category_colors=None))
    binding = {'dataset_id': 'explicit', 'static': True}
    if kind is not DoughnutChart:
        binding['stack_by'] = 'channel'
    declaration = copy.deepcopy(DECLARATION)
    before = copy.deepcopy(declaration)
    component = chart(kind, category_colors=declaration, **binding)
    _, cfg = config(component)
    assert declaration == before and cfg['category_colors'] == before
    cfg['category_colors']['slots']['UA'] = 9
    assert declaration == before
    assert cfg['dataset_id'] == 'explicit'
    declaration['slots']['UA'] = True
    with pytest.raises(ValueError, match='category_colors'):
        config(component)


BAD = [False, [], '', {}, {'unknown': 1},
       {**DECLARATION, 'domain': ''}, {**DECLARATION, 'domain': '  '},
       {**DECLARATION, 'column': None}, {**DECLARATION, 'column': 3},
       {**DECLARATION, 'slots': {}}, {**DECLARATION, 'slots': []},
       *[{**DECLARATION, 'slots': {'UA': slot}} for slot in (True, -1, 2**31, 1.0, '0', None)],
       {**DECLARATION, 'slots': {1: 0}}, {**DECLARATION, 'overrides': None},
       {**DECLARATION, 'overrides': {'Unknown': '#b1457b'}},
       *[{**DECLARATION, 'overrides': {'UA': color}} for color in ('red', '#123', '#12345678', '123456', '#gg0000', 1)],
       {**DECLARATION, 'extra': True}]


@pytest.mark.parametrize('kind', [LineChart, StackedBar, DoughnutChart])
@pytest.mark.parametrize('declaration', BAD)
def test_new_option_rejects_invalid_declarations_early(kind, declaration):
    with pytest.raises(ValueError, match='category_colors'):
        chart(kind, category_colors=declaration)


@pytest.mark.parametrize('kind', [LineChart, StackedBar])
def test_split_applicability_and_static_wide_rejection(kind):
    with pytest.raises(ValueError, match='static wide-column'):
        config(chart(kind, static=True, stack_by='channel', category_colors=DECLARATION))
    with pytest.raises(ValueError, match='configured stack_by'):
        config(chart(kind, dataset_id='d', category_colors=DECLARATION))
    _, cfg = config(chart(kind, dataset_id='d', stack_by_options={'Total': '', 'Channel': 'channel'},
                          category_colors=DECLARATION))
    assert cfg['category_colors'] == DECLARATION


def test_domain_definitions_ignore_order_column_and_per_chart_overrides():
    ctx = RenderContext(DEFAULT_THEME)
    config(chart(LineChart, dataset_id='d', stack_by='channel', category_colors=DECLARATION), ctx)
    declaration = {'domain': 'acquisition', 'column': 'source',
                   'slots': {'Retargeting': 2, 'UA': 0}, 'overrides': {'UA': '#B1457B'}}
    config(LineChart(FRAME, 'day', 'value', dataset_id='e', stack_by='source', category_colors=declaration), ctx)
    for slots in ({'UA': 1, 'Retargeting': 2}, {'UA': 0}, {'UA': 0, 'Retargeting': 2, 'Extra': 3}):
        with pytest.raises(ValueError, match="Conflicting category_colors slots.*acquisition"):
            config(chart(DoughnutChart, category_colors={**DECLARATION, 'slots': slots}), ctx)
    config(chart(DoughnutChart, category_colors={**DECLARATION, 'domain': '__proto__', 'slots': {'': 2**31-1}}), ctx)
    config(chart(DoughnutChart, category_colors={**DECLARATION, 'domain': 'constructor', 'slots': {'toString': 2}}), ctx)
    ctx.reset()
    config(chart(DoughnutChart, category_colors={**DECLARATION, 'slots': {'UA': 9}}), ctx)
    with pytest.raises(ValueError, match='must equal label'):
        config(chart(DoughnutChart, category_colors={**DECLARATION, 'column': 'wrong'}))


def test_static_raw_category_keys_preserve_string_identity_and_duplicate_rows():
    frame = pd.DataFrame({'channel': pd.Series(['UA', 'UA', '', None, 1, '1'], dtype=object), 'value': [1, 2, 3, 4, 5, 6]})
    _, cfg = config(DoughnutChart(frame, 'channel', 'value', 'Raw', static=True,
                                category_colors={**DECLARATION, 'slots': {'UA': 0, '': 1, '1': 2}}))
    assert cfg['labels'] == ['UA', 'UA', '', None, 1, '1']
    assert cfg['category_keys'] == ['UA', 'UA', '', None, None, '1']
    assert cfg['data'] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize('kind', [LineChart, StackedBar])
def test_late_dataset_binding_precedes_applicability(tmp_path, kind):
    ctx = ReportContext(config={'name': 'Auto binding'}, slug='auto', output_dir=str(tmp_path))
    component = chart(kind, stack_by='channel', category_colors=DECLARATION)
    ctx.add_section('Bound', [DataSource('auto', FRAME), FilterBar('auto', FRAME, filters=[{'column': 'channel', 'type': 'dropdown'}]), component])
    # Host/report binding can be supplied after construction and before render.
    component.dataset_id = 'auto'
    render_report(ctx, str(tmp_path), auto_refresh=False)
    data = json.loads((tmp_path / 'data.json').read_text())
    cfg = next(c for c in data['components'].values() if c.get('chartType'))
    assert cfg['dataset_id'] == 'auto' and cfg['category_colors'] == DECLARATION


def test_safe_resolver_raw_membership_and_finite_palette():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.add_script_tag(content=load_js('runtime/color_registry.js'))
        result = page.evaluate('''() => {
            const slots=JSON.parse('{"__proto__":0,"constructor":2,"toString":3,"1":4,"":5,"UA":12}');
            const d={domain:'__proto__',column:'c',slots,overrides:JSON.parse('{"__proto__":"#b1457b"}')};
            const colors=['a','b','c','d','e','f','g','h','i','j'];
            const resolve=(key,m)=>window._fwCategoryColor(d,key,colors,m);
            const m=window._fwCategoryMembership([{c:1},{c:'1'},{c:''},{c:null},{c:'UA'},{c:'__proto__'}],'c');
            const hostile=['__proto__','constructor','toString'].map(k=>window._fwGetLabelColor('__proto__',k,colors));
            return {hostile,own:Object.keys(window._fwColorRegistry['__proto__']),
                mapped:['__proto__','constructor','toString','1','',1,null,'Unknown','UA'].map(k=>resolve(k)),
                mixed:['1','','UA','__proto__'].map(k=>resolve(k,m)),
                repeated:window._fwGetLabelColor('__proto__','constructor',colors)};
        }''')
        browser.close()
    assert result == {'hostile': ['a', 'b', 'c'], 'own': ['__proto__', 'constructor', 'toString'],
                      'mapped': ['#b1457b', 'c', 'd', 'e', 'f', None, None, None, 'c'],
                      'mixed': [None, None, 'c', '#b1457b'], 'repeated': 'b'}
