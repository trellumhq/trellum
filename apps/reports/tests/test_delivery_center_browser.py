"""Dedicated delivery CRUD navigation and shared form handling in both engines."""
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.reports.models import EmailSchedule
from apps.core import roles

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(params=['chromium', 'webkit'])
def delivery_browser(request, login, org_admin, report_row, make_user, grant_studio, org):
    row = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
    row.recipients.add(org_admin)
    client = login(org_admin)
    prefix = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}'
    listing = f'{prefix}/deliveries'
    edit = f'{listing}/{row.pk}/edit'
    create = f'{listing}/new?report={report_row.pk}'
    body = {'delivery_page': '1', 'return_to': listing, 'freq': 'daily', 'send_hour': '8',
            'send_minute': '0', 'weekday': '0', 'month_day': '1', 'timezone': 'Wrong/Zone',
            'enabled': 'on', 'recipient_ids': [org_admin.pk]}
    api = f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}'
    invalid = client.post(api, body)
    assert invalid.status_code == 400
    pages = {path: client.get(path).content.decode() for path in [listing, edit, create, f'{listing}/new']}
    pages[api] = invalid.content.decode()
    viewer = make_user('drawer-viewer@example.test', org=org)
    grant_studio(viewer, report_row.studio, roles.VIEWER)
    schedules_url = api.rsplit('/', 1)[0]
    pages['drawer_payloads'] = {
        False: login(viewer).get(schedules_url).json(),
        True: login(org_admin).get(schedules_url).json(),
    }
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch(headless=True)
        page = browser.new_page()
        def respond(route):
            path = urlsplit(route.request.url).path
            query = urlsplit(route.request.url).query
            key = path + ('?' + query if query else '')
            if key not in pages:
                route.abort()
                return
            html = pages[key]
            html = html.replace('</head>', '<style>body {min-height: 2500px} form#delivery-schedule-form {margin-top: 400px}</style></head>')
            route.fulfill(body=html, status=400 if path == api else 200, content_type='text/html')
        page.route('**/*', respond)
        yield page, pages, listing, edit, create, api, browser
        browser.close()


def init_forms(page):
    page.add_script_tag(content=(ROOT / 'static/settings-forms.js').read_text(encoding='utf-8'))
    page.evaluate('TrellumSettingsForms.init(document)')


def test_list_add_edit_and_cancel_use_dedicated_pages(delivery_browser):
    page, pages, listing, edit, create, api, browser = delivery_browser
    page.goto('https://example.test' + listing)
    assert page.locator('[data-schedule-editor]').count() == 0
    page.get_by_role('link', name='Add delivery', exact=True).click()
    expect(page.locator('#delivery-report')).to_be_visible()
    page.locator('#delivery-report').select_option(index=1)
    page.get_by_role('button', name='Continue', exact=True).click()
    expect(page.locator('#delivery-schedule-form')).to_be_visible()
    page.get_by_role('link', name='Cancel', exact=True).click()
    expect(page).to_have_url('https://example.test' + listing)
    page.get_by_role('link', name='Edit', exact=True).click()
    expect(page).to_have_url('https://example.test' + edit)
    page.locator('[name=timezone]').fill('Europe/Amsterdam')
    page.get_by_role('link', name='Cancel', exact=True).click()
    expect(page).to_have_url('https://example.test' + listing)


@pytest.mark.parametrize('width,columns', [(390, 1), (800, 3), (1440, 3)])
def test_delivery_list_is_compact_and_usable_at_common_widths(delivery_browser, width, columns):
    page, pages, listing, edit, create, api, browser = delivery_browser
    if browser.browser_type.name != 'chromium':
        pytest.skip('Responsive layout is checked in Chromium.')
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto('https://example.test' + listing)
    page.add_style_tag(content=(ROOT / 'static/ui.css').read_text(encoding='utf-8'))
    filters = page.locator('.delivery-filters')
    assert len(page.locator('form.delivery-filters > label').all()) + 1 >= columns
    assert page.evaluate('(el) => getComputedStyle(el).gridTemplateColumns.split(" ").length', filters.element_handle()) == columns
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    expect(page.locator('[data-delivery-row]')).to_have_count(1)
    actions = page.locator('[data-delivery-row] .ui-row-actions')
    expect(actions.locator('.ui-btn')).to_have_count(5)
    assert all(button.is_visible() for button in actions.locator('.ui-btn').all())
    if width < 768:
        assert actions.evaluate('(el) => getComputedStyle(el).flexWrap') == 'wrap'
        assert page.locator('.delivery-row-detail td').evaluate('(el) => getComputedStyle(el).paddingTop') == '0px'


