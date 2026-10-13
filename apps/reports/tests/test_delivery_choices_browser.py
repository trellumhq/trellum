"""Shared remote picker lifecycle; live persistence is covered separately."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(params=['chromium', 'webkit'])
def remote_picker(request):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        behavior = {'fail': False, 'hold': False, 'held': [], 'requests': []}
        page.route('http://test.local/', lambda route: route.fulfill(content_type='text/html', body='''<form><label>Report<select name="report" data-choice-picker data-picker-label="Report" data-choice-source="/api/choices"><option value="">All reports</option><option value="99" selected>Saved selection</option></select></label></form>'''))
        def choices(route):
            params = parse_qs(urlsplit(route.request.url).query)
            behavior['requests'].append(params)
            if behavior['hold']:
                behavior['held'].append(route)
                return
            if behavior['fail']:
                route.fulfill(status=503, json={})
                return
            number = int(params.get('page', ['1'])[0])
            value = params.get('q', [''])[0] or str(number)
            route.fulfill(json={'results': [{'value': value, 'label': 'Choice ' + value}],
                'selected': {'value': '99', 'label': 'Saved selection'},
                'pagination': {'page': number, 'total': 50, 'has_previous': number > 1, 'has_next': number < 2}})
        page.route('**/api/choices?*', choices)
        page.goto('http://test.local/')
        page.add_style_tag(content=(ROOT / 'static/choice-picker.css').read_text())
        page.add_script_tag(content=(ROOT / 'static/choice-picker.js').read_text())
        page.evaluate("window.draftChanges=0;document.querySelector('form').addEventListener('change',()=>window.draftChanges++)")
        yield page, behavior
        browser.close()


def test_retry_paging_and_escape_keep_meaningful_focus_and_selection(remote_picker):
    page, behavior = remote_picker
    trigger = page.locator('.ui-picker-trigger')
    trigger.click()
    expect(page.get_by_role('button', name='Choice 1', exact=True)).to_be_visible()
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.get_by_role('button', name='Choice 2', exact=True)).to_be_visible()
    expect(page.locator('.ui-picker-search')).to_be_focused()
    assert page.locator('[name=report]').input_value() == '99'
    page.get_by_role('button', name='Previous', exact=True).click()
    expect(page.get_by_role('button', name='Choice 1', exact=True)).to_be_visible()
    expect(page.locator('.ui-picker-search')).to_be_focused()
    page.keyboard.press('Escape')
    expect(trigger).to_be_focused()
    expect(page.locator('.ui-picker-popup')).to_be_hidden()
    behavior['fail'] = True
    trigger.click()
    expect(page.locator('.ui-picker-message')).to_contain_text('Could not load')
    behavior['fail'] = False
    page.get_by_role('button', name='Retry', exact=True).click()
    expect(page.get_by_role('button', name='Choice 1', exact=True)).to_be_visible()
    expect(page.locator('.ui-picker-search')).to_be_focused()
    page.keyboard.press('Escape')
    expect(trigger).to_be_focused()
    trigger.click()
    expect(page.get_by_role('button', name='Choice 1', exact=True)).to_be_visible()
    assert page.evaluate('window.draftChanges') == 0
    page.get_by_role('button', name='Choice 1', exact=True).click()
    assert page.locator('[name=report]').input_value() == '1'
    assert page.evaluate('window.draftChanges') == 1
    expect(trigger).to_be_focused()


def test_timeout_retry_and_removed_picker_reject_pending_work(remote_picker):
    page, behavior = remote_picker
    page.clock.install()
    behavior['hold'] = True
    page.locator('.ui-picker-trigger').click()
    page.wait_for_timeout(100)
    page.clock.fast_forward(15001)
    expect(page.locator('.ui-picker-message')).to_contain_text('Could not load')
    assert page.locator('[name=report]').input_value() == '99'
    behavior['hold'] = False
    page.get_by_role('button', name='Retry', exact=True).click()
    expect(page.get_by_role('button', name='Choice 1', exact=True)).to_be_visible()
    page.locator('.ui-picker-search').fill('fresh')
    page.clock.fast_forward(200)
    expect(page.get_by_role('button', name='Choice fresh', exact=True)).to_be_visible()
    for route in behavior['held']:
        try:
            route.fulfill(json={'results': [{'value': 'stale', 'label': 'Stale'}], 'pagination': {'page': 1, 'total': 1}})
        except Exception:
            pass  # Browser aborted the old read; it cannot update the picker.
    assert page.get_by_role('button', name='Stale', exact=True).count() == 0
    behavior['hold'] = True
    page.locator('.ui-picker-search').fill('pending')
    page.clock.fast_forward(200)
    page.evaluate("document.querySelector('.ui-choice-picker').remove()")
    page.wait_for_timeout(100)
    assert page.locator('.ui-picker-popup:popover-open').count() == 0
    assert page.evaluate('window.draftChanges') == 0


def test_remote_picker_stays_inside_scaled_viewport(remote_picker):
    page, behavior = remote_picker
    page.locator('.ui-picker-trigger').click()
    expect(page.get_by_role('button', name='Choice 1', exact=True)).to_be_visible()
    page.evaluate("document.documentElement.style.zoom='2';window.dispatchEvent(new Event('resize'))")
    bounds = page.locator('.ui-picker-popup').bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 391
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 845


def test_drawer_pager_fits_200_percent_mobile_layout_and_returns_focus(remote_picker):
    page, behavior = remote_picker
    page.set_viewport_size({'width': 195, 'height': 450})
    page.add_style_tag(content='body {min-height:3000px}')
    def schedules(route):
        number = int(parse_qs(urlsplit(route.request.url).query).get('page', ['1'])[0])
        rows = [{'id': i, 'freq': 'daily', 'send_hour': 8, 'send_minute': 0, 'timezone': 'UTC',
                 'enabled': True, 'editable': False, 'recipient_summary': 'Everyone in Studio'}
                for i in (range(50) if number == 1 else range(50, 57))]
        route.fulfill(json={'schedules': rows, 'can_manage': False, 'subscribed_count': 57,
            'pagination': {'page': number, 'total': 57, 'per_page': 50,
                           'has_previous': number == 2, 'has_next': number == 1}})
    page.route('**/s/example/studio/api/reports/shared/schedules*', schedules)
    page.add_script_tag(content=(ROOT / 'static/report_delivery.js').read_text())
    page.evaluate("ReportDelivery.open('/s/example/studio','shared','Shared report')")
    expect(page.locator('#rdwScheduleList .rdw-schedule-row')).to_have_count(50)
    page.locator('[data-schedule-page="2"]').click()
    expect(page.locator('#rdwScheduleList .rdw-schedule-row')).to_have_count(7)
    expect(page.locator('#rdwScheduleSearch input')).to_be_focused()
    assert page.locator('#rdwDrawer').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    bounds = page.locator('#rdwDrawer').bounding_box()
    assert bounds['x'] >= -1 and bounds['x'] + bounds['width'] <= 196
    page.locator('[data-schedule-page="1"]').click()
    expect(page.locator('#rdwScheduleList .rdw-schedule-row')).to_have_count(50)
    expect(page.locator('#rdwScheduleSearch input')).to_be_focused()
