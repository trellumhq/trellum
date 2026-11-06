"""Native generated reports, exact arithmetic, snapshot filtering and capture."""

import base64
import csv
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, ScopedDataSource
from trellum.components.hierarchy_table import HierarchyTable
from trellum.rendering.html_builder import render_report
from trellum.testing.test_hierarchy_table import ROOT, context, diagnostics, examples, frame, table


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def native_page(tmp_path, browser):
    ctx = context(tmp_path/'report')
    examples().add_sections(ctx)
    df = frame()
    df['value'] = [0.1, 0.2, 0]
    special = pd.DataFrame({'id':[str(i) for i in range(11)],
        'source':['__proto__','a|b','a',None,'',True,'true',1,'1','Missing','Long '+ 'wrapping label '*30],
        'platform':['leaf','c','b|c','x','x','x','x','x','x','x','x'],
        'month':['Sep']*11, 'value':pd.Series([Decimal('0.1'),Decimal('0.2'),2**53+1,0,None,False,'2',-2,3,4,5],dtype=object)})
    ctx.add_section('Typed leaf facts', [table(special, title='Typed paths', static=True, known_subtotals=True)])
    ctx.add_section('Exact duplicate coordinates', [table(pd.DataFrame({
        'id':['a','b','c'], 'source':['A']*3,'platform':['iOS']*3, 'month':['Sep','Sep','Oct'],
        'value':pd.Series([Decimal('0.1'),Decimal('0.2'),2**53+1],dtype=object)}),
        title='Exact values',static=True,precision=6)])
    ctx.add_section('Scoped hierarchy', [DataSource('hierarchy_root',df),
        ScopedDataSource('hierarchy_child',parent='hierarchy_root'),
        FilterBar('hierarchy_child',df,filters=[{'column':'platform','type':'toggle'}]),
        table(title='Scoped',dataset_id='hierarchy_child')])
    deep = pd.DataFrame([{'id':'deep','value':1,**{'d'+str(i):'Depth '+str(i) for i in range(8)}}])
    ctx.add_section('Eight levels', [HierarchyTable(deep,'Deep',rows=['d'+str(i) for i in range(8)],
        value='value',record_id='id',expanded_paths=[['Depth '+str(i) for i in range(n)] for n in range(1,8)],static=True)])
    details, result = diagnostics(ctx)
    assert result.summary['warn'] == result.summary['fail'] == 0, result.to_dict()
    render_report(ctx,auto_refresh=False,validation=result,details=details)
    data = json.loads((tmp_path/'report/data.json').read_text())
    ids = [cid for cid,cfg in data['components'].items() if cfg['type']=='hierarchy_table']
    page = browser.new_page(viewport={'width':1280,'height':1000})
    errors = []
    page.on('pageerror',lambda error:errors.append(str(error)))
    def fulfill(route):
        url = urlsplit(route.request.url)
        assert url.hostname == 'hierarchy.test', route.request.url
        path = unquote(url.path).lstrip('/')
        file = ROOT/'trellum/static/vendor'/path[len('_vendor/'):] if path.startswith('_vendor/') else tmp_path/path
        route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404,body='missing')
    page.route('**/*',fulfill)
    page.goto('http://hierarchy.test/report/index.html',wait_until='networkidle')
    for cid in ids:
        page.locator(f'#{cid}').scroll_into_view_if_needed()
        page.locator(f'#{cid} tbody tr').first.wait_for()
    yield page, ids, data, tmp_path
    assert not errors
    page.close()


def footer(page,cid):
    return page.locator(f'#{cid} tfoot tr').inner_text().split('\t')


def export(page,cid):
    with page.expect_download() as download:
        page.locator(f'#{cid} [data-leaf-csv]').click()
    with open(download.value.path(), newline='') as file:
        rows = list(csv.reader(file))
    return {'columns':rows[0],'rows':rows[1:]}


def filter_button(page,identity,value):
    page.locator(f'[data-fw-ds-id="{identity}"] .fw-toggle-btn').filter(has_text=value).click()


