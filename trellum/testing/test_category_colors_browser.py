"""Vendored native-chart acceptance, including actual pre-change comparisons."""
from __future__ import annotations

import importlib
import importlib.util
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest

from trellum.components import DataSource, DoughnutChart, Grid, LineChart, StackedBar
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'a48a4507fe0972d9b7c0f124e7fb37c31bd8c69b'
DECLARATION = {'domain': 'acquisition', 'column': 'channel', 'slots': {'UA': 0, 'Retargeting': 2}}


def _install_baseline():
    """Use historical code when available, otherwise a current opt-out control.

    Shallow checkouts still exercise every functional assertion. Evidence names
    the actual source; an opt-out control is never reported as historical code.
    """
    try:
        historical = subprocess.run(['git', 'cat-file', '-e', BASELINE], cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        historical = False
    if historical:
        import trellum.assets as assets
        import trellum.rendering.js_runtime as runtime
        original = assets.load_js
        names = ['runtime/color_registry.js', 'runtime/theme.js', 'components/chart_base.js', 'components/doughnut.js']
        sources = {name: subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/{name}'], cwd=ROOT, text=True) for name in names}
        assets.load_js = lambda name: sources[name] if name in sources else original(name)
        runtime.load_js = assets.load_js
        for name in ('base', 'line_area', 'bar', 'doughnut'):
            module = importlib.import_module('trellum.components.charts.' + name)
            source = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/{name}.py'], cwd=ROOT, text=True)
            exec(compile(source, f'{BASELINE}/{name}.py', 'exec'), module.__dict__)  # noqa: S102 - Trusted local Git source for exact historical compatibility proof.
    import trellum.components as components
    import trellum.components.charts as charts
    for name, module in [('LineChart', 'line_area'), ('StackedBar', 'bar'), ('DoughnutChart', 'doughnut')]:
        kind = getattr(importlib.import_module('trellum.components.charts.' + module), name)
        def constructor(*args, _kind=kind, **kwargs):
            kwargs.pop('category_colors', None)
            return _kind(*args, **kwargs)
        setattr(components, name, constructor)
        setattr(charts, name, constructor)
        globals()[name] = constructor
    return BASELINE if historical else None


def _context(output):
    return ReportContext(config={'name': 'Declared category colors', 'studio': 'test', 'category': 'Test',
        'description': 'Daily synthetic acquisition spend and revenue, independent filters, declared color slots and raw category observations.',
        'theme': 'trellum light'}, slug='category-colors', output_dir=str(output), theme=THEME_REGISTRY['trellum light'])


def build_report(output, *, baseline=False, doughnut_only=False):
    output = Path(output)
    reference = _install_baseline() if baseline else None
    ctx = _context(output)
    if not doughnut_only:
        path = ROOT / 'trellum/demo/reports/component-showcase/examples_category_colors.py'
        spec = importlib.util.spec_from_file_location('category_examples', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.add_sections(ctx)
        # Validate the complete genuine module with grain details, independently
        # of deliberately mixed-type runtime regression inputs below.
        validation = validate_report(ctx, details=compute_details(ctx))
        assert validation.summary['warn'] == validation.summary['fail'] == 0, validation.to_dict()
        output.mkdir(parents=True, exist_ok=True)
        (output / 'module-validation.json').write_text(json.dumps(validation.to_dict(), indent=2))
        mixed = pd.DataFrame({'day': ['2026-09-01'] * 7,
            'channel': pd.Series([1, '1', '', None, 'UA', 'Unknown', True], dtype=object),
            'value': [13, 17, 19, 23, 29, 31, 37]})
        raw = {'domain': 'raw-types', 'column': 'channel', 'slots': {'1': 7, '': 8, 'UA': 2, 'true': 9}}
        ratios = [{'numerator': 'spend', 'denominator': 'users', 'label': 'Per user'},
                  {'numerator': 'refunds', 'denominator': 'users', 'label': 'Refund rate'}]
        split = pd.DataFrame([{'day': f'2026-09-{day:02d}', 'channel': channel,
            'spend': amount + day, 'refunds': -(index + day), 'users': index + 2}
            for day in range(1, 4) for index, (channel, amount) in enumerate((('UA', 80), ('Retargeting', 40)))])
        tail = pd.DataFrame({'day': ['2026-09-01'] * 4, 'channel': ['A', 'B', 'C', 'D'], 'value': [90, 70, 30, 10]})
        ctx.add_section('Runtime regression observations', [
            DataSource('category_mixed', mixed), DataSource('category_ratios', split), DataSource('category_tail', tail),
            Grid(columns=2, min_width=280, children=[
                LineChart(mixed, 'day', 'value', 'Mixed line', dataset_id='category_mixed', stack_by='channel', category_colors=raw),
                StackedBar(mixed, 'day', ['value'], 'Mixed stack', dataset_id='category_mixed', stack_by='channel', category_colors=raw),
                DoughnutChart(mixed, 'channel', 'value', 'Mixed live ring', dataset_id='category_mixed', category_colors=raw),
                LineChart(split, 'day', title='Split ratios', dataset_id='category_ratios', stack_by='channel',
                          ratios=ratios, tension=.35, category_colors=DECLARATION),
                StackedBar(split, 'day', ['spend', 'refunds'], 'Diverging categories', dataset_id='category_ratios',
                           stack_by='channel', diverging=True, line_cols=['users'], category_colors=DECLARATION),
                StackedBar(tail, 'day', ['value'], 'Synthetic Other', dataset_id='category_tail', stack_by='channel',
                           max_stacks=2, category_colors={'domain': 'tail', 'column': 'channel', 'slots': {'A': 0, 'B': 1, 'Other (2)': 8}}),
            ]),
            LineChart(split, 'day', 'spend', 'Explicit static line', static=True, colors=['#b1457b']),
        ])
    hostile = pd.DataFrame({'category': ['__proto__', 'constructor', 'toString', '', 'UA', 'UA', 1, '1', None],
                            'value': [17, 23, 31, 37, 41, 43, 47, 53, 59]})
    categories = pd.DataFrame({'category': [f'Category {i}' for i in range(14)], 'value': [101 + i * 13 for i in range(14)]})
    if not doughnut_only:
        many = pd.DataFrame([{'day': f'2026-09-{day:02d}', 'category': f'Category {i}',
                              'value': 100 + i * 17 + day * (i + 1)}
                             for day in range(1, 4) for i in range(14)])
        many_colors = {'domain': 'fourteen', 'column': 'category', 'slots': {f'Category {i}': i for i in range(14)}}
        ctx.add_section('Finite palette splits', [DataSource('category_many', many),
            Grid(columns=2, min_width=280, children=[
                LineChart(many, 'day', 'value', 'Finite line', dataset_id='category_many', stack_by='category',
                          max_stacks=14, category_colors=many_colors),
                StackedBar(many, 'day', ['value'], 'Finite stack', dataset_id='category_many', stack_by='category',
                           max_stacks=14, category_colors=many_colors),
            ])])
    ctx.add_section('Static resolver observations', [
        Grid(columns=2, min_width=280, children=[
            DoughnutChart(hostile, 'category', 'value', 'Hostile static ring', static=True,
                category_colors={'domain': '__proto__', 'column': 'category',
                    'slots': {'__proto__': 0, 'constructor': 2, 'toString': 4, '': 6, 'UA': 8, '1': 3},
                    'overrides': {'__proto__': '#b1457b'}}),
            DoughnutChart(categories, 'category', 'value', 'Finite palette', static=True,
                category_colors={'domain': 'fourteen', 'column': 'category', 'slots': {f'Category {i}': i for i in range(14)}}),
        ])])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    (output / 'source.json').write_text(json.dumps({'baseline': reference,
        'control': 'historical' if reference else ('current opt-out' if baseline else None),
        'only_argument_removed': baseline}))


def _open(page, output):
    def fulfill(route):
        path = output / urlparse(route.request.url).path.lstrip('/')
        route.fulfill(status=200 if path.is_file() else 404,
                      body=path.read_bytes() if path.is_file() else b'missing fixture',
                      content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
    page.route('https://category.test/**', fulfill)
    page.goto('https://category.test/index.html', wait_until='networkidle')
    for canvas in page.locator('canvas[data-fw-kind]').all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function('id => !!window._chartInstances[id]', arg=canvas.get_attribute('id'))
    page.evaluate('Object.values(window._chartInstances).forEach(c=>{c.stop();c.update("none")})')


STATE = '''title => {
    const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
    c.stop();c.update('none');
    return {id:c.canvas.id,labels:c.data.labels, width:c.width,height:c.height,
      area:{left:c.chartArea.left,right:c.chartArea.right,top:c.chartArea.top,bottom:c.chartArea.bottom},
      axes:Object.fromEntries(Object.entries(c.scales).map(([k,s])=>[k,{min:s.min,max:s.max,type:s.type}])),
      datasets:c.data.datasets.map((d,i)=>({label:d.label||'',data:d.data,color:d.borderColor,bg:d.backgroundColor,
        border:d.borderWidth,tension:d.tension,axis:d.yAxisID,
        elements:c.getDatasetMeta(i).data.map(e=>({x:e.x,y:e.y,base:e.base,width:e.width,height:e.height,
          radius:e.options.radius,inner:e.innerRadius,outer:e.outerRadius,start:e.startAngle,end:e.endAngle,
          border:e.options.borderColor,bg:e.options.backgroundColor}))}))};
}'''


def _state(page, title):
    canvas = page.locator(f'canvas[data-fw-title="{title}"]')
    canvas.scroll_into_view_if_needed()
    page.wait_for_function('id=>!!window._chartInstances[id] && !document.getElementById(id)._fwPendingTheme', arg=canvas.get_attribute('id'))
    return page.evaluate(STATE, title)


def _semantic(state):
    """Keep actual plotted values, order, geometry, axes and style parameters."""
    state = json.loads(json.dumps(state))
    for dataset in state['datasets']:
        dataset.pop('color', None)
        dataset.pop('bg', None)
        for element in dataset['elements']:
            element.pop('border', None)
            element.pop('bg', None)
    return state


def _colors(state):
    return {d['label']: d['color'] for d in state['datasets']}


def _palette(page):
    return page.evaluate('window.getThemeColors().chart_colors')


def _theme(page, name):
    page.locator('#fwThemeSelect').select_option(name)
    page.wait_for_function('name=>document.documentElement.dataset.theme===name', arg=name)


def _capture(page, shots, prefix):
    # Resolve every lazy theme update before inspecting an entire section.
    for canvas in page.locator('canvas[data-fw-kind]').all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function('id=>!!window._chartInstances[id] && !document.getElementById(id)._fwPendingTheme',
                               arg=canvas.get_attribute('id'))
    states = {}
    for title in ('Spend declared', 'Spend by selected breakdown', 'Snapshot declared', 'Hostile static ring'):
        states[title] = _state(page, title)
        # Full-page capture at the document top preserves native sticky layout
        # without overlaying a scrolled sticky header on a section crop.
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(shots / f'{prefix}-report.png'), full_page=True)
    for title, suffix in [('Spend declared', 'lines'), ('Spend by selected breakdown', 'stacks'),
                          ('Snapshot declared', 'rings'), ('Hostile static ring', 'hostile')]:
        section = page.locator(f'canvas[data-fw-title="{title}"]').locator(
            'xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," fw-section ")][1]')
        page.screenshot(path=str(shots / f'{prefix}-{suffix}.png'), full_page=True, clip=section.bounding_box())
    (shots / f'{prefix}.json').write_text(json.dumps(states, indent=2))


def _csv(page, title):
    state = _state(page, title)
    # Observe the real CSV writer's Blob; routed origins need not admit a
    # browser download. The serializer and button handlers remain untouched.
    page.evaluate("""()=>{window._categoryCsvBlob=null;
        if(!window._categoryBlobOriginal){window._categoryBlobOriginal=URL.createObjectURL;
            URL.createObjectURL=function(blob){window._categoryCsvBlob=blob;
                return window._categoryBlobOriginal.call(URL,blob);};}}""")
    page.locator(f'[data-csv-chart="{state["id"]}"]').click()
    page.wait_for_function('window._categoryCsvBlob !== null')
    return page.evaluate('window._categoryCsvBlob.text()')


def _check_ring(page, title, keys, declaration):
    state = _state(page, title)
    palette = _palette(page)
    expected = [declaration.get('overrides', {}).get(key, palette[declaration['slots'][key] % len(palette)])
                if isinstance(key, str) and key in declaration['slots'] else palette[i % len(palette)]
                for i, key in enumerate(keys)]
    assert state['datasets'][0]['bg'] == expected
    # Resolved real arcs and DOM legend swatches must carry the same colors.
    assert [e['bg'] for e in state['datasets'][0]['elements']] == expected
    swatches = page.locator(f'#{state["id"]}').locator('xpath=ancestor::div[@class="fw-doughnut"][1]').locator('.fw-doughnut-swatch').evaluate_all('(es)=>es.map(e=>e.style.backgroundColor)')
    expected_rgb = page.evaluate('(colors)=>colors.map(color=>{const e=document.createElement("span");e.style.backgroundColor=color;return e.style.backgroundColor;})', expected)
    assert swatches == expected_rgb
    # Sample actual painted interiors, away from edges and labels.
    painted = page.evaluate('''id=>{const c=window._chartInstances[id],d=c.currentDevicePixelRatio;
      return c.getDatasetMeta(0).data.map(e=>{const a=(e.startAngle+e.endAngle)/2,r=(e.innerRadius+e.outerRadius)/2;
        return Array.from(c.ctx.getImageData(Math.floor((e.x+Math.cos(a)*r)*d),Math.floor((e.y+Math.sin(a)*r)*d),1,1).data).slice(0,3);});}''', state['id'])
    assert painted == [[int(color[j:j+2], 16) for j in (1, 3, 5)] for color in expected]
    return state


def _browser_check(tmp_path, width):
    from playwright.sync_api import sync_playwright
    before, after = tmp_path / 'baseline', tmp_path / 'changed'
    subprocess.run([sys.executable, str(Path(__file__).resolve()), '--baseline', str(before)], check=True, cwd=ROOT)
    build_report(after)
    baseline_data = json.loads((before / 'data.json').read_text())
    changed_data = json.loads((after / 'data.json').read_text())
    for cfg in changed_data['components'].values():
        cfg.pop('category_colors', None)
        cfg.pop('category_keys', None)
    assert baseline_data['components'] == changed_data['components']
    shots = Path(os.environ.get('TRELLUM_SCREENSHOT_DIR', str(tmp_path / 'evidence')))
    shots.mkdir(parents=True, exist_ok=True)
    titles = ['Spend default','Spend declared','Revenue default','Revenue declared','Spend by selected breakdown',
              'Snapshot declared','Live declared','Snapshot fixed override','Other: sources','Other: regions',
              'Mixed line','Mixed stack','Mixed live ring','Split ratios','Diverging categories','Synthetic Other',
              'Explicit static line','Hostile static ring','Finite palette','Finite line','Finite stack']
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        baseline_page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
        _open(baseline_page, before)
        baseline_states = {title: _state(baseline_page, title) for title in titles}
        baseline_csv = {title: _csv(baseline_page, title) for title in titles}
        _capture(baseline_page, shots, f'{width}-before-light')
        _theme(baseline_page, 'trellum dark')
        _capture(baseline_page, shots, f'{width}-before-dark')
        page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('''window._categoryThemeAdds=0;window._categoryThemeRemoves=0;
            const add=window.addEventListener,remove=window.removeEventListener;
            window.addEventListener=function(t,f,o){if(t==='fw-theme-change')window._categoryThemeAdds++;return add.call(this,t,f,o)};
            window.removeEventListener=function(t,f,o){if(t==='fw-theme-change')window._categoryThemeRemoves++;return remove.call(this,t,f,o)};''')
        _open(page, after)
        changed_states = {title: _state(page, title) for title in titles}
        assert {title: _semantic(s) for title, s in changed_states.items()} == {title: _semantic(s) for title, s in baseline_states.items()}
        assert {title: _csv(page, title) for title in titles} == baseline_csv
        assert changed_states['Spend default'] == baseline_states['Spend default']
        assert changed_states['Revenue default'] == baseline_states['Revenue default']
        _capture(page, shots, f'{width}-after-light')
        for theme in ('trellum light', 'trellum dark', 'trellum light'):
            _theme(page, theme)
            palette = _palette(page)
            for title in ('Spend declared', 'Revenue declared', 'Spend by selected breakdown'):
                assert _colors(_state(page, title)) == {'UA': palette[0], 'Retargeting': palette[2]}
            assert _state(page, 'Spend declared')['datasets'][0]['label'] == 'UA'
            assert _state(page, 'Revenue declared')['datasets'][0]['label'] == 'Retargeting'
            _check_ring(page, 'Snapshot declared', ['UA','Retargeting','UA','Unmapped'], DECLARATION)
            live = _state(page, 'Live declared')
            _check_ring(page, 'Live declared', live['labels'], DECLARATION)
            fixed = {**DECLARATION, 'overrides': {'UA': '#b1457b'}}
            _check_ring(page, 'Snapshot fixed override', ['UA','Retargeting','UA','Unmapped'], fixed)
            hostile = {'slots': {'__proto__':0,'constructor':2,'toString':4,'':6,'UA':8,'1':3}, 'overrides': {'__proto__':'#b1457b'}}
            _check_ring(page, 'Hostile static ring', ['__proto__','constructor','toString','','UA','UA',None,'1',None], hostile)
            finite = _check_ring(page, 'Finite palette', [f'Category {i}' for i in range(14)], {'slots': {f'Category {i}': i for i in range(14)}})
            assert finite['datasets'][0]['bg'][0] == finite['datasets'][0]['bg'][len(palette)]
            for title in ('Finite line', 'Finite stack'):
                assert _colors(_state(page, title)) == {f'Category {i}': palette[i % len(palette)] for i in range(14)}
            assert _state(page, 'Explicit static line')['datasets'][0]['color'] == '#b1457b'
            assert _state(page, 'Other: sources')['datasets'][0]['bg'][0] == palette[1]
            assert _state(page, 'Other: regions')['datasets'][0]['bg'][0] == palette[4]
            # Real hover and exit, followed by a redraw, preserve resolved point colors.
            line = _state(page, 'Spend declared')
            box = page.locator('#'+line['id']).bounding_box()
            point = line['datasets'][0]['elements'][1]
            page.mouse.move(box['x']+point['x'], box['y']+point['y'])
            page.mouse.move(0, 0)
            for dataset in _state(page, 'Spend declared')['datasets']:
                assert all(e['border'] == e['bg'] == dataset['color'] for e in dataset['elements'])
            painted = page.evaluate('''id=>{const c=window._chartInstances[id],ratio=c.currentDevicePixelRatio;
                return c.data.datasets.map((ds,i)=>{const p=c.getDatasetMeta(i).data[1];
                    const rgb=ds.borderColor.slice(1).match(/../g).map(s=>parseInt(s,16));
                    const pixels=c.ctx.getImageData(Math.floor(p.x*ratio)-4,Math.floor(p.y*ratio)-4,9,9).data;
                    let count=0;for(let j=0;j<pixels.length;j+=4)if(pixels[j+3]>100&&rgb.every((v,k)=>Math.abs(pixels[j+k]-v)<10))count++;
                    return count;});}''',line['id'])
            assert all(count>4 for count in painted)
            ratios = _state(page, 'Split ratios')
            assert [d['color'] for d in ratios['datasets']] == [palette[0],palette[0],palette[2],palette[2]]
            assert all(d['tension'] == .35 for d in ratios['datasets'])
            diverging = _state(page, 'Diverging categories')
            assert [d['color'] for d in diverging['datasets'][:4]] == [palette[0],palette[0],palette[2],palette[2]]
            assert _state(page, 'Synthetic Other')['datasets'][-1]['color'] == baseline_states['Synthetic Other']['datasets'][-1]['color']
            for title in ('Mixed line','Mixed stack'):
                colors = _colors(_state(page, title))
                assert colors['UA'] == palette[2]
                assert colors['1'] != palette[7] and colors[''] != palette[8]
            mixed_ring = _state(page, 'Mixed live ring')
            expected_keys = [label if label == 'UA' else None for label in mixed_ring['labels']]
            _check_ring(page, 'Mixed live ring', expected_keys, {'slots': {'UA':2,'1':7,'':8}})
            if theme == 'trellum dark':
                _capture(page, shots, f'{width}-after-dark')
        # Named overrides also win on live split charts and live doughnuts,
        # independently of other charts declaring the same domain.
        for title in ('Spend declared', 'Spend by selected breakdown', 'Live declared'):
            current = _state(page, title)
            page.evaluate("""id=>{const cfg=window._reportData.components[id];
                cfg.category_colors={...cfg.category_colors,overrides:{UA:'#b1457b'}};
                window._fwRenderers[cfg.type](id,cfg);}""",current['id'])
        _theme(page,'trellum dark')
        for title in ('Spend declared', 'Spend by selected breakdown'):
            assert _colors(_state(page,title)) == {'UA':'#b1457b','Retargeting':_palette(page)[2]}
        live = _state(page,'Live declared')
        _check_ring(page,'Live declared',live['labels'],{**DECLARATION,'overrides':{'UA':'#b1457b'}})
        assert _colors(_state(page,'Revenue declared'))['UA'] == _palette(page)[0]
        for title in ('Spend declared', 'Spend by selected breakdown', 'Live declared'):
            current = _state(page,title)
            page.evaluate("""id=>{const cfg=window._reportData.components[id];
                delete cfg.category_colors.overrides;window._fwRenderers[cfg.type](id,cfg);}""",current['id'])
        _theme(page,'trellum light')
        line = _state(page,'Spend declared')
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:1,max:3},'none')",line['id'])
        zoomed = _state(page,'Spend declared')
        assert zoomed['axes']['x']['min'] == 1 and zoomed['axes']['x']['max'] == 3
        assert _colors(zoomed) == {'UA':_palette(page)[0],'Retargeting':_palette(page)[2]}
        page.evaluate("id=>window._chartInstances[id].resetZoom()",line['id'])
        # Independent report filtering, removal and restore use real engine
        # notifications; the existing native filter widgets subscribe to these.
        original_revenue = _state(page, 'Revenue declared')
        dropdown = page.locator('select[data-filter-col="channel"]').first
        field = dropdown.locator('xpath=parent::*')
        field.locator('.ss-main').click()
        page.locator('.ss-content:visible .ss-option', has_text='Retargeting').click()
        page.wait_for_function("window._fwFilterEngine.getFiltered('category_spend').length===6")
        page.keyboard.press('Escape')
        assert list(_colors(_state(page, 'Spend declared'))) == ['Retargeting']
        assert _colors(_state(page, 'Spend declared'))['Retargeting'] == _palette(page)[2]
        assert _state(page, 'Revenue declared') == original_revenue
        field.locator('[data-action="clear-all"]').click()
        page.wait_for_function("window._fwFilterEngine.getFiltered('category_spend').length===12")
        assert _semantic(_state(page, 'Spend declared')) == _semantic(changed_states['Spend declared'])
        stack = _state(page, 'Spend by selected breakdown')
        page.locator(f'[data-stack-toggle="{stack["id"]}"] button', has_text='Platform').click()
        alternate = _state(page, 'Spend by selected breakdown')
        baseline_stack = baseline_states['Spend by selected breakdown']
        baseline_page.locator(f'[data-stack-toggle="{baseline_stack["id"]}"] button', has_text='Platform').click()
        _theme(baseline_page, 'trellum light')
        assert alternate == _state(baseline_page, 'Spend by selected breakdown')
        # Total uses the existing unsplit path even with a declaration present.
        for target in (page, baseline_page):
            target.evaluate('''id=>{const cfg={...window._reportData.components[id],stack_by:null,stack_by_options:null};
                window._fwRenderers.chart(id,cfg);}''', stack['id'])
        assert _state(page, 'Spend by selected breakdown') == _state(baseline_page, 'Spend by selected breakdown')
        for target in (page, baseline_page):
            target.evaluate('id=>window._fwRenderers.chart(id,window._reportData.components[id])', stack['id'])
        page.locator(f'[data-stack-toggle="{stack["id"]}"] button', has_text='Channel').click()
        page.locator(f'[data-normalize-toggle="{stack["id"]}"] button', has_text='Percentage').click()
        normalized = _state(page, 'Spend by selected breakdown')
        assert _colors(normalized) == {'UA':_palette(page)[0], 'Retargeting':_palette(page)[2]}
        assert all(abs(sum(d['data'][i] for d in normalized['datasets'])-100)<1e-8 for i in range(6))
        ring_before = _state(page, 'Live declared')
        snapshot_before = _state(page, 'Snapshot declared')
        ring_section = page.locator('#'+ring_before['id']).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
        ring_section.get_by_role('button',name='North',exact=True).click()
        page.wait_for_function("window._fwFilterEngine.getFiltered('category_rings').length===3")
        assert _state(page,'Live declared')['datasets'][0]['data'] == [323,113,43]
        assert _state(page,'Snapshot declared') == snapshot_before
        page.evaluate("window._fwFilterEngine.setFilter('category_rings','probe','channel','equals','Retargeting')")
        selected_ring = _state(page,'Live declared')
        assert selected_ring['labels'] == ['Retargeting']
        _check_ring(page,'Live declared',['Retargeting'],DECLARATION)
        page.evaluate("window._fwFilterEngine.clearFilter('category_rings','probe')")
        ring_section.get_by_role('button',name='All',exact=True).click()
        page.wait_for_function("window._fwFilterEngine.getFiltered('category_rings').length===6")
        assert _state(page,'Live declared') == ring_before
        # Repeated renderer calls do not admit listeners or lose active controls.
        counts = page.evaluate('[window._categoryThemeAdds,window._categoryThemeRemoves]')
        page.evaluate('''()=>{for(let j=0;j<3;j++)Object.entries(window._reportData.components).forEach(([id,cfg])=>{
            if(cfg.category_colors)window._fwRenderers[cfg.type](id,cfg);
        });}''')
        after_counts = page.evaluate('[window._categoryThemeAdds,window._categoryThemeRemoves]')
        assert after_counts[0]-after_counts[1] == counts[0]-counts[1]
        assert _semantic(_state(page, 'Spend by selected breakdown')) == _semantic(normalized)
        live_counts = page.evaluate('window._categoryThemeAdds-window._categoryThemeRemoves')
        for title in ('Spend declared','Live declared'):
            current = _state(page,title)
            page.evaluate("""id=>{const old=document.getElementById(id),canvas=old.cloneNode(true);
                old.replaceWith(canvas);const cfg={...window._reportData.components[id]};
                window._reportData.components[id]=cfg;window._fwRenderers[cfg.type](id,cfg);}""",current['id'])
            assert _semantic(_state(page,title)) == _semantic(current)
        assert page.evaluate('window._categoryThemeAdds-window._categoryThemeRemoves') == live_counts
        # Removing the opt-in on a reused StackedBar config must recreate the
        # normal live binding after disposing its category-specific listeners.
        page.evaluate("""id=>{const cfg=window._reportData.components[id];
            delete cfg.category_colors;window._fwRenderers.chart(id,cfg);
            window._fwFilterEngine.setFilter('category_stacks','mapping-removal','channel','equals','Retargeting');
        }""", stack['id'])
        assert [d['label'] for d in _state(page, 'Spend by selected breakdown')['datasets']] == ['Retargeting']
        page.locator(f'[data-stack-toggle="{stack["id"]}"] button', has_text='Platform').click()
        assert set(_colors(_state(page, 'Spend by selected breakdown'))) == {'iOS', 'Android'}
        page.evaluate("window._fwFilterEngine.clearFilter('category_stacks','mapping-removal')")
        page.locator(f'[data-stack-toggle="{stack["id"]}"] button', has_text='Channel').click()
        assert set(_colors(_state(page, 'Spend by selected breakdown'))) == {'UA', 'Retargeting'}
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        (shots / f'{width}-comparison.json').write_text(json.dumps({'source':json.loads((before/'source.json').read_text()),
            'before':baseline_states,'after':changed_states,'errors':errors}, indent=2))
        assert errors == []
        browser.close()


def test_category_colors_desktop(tmp_path):
    _browser_check(tmp_path, 1280)


@pytest.mark.heavy
def test_category_colors_phone(tmp_path):
    _browser_check(tmp_path, 390)


def test_static_doughnut_only_has_helpers_and_theme_metadata(tmp_path):
    from playwright.sync_api import sync_playwright
    build_report(tmp_path, doughnut_only=True)
    html = (tmp_path/'index.html').read_text()
    assert 'function renderChart(' not in html
    assert '_fwCategoryColor = function' in html
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors=[]
        page.on('pageerror', lambda error:errors.append(str(error)))
        _open(page, tmp_path)
        before = _state(page,'Hostile static ring')
        _theme(page,'trellum dark')
        after = _state(page,'Hostile static ring')
        assert before['datasets'][0]['bg'][0] == after['datasets'][0]['bg'][0] == '#b1457b'
        assert after['datasets'][0]['bg'][1] == _palette(page)[2]
        assert before['datasets'][0]['bg'][1] != after['datasets'][0]['bg'][1]
        assert errors == []
        browser.close()


if __name__ == '__main__':
    build_report(Path(sys.argv[2]), baseline=sys.argv[1] == '--baseline')
