"""Browser checks against the server-rendered shared delivery center."""
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.reports.models import EmailSchedule
from apps.reports.views import _schedule_dict

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def center_browser(login, org_admin, report_row):
    rows = []
    for hour in (8, 9):
        row = EmailSchedule.objects.create(report=report_row, created_by=org_admin, send_hour=hour)
        row.recipients.add(org_admin)
        rows.append(row)
    client = login(org_admin)
    html = client.get('/deliveries').content.decode()
    invalid = client.post(f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/schedules/{rows[0].pk}', {
        'return_to': '/deliveries', 'freq': 'daily', 'send_hour': '8', 'send_minute': '0',
        'weekday': '0', 'month_day': '1', 'timezone': 'Wrong/Zone', 'enabled': 'on',
        'recipient_ids': [org_admin.pk],
    })
    assert invalid.status_code == 400
    rows[0].native_error_html = invalid.content.decode()
    rows[0].browser_html = html
    for row in rows:
        row.browser_payload = _schedule_dict(row, viewer=org_admin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html') if route.request.url == 'https://example.test/deliveries' else route.abort())
        page.goto('https://example.test/deliveries')
        page.add_script_tag(content=(ROOT / 'static/settings-forms.js').read_text(encoding='utf-8'))
        page.add_script_tag(content=(ROOT / 'static/report_delivery.js').read_text(encoding='utf-8'))
        page.evaluate('ReportDelivery.mountCenter(document)')
        yield page, rows, org_admin
        browser.close()


def test_native_mobile_editor_and_server_validation_without_javascript(center_browser):
    page, rows, user = center_browser
    context = page.context.browser.new_context(java_script_enabled=False, viewport={'width': 320, 'height': 820})
    native = context.new_page()
    def respond(route):
        if route.request.url == 'https://example.test/deliveries':
            html = rows[0].browser_html
            status = 200
        elif route.request.method == 'POST' and '/schedules/' in route.request.url:
            html = rows[0].native_error_html
            status = 400
        else:
            route.abort()
            return
        css = (ROOT / 'static/ui.css').read_text(encoding='utf-8')
        route.fulfill(status=status, body=html.replace('</head>', f'<style>{css}</style></head>'), content_type='text/html')
    native.route('**/*', respond)
    try:
        native.goto('https://example.test/deliveries')
        editor = native.locator('[data-schedule-editor]').first
        expect(editor.locator('form')).not_to_be_visible()
        editor.locator('summary').click()
        expect(editor.locator('form')).to_be_visible()
        editor.locator('[name=timezone]').fill('Wrong/Zone')
        editor.locator('button[type=submit]').click()
        reopened = native.locator('[data-schedule-editor]').first
        expect(reopened).to_have_attribute('open', '')
        expect(reopened.locator('form')).to_be_visible()
        expect(reopened.locator('[name=timezone]')).to_have_value('Wrong/Zone')
        expect(reopened).to_contain_text('timezone')
    finally:
        context.close()


def test_schedule_save_retains_focus_scroll_neighbor_and_new_draft(center_browser):
    page, rows, user = center_browser
    pending = []
    page.route('**/schedules/*', lambda route: pending.append(route))
    forms = page.locator('form[id^="delivery-schedule-"]')
    page.locator('[data-schedule-editor]').evaluate_all('nodes => nodes.forEach(node => node.open = true)')
    first = forms.nth(0)
    neighbor = forms.nth(1)
    first.locator('[name="send_hour"]').fill('10')
    neighbor.locator('[name="send_hour"]').fill('17')
    first.locator('[name="send_hour"]').focus()
    page.evaluate('window.originalDocument = document; window.scrollBy(0, -100)')
    position = page.evaluate('scrollY')
    first.evaluate('form => form.requestSubmit()')
    page.wait_for_function('document.querySelector("form[id^=delivery-schedule-]").dataset.settingsState === "saving"')
    first.locator('[name="send_hour"]').fill('11')
    saved = dict(rows[0].browser_payload)
    saved.update(send_hour=10, delivery_revision=1, delete_confirmation='Delete schedule for new-recipient@example.test?')
    pending[0].fulfill(json={'ok': True, 'message': 'Schedule saved.', 'schedule': saved})
    expect(first.locator('[data-settings-dirty-status]')).to_have_text('Schedule saved. Newer changes are still unsaved.')
    assert first.locator('[name="send_hour"]').input_value() == '11'
    assert neighbor.locator('[name="send_hour"]').input_value() == '17'
    assert page.locator('[data-delete-confirm]').first.get_attribute('data-delete-confirm') == saved['delete_confirmation']
    assert page.evaluate('document === originalDocument')
    assert page.evaluate('scrollY') == position
    assert page.evaluate('document.activeElement.name') == 'send_hour'


@pytest.mark.parametrize('enhanced', [True, False])
def test_validation_keeps_draft_and_cancelled_delete_sends_nothing(center_browser, enhanced):
    page, rows, user = center_browser
    requests = []
    def fail(route):
        requests.append(route.request)
        route.fulfill(status=400, json={'ok': False, 'message': 'Invalid hour.', 'errors': {'send_hour': ['Must be 0-23.']}})
    page.route('**/schedules/*', fail)
    page.locator('[data-schedule-editor]').first.evaluate('node => node.open = true')
    form = page.locator('form[id^="delivery-schedule-"]').first
    form.locator('[name="send_hour"]').fill('10')
    form.evaluate('form => form.requestSubmit()')
    expect(form.locator('[name="send_hour"]')).to_have_attribute('aria-invalid', 'true')
    assert form.locator('[name="send_hour"]').input_value() == '10'
    page.on('dialog', lambda dialog: dialog.dismiss())
    delete = page.locator('form[data-delete-confirm]').first
    if not enhanced:
        delete.evaluate('form => form.removeAttribute("data-settings-save")')
    assert f'#{rows[0].pk}' in delete.get_attribute('data-delete-confirm')
    delete.evaluate('form => form.requestSubmit()')
    page.wait_for_timeout(50)
    assert len(requests) == 1
    assert page.locator('[data-delivery-row]').count() == 2


def test_out_of_order_row_responses_cannot_revert_summary(center_browser):
    page, rows, user = center_browser
    pending = []
    page.route('**/schedules/*', lambda route: pending.append(route))
    row = page.locator('[data-delivery-row]').first
    page.locator('[data-schedule-editor]').first.evaluate('node => node.open = true')
    edit = page.locator('form[id^="delivery-schedule-"]').first
    edit.locator('[name="send_hour"]').fill('10')
    edit.evaluate('form => form.requestSubmit()')
    row.locator('form').first.evaluate('form => form.requestSubmit()')
    page.wait_for_function('document.querySelectorAll("form[data-settings-state=saving]").length === 2')
    newer = dict(rows[0].browser_payload)
    newer.update(enabled=False, delivery_revision=2, send_hour=10)
    pending[1].fulfill(json={'ok': True, 'message': 'Saved.', 'schedule': newer})
    expect(row.locator('[data-field="status"]')).to_have_text('Disabled')
    older = dict(newer, enabled=True, delivery_revision=1)
    pending[0].fulfill(json={'ok': True, 'message': 'Saved.', 'schedule': older})
    expect(edit.locator('[data-settings-dirty-status]')).to_have_text('Saved.')
    expect(row.locator('[data-field="status"]')).to_have_text('Disabled')