def test_native_totals_expansion_filters_csv_and_unavailable(native_page):
    page, ids, _, _ = native_page
    collapsed, expanded, withheld, known = ids[:4]
    assert footer(page,collapsed) == ['All matching leaf facts','$7,600.00','$10,250.00','$17,850.00']
    assert page.locator(f'#{collapsed} tbody tr').count() == 3
    before = export(page,collapsed)
    assert len(before['rows']) == 12 and len({r[0] for r in before['rows']}) == 12
    assert all(r[1]=='leaf_fact' for r in before['rows'])
    for _ in range(3):
        page.locator(f'#{collapsed} button[aria-expanded="false"]').first.click()
    assert page.locator(f'#{collapsed} tbody tr').count() == 9
    assert footer(page,collapsed)[-1] == '$17,850.00'
    assert export(page,collapsed) == before
    assert page.locator(f'#{collapsed} button[data-path]').first.evaluate('e=>e===document.activeElement') is False
    filter_button(page,'hierarchy_collapsed','iOS')
    assert footer(page,collapsed)[-1] == '$10,250.00'
    assert page.locator(f'#{collapsed} tbody tr').count() == 6
    assert len(export(page,collapsed)['rows']) == 6
    filter_button(page,'hierarchy_collapsed','Web')
    assert page.locator(f'#{collapsed} tfoot').count() == 0
    assert 'No leaf facts' in page.locator(f'#{collapsed}').inner_text()
    assert export(page,collapsed)['rows'] == []
    filter_button(page,'hierarchy_collapsed','All')
    assert page.locator(f'#{collapsed} tbody tr').count() == 9
    assert footer(page,collapsed)[-1] == '$17,850.00'
    assert footer(page,expanded)[-1] == '$17,850.00'
    assert footer(page,withheld) == ['All matching leaf facts','$7,600.00','—','—']
    assert footer(page,known) == ['All matching leaf facts','$7,600.00','$9,250.00*','$16,850.00*']
    assert '1 unavailable inputs' in page.locator(f'#{known} .fw-hierarchy-note').inner_text()
    assert page.locator(f'#{known} td').filter(has_text='$0.00').count() >= 1
    assert 'No observations' in str(page.locator(f'#{ids[6]} td').evaluate_all('els=>els.map(e=>e.title)'))


def test_typed_exact_and_depth(native_page):
    page, ids, _, _ = native_page
    special, exact, _, deep = ids[4:]
    assert page.locator(f'#{special} tbody tr').count() == 11
    assert footer(page,exact)[-1] == '9,007,199,254,740,993.300000'
    page.locator(f'#{exact} button[data-path]').click()
    assert footer(page,exact)[-1] == '9,007,199,254,740,993.300000'
    assert page.locator(f'#{exact} tbody tr').count() == 2
    rows = export(page,special)['rows']
    typed = [json.loads(row[2])[0] for row in rows]
    assert ['null',None] in typed and ['string',''] in typed and ['boolean',True] in typed
    assert ['string','true'] in typed and ['number',1] in typed and ['string','1'] in typed
    assert rows[2][6] == '9007199254740993'
    assert page.locator(f'#{deep} tbody tr').count() == 8
    assert footer(page,deep)[-1] == '1.00'


def test_focus_instances_theme_reentry_replacement_removal_and_runtime_refusal(native_page):
    page, ids, data, _ = native_page
    cid = ids[0]
    toggle = page.locator(f'#{cid} button[data-path]').first
    toggle.click()
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert toggle.evaluate('e=>e===document.activeElement')
    for _ in range(3):
        page.evaluate("window.dispatchEvent(new Event('fw-theme-change'))")
        page.evaluate('id=>_fwRenderers.hierarchy_table(id,_reportData.components[id])',cid)
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert toggle.evaluate('e=>e===document.activeElement')
    assert page.locator(f'#{cid} table').count() == 1
    assert page.locator(f'#{ids[1]} tbody tr').count() == 9
    assert page.evaluate("_fwFilterEngine._subscribers.hierarchy_collapsed.filter(e=>e.id.endsWith('_hierarchy')).length") == 1
    page.evaluate("""id=>{_fwFilterEngine.setFilter('hierarchy_collapsed','direct','Source','equals','Partners');}""",cid)
    assert page.locator(f'#{cid} [data-leaf-csv]').evaluate('e=>e===document.activeElement')
    page.evaluate("_fwFilterEngine.clearFilter('hierarchy_collapsed','direct')")
    scoped = ids[6]
    assert footer(page,scoped)[-1] == '0.30'
    page.evaluate("_fwFilterEngine.setFilter('hierarchy_root','parent','platform','equals','iOS')")
    assert footer(page,scoped)[-1] == '0.10'
    page.evaluate("""id=>{const el=document.getElementById(id);el.replaceWith(el.cloneNode(false));_fwRenderers.hierarchy_table(id,_reportData.components[id]);}""",cid)
    assert page.locator(f'#{cid} table').count() == 1
    page.locator(f'#{cid}').evaluate('el=>el.remove()')
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(resolve))')
    assert page.evaluate("_fwFilterEngine._subscribers.hierarchy_collapsed.filter(e=>e.id.endsWith('_hierarchy')).length") == 0
    # Real source changes, including malformed data and precision loss, refuse
    # complete-parent claims instead of retaining stale totals or CSV.
    page.evaluate("_fwFilterEngine._liveDatasets={hierarchy_root:true}")
    page.evaluate('id=>_fwRenderers.hierarchy_table(id,_reportData.components[id])',scoped)
    assert 'live ancestry' in page.locator(f'#{scoped}').inner_text()
    page.evaluate("_fwFilterEngine._liveDatasets={}")


