"""Bar-only label contracts with genuine reports and vendored Chart.js paint."""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import types
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import BarChart, StackedBar, RenderContext
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DefaultTheme

# Optional exact Git baseline for visual review; ordinary CI needs no historic ref.
BASELINE = os.environ.get('TRELLUM_BAR_BASELINE_REF')


def _module():
    path = Path(__file__).resolve().parents[1] / 'demo/reports/component-showcase/examples_bars.py'
    spec = importlib.util.spec_from_file_location('examples_bars', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(output):
    return ReportContext(config={'name': 'Bar label examples', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic daily additive revenue by source and platform, category ratios and independent fixed snapshots.',
        'theme': 'trellum light'}, slug='bar-labels', output_dir=str(output))


@pytest.mark.parametrize('cls,names', [(BarChart, ['show_values']), (StackedBar, ['show_totals', 'show_percentages'])])
def test_keyword_only_and_dataclass_subclasses(cls, names):
    @dataclasses.dataclass
    class Child(cls):
        extra: str = 'child'
    frame = pd.DataFrame({'x': ['A'], 'y': [10]})
    args = (frame, 'x', 'y' if cls is BarChart else ['y'], 'Title')
    for name in names:
        assert inspect.signature(cls).parameters[name].kind == inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(Child).parameters['extra'].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    original = Child(*args)
    # Every historical positional field including the subclass extension keeps its position.
    positional = [getattr(original, f.name) for f in dataclasses.fields(original) if f.init and not f.kw_only]
    assert Child(*positional).extra == 'child'
    for binding in ({'static': True}, {'dataset_id': 'bound', 'static': True}):
        chart = Child(*args, **binding)
        ctx = RenderContext(DefaultTheme)
        chart.render_html(ctx)
        cfg = next(iter(ctx.component_data.values()))
        assert bool(cfg.get('dataset_id')) == bool(binding.get('dataset_id'))
        if cls is BarChart:
            assert cfg['bar_values'] == {'show': None}
        else:
            assert cfg['stack_labels'] == {'totals': False, 'percentages': False, 'format': 'currency'}


@pytest.mark.parametrize('name', ['show_values', 'show_totals', 'show_percentages'])
@pytest.mark.parametrize('value', [0, 1, 'true', [], {}, 1.5])
def test_bool_validation_at_construction_and_render(name, value):
    frame = pd.DataFrame({'x': ['A'], 'y': [10]})
    cls = BarChart if name == 'show_values' else StackedBar
    args = (frame, 'x', 'y' if cls is BarChart else ['y'], 'Title')
    with pytest.raises(ValueError, match=name):
        cls(*args, **{name: value})
    chart = cls(*args)
    setattr(chart, name, value)
    with pytest.raises(ValueError, match=name):
        chart.render_html(RenderContext(DefaultTheme))


@pytest.mark.parametrize('name', ['show_totals', 'show_percentages'])
def test_stack_none_invalid(name):
    with pytest.raises(ValueError, match=name):
        StackedBar(pd.DataFrame(), 'x', [], 'Title', **{name: None})


def test_showcase_full_validation_and_build(tmp_path):
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report
    ctx = _context(tmp_path)
    _module().add_sections(ctx)
    details = compute_details(ctx)
    assert details['totals']['datasource_count'] == 2
    assert details['totals']['chart_count'] == 12
    result = validate_report(ctx, details=details)
    assert result.summary['fail'] == result.summary['warn'] == 0, result.to_dict()
    result.write(str(tmp_path))
    render_report(ctx, str(tmp_path), auto_refresh=False)
    assert (tmp_path / 'index.html').is_file()


def _report(output, baseline=False):
    ctx = _context(output)
    _module().add_sections(ctx)
    frame = pd.DataFrame({'category': ['Positive', 'Negative', 'Zero', 'Missing'], 'a': [150, -25, 0, None], 'b': [80, -10, 0, None]})
    dates = pd.DataFrame({'day': ['2026-10-01', '2026-10-02'], 'value': [31, 47]})
    dense = pd.DataFrame({'x': [f'C{i:02d}' for i in range(40)], 'a': range(40), 'b': range(40, 80)})
    categories = ctx.sections[0]['components'][0].df
    ctx.add_section('Test label boundaries', [
        BarChart(categories, 'category', 'adjustment', 'Legacy net-color orientation', horizontal=True, net_color=True, dataset_id='showcase_bar_categories'),
        BarChart(categories, 'category', 'revenue', 'Legacy ratio stacking', horizontal=True, stacked=True, dataset_id='showcase_bar_categories',
                 ratios=[{'numerator': 'revenue', 'denominator': 'orders', 'label': 'Ratio'}]),
        BarChart(frame, 'category', 'a', 'Static signed grouped', y_cols=['a', 'b'], horizontal=True, static=True),
        BarChart(dates, 'day', 'value', 'Dates explicit on', horizontal=True, show_values=True, static=True),
        BarChart(frame, 'category', 'a', 'Generic stacked BarChart unchanged', stacked=True, horizontal=True, show_values=True, static=True),
        BarChart(dense, 'x', 'a', 'Dense grouped', y_cols=['a', 'b'], horizontal=True, static=True),
        StackedBar(frame.iloc[:0], 'category', ['a', 'b'], 'Empty stacks', static=True, show_totals=True, show_percentages=True),
        StackedBar(pd.DataFrame({'x': ['A'], 'a': [1]}), 'x', ['a', 'a'], 'Duplicate source columns', static=True, show_totals=True),
    ])
    if baseline:
        # Execute the exact accepted dependency source without changing this checkout.
        source = subprocess.check_output(['git', 'show', BASELINE + ':trellum/components/charts/bar.py'], text=True)
        module = types.ModuleType('_bar_labels_actual_baseline')
        sys.modules[module.__name__] = module
        exec(compile(source, '<git baseline bar.py>', 'exec'), module.__dict__)
        for section in ctx.sections:
            for i, chart in enumerate(section['components']):
                if isinstance(chart, (BarChart, StackedBar)):
                    cls = getattr(module, type(chart).__name__)
                    names = {f.name for f in dataclasses.fields(cls) if f.init}
                    section['components'][i] = cls(**{f.name: getattr(chart, f.name) for f in dataclasses.fields(chart) if f.name in names})
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    if baseline:
        old = subprocess.check_output(['git', 'show', BASELINE + ':trellum/static/js/components/chart_base.js'], text=True)
        html = output / 'index.html'
        content = html.read_text()
        assert load_js('components/chart_base.js') in content
        html.write_text(content.replace(load_js('components/chart_base.js'), old))


def _open(page, output):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip('/')
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='Missing test asset')
    page.route('**/*', serve)
    page.goto('http://bars.test/index.html', wait_until='networkidle')
    page.evaluate('document.fonts.ready')
    for cid in page.locator('canvas[data-fw-kind]').evaluate_all('els=>els.map(e=>e.id)'):
        if not page.locator('#' + cid).is_visible():
            continue
        page.locator('#' + cid).scroll_into_view_if_needed()
        page.wait_for_function('id=>!!window._chartInstances[id]', arg=cid)


