"""Ordinary per-column display; exact raw sort/export and option-off compatibility."""
from __future__ import annotations
import csv
from decimal import Decimal
import inspect
import io
import json
from pathlib import Path
import subprocess
import sys
import types

import pandas as pd
import pytest

from trellum.components import DataSource, DataTable, MiniKpi, ComparisonTable, RenderContext
from trellum.validation.checks.metric_views import check_column_formats
from trellum.rendering.html_builder import render_report
from trellum.rendering.cdn import copy_vendor_tree
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME
from trellum.validation.result import ValidationResult
from trellum.testing.test_metric_views import ROOT
from trellum.testing.test_chart_header_summaries_browser import _route


def registered(table):
    ctx=RenderContext(DEFAULT_THEME)
    html=table.render_html(ctx)
    return html,next(iter(ctx.component_data.values()))


@pytest.mark.parametrize('option', [False,[], '',{'absent':{'format':'number'}},{'x':{}},{'x':{'format':'chips'}},
    {'x':{'format':[]}}, {'x':{'format':None}}, {'x':{'format':'number','extra':1}},
    *[{'x':{'format':'number','precision':v}} for v in (True,False,None,-1,13,2.0,'2')],
    {'x':{'format':'plain','precision':2}}, {1:{'format':'number'}}])
def test_strict_definition_and_mutation(option):
    frame=pd.DataFrame({'x':[1]})
    with pytest.raises(ValueError,match='column_formats'):
        DataTable(frame,'Bad',column_formats=option)
    table=DataTable(frame,'Mutated');table.column_formats=option
    with pytest.raises(ValueError,match='column_formats'):
        registered(table)
    result=ValidationResult(slug='formats');check_column_formats([(table,'Mutated')],result)
    assert [c.id for c in result.checks]==['datatable-column-formats-invalid']


def test_keyword_only_known_columns_copy_and_unsafe_integer():
    frame=pd.DataFrame({'x':[1],'hidden':[2]})
    with pytest.raises(ValueError,match='displayed'):
        DataTable(frame,'Hidden',columns=['x'],column_formats={'hidden':{'format':'number'}})
    option={'x':{'format':'currency','precision':2}}
    table=DataTable(frame,'Copied',column_formats=option);option['x']['precision']=True
    assert registered(table)[1]['columnFormats']=={'x':{'format':'currency','precision':2}}
    assert inspect.signature(DataTable).parameters['column_formats'].kind==inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError,match='column_formats'):
        ComparisonTable(frame,'Compare','x',[],column_formats={})
    with pytest.raises(ValueError,match='boolean input'):
        DataTable(pd.DataFrame({'x':[True]}),'Boolean',column_formats={'x':{'format':'plain'}})
    with pytest.raises(ValueError,match='unsafe integer'):
        DataTable(pd.DataFrame({'x':[2**53+1]}),'Unsafe',column_formats={'x':{'format':'number'}})


@pytest.mark.parametrize('bound',[False,True])
def test_exact_option_off_baseline_markup_payload_and_positions(bound):
    source=subprocess.check_output(['git','show','1c0c49710c5d073a78f83ab069fd517cd49c1632:trellum/components/tables.py'],cwd=ROOT,text=True)
    module=types.ModuleType('_column_formats_baseline');sys.modules[module.__name__]=module;exec(source,module.__dict__)
    frame=pd.DataFrame({'label':['A','B'],'value':[1.234,0]})
    args=(frame,'Legacy',20,True,None,'facts' if bound else None,False,None,None,True,None)
    baseline=registered(module.DataTable(*args))
    assert registered(DataTable(*args))==baseline
    assert registered(DataTable(*args,column_formats=None))==baseline
    assert registered(DataTable(*args,column_formats={}))==baseline


