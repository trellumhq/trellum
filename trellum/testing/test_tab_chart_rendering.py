"""Generated tabs exercised with the real runtime, loader and vendored charts."""

from pathlib import Path

import pandas as pd
import pytest
from playwright.sync_api import sync_playwright

import trellum
from trellum.assets import load_js
from trellum.components import Grid, LineChart, RenderContext, TabGroup
from trellum.rendering.js_runtime import generate_js_runtime
from trellum.themes import THEME_REGISTRY, DefaultTheme


@pytest.fixture
def tab_page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.set_default_timeout(4000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def build(mode, layout, query=''):
            ctx = RenderContext(theme=DefaultTheme)
            data = pd.DataFrame({'label': ['A', 'B'], 'value': [2, 5]})
            charts = {}

            def row(name):
                children = [LineChart(data, x='label', y='value', title=f'{name} {i}') for i in range(3)]
                charts[name] = children
                return Grid(children, columns=3)

            def group(name, contents):
                return TabGroup(id=name, mode=mode, tabs=[
                    {'label': f'{name} {i}', 'content': content} for i, content in enumerate(contents)
                ])

            inner = group('inner', [[row('inner0')], [row('inner1')]]) if layout == 'nested' else None
            outer = group('outer', [[inner] if layout == 'nested' else [row('outer0')],
                                    [row('outer1')], [row('outer2')]])
            children = [outer, group('adjacent', [[row('adjacent0')], [row('adjacent1')]])]
            if layout == 'grid':
                children = [Grid(children, columns=2)]
            # Offscreen content must remain lazy even while exercising tab reveals.
            markup = ctx.render_children(children) + '<div style="height:2500px"></div>'
            markup += ctx.render_child(row('offscreen'))
            css, js = ctx.collect_assets()
            runtime = generate_js_runtime('light', THEME_REGISTRY, refresh_seconds=0)
            vendor = Path(trellum.__file__).resolve().parent / 'static/vendor'
            html = f'''<!doctype html><html><head><style>{DefaultTheme.to_css_vars(':root')}{(vendor / "slimselect.css").read_text()}{css}</style>
                <script src="/chart.js"></script><script src="/slimselect.js"></script>
                </head><body>{markup}
                <script>window._chartInstances = {{}}; window._fwRenderers = {{}};</script>
                {runtime}<script>{js}</script><script>{load_js('data_loader.js')}</script>
                </body></html>'''
            page.route('http://tabs.test/**', lambda route: route.fulfill(content_type='text/html', body=html))
            page.route('**/chart.js', lambda route: route.fulfill(path=str(vendor / 'chart.umd.min.js')))
            page.route('**/slimselect.js', lambda route: route.fulfill(path=str(vendor / 'slimselect.min.js')))
            page.route('**/_meta.json', lambda route: route.fulfill(json={}))
            page.route('**/data.json?*', lambda route: route.fulfill(json={'components': ctx._component_data}))
            page.goto('http://tabs.test/' + query)
            page.wait_for_function('window._fwTimings.total_ms !== undefined')
            page.evaluate('() => new Promise(r => requestIdleCallback(() => requestAnimationFrame(() => requestAnimationFrame(r))))')
            return {name: [page.locator(f'[data-fw-title="{chart.title}"]').get_attribute('id')
                           for chart in values] for name, values in charts.items()}

        yield page, build
        assert errors == []
        browser.close()


def select(page, mode, group, index):
    control = page.locator(f'[data-tab-id="{group}"]')
    if mode == 'tabs':
        control.locator('button').nth(index).click()
    else:
        # SlimSelect hides the native select; use its real visible menu.
        control.locator('xpath=following-sibling::*[contains(@class,"ss-main")][1]').click()
        page.get_by_role('option', name=f'{group} {index}', exact=True).click()


def painted(page, ids):
    for cid in ids:
        assert page.locator('#' + cid).is_visible(), f'{cid} canvas is hidden'
        page.wait_for_function('''id => {
            const c = window._chartInstances[id];
            return c && c.width > 0 && c.height > 0 && c.getDatasetMeta(0).data.length === 2
                && c.canvas.getContext('2d').getImageData(0, 0, c.canvas.width, c.canvas.height).data.some(v => v);
        }''', arg=cid, timeout=3000)
    return page.evaluate('ids => ids.map(id => window._chartInstances[id].id)', ids)


@pytest.mark.parametrize('mode', ['tabs', 'dropdown'])
@pytest.mark.parametrize('layout', ['nested', 'adjacent', 'grid'])
def test_tab_chart_ownership_and_revisits(tab_page, mode, layout):
    page, build = tab_page
    ids = build(mode, layout)
    first = 'inner0' if layout == 'nested' else 'outer0'
    initial = painted(page, ids[first])
    adjacent = painted(page, ids['adjacent0'])
    if layout == 'nested':
        select(page, mode, 'inner', 1)
        inner = painted(page, ids['inner1'])
    for index in (1, 2):
        select(page, mode, 'outer', index)
        identity = painted(page, ids[f'outer{index}'])
        painted(page, ids['adjacent0'])
        select(page, mode, 'outer', 0)
        assert painted(page, ids['inner1'] if layout == 'nested' else ids[first]) == (
            inner if layout == 'nested' else initial)
        select(page, mode, 'outer', index)
        assert painted(page, ids[f'outer{index}']) == identity
    select(page, mode, 'adjacent', 1)
    painted(page, ids['adjacent1'])
    painted(page, ids['outer2'])
    select(page, mode, 'adjacent', 0)
    assert painted(page, ids['adjacent0']) == adjacent
    assert page.evaluate('ids => ids.every(id => !window._chartInstances[id])', ids['offscreen'])


@pytest.mark.parametrize('mode', ['tabs', 'dropdown'])
@pytest.mark.parametrize('target', ['outer', 'inner'])
def test_tab_chart_direct_url(tab_page, mode, target):
    page, build = tab_page
    value = '1' if mode == 'dropdown' else f'{target} 1'
    ids = build(mode, 'nested', f'?tab.{target}={value}')
    painted(page, ids[f'{target}1'])
    painted(page, ids['adjacent0'])
    if target == 'outer':
        select(page, mode, 'outer', 0)
        painted(page, ids['inner0'])
    else:
        select(page, mode, 'outer', 1)
        painted(page, ids['outer1'])
        select(page, mode, 'outer', 0)
        painted(page, ids['inner1'])