def test_runtime_full_source_admission_and_numeric_availability(native_page):
    page, ids, _, _ = native_page
    cid = ids[6]
    page.evaluate('id=>window.hierarchyTestId=id',cid)
    def replace(rows):
        page.evaluate("""rows=>{const e=_fwFilterEngine;e.initColumnar('hierarchy_root',{_cols:['id','source','platform','month','value'],_data:rows,_dict:{}});e.notify('hierarchy_root');}""",rows)
    replace([['a','A','iOS','Sep',0],['b','B','Android','Oct',None]])
    assert footer(page,cid)[-1] == '—'
    assert page.locator(f'#{cid} td').filter(has_text='0.00').count() > 0
    assert 'No observations' in str(page.locator(f'#{cid} td').evaluate_all('els=>els.map(e=>e.title)'))
    for invalid in ['2',True,None,9007199254740992]:
        replace([['a','A','iOS','Sep',invalid]])
        assert footer(page,cid)[-1] == '—'
        assert export(page,cid)['rows'][0][-3:-1] == ['unavailable','unavailable']
        if invalid == 9007199254740992:
            assert 'precision is unsafe' in page.locator(f'#{cid} tfoot td').last.get_attribute('title')
    replace([['a','A','iOS','Sep',1],['a','Hidden','Android','Oct',2]])
    assert 'duplicate record_id' in page.locator(f'#{cid}').inner_text()
    assert page.locator(f'#{cid} [data-leaf-csv]').count() == 0
    replace([['a',{'nested':'bad'},'iOS','Sep',1]])
    assert 'scalar' in page.locator(f'#{cid}').inner_text()
    replace([['a','A','iOS','Sep',1]])
    page.evaluate("delete _fwFilterEngine._columnar.hierarchy_root;window.dispatchEvent(new Event('fw-theme-change'))")
    assert 'source is not ready' in page.locator(f'#{cid}').inner_text()
    replace([['a','A','iOS','Sep',1]])
    page.evaluate("""() => { _fwFilterEngine.registerLiveDataset('live-neighbor',{});
        _reportData.components.testHierarchyLive={type:'filter_bar',dataset_id:'hierarchy_root',propagate_to:{'live-neighbor':{source:'source'}}};
        window.dispatchEvent(new Event('fw-theme-change')); }""")
    assert 'live ancestry' in page.locator(f'#{cid}').inner_text()


def test_static_decimal_range_completeness_and_rounding(native_page):
    page, ids, _, _ = native_page
    cid = ids[5]
    cases = [
        ([Decimal('0.1'),Decimal('0.2')],False,'0.30'),
        ([Decimal('-1.005')],False,'-1.01'),
        ([2**53+1,2**53+3],False,'18,014,398,509,481,988.00'),
        ([Decimal('1E+1001')],False,'—'),
        ([Decimal('1E-1001')],True,'—'),
        ([0,None],False,'—'),([0,None],True,'0.00*'),
        ([None,None],True,'—'),([True,'2'],True,'—'),
        ([-2,2,0],False,'0.00'),
    ]
    for values, known, expected in cases:
        df = pd.DataFrame({'id':[str(i) for i in range(len(values))], 'source':['A']*len(values),
                           'platform':['iOS']*len(values),'month':['Sep']*len(values),'value':pd.Series(values,dtype=object)})
        t = table(df,known_subtotals=known,static=True)
        facts,_ = t.prepare(df)
        page.evaluate("""({id,facts,known})=>{let cfg={..._reportData.components[id],facts,precision:2,knownSubtotals:known,expanded:[]};_fwRenderers.hierarchy_table(id,cfg);}""",{'id':cid,'facts':facts,'known':known})
        assert footer(page,cid)[-1] == expected
        if any(isinstance(v,Decimal) and abs(v.as_tuple().exponent)>1000 for v in values):
            assert 'range is unsupported' in page.locator(f'#{cid} tfoot td').last.get_attribute('title')


