"""Nested visibility through the real component, lazy-loader and Chart.js pipeline."""

from pathlib import Path

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import BarChart, RenderContext, Toggle, Visible
from trellum.rendering.js_runtime import generate_js_runtime
from trellum.themes import DefaultTheme, THEME_REGISTRY


@pytest.mark.parametrize('hidden', ['outer', 'middle', 'inner'])
def test_nested_visible_defers_and_reveals_real_charts(hidden):
    from playwright.sync_api import sync_playwright

    ctx = RenderContext(theme=DefaultTheme)
    data = pd.DataFrame({'label': ['A', 'B'], 'value': [2, 5]})
    children = [BarChart(data, x='label', y='value', title=f'Chart {i}') for i in range(3)]
    for level in ('inner', 'middle', 'outer'):
        children = [
            Toggle(level, ['Show', 'Hide'], default='Hide' if level == hidden else 'Show'),
            Visible(children, toggle_target=level, toggle_value='Show'),
        ]
    markup = ctx.render_children(children)
    css, js = ctx.collect_assets()
    runtime = generate_js_runtime('light', THEME_REGISTRY, refresh_seconds=0)
    html = f'''<!doctype html><html><head><style>{css}</style>
        <script src="/chart.js"></script></head><body>{markup}
        <script>window._chartInstances = {{}}; window._fwRenderers = {{}};</script>
        {runtime}<script>{js}</script><script>{load_js('data_loader.js')}</script>
        </body></html>'''
    ids = list(ctx._component_data)
    vendor = Path(__file__).resolve().parents[1] / 'static/vendor/chart.umd.min.js'
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://visible.test/', lambda route: route.fulfill(content_type='text/html', body=html))
        page.route('**/chart.js', lambda route: route.fulfill(path=str(vendor)))
        page.route('**/_meta.json', lambda route: route.fulfill(json={}))
        page.route('**/data.json?*', lambda route: route.fulfill(json={'components': ctx._component_data}))
        page.goto('http://visible.test/')
        page.wait_for_function('Object.keys(window._reportData?.components || {}).length === 3')
        # Queue behind the loader's idle callback, then allow observer delivery.
        page.evaluate('() => new Promise(resolve => requestIdleCallback(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))')
        assert page.evaluate('Object.keys(window._chartInstances)') == []

        def select(level, value):
            page.locator(f'[data-toggle-id="{level}"]').get_by_text(value, exact=True).click()

        def rendered():
            for cid in ids:
                page.locator('#' + cid).scroll_into_view_if_needed()
                page.wait_for_function('''id => {
                    const c = window._chartInstances[id];
                    return c && c.width > 0 && c.height > 0 && c.getDatasetMeta(0).data.length === 2;
                }''', arg=cid, timeout=5000)
                assert page.locator('#' + cid).evaluate('''canvas =>
                    canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some(v => v !== 0)
                ''')

        select(hidden, 'Show')
        rendered()
        instances = page.evaluate('(ids) => ids.map(id => window._chartInstances[id].id)', ids)
        # Track real resize calls, retaining Chart.js behavior. Explicit toggle
        # resizes must not reach a chart with any hidden ancestor.
        page.evaluate('''() => {
            window.hiddenResizes = [];
            Object.values(window._chartInstances).forEach(chart => {
                const resize = chart.resize;
                chart.resize = function(...args) {
                    if (!chart.canvas.getClientRects().length) window.hiddenResizes.push(chart.canvas.id);
                    return resize.apply(this, args);
                };
            });
        }''')
        select('outer', 'Hide')
        # Simulate programmatic nested toggle changes while the outer stays hidden.
        page.evaluate('''() => {
            const buttons = document.querySelectorAll('[data-toggle-id="inner"] button');
            buttons[1].click(); buttons[0].click();
        }''')
        assert page.evaluate('window.hiddenResizes') == []
        select('outer', 'Show')
        rendered()
        assert page.evaluate('(ids) => ids.map(id => window._chartInstances[id].id)', ids) == instances
        assert errors == []
        browser.close()