_STATE = '''() => Object.values(window._chartInstances).map(c=>({
    id:c.canvas.id,title:c.canvas.dataset.fwTitle,height:c.height,
    domain:Object.fromEntries(Object.entries(c.scales).map(([k,s])=>[k,[s.min,s.max]])),
    values:c.data.datasets.map(d=>d.data.slice()),
    labels:c.data.labels.slice(),
    styles:c.data.datasets.map(d=>[d.backgroundColor,d.borderColor,d.borderWidth,d.borderRadius,d.type||'bar']),
    grid:Object.values(c.scales).map(s=>s.options.grid.color),
    ticks:Object.values(c.scales).map(s=>[s.options.ticks.color,s.options.ticks.font.size]),
    interaction:c.options.interaction.mode,
    categoryWidth:c.options.indexAxis==='y'?c.scales.y.width:null
}))'''


def _check_browser(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright
    evidence = Path(os.environ.get('TRELLUM_BAR_EVIDENCE', tmp_path))
    output, before = evidence / f'current-{width}', evidence / f'baseline-{width}'
    output.mkdir(parents=True, exist_ok=True)
    before.mkdir(parents=True, exist_ok=True)
    _report(output)
    if BASELINE:
        _report(before, baseline=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            original = None
            before_absolute = before_percent = before_percent_state = None
            if BASELINE:
                baseline = browser.new_page(viewport={'width': width, 'height': 900})
                _open(baseline, before)
                original = baseline.evaluate(_STATE)
                (before / 'chart-state.json').write_text(json.dumps(original, indent=2))
                baseline.screenshot(path=str(before / 'light.png'), full_page=True)
                baseline_stack = next(c['id'] for c in original if c['title'] == 'Totals and fitting percentages · switch to Percentage')
                for title in ('Horizontal defaults · values automatic', 'Weighted revenue per order · undefined ratio is a dash', 'Totals and fitting percentages · switch to Percentage'):
                    cid = next(c['id'] for c in original if c['title'] == title)
                    baseline.locator('#' + cid).scroll_into_view_if_needed()
                    baseline.locator('#' + cid).screenshot(path=str(before / f'{cid}-light.png'))
                baseline.evaluate('window._barCsv=[];window._fwDownloadCsv=(...args)=>window._barCsv.push(args)')
                baseline.locator(f'[data-csv-chart="{baseline_stack}"]').click(force=True)
                before_absolute = baseline.evaluate('window._barCsv.at(-1)')
                baseline.locator(f'[data-normalize-toggle="{baseline_stack}"] button').filter(has_text='Percentage').click()
                before_percent_state = next(c for c in baseline.evaluate(_STATE) if c['id'] == baseline_stack)
                baseline.locator(f'[data-csv-chart="{baseline_stack}"]').click(force=True)
                before_percent = baseline.evaluate('window._barCsv.at(-1)')
                baseline.locator('#' + baseline_stack).screenshot(path=str(before / 'normalized.png'))
                baseline.locator('#fwThemeSelect').select_option('trellum dark')
                baseline.locator('canvas').first.scroll_into_view_if_needed()
                baseline.screenshot(path=str(before / 'dark.png'), full_page=True)
                baseline.close()
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            _open(page, output)
            state = page.evaluate(_STATE)
            (output / 'chart-state.json').write_text(json.dumps(state, indent=2))
            if original is not None:
                assert state == original
            ids = {s['title']: s['id'] for s in state}
            bar = ids['Horizontal defaults · values automatic']
            off = ids['Horizontal show_values=False · full plot width']
            grouped = ids['Static signed grouped']
            ratio = ids['Weighted revenue per order · undefined ratio is a dash']
            stack = ids['Totals and fitting percentages · switch to Percentage']
            line = ids['Revenue totals exclude order line overlay']
            def paint(cid):
                return page.evaluate('id=>window._chartInstances[id]._fwBarLabelPaint', cid)
            def space(cid):
                return page.evaluate('id=>window._chartInstances[id]._fwBarLabelSpace', cid)
            assert space(bar) >= 62 and space(off) == 0
            assert not paint(off)
            assert [p['text'] for p in paint(grouped)].count('0.00') == 2
            assert any(p['text'].startswith('-') for p in paint(grouped))
            assert any(p['text'] == '—' for p in paint(ratio))
            assert page.evaluate('id=>{const c=window._chartInstances[id];return c.data.datasets[0].data[c.data.labels.indexOf("Direct storefront")]}', ratio) == 24600 / 1720
            for title in ('Horizontal dates · automatic labels remain off', 'Vertical presentation unchanged · no new values', 'Generic stacked BarChart unchanged', 'Legacy net-color orientation', 'Legacy ratio stacking'):
                assert not paint(ids[title])
            assert len(paint(ids['Dates explicit on'])) == 2
            assert len(paint(ids['Dense grouped'])) < 80
            assert not paint(ids['Empty stacks'])
            expect(page.locator(f'[data-stack-label-context="{ids["Duplicate source columns"]}"]')).to_contain_text('duplicate source columns')
            # All actual fillText bounds fit and do not collide.
            def fitting():
                return page.evaluate('''() => Object.values(window._chartInstances).every(c=>{
                    const a=c._fwBarLabelPaint||[];
                    return a.every((p,i)=>p.bounds.left>=0 && p.bounds.right<=c.width && p.bounds.top>=0 && p.bounds.bottom<=c.height &&
                      a.slice(i+1).every(q=>p.bounds.right<=q.bounds.left || q.bounds.right<=p.bounds.left || p.bounds.bottom<=q.bounds.top || q.bounds.bottom<=p.bounds.top));
                })''')
            assert fitting()
            # Genuine canvas raster around a newly drawn label (outside plot).
            assert page.evaluate('''id=>{const c=window._chartInstances[id],p=c._fwBarLabelPaint[0],r=c.currentDevicePixelRatio,b=p.bounds;
              const pixels=c.ctx.getImageData(Math.floor(b.left*r),Math.floor(b.top*r),Math.ceil((b.right-b.left)*r),Math.ceil((b.bottom-b.top)*r)).data;
              return pixels.some((v,i)=>i%4===3 && v>100);}''', bar)
            for cid in (bar, ratio, stack):
                page.locator('#' + cid).scroll_into_view_if_needed()
                page.locator('#' + cid).screenshot(path=str(output / f'{cid}-light.png'))
            page.screenshot(path=str(output / 'light.png'), full_page=True)
            # Grouped centers are the real series centers, and hidden-series values disappear.
            assert page.evaluate('''id=>{const c=window._chartInstances[id];return c._fwBarLabelPaint.every(p=>p.y===c.getDatasetMeta(p.series).data[p.index].y)}''', grouped)
            page.locator('#' + grouped).scroll_into_view_if_needed()
            legend = page.locator('#' + grouped).locator('..').locator('xpath=preceding-sibling::*[1]')
            legend.locator('.cl').first.click()
            assert all(p['series'] == 1 for p in paint(grouped))
            legend.locator('.cl').first.click()
            # Actual pointer tooltip stays on the existing exact-value path.
            page.locator('#' + bar).scroll_into_view_if_needed()
            pos = page.evaluate('''id=>{const c=window._chartInstances[id],e=c.getDatasetMeta(0).data[c.data.labels.indexOf("Direct storefront")],r=c.canvas.getBoundingClientRect();return {x:r.x+(e.x+e.base)/2,y:r.y+e.y}}''', bar)
            page.mouse.move(pos['x'], pos['y'])
            page.wait_for_function('id=>window._chartInstances[id].tooltip.dataPoints?.length>0', arg=bar)
            assert '24.6k' in page.evaluate('id=>window._chartInstances[id].tooltip.body[0].lines[0]', bar)
            page.screenshot(path=str(output / 'tooltip.png'))
            # Capture the actual CSV button's raw download arguments in both modes.
            page.evaluate('window._barCsv=[];window._fwDownloadCsv=(...args)=>window._barCsv.push(args)')
            page.locator(f'[data-csv-chart="{stack}"]').click(force=True)
            absolute_csv = page.evaluate('window._barCsv.at(-1)')
            if before_absolute is not None:
                assert absolute_csv == before_absolute
            page.locator(f'[data-normalize-toggle="{stack}"] button').filter(has_text='Percentage').click()
            assert any(p['kind'] == 'percentage' for p in paint(stack))
            assert not any(p['kind'] == 'percentage' and p['index'] == 5 for p in paint(stack))
            context = page.locator(f'[data-stack-label-context="{stack}"]')
            expect(context).to_contain_text('including hidden series')
            total_before = [p['text'] for p in paint(stack) if p['kind'] == 'total' and p['index'] == 0]
            assert total_before == ['$23.4k']
            page.locator(f'[data-csv-chart="{stack}"]').click(force=True)
            percent_csv = page.evaluate('window._barCsv.at(-1)')
            if before_percent is not None:
                assert percent_csv == before_percent
                assert next(c for c in page.evaluate(_STATE) if c['id'] == stack) == before_percent_state
            assert absolute_csv[0] == percent_csv[0]
            assert sum(absolute_csv[1][0][1:]) == 23421
            assert sum(percent_csv[1][0][1:]) == pytest.approx(100)
            # Toggle the real HTML legend, preserving the all-series denominator.
            page.locator('#' + stack).scroll_into_view_if_needed()
            legend = page.locator('#' + stack).locator('..').locator('xpath=preceding-sibling::*[1]')
            values_before = page.evaluate('id=>window._chartInstances[id].data.datasets.map(d=>d.data.slice())', stack)
            legend.locator('.cl').first.click()
            assert page.evaluate('id=>window._chartInstances[id].data.datasets.map(d=>d.data.slice())', stack) == values_before
            assert [p['text'] for p in paint(stack) if p['kind'] == 'total' and p['index'] == 0] == ['$9.0k']
            page.locator(f'[data-csv-chart="{stack}"]').click(force=True)
            assert page.evaluate('window._barCsv.at(-1)') == percent_csv
            assert page.evaluate('id=>window._chartInstances[id].scales.y.max', stack) < 100
            legend.locator('.cl').first.click()
            page.screenshot(path=str(output / 'normalized.png'))
            # Totals exclude the actual order line.
            if width == 1280:
                assert [p['text'] for p in paint(line) if p['kind'] == 'total' and p['index'] == 0] == ['$23.4k']
            # Breakdown and filter controls redraw labels from actual current state.
            page.locator(f'[data-stack-toggle="{stack}"] button').filter(has_text='Platform').click()
            assert len(page.evaluate('id=>window._chartInstances[id].data.datasets', stack)) == 2
            assert [p['text'] for p in paint(stack) if p['kind'] == 'total' and p['index'] == 0] == ['$23.4k']
            page.get_by_role('button', name='Mobile', exact=True).last.click()
            page.wait_for_function('id=>window._chartInstances[id].data.datasets.length===1', arg=stack)
            assert [p['text'] for p in paint(stack) if p['kind'] == 'total' and p['index'] == 0] == ['$15.6k']
            for theme in ('trellum dark', 'trellum light'):
                page.locator('#fwThemeSelect').select_option(theme)
                for cid in (bar, ratio, stack):
                    page.locator('#' + cid).scroll_into_view_if_needed()
                    page.wait_for_function('id=>!!window._chartInstances[id]', arg=cid)
                assert page.locator('html').get_attribute('data-theme') == theme
                assert fitting()
                if theme == 'trellum dark':
                    for cid in (bar, ratio, stack):
                        page.locator('#' + cid).scroll_into_view_if_needed()
                        page.locator('#' + cid).screenshot(path=str(output / f'{cid}-dark.png'))
                    page.screenshot(path=str(output / 'dark.png'), full_page=True)
            for _ in range(3):
                page.evaluate('window.renderAll()')
                page.locator('#' + stack).scroll_into_view_if_needed()
                page.wait_for_function('id=>!!window._chartInstances[id]', arg=stack)
            assert context.count() == 1
            page.locator(f'[data-normalize-toggle="{stack}"] button').filter(has_text='Absolute').click()
            assert not any(p['kind'] == 'percentage' for p in paint(stack))
            # Local 240px panel drops labels and releases reserved width.
            page.locator('#' + bar).evaluate("e=>e.parentElement.style.width='240px'")
            page.locator('#' + bar).scroll_into_view_if_needed()
            page.wait_for_function('id=>window._chartInstances[id].width===240', arg=bar)
            assert space(bar) == 0 and not paint(bar)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors, errors
            (output / 'label-paint.json').write_text(json.dumps(page.evaluate('() => Object.fromEntries(Object.entries(window._chartInstances).map(([id,c])=>[id,c._fwBarLabelPaint]))'), indent=2))
        finally:
            browser.close()


def test_bar_labels_chromium_desktop(tmp_path):
    _check_browser(tmp_path, 1280)


@pytest.mark.heavy
def test_bar_labels_chromium_phone(tmp_path):
    _check_browser(tmp_path, 390)


def test_raw_label_eligibility_guards_and_population_checks():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.evaluate("window._fwRenderers={}")
            page.evaluate(load_js('components/chart_base.js'))
            result = page.evaluate('''() => {
              const base={x:'x',value_cols:['v']}, rows=[{x:'A',s:'One',v:10}],
                plot={labels:['A'],datasets:[{data:[10]}]};
              const check=(cfg,r,s,p)=>_fwStackLabelEligibility(cfg,r,s,p);
              const before=JSON.stringify(plot);
              const out={
                complete:check(base,rows,'s',plot),
                missing:check(base,[{x:'A',s:'One',v:null}],'s',plot),
                signed:check(base,[{x:'A',s:'One',v:-1}],'s',plot),
                nonfinite:check(base,[{x:'A',s:'One',v:Infinity}],'s',plot),
                duplicate:check({...base,value_cols:['v','v']},rows,'s',plot),
                tuple:check(base,[{x:'A||B',s:'C',v:10},{x:'A',s:'B||C',v:20}],'s',{labels:['A||B','A'],datasets:[{data:[30,30]}]}),
                other:check({...base,max_stacks:2},[{x:'A',s:'Other (2)',v:100},{x:'A',s:'A',v:90},{x:'A',s:'B',v:20},{x:'A',s:'C',v:10}],'s',{labels:['A'],datasets:[{data:[100]},{data:[90]},{data:[30]}]}),
                mismatch:check(base,rows,'s',{labels:['A'],datasets:[{data:[20]}]}),
                omitted:check(base,rows,'s',{labels:[],datasets:[]}),
                overlay:check(base,rows,'s',{labels:['A'],datasets:[{data:[10]},{type:'line',data:[999],yAxisID:'y1'}]}),
                normalized:check(base,rows,'s',{labels:['A'],datasets:[{data:[100],_absData:[10]}]}),
                zero:check(base,[{x:'A',s:'One',v:0}],'s',{labels:['A'],datasets:[{data:[0],_absData:[0]}]}),
                overflow:check(base,[{x:'A',s:'One',v:1e308},{x:'A',s:'Two',v:1e308}],'s',{labels:['A'],datasets:[{data:[1e308]},{data:[1e308]}]})
              };
              return {out,unmodified:before===JSON.stringify(plot)};
            }''')
            assert result['unmodified']
            reasons = result['out']
            for key in ('complete', 'overlay', 'normalized', 'zero'):
                assert reasons[key] == ''
            assert reasons['duplicate'] == 'duplicate source columns'
            assert 'identity' in reasons['tuple'] and 'Other' in reasons['other']
            for key in ('missing', 'nonfinite'):
                assert 'incomplete or nonfinite' in reasons[key]
            assert 'signed' in reasons['signed']
            for key in ('mismatch', 'omitted', 'overflow'):
                assert 'populations differ' in reasons[key]
        finally:
            browser.close()


def test_live_raw_eligibility_with_actual_filters_and_normalization(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from trellum.components import DataSource, FilterBar, Toggle, Visible
    frame = pd.DataFrame([
        {'case': case, 'x': x, 'source': source, 'amount': amount}
        for case, observations in {
            'Complete': [('A', 'One', 25), ('A', 'Two', 15), ('Zero', 'One', 0), ('Zero', 'Two', 0)],
            'Incomplete': [('A', 'One', 90), ('A', 'Two', None)],
            'Signed': [('A', 'One', 100), ('A', 'Two', -60)],
            'Tuple': [('A||B', 'C', 10), ('A', 'B||C', 20)],
            'Other': [('A', 'Other (2)', 100), ('A', 'A', 90), ('A', 'B', 20), ('A', 'C', 10)],
        }.items() for x, source, amount in observations
    ])
    ctx = _context(tmp_path)
    ctx.add_section('Raw eligibility', [DataSource('eligibility', frame),
        FilterBar('eligibility', frame, filters=[{'column': 'case', 'type': 'toggle'}]),
        StackedBar(frame, 'x', ['amount'], 'Raw population', dataset_id='eligibility', stack_by='source',
                   max_stacks=2, normalize_toggle=True, show_totals=True, show_percentages=True),
        StackedBar(frame, 'x', ['amount'], 'Raw legacy', dataset_id='eligibility', stack_by='source',
                   max_stacks=2, normalize_toggle=True),
    ])
    ctx.add_section('Reveal example', [Toggle('bar-reveal', ['Hide example', 'Show example']),
        Visible([BarChart(pd.DataFrame({'category': ['A', 'B'], 'value': [14, 27]}),
            'category', 'value', 'Revealed bars', horizontal=True, static=True)], 'bar-reveal', 'Show example')])
    render_report(ctx, str(tmp_path), auto_refresh=False)
    copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            _open(page, tmp_path)
            cid = page.locator('canvas[data-fw-title="Raw population"]').get_attribute('id')
            old = page.locator('canvas[data-fw-title="Raw legacy"]').get_attribute('id')
            context = page.locator(f'[data-stack-label-context="{cid}"]')
            for case, reason in [('Incomplete', 'incomplete'), ('Signed', 'signed'), ('Tuple', 'identity'), ('Other', 'Other identity')]:
                page.get_by_role('button', name=case, exact=True).click()
                expect(context).to_contain_text(reason)
                assert page.evaluate('id=>window._chartInstances[id]._fwBarLabelPaint', cid) == []
                assert page.locator(f'[data-stack-label-context="{old}"]').count() == 0
                assert page.evaluate('ids=>ids.map(id=>window._chartInstances[id].data.datasets.map(d=>d.data))', [cid, old])[0] == page.evaluate('id=>window._chartInstances[id].data.datasets.map(d=>d.data)', old)
            page.get_by_role('button', name='Complete', exact=True).click()
            expect(context).not_to_contain_text('withheld')
            assert [p['text'] for p in page.evaluate('id=>window._chartInstances[id]._fwBarLabelPaint', cid)] == ['$40.00', '$0.00']
            page.locator(f'[data-normalize-toggle="{cid}"] button').filter(has_text='Percentage').click()
            labels = page.evaluate('id=>window._chartInstances[id]._fwBarLabelPaint', cid)
            assert {p['text'] for p in labels if p['kind'] == 'percentage'} == {'63%', '38%'}
            assert not any(p['index'] == 1 and p['kind'] == 'percentage' for p in labels)
            # Turning options off via a rebuilt config releases top padding and removes context.
            page.evaluate('''id=>{const cfg=window._reportData.components[id];
              cfg.stack_labels={totals:false,percentages:false,format:'currency'};
              window.renderAll();}''', cid)
            page.locator('#' + cid).scroll_into_view_if_needed()
            assert context.count() == 0
            assert page.evaluate('id=>window._chartInstances[id].options.layout.padding.top', cid) == 0
            page.get_by_role('button', name='Show example', exact=True).click()
            revealed = page.locator('canvas[data-fw-title="Revealed bars"]')
            revealed.scroll_into_view_if_needed()
            reveal_id = revealed.get_attribute('id')
            page.wait_for_function('id=>window._chartInstances[id]?._fwBarLabelPaint?.length===2', arg=reveal_id)
            assert page.evaluate('id=>window._chartInstances[id]._fwBarLabelSpace', reveal_id) >= 62
            assert not errors, errors
        finally:
            browser.close()


def test_tagged_stack_preserves_workspace_leases(tmp_path):
    from playwright.sync_api import sync_playwright
    from trellum.components import DataSource, FilterBar
    frame = pd.DataFrame({'x': ['A', 'A', 'B', 'B'], 'source': ['One', 'Two'] * 2,
                          'platform': ['Web', 'Mobile'] * 2, 'value': [25, 15, 30, 20]})
    ctx = _context(tmp_path)
    ctx.add_section('Workspace labels', [DataSource('workspace-bars', frame),
        FilterBar('workspace-bars', frame, filters=[{'column': 'platform', 'type': 'toggle'}]),
        StackedBar(frame, 'x', ['value'], 'Workspace stack', dataset_id='workspace-bars', stack_by='source',
                   stack_by_options={'Source': 'source', 'Platform': 'platform'}, normalize_toggle=True,
                   show_totals=True, show_percentages=True)])
    render_report(ctx, str(tmp_path), auto_refresh=False)
    copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            _open(page, tmp_path)
            page.evaluate('window._fwMetricsWorkspace={};window._fwWorkspaceWork={max_chart_ms:0,max_consumer_ms:0}')
            cid = page.locator('canvas').get_attribute('id')
            for _ in range(3):
                page.evaluate('window.renderAll()')
                page.locator('#' + cid).scroll_into_view_if_needed()
            assert page.evaluate('Object.keys(window._fwMetricDrawLeases).length') == 1
            assert page.locator(f'[data-stack-label-context="{cid}"]').count() == 1
            page.locator(f'[data-normalize-toggle="{cid}"] button').filter(has_text='Percentage').click()
            assert any(p['kind'] == 'percentage' for p in page.evaluate('id=>window._chartInstances[id]._fwBarLabelPaint', cid))
            page.get_by_role('button', name='Mobile', exact=True).click()
            page.wait_for_function('id=>window._chartInstances[id].data.datasets.length===1', arg=cid)
            assert [p['text'] for p in page.evaluate('id=>window._chartInstances[id]._fwBarLabelPaint', cid) if p['kind'] == 'total'] == ['$15.00', '$20.00']
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
            assert page.evaluate('Object.keys(window._fwMetricDrawLeases).length') == 0
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
            page.locator('#' + cid).scroll_into_view_if_needed()
            page.wait_for_function('Object.keys(window._fwMetricDrawLeases).length===1')
            page.get_by_role('button', name='Web', exact=True).click()
            page.wait_for_function('id=>{const d=window._chartInstances[id].data.datasets[0];return (d._absData||d.data)[0]===25}', arg=cid)
            assert page.locator(f'[data-stack-label-context="{cid}"]').count() == 1
            assert not errors, errors
        finally:
            browser.close()