def evidence(tmp):
    path = Path(os.environ.get('TRELLUM_SCREENSHOT_DIR',str(tmp/'evidence')))
    path.mkdir(parents=True,exist_ok=True)
    return path


@pytest.mark.parametrize('width',[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_native_desktop_phone_paint_capture_png_pdf(native_page,width):
    page, ids, _, tmp = native_page
    page.set_viewport_size({'width':width,'height':1000})
    out = evidence(tmp)
    (out/f'hierarchy-{width}-native-meta.json').write_bytes((tmp/'report/_meta.json').read_bytes())
    for theme in ['trellum light','trellum dark']:
        page.locator('#fwThemeSelect').select_option(theme)
        page.evaluate('document.fonts.ready')
        page.mouse.move(0,0)
        for cid, name in zip([ids[0],ids[1],ids[3],ids[4],ids[7]],['collapsed','expanded','partial','typed','deep']):
            section = page.locator(f'#{cid}').locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
            section.screenshot(path=str(out/f'hierarchy-{name}-{width}-{theme.replace(" ","-")}.png'),animations='disabled')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if width == 390:
            scroller = page.locator(f'#{ids[3]} .fw-hierarchy-scroll')
            scroller.evaluate('e=>e.scrollLeft=e.scrollWidth')
            assert scroller.evaluate('e=>e.scrollLeft>0')
            assert page.locator(f'#{ids[3]} .fw-hierarchy-note').bounding_box()['x'] >= 0
            scroller.evaluate('e=>e.scrollLeft=0')
    section = page.locator(f'#{ids[3]}').locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
    section.scroll_into_view_if_needed()
    capture = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
    assert capture['format'] == 'trellum-analysis-capture'
    (out/f'hierarchy-{width}-native-section.png').write_bytes(base64.b64decode(capture['image']['data_base64']))
    (out/f'hierarchy-{width}-capture-source.json').write_text(json.dumps(capture['source'],indent=2))
    assert page.locator(f'#{ids[3]} tbody tr').count() == 7
    if width == 1280:
        # Native report exports use the current layout and completeness note.
        for method,suffix in [('exportPNG','png'),('exportPDF','pdf')]:
            with page.expect_download(timeout=45000) as download:
                page.evaluate('method=>fw[method]()',method)
            download.value.save_as(str(out/f'hierarchy-native-report.{suffix}'))
            assert (out/f'hierarchy-native-report.{suffix}').stat().st_size > 1000
    # Repeated reveal/redraw and a narrow host panel preserve table geometry
    # without allocating a document-width scrolling surface.
    page.locator(f'#{ids[1]}').evaluate("el=>{el.style.display='none';el.style.maxWidth='260px'}")
    page.evaluate("window.dispatchEvent(new Event('fw-theme-change'))")
    page.locator(f'#{ids[1]}').evaluate("el=>el.style.display=''")
    assert footer(page,ids[1])[-1] == '$17,850.00'
    assert page.locator(f'#{ids[1]} .fw-hierarchy-scroll').evaluate('e=>e.clientWidth<=260 && e.scrollWidth>e.clientWidth')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')


def test_csv_preserves_special_headings_raw_precision_and_text(native_page):
    page, ids, _, _ = native_page
    df = pd.DataFrame({'fact,id':['fact\rone'], 'source, label':['<img src=x onerror=alert(1)>\r"line"'],
                       'platform':['__proto__'], 'value':pd.Series([Decimal('1.23000000000000001')],dtype=object)})
    t = HierarchyTable(df,'CSV precision',rows=['source, label','platform'],value='value',record_id='fact,id',static=True)
    facts,_ = t.prepare(df)
    cid = ids[5]
    page.evaluate("""({id,facts})=>_fwRenderers.hierarchy_table(id,{..._reportData.components[id],facts,
        dimensions:['source, label','platform'],recordId:'fact,id',column:null,expanded:[]})""",{'id':cid,'facts':facts})
    assert page.locator(f'#{cid} img').count() == 0
    result = export(page,cid)
    assert 'source, label (typed)' in result['columns']
    row = dict(zip(result['columns'],result['rows'][0]))
    assert row['record_id'] == 'fact\rone'
    assert row['raw_value'] == '1.23000000000000001'
    assert json.loads(row['source, label (typed)']) == ['string','<img src=x onerror=alert(1)>\r"line"']


def test_focus_falls_back_to_closest_ancestor_and_stale_callbacks_cannot_draw(native_page):
    page, ids, _, _ = native_page
    cid = ids[6]
    page.evaluate("""id=>{window.staleHierarchyDraw=_fwFilterEngine._subscribers.hierarchy_child.find(e=>e.id===id+'_hierarchy').fn;
        _fwRenderers.hierarchy_table(id,{..._reportData.components[id],dimensions:['source','platform','month'],column:null});}""",cid)
    buttons = page.locator(f'#{cid} button[data-path]')
    buttons.first.click()
    child = buttons.nth(1)
    child.click()
    assert child.evaluate('e=>e===document.activeElement')
    page.evaluate("_fwFilterEngine.setFilter('hierarchy_root','hide-child','platform','equals','Android')")
    assert buttons.first.evaluate('e=>e===document.activeElement')
    assert buttons.first.get_attribute('aria-expanded') == 'true'
    assert footer(page,cid)[-1] == '0.20'
    page.evaluate('staleHierarchyDraw([])')
    assert footer(page,cid)[-1] == '0.20'
    page.evaluate("_fwFilterEngine.setFilter('hierarchy_root','hide-child','platform','equals','Web')")
    assert page.locator(f'#{cid} [data-leaf-csv]').evaluate('e=>e===document.activeElement')
    page.evaluate("_fwFilterEngine.clearFilter('hierarchy_root','hide-child')")
    assert page.locator(f'#{cid} tbody tr').count() == 5


@pytest.mark.heavy
def test_runtime_resource_limits_refuse_before_dom_expansion(native_page):
    page, ids, _, _ = native_page
    cid = ids[5]
    results = page.evaluate("""id=>{
        const base=_reportData.components[id], template=base.facts[0], result=[];
        function run(facts){_fwRenderers.hierarchy_table(id,{...base,facts,expanded:[]});result.push(document.getElementById(id).textContent);}
        run(Array(50001).fill(template));
        run(Array.from({length:2000},(_,i)=>({...template,id:String(i),path:[['string',String(i)],['string','child']]})));
        run(Array.from({length:33},(_,i)=>({...template,id:String(i),bucket:['string',String(i)]})));
        run(Array.from({length:1940},(_,i)=>({...template,id:String(i),path:[['string','A'],['string',String(i)]],bucket:['string',String(i%32)]})));
        run(Array.from({length:10000},(_,i)=>({...template,id:String(i),path:[['string','x'.repeat(1024)],['string','child']]})));
        return result;
    }""",cid)
    for reason, message in zip(['50,000','2,000','32 column','64,000','8 MiB'],results):
        assert reason in message
    assert page.locator(f'#{cid} table').count() == 0


def test_sibling_and_bucket_order_stay_first_seen_in_full_snapshot(native_page):
    page, ids, _, _ = native_page
    cid = ids[6]
    page.evaluate("""() => {
        const e=_fwFilterEngine;
        e.initColumnar('hierarchy_root',{_cols:['id','source','platform','month','value'],_dict:{},_data:[
            ['a','A','iOS','Sep',1],['b','B','Android','Oct',2],['c','A','Android','Oct',3]]});
        e.setFilter('hierarchy_root','order','platform','equals','Android');
    }""")
    labels = page.locator(f'#{cid} tbody th').all_text_contents()
    assert labels == ['▸A','▸B']
    assert page.locator(f'#{cid} thead th').all_text_contents() == ['source / platform','Sep','Oct','Total']
    assert footer(page,cid)[-1] == '5.00'


def test_malformed_configuration_and_source_row_shape_refuse_locally(native_page):
    page, ids, _, _ = native_page
    cid = ids[6]
    for update in [{'expanded':{}},{'column':False},{'title':None}]:
        page.evaluate('({id,update})=>_fwRenderers.hierarchy_table(id,{..._reportData.components[id],...update})',{'id':cid,'update':update})
        assert page.locator(f'#{cid} table').count() == 0
        assert 'HierarchyTable:' in page.locator(f'#{cid}').inner_text()
    page.evaluate('id=>_fwRenderers.hierarchy_table(id,_reportData.components[id])',cid)
    page.evaluate("""() => {_fwFilterEngine._columnar.hierarchy_root._data[0].pop();
        window.dispatchEvent(new Event('fw-theme-change'));}""")
    assert 'malformed source row shape' in page.locator(f'#{cid}').inner_text()
    assert page.locator(f'#{cid} [data-leaf-csv]').count() == 0
