"""Native report controls in route-intercepted builds with vendored assets."""

import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from trellum.components import Dropdown, RawHTML, TabGroup
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_report_controls import example_context
from trellum.validation import validate_report


def _build(output):
    ctx = example_context(output)
    result = validate_report(ctx, details=compute_details(ctx))
    result.write(str(output))
    assert not [c for c in result.checks if c.level in ('fail', 'warn')], result.checks
    ctx.add_section('Escaping and keyboard', [
        Dropdown('quoted"id', ['<img src=x onerror=alert(1)> & text', 'Second choice'], label='Safe <caption>'),
        Dropdown('short', ['Revenue', 'Orders'], label='Short choice'),
    ])
    # Compatibility fixture: the exact unwrapped control/panel structure emitted
    # by previous versions, including siblings and a nested group.
    ctx.add_section('Legacy markup', [RawHTML('''
        <select id="legacy-choice" class="fw-dropdown"><option>A</option><option>B</option></select>
        <div class="fw-tab-group" data-tab-id="legacy-a"><button class="fw-tab-btn active">A1</button><button class="fw-tab-btn">A2</button></div>
        <div class="fw-tab-panel" id="legacy-a1">
          <div class="fw-tab-group" data-tab-id="legacy-inner"><button class="fw-tab-btn active">I1</button><button class="fw-tab-btn">I2</button></div>
          <div class="fw-tab-panel" id="legacy-i1">Inner one</div><div class="fw-tab-panel" id="legacy-i2" style="display:none">Inner two</div>
        </div><div class="fw-tab-panel" id="legacy-a2" style="display:none">A second</div>
        <select class="fw-tab-dropdown" data-tab-id="legacy-b"><option value="0">B1</option><option value="1">B2</option></select>
        <div class="fw-tab-panel" id="legacy-b1">B first</div><div class="fw-tab-panel" id="legacy-b2" style="display:none">B second</div>
        <div class="fw-tab-group" data-tab-id="legacy-c"><button class="fw-tab-btn active">C1</button><button class="fw-tab-btn">C2</button></div>
        <div class="fw-tab-panel" id="legacy-c1">C first</div><div class="fw-tab-panel" id="legacy-c2" style="display:none">C second</div>
    ''')])
    ctx.add_section('Nested selector ownership', [
        TabGroup([
            {'label': 'Selector parent one', 'content': [TabGroup([
                {'label': 'Selector inner one', 'content': [Dropdown('nested-choice', ['One', 'Two'])]},
                {'label': 'Selector inner two', 'content': [Dropdown('nested-other', ['Three', 'Four'])]},
            ], 'selector-inner')]},
            {'label': 'Selector parent two', 'content': [Dropdown('parent-choice', ['Five', 'Six'])]},
        ], 'selector-parent', 'dropdown'),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


def _root(page, tab_id):
    return page.locator(f'[data-tab-id="{tab_id}"]').first.locator('xpath=ancestor::div[contains(@class,"fw-tab-root")][1]')


def _choice(select):
    return select.locator('xpath=..').locator('.ss-main')


def _pick(page, select, search):
    _choice(select).click()
    popup = page.locator('.ss-content.ss-open-below, .ss-content.ss-open-above').last
    popup.locator('input').fill(search)
    popup.locator('.ss-option').filter(has_text=search).last.click()
    # SlimSelect closes and returns keyboard focus to the trigger.
    page.wait_for_function('el => el.getAttribute("aria-expanded") === "false"', arg=_choice(select).element_handle())


def _painted(page, root):
    canvas = root.locator('canvas:visible').first
    canvas.scroll_into_view_if_needed()
    page.wait_for_function('''el => {
        const chart = (window._chartInstances || {})[el.id];
        return chart && chart.width > 0 && chart.height > 0 &&
            el.getContext('2d').getImageData(0,0,el.width,el.height).data.some(v => v > 0);
    }''', arg=canvas.element_handle())
    return canvas.get_attribute('id')


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_report_choices_tabs_and_painted_charts(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: m.type == 'error' and errors.append(m.text))

            def serve(route):
                assert urlparse(route.request.url).hostname == 'controls.test'
                path = tmp_path / unquote(urlparse(route.request.url).path).lstrip('/')
                if path.is_file():
                    route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                else:
                    route.fulfill(status=404, body='Missing test asset')

            page.route('**/*', serve)
            url = ('http://controls.test/index.html?tab.showcase_controls_tabs='
                   'Orders%20across%20all%20supported%20platforms%20%E2%80%94%20daily%20supplied%20totals'
                   '&tab.showcase_controls_selector=11&tab.showcase_controls_sidebar=Daily%20orders'
                   '&tab.showcase_controls_inner=Nested%20orders&tab.legacy-b=1')
            page.goto(url, wait_until='networkidle')
            page.evaluate('document.fonts.ready')
            assert errors == []

            # Explicit labels, native option text and a disabled empty explanation.
            assert page.get_by_text('Safe <caption>', exact=True).is_visible()
            assert page.locator('.fw-choice img').count() == 0
            safe = page.locator('select').filter(has=page.locator('option', has_text='<img src=x'))
            assert safe.evaluate('el => el.id') == 'quoted"id'
            assert _choice(safe).inner_text() == '<img src=x onerror=alert(1)> & text'
            empty = page.locator('#showcase_controls_empty')
            assert empty.is_disabled()
            assert 'No choices available' in _choice(empty).inner_text()
            assert page.get_by_text('No report views available', exact=True).is_visible()
            assert _choice(page.locator('#short')).bounding_box()['height'] >= (44 if width == 390 else 36)

            choice = page.locator('#showcase_controls_caption')
            choice.evaluate('el => { window.choiceChanges=[]; el.addEventListener("change", () => window.choiceChanges.push(el.value)); }')
            _pick(page, choice, 'Orders across platforms')
            assert choice.input_value().startswith('Orders across platforms')
            assert page.evaluate('window.choiceChanges.length') == 1
            assert _choice(choice).evaluate('el => el === document.activeElement')
            saved = page.evaluate('window._saveState()')
            assert saved['dropdown_showcase_controls_caption'] == choice.input_value()
            _pick(page, choice, 'UnbrokenIdentifier_')
            assert choice.input_value().startswith('UnbrokenIdentifier_')
            page.evaluate('s => window._restoreState(s)', saved)
            assert _choice(choice).inner_text().startswith('Orders across platforms')
            # Focus label and use the vendor's keyboard search/selection.
            page.get_by_text('Displayed caption', exact=True).click()
            _choice(choice).press('Enter')
            popup = page.locator('.ss-content.ss-open-below, .ss-content.ss-open-above').last
            popup.locator('input').fill('UnbrokenIdentifier_')
            popup.locator('input').press('ArrowDown')
            popup.locator('input').press('Enter')
            assert choice.input_value().startswith('UnbrokenIdentifier_')
            # Same wide viewport, a genuinely narrow containing panel.
            if width == 1280:
                choice.evaluate('el => el.parentElement.style.width = "200px"')
            _choice(choice).click()
            popup = page.locator('.ss-content.ss-open-below, .ss-content.ss-open-above').last
            assert popup.locator('.ss-option').count() == 3
            popup.locator('input').fill('platform')
            assert popup.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
            assert choice.evaluate('el => el.parentElement.scrollWidth <= el.parentElement.clientWidth + 1')
            popup.locator('input').press('Escape')
            choice.evaluate('el => el.parentElement.style.width = ""')
            # External listeners still use native value + change, with a synced caption.
            choice.evaluate("el => {el.value = el.options[0].value; el.dispatchEvent(new Event('change', {bubbles:true}));}")
            assert _choice(choice).inner_text().startswith('Daily revenue')

            tabs = _root(page, 'showcase_controls_tabs')
            assert tabs.locator('.fw-tab-btn.active').inner_text().startswith('Orders across')
            _painted(page, tabs)
            tablist = tabs.locator('.fw-tab-group')
            tablist.locator('.fw-tab-btn.active').press('Home')
            assert tablist.locator('.fw-tab-btn.active').inner_text() == 'Revenue'
            assert tablist.locator('.fw-tab-btn.active').get_attribute('aria-selected') == 'true'
            assert tablist.locator('.fw-tab-btn.active').evaluate('el => el === document.activeElement')
            tablist.locator('.fw-tab-btn.active').press('ArrowRight')
            assert tablist.locator('.fw-tab-btn.active').inner_text().startswith('Orders across')
            assert tablist.locator('.fw-tab-btn').first.bounding_box()['height'] >= (44 if width == 390 else 34)
            _painted(page, tabs)

            selector = page.locator('select[data-tab-id="showcase_controls_selector"]')
            assert selector.input_value() == '11'
            assert _choice(selector).inner_text() == 'Final supplied view'
            _painted(page, _root(page, 'showcase_controls_selector'))
            assert selector.locator('option').count() == 12
            _pick(page, selector, 'Longer report view')
            assert selector.input_value() == '8'
            assert 'tab.showcase_controls_selector=8' in page.url
            _painted(page, _root(page, 'showcase_controls_selector'))

            sidebar = _root(page, 'showcase_controls_sidebar')
            side_buttons = sidebar.locator('.fw-tab-group')
            side_select = sidebar.locator('select.fw-tab-dropdown')
            assert side_buttons.locator('.fw-tab-btn.active').inner_text() == 'Daily orders'
            assert side_select.input_value() == '2'
            assert side_buttons.is_visible() is (width == 1280)
            assert _choice(side_select).is_visible() is (width == 390)
            _painted(page, sidebar)
            assert sidebar.locator('.fw-chart-container:visible').bounding_box()['height'] == 220
            if width == 1280:
                assert side_buttons.bounding_box()['width'] == pytest.approx(180)
                # A native callback may select a different group synchronously.
                side_select.evaluate("""el => el.addEventListener('change', () => {
                    const other = document.querySelector('select[data-tab-id="showcase_controls_selector"]');
                    other.value = '7'; other.dispatchEvent(new Event('change', {bubbles:true}));
                }, {once:true})""")
                side_buttons.get_by_role('tab', name='Final supplied view', exact=True).click()
                assert _root(page, 'showcase_controls_selector').locator('.fw-tab-panel:visible').get_by_text(
                    'Selector: Regional coverage', exact=True).is_visible()
                _pick(page, selector, 'Longer report view')
                assert side_select.input_value() == '11'
                side_buttons.locator('.fw-tab-btn.active').press('Home')
                side_buttons.locator('.fw-tab-btn.active').press('ArrowDown')
                assert side_select.input_value() == '1'
                sidebar.evaluate('el => el.style.width = "400px"')
                page.wait_for_function('el => getComputedStyle(el).display === "none"', arg=side_buttons.element_handle())
                page.wait_for_function('el => el === document.activeElement', arg=_choice(side_select).element_handle())
            _pick(page, side_select, 'Revenue comparison')
            assert side_select.input_value() == '10'
            assert side_buttons.locator('.fw-tab-btn.active').inner_text() == 'Revenue comparison'
            assert 'tab.showcase_controls_sidebar=Revenue+comparison' in page.url
            _painted(page, sidebar)
            if width == 1280:
                _choice(side_select).focus()
                sidebar.evaluate('el => el.style.width = ""')
                page.wait_for_function('el => getComputedStyle(el).display !== "none"', arg=side_buttons.element_handle())
                assert side_buttons.locator('.fw-tab-btn.active').inner_text() == 'Revenue comparison'
                page.wait_for_function('el => el === document.activeElement', arg=side_buttons.locator('.fw-tab-btn.active').element_handle())
                assert _choice(side_select).inner_text() == 'Revenue comparison'
                _painted(page, sidebar)
                for panel_width, list_visible in ((520, False), (521, True)):
                    sidebar.evaluate('(el, width) => el.style.width = width + "px"', panel_width)
                    page.wait_for_function('(args) => (getComputedStyle(args.el).display !== "none") === args.visible',
                                           arg={'el': side_buttons.element_handle(), 'visible': list_visible})
                    assert side_select.input_value() == '10'
                    assert sidebar.locator('.fw-chart-container:visible').bounding_box()['height'] == 220
                # Expanding while search is open closes the now-hidden selector.
                sidebar.evaluate('el => el.style.width = "400px"')
                _choice(side_select).click()
                popup = page.locator('.ss-content.ss-open-below, .ss-content.ss-open-above').last
                popup.locator('input').fill('Daily')
                sidebar.evaluate('el => el.style.width = "700px"')
                page.wait_for_function('el => el === document.activeElement',
                                       arg=side_buttons.locator('.fw-tab-btn.active').element_handle())
                assert _choice(side_select).get_attribute('aria-expanded') == 'false'
                assert side_select.input_value() == '10'
                sidebar.evaluate('el => el.style.width = ""')

            outer = _root(page, 'showcase_controls_outer')
            inner = _root(page, 'showcase_controls_inner')
            sibling = _root(page, 'showcase_controls_sibling')
            assert inner.locator('.fw-tab-btn.active').inner_text() == 'Nested orders'
            _painted(page, inner)
            outer.get_by_role('tab', name='Outer orders', exact=True).click()
            assert inner.is_hidden()
            _painted(page, outer)
            assert sibling.get_by_role('tab', name='Sibling revenue', exact=True).get_attribute('aria-selected') == 'true'
            _painted(page, sibling)
            outer.get_by_role('tab', name='Outer revenue', exact=True).click()
            assert inner.locator('.fw-tab-btn.active').inner_text() == 'Nested orders'
            chart_id = _painted(page, inner)
            # Ordinary legend interaction still acts on the real chart.
            inner.locator('.fw-chart-legend:visible').get_by_text('iOS', exact=True).click()
            assert page.evaluate('id => window._chartInstances[id].isDatasetVisible(0)', chart_id) is False
            inner.locator('.fw-chart-legend:visible').get_by_text('iOS', exact=True).click()

            page.get_by_role('button', name='Android', exact=True).first.click()
            page.wait_for_function('id => window._chartInstances[id].data.datasets.length === 1', arg=chart_id)
            assert page.evaluate('id => window._chartInstances[id].data.datasets[0].label', chart_id) == 'Android'
            page.get_by_role('button', name='All', exact=True).first.click()
            page.wait_for_function('id => window._chartInstances[id].data.datasets.length === 2', arg=chart_id)

            # Refresh-state restoration keeps each group's key and own panels.
            saved = page.evaluate('window._saveState()')
            assert saved['tab_showcase_controls_selector'] == '8'
            assert saved['tab_showcase_controls_sidebar'] == 'Revenue comparison'
            sibling.get_by_role('tab', name='Sibling orders', exact=True).click()
            inner.get_by_role('tab', name='Nested revenue', exact=True).click()
            page.evaluate('s => window._restoreState(s)', saved)
            assert sibling.locator('.fw-tab-btn.active').inner_text() == 'Sibling revenue'
            assert inner.locator('.fw-tab-btn.active').inner_text() == 'Nested orders'
            _painted(page, inner)

            # A dropdown parent never changes its nested tab identity or state.
            parent_select = page.locator('select[data-tab-id="selector-parent"]')
            nested = _root(page, 'selector-inner')
            nested.get_by_role('tab', name='Selector inner two', exact=True).click()
            _pick(page, parent_select, 'Selector parent two')
            assert nested.is_hidden()
            _pick(page, parent_select, 'Selector parent one')
            assert nested.locator('.fw-tab-btn.active').inner_text() == 'Selector inner two'
            assert page.locator('#nested-other').evaluate('el => el.slim.getSelected()[0]') == 'Three'

            # Old standalone selects stay native; legacy sibling/nested ownership.
            assert page.locator('#legacy-choice').evaluate('el => !el.slim')
            assert page.locator('#legacy-b2').is_visible()
            page.get_by_role('button', name='I2', exact=True).click()
            page.get_by_role('button', name='A2', exact=True).click()
            assert page.locator('#legacy-a2').is_visible()
            assert page.locator('#legacy-b2').is_visible()
            assert page.locator('#legacy-c1').is_visible()
            page.get_by_role('button', name='A1', exact=True).click()
            assert page.locator('#legacy-i2').is_visible()
            page.locator('select[data-tab-id="legacy-b"]').select_option('0', force=True)
            assert page.locator('#legacy-b1').is_visible() and page.locator('#legacy-c1').is_visible()
            page.get_by_role('button', name='C2', exact=True).click()
            assert page.locator('#legacy-b1').is_visible() and page.locator('#legacy-c2').is_visible()
            legacy_saved = page.evaluate('window._saveState()')
            page.get_by_role('button', name='A2', exact=True).click()
            page.get_by_role('button', name='C1', exact=True).click()
            page.evaluate('s => window._restoreState(s)', legacy_saved)
            assert page.locator('#legacy-i2').is_visible()
            assert page.locator('#legacy-b1').is_visible() and page.locator('#legacy-c2').is_visible()
            # Reopening a shared URL restores each mode and nested group.
            page.evaluate('window._fwUrlSync.write()')
            page.reload(wait_until='networkidle')
            assert sidebar.locator('.fw-tab-btn.active').inner_text() == 'Revenue comparison'
            assert side_select.input_value() == '10'
            assert _choice(selector).inner_text().startswith('Longer report view')
            assert inner.locator('.fw-tab-btn.active').inner_text() == 'Nested orders'
            assert nested.locator('.fw-tab-btn.active').inner_text() == 'Selector inner two'
            _painted(page, inner)

            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.fw-choice').evaluate_all('els => els.every(el => el.scrollWidth <= el.clientWidth + 1)')
            shots = os.environ.get('TRELLUM_SCREENSHOT_DIR')
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                for canvas in page.locator('canvas:visible').all():
                    _painted(page, canvas.locator('xpath=..'))
                page.screenshot(path=str(Path(shots) / f'report-controls-{width}.png'), full_page=True)
                sidebar.evaluate('el => el.scrollIntoView({block: "center"})')
                sidebar.screenshot(path=str(Path(shots) / f'report-controls-sidebar-{width}.png'))
                _choice(choice).click()
                page.locator('.ss-content.ss-open-below, .ss-content.ss-open-above').last.locator('input').fill('platform')
                popup = page.locator('.ss-content.ss-open-below, .ss-content.ss-open-above').last
                page.wait_for_function('el => getComputedStyle(el).opacity === "1"', arg=popup.element_handle())
                page.screenshot(path=str(Path(shots) / f'report-controls-search-{width}.png'))
            assert errors == []
        finally:
            browser.close()
