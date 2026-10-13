"""Dynamic choices retain the shared logical draft and submitted snapshot."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(params=['chromium', 'webkit'])
def page(request):
    with sync_playwright() as pw:
        browser = getattr(pw, request.param).launch(headless=True)
        page = browser.new_page()
        page.route('https://example.test/editor', lambda route: route.fulfill(body='''<form id="draft" data-settings-form data-settings-save>
          <input name="title" value="Original"><label><input type="checkbox" name="people" value="1" checked>One</label>
          <select name="report"><option value="1" selected>One</option><option value="2">Two</option></select>
          <select name="groups" multiple><option value="a" selected>A</option><option value="b" selected>B</option></select>
          <button type="submit">Save</button><a href="/away" data-settings-leave>Cancel</a></form>''', content_type='text/html'))
        page.goto('https://example.test/editor')
        page.add_script_tag(content=(ROOT/'static/settings-forms.js').read_text())
        yield page
        browser.close()


def test_logical_checkbox_survives_removal_recreation_and_reset(page):
    form = page.locator('#draft')
    page.locator('[name=people]').uncheck()
    expect(form).to_have_class('is-dirty')
    page.evaluate('''() => {
      const form = document.querySelector('form'); form.querySelector('[name=people]').remove();
      TrellumSettingsForms.trackControls(form, []);
      const field = document.createElement('input'); field.type='checkbox'; field.name='people'; field.value='1';
      field.defaultChecked=true; field.checked=false; form.append(field); TrellumSettingsForms.trackControls(form,[field]);
    }''')
    expect(form).to_have_class('is-dirty')
    page.locator('[name=people]').check()
    expect(form).not_to_have_class('is-dirty')
    page.locator('[name=people]').uncheck(); form.evaluate('f=>f.reset()')
    expect(form).not_to_have_class('is-dirty')


def test_option_positions_and_search_are_not_draft_values(page):
    page.evaluate('''() => {
      const single = document.querySelector('[name=report]'), multiple = document.querySelector('[name=groups]');
      single.prepend(new Option('New', '0')); single.value='1';
      multiple.append(multiple.options[0]);
      document.querySelector('[name=title]').dispatchEvent(new Event('input',{bubbles:true}));
    }''')
    expect(page.locator('#draft')).not_to_have_class('is-dirty')
    page.locator('[name=report]').select_option('2')
    expect(page.locator('#draft')).to_have_class('is-dirty')


def test_new_choices_during_save_keep_later_edits_dirty_and_leave_cancel(page):
    pending = []
    page.route('https://example.test/editor', lambda route: pending.append(route))
    page.locator('[name=title]').fill('Submitted')
    page.locator('button[type=submit]').click()
    page.wait_for_function("document.querySelector('form').dataset.settingsState==='saving'")
    page.evaluate('''() => {
      const form=document.querySelector('form'), field=document.createElement('input');
      field.type='checkbox';field.name='people';field.value='2';form.append(field);
      TrellumSettingsForms.trackControls(form,[field]);field.checked=true;field.dispatchEvent(new Event('input',{bubbles:true}));
    }''')
    pending.pop().fulfill(json={'ok': True, 'message': 'Saved.'})
    expect(page.locator('#draft')).to_have_class('is-dirty')
    expect(page.locator('[data-settings-dirty-status]')).to_contain_text('Newer changes')
    page.on('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('link', name='Cancel').click()
    expect(page).to_have_url('https://example.test/editor')
    page.locator('input[value="2"]').uncheck()
    expect(page.locator('#draft')).not_to_have_class('is-dirty')
    page.locator('input[value="2"]').check();page.locator('button[type=submit]').click()
    page.wait_for_function("document.querySelector('form').dataset.settingsState==='saving'")
    pending.pop().fulfill(status=400,json={'ok':False,'message':'Invalid','errors':{'title':['Rejected']}})
    expect(page.locator('#draft')).to_have_class('is-dirty')
    expect(page.locator('[name=title]')).to_have_value('Submitted')


@pytest.mark.parametrize('newer_edit', [False, True])
def test_normalization_preserves_recreated_checkbox_and_select_drafts(page, newer_edit):
    pending = []
    page.route('https://example.test/editor', lambda route: pending.append(route))
    page.locator('[name=title]').fill('Submitted')
    page.locator('button[type=submit]').click()
    page.wait_for_function("document.querySelector('form').dataset.settingsState==='saving'")
    page.evaluate('''() => {
      const form=document.querySelector('form');form.querySelector('[name=people]').remove();
      const field=document.createElement('input');field.type='checkbox';field.name='people';field.value='1';field.checked=true;
      form.append(field);TrellumSettingsForms.trackControls(form,[field]);
      const select=form.querySelector('[name=report]');select.prepend(new Option('Zero','0'));select.value='1';
    }''')
    if newer_edit:
        page.locator('[name=people]').uncheck()
        page.locator('[name=report]').select_option('0')
    pending.pop().fulfill(json={'ok': True, 'message': 'Saved.', 'values': {'people': True, 'report': '2', 'groups': ['b', 'a']}})
    expect(page.locator('[data-settings-dirty-status]')).to_contain_text('Saved.')
    if newer_edit:
        expect(page.locator('[name=people]')).not_to_be_checked()
        expect(page.locator('[name=report]')).to_have_value('0')
        expect(page.locator('#draft')).to_have_class('is-dirty')
    else:
        expect(page.locator('[name=people]')).to_be_checked()
        expect(page.locator('[name=report]')).to_have_value('2')
        expect(page.locator('#draft')).not_to_have_class('is-dirty')