@pytest.mark.parametrize('bound',[False,True])
def test_native_formatted_search_numeric_sort_raw_csv_and_mini_parity(tmp_path,bound):
    from playwright.sync_api import sync_playwright
    frame=pd.DataFrame({'label':['A <safe>','B','C','Missing','Text','Decimal'],
        'amount':pd.Series([12/7,2.1,0,None,'001.234',Decimal('1.234567890123456789')],dtype=object),
        'recovery':pd.Series([1.0875,0.125,0,float('inf'),'<em>literal</em>','True'],dtype=object)})
    option={'amount':{'format':'currency','precision':2},'recovery':{'format':'percent','precision':2}}
    # Decimal is delivered as exact text, as ordinary DataSource does not support Decimal serialization.
    source_frame=frame.copy();source_frame.loc[5,'amount']=str(frame.loc[5,'amount'])
    ctx=ReportContext(config={'name':'Column formats','studio':'test','category':'Test',
        'description':'Synthetic numeric and exact textual cells verify ordinary table display without changing raw CSV values.'},slug='column-formats',output_dir=str(tmp_path))
    ctx.add_section('Formatting',[*([DataSource('facts',source_frame)] if bound else []),
        DataTable(source_frame if bound else frame,'Formatted',dataset_id='facts' if bound else None,
                  searchable=True,sortable=True,column_formats=option),
        MiniKpi('CPI',12/7,'currency',full_number=True,precision=2),
        MiniKpi('Recovery',1.0875,'percent',full_number=True,precision=2)])
    render_report(ctx,str(tmp_path),auto_refresh=False);copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();_route(page,tmp_path)
            page.goto('http://table-formats.test/index.html',wait_until='networkidle')
            table=page.locator('.fw-table-scroll').first
            page.wait_for_function('()=>document.querySelectorAll("tbody tr").length===6')
            cells=[row.locator('td').all_text_contents() for row in table.locator('tbody tr').all()]
            assert cells[0]==['A <safe>','$1.71','108.75%']
            assert page.locator('.fw-mini-val').all_text_contents()==cells[0][1:]
            assert cells[2]==['C','$0.00','0.00%']
            assert cells[3][1:]==['—','—']
            assert cells[4]==['Text','001.234','<em>literal</em>']
            assert cells[5][1]=='1.234567890123456789' and cells[5][2] in ('true','True')
            assert table.locator('em,safe').count()==0
            # Displayed percentage drives search; raw numeric fraction remains in the matching CSV.
            search=page.locator('input.fw-table-search');search.fill('108.75%')
            page.wait_for_function('()=>document.querySelectorAll("tbody tr").length===1')
            with page.expect_download() as info: page.get_by_title('Download CSV').click()
            path=tmp_path/'matching.csv';info.value.save_as(str(path))
            exported=list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            assert len(exported)==1 and float(exported[0]['amount'])==pytest.approx(12/7)
            assert float(exported[0]['recovery'])==1.0875
            search.fill('');page.wait_for_function('()=>document.querySelectorAll("tbody tr").length===6')
            table.locator('thead th').nth(2).click()
            with page.expect_download() as info: page.get_by_title('Download CSV').click()
            path=tmp_path/'sorted.csv';info.value.save_as(str(path))
            exported=list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            # Mixed text does not acquire numeric identity; native raw numeric-only columns sort numerically below.
            assert next(r for r in exported if r['label']=='Decimal')['amount']=='1.234567890123456789'
            if not bound:
                page.evaluate('''id=>_fwRenderers.table(id,{columns:['label','amount'],rows:[['Low','2'],['High','10']],rawRows:[['Low',2],['High',10]],numericColumns:['amount'],columnFormats:{amount:{format:'currency',precision:2}},sortable:true,maxRows:20,totalRows:2})''',table.get_attribute('id'))
            if bound:
                page.evaluate("fw.filterEngine.init('facts',[{label:'Low',amount:2,recovery:0.2},{label:'High',amount:10,recovery:0.1}]);fw.filterEngine.notify('facts')")
            table.locator('thead th').nth(1).click()
            with page.expect_download() as info: page.get_by_title('Download CSV').click()
            path=tmp_path/'numeric-sort.csv';info.value.save_as(str(path))
            exported=list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            assert [r['label'] for r in exported]==['High','Low']
            assert [r['amount'] for r in exported]==['10','2']
        finally: browser.close()