def test_ajax_failure_retains_draft_focus_scroll_and_success_returns_list(delivery_browser):
    page, pages, listing, edit, create, api, browser = delivery_browser
    pending = []
    page.route('**/schedules/*', lambda route: pending.append(route))
    page.goto('https://example.test' + edit)
    init_forms(page)
    form = page.locator('#delivery-schedule-form')
    hour = form.locator('[name=send_hour]')
    hour.fill('10')
    hour.focus()
    page.evaluate('window.scrollTo(0, 450); window.originalDocument = document')
    position = page.evaluate('scrollY')
    form.evaluate('form => { form.requestSubmit(); form.requestSubmit(); }')
    page.wait_for_function('document.querySelector("#delivery-schedule-form").dataset.settingsState === "saving"')
    assert len(pending) == 1
    pending.pop().fulfill(status=400, json={'ok': False, 'message': 'Invalid hour.', 'errors': {'send_hour': ['Must be 0-23.']}})
    expect(hour).to_have_attribute('aria-invalid', 'true')
    expect(hour).to_have_value('10')
    assert page.evaluate('document === originalDocument && document.activeElement.name === "send_hour"')
    assert page.evaluate('scrollY') == position
    hour.fill('11')
    form.evaluate('form => form.requestSubmit()')
    page.wait_for_function('document.querySelector("#delivery-schedule-form").dataset.settingsState === "saving"')
    pending.pop().fulfill(json={'ok': True, 'message': 'Saved.', 'redirect': listing})
    expect(page).to_have_url('https://example.test' + listing)


def test_native_failed_edit_keeps_draft_and_cancel_returns_list(delivery_browser):
    page, pages, listing, edit, create, api, browser = delivery_browser
    context = browser.new_context(java_script_enabled=False, viewport={'width': 360, 'height': 820})
    native = context.new_page()
    native.route('**/*', lambda route: route.fulfill(body=pages.get(urlsplit(route.request.url).path, ''),
        content_type='text/html', status=400 if route.request.method == 'POST' else 200))
    try:
        native.goto('https://example.test' + edit)
        native.locator('[name=timezone]').fill('Wrong/Zone')
        native.get_by_role('button', name='Save delivery', exact=True).click()
        expect(native.locator('[name=timezone]')).to_have_value('Wrong/Zone')
        expect(native.locator('#delivery-schedule-form')).to_contain_text('timezone')
        native.get_by_role('link', name='Cancel', exact=True).click()
        expect(native).to_have_url('https://example.test' + listing)
    finally:
        context.close()


def test_cancelled_delete_sends_no_request(delivery_browser):
    page, pages, listing, edit, create, api, browser = delivery_browser
    writes = []
    page.goto('https://example.test' + listing)
    init_forms(page)
    page.add_script_tag(content=(ROOT / 'static/report_delivery.js').read_text(encoding='utf-8'))
    page.evaluate('ReportDelivery.mountCenter(document)')
    page.route('**/schedules/*', lambda route: (writes.append(route.request), route.abort()))
    page.on('dialog', lambda dialog: dialog.dismiss())
    page.locator('form[data-delete-confirm]').first.evaluate('form => form.requestSubmit()')
    page.wait_for_timeout(50)
    assert writes == [] and page.locator('[data-delivery-row]').count() == 1


@pytest.mark.parametrize('can_manage', [False, True])
def test_drawer_uses_shared_permissions(delivery_browser, can_manage, report_row):
    page, pages, listing, edit, create, api, browser = delivery_browser
    payload = pages['drawer_payloads'][can_manage]
    assert payload['can_manage'] is can_manage
    assert len(payload['schedules']) == 1
    assert payload['schedules'][0]['editable'] is can_manage
    page.goto('https://example.test' + listing)
    page.route('**/schedules', lambda route: route.fulfill(json=payload))
    page.route('**/recipients', lambda route: route.fulfill(json={'members': [], 'roles': {}, 'groups': []}))
    page.add_script_tag(content=(ROOT / 'static/report_delivery.js').read_text(encoding='utf-8'))
    prefix = listing.removesuffix('/deliveries')
    page.evaluate('args => ReportDelivery.open(args[0], args[1], "Report")', [prefix, report_row.slug])
    expect(page.locator('.rdw-schedule-row')).to_have_count(1)
    assert page.locator('#rdwNewSchedule').count() == int(can_manage)
    for action in ('edit', 'sample', 'delete'):
        assert page.locator(f'[data-{action}]').count() == int(can_manage)
    if not can_manage:
        expect(page.locator('#rdwBody')).to_contain_text('Read only')