def test_table_local_helper_parity_and_workspace_comparison_exclusion():
    from trellum.assets import load_js
    from trellum.testing.test_metric_views import node
    # The local helper only needs existing named formatters, never a KPI renderer in a table build.
    script=load_js('runtime/formatters.js')+load_js('components/data_table_column_formats.js')+load_js('components/kpi_card.js')
    output=node(script+'''const output=[];
    for(const format of ['number','currency','percent','ratio']) for(const precision of [0,2,12])
      for(const value of [0,-12.345,1.0875,2520]) output.push([_fwTableColumnFormat(value,{format,precision}),_fwKpiFormat(value,format,true,precision)]);
    console.log(JSON.stringify(output));''')
    assert all(a==b for a,b in output)


from trellum.testing.test_js_runtime import browser_page, capture_page  # noqa: E402,F401


def test_formats_preserve_bars_rules_shading_totals_and_exclude_other_tables(capture_page):
    from trellum.assets import load_js
    output=capture_page.evaluate('''js=>{
        document.body.innerHTML='<div id="off"></div><div id="on"></div><div id="workspace"></div><div id="comparison"></div>';
        window._fwRenderers=window._fwRenderers||{};
        eval(js);
        const base={columns:['label','n'],rows:[['A','1'],['B','0.3']],rawRows:[['A',1.2],['B',0.3]],
            numericColumns:['n'],maxRows:20,totalRows:2,barColumn:1,
            conditionalFormats:{n:[{op:'>',value:0.8,color:'var(--accent-green)'}]},
            cellShading:{n:{target:'0.5',direction:'higher'}},
            totals:{n:{agg:'sum',format:'currency',precision:2}},totalsInputs:{n:['1.2','0.3']}};
        const formats={n:{format:'percent',precision:2}};
        _fwRenderers.table('off',base);_fwRenderers.table('on',{...base,columnFormats:formats});
        _fwRenderers.table('workspace',{...base,workspacePageSize:25,columnFormats:formats});
        _fwRenderers.table('comparison',{...base,comparison:{valueColumn:'n',compareColumns:[],showDivider:false},columnFormats:formats});
        const read=id=>{const root=document.getElementById(id);return {
            cells:Array.from(root.querySelectorAll('tbody tr')).map(r=>r.cells[1].textContent),
            bar:Array.from(root.querySelectorAll('.fw-bar-fill')).map(n=>n.style.width),
            color:Array.from(root.querySelectorAll('tbody td:nth-child(2)')).map(n=>n.style.color),
            shade:Array.from(root.querySelectorAll('tbody td:nth-child(2)')).map(n=>n.className),
            total:root.querySelector('tfoot')?.textContent||''};};
        return {off:read('off'),on:read('on'),workspace:read('workspace'),comparison:read('comparison')};
    }''',load_js('components/data_table_totals.js')+load_js('components/data_table_column_formats.js')+load_js('components/data_table.js'))
    assert output['off']['cells']==['1','0.3'] and output['on']['cells']==['120.00%','30.00%']
    for key in ('bar','color','shade','total'):
        assert output['off'][key]==output['on'][key]
    assert '$1.50' in output['on']['total']
    assert output['workspace']['cells']==output['comparison']['cells']==output['off']['cells']
    assert output['workspace']['total']==output['comparison']['total']==''


def test_unsafe_runtime_number_is_explicit_and_other_scalars_stay_text():
    from trellum.assets import load_js
    from trellum.testing.test_metric_views import node
    output=node(load_js('runtime/formatters.js')+load_js('components/data_table_column_formats.js')+'''
    console.log(JSON.stringify([true,'0001','9007199254740993',9007199254740992,null,Infinity,0].map(value=>_fwTableColumnFormat(value,{format:'currency',precision:2}))));''')
    assert output==['true','0001','9007199254740993','Unsafe number; supply exact text','—','—','$0.00']
