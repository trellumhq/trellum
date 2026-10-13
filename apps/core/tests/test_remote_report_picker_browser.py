"""Generic remote reports retain native identity and shared form drafts."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(params=["chromium", "webkit"])
def picker(request):
    with sync_playwright() as pw:
        browser = getattr(pw, request.param).launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.route("https://example.test/editor", lambda route: route.fulfill(body='''<form id="editor" data-settings-form data-settings-save>
          <fieldset data-recipient-picker data-picker-label="Reports" data-recipient-mode="choices" data-recipient-name="reports" data-recipient-source="/choices" data-unavailable-label="Selected report is unavailable">
          <div><label><input type="checkbox" name="reports" value="59" checked>Report 59</label></div>
          <div class="recipient-picker-fallback" data-picker-fallback><button type="submit" name="action" value="browse_access">Next reports</button></div></fieldset>
          <button type="submit" name="action" value="studio_access">Save</button><a href="/away" data-settings-leave>Leave</a></form>''', content_type="text/html"))
        page.goto("https://example.test/editor")
        page.add_style_tag(content=(ROOT / "static/choice-picker.css").read_text() + (ROOT / "static/recipient-picker.css").read_text())
        for asset in ("choice-picker.js", "recipient-picker.js", "settings-forms.js"):
            page.add_script_tag(content=(ROOT / "static" / asset).read_text())
        yield page
        browser.close()


def payload(start=0, selected=True, total=60):
    return {"results": [{"value": str(i), "label": f"Report {i}"} for i in range(start, min(start + 25, total))],
            "selected": [{"value": "59", "label": "Report 59"}] if selected else [],
            "pagination": {"page": start // 25 + 1, "per_page": 25, "total": total, "has_previous": start > 0, "has_next": start + 25 < total}}


def test_report_paging_preserves_native_selections_reset_and_unavailable(picker):
    queries = []
    def choices(route):
        args = parse_qs(urlparse(route.request.url).query); queries.append(args)
        data = payload(25 if args.get("page") == ["2"] else 0, selected=not args.get("q"))
        if args.get("q"):
            data["results"] = []
        route.fulfill(json=data)
    picker.route("https://example.test/choices**", choices)
    trigger = picker.get_by_role("button", name="Reports: 1 selected", exact=True)
    trigger.click()
    expect(picker.locator('.recipient-picker-option:not([hidden])')).to_have_count(25)
    assert not picker.get_by_role("group", name="Recipient type").count()
    expect(picker.get_by_role("button", name="Next reports")).not_to_be_visible()
    picker.get_by_role("checkbox", name="Report 0", exact=True).check()
    picker.get_by_role("button", name="Next", exact=True).click()
    expect(picker.locator(".ui-picker-pager")).to_contain_text("Page 2")
    assert queries[-1]["selected"] == ["59", "0"]
    assert picker.locator('[name=reports]:checked').count() == 2
    expect(picker.locator("#editor")).to_have_class("is-dirty")
    picker.locator(".ui-picker-search").fill("unavailable")
    expect(picker.get_by_role("button", name="Remove Selected report is unavailable")).to_have_count(2)
    assert picker.locator('[name=reports]:checked').count() == 2
    picker.locator("#editor").evaluate("f => f.reset()")
    expect(picker.locator('[name=reports]:checked')).to_have_count(1)
    expect(picker.locator("#editor")).not_to_have_class("is-dirty")


def test_failed_malformed_and_stale_reads_do_not_clear_selection(picker):
    pending = []
    picker.route("https://example.test/choices**", lambda route: pending.append(route))
    picker.locator(".ui-picker-trigger").click()
    picker.wait_for_function("document.querySelector('.ui-picker-message').textContent.includes('Loading')")
    assert picker.locator('[name=reports]:checked').count() == 1
    while not pending:
        picker.wait_for_timeout(20)
    pending.pop().fulfill(status=503, body="Unavailable")
    expect(picker.locator(".ui-picker-message")).to_contain_text("Your selections are retained")
    picker.get_by_role("button", name="Retry", exact=True).click()
    while not pending:
        picker.wait_for_timeout(20)
    broken = payload(); broken["results"][0]["value"] = 1
    pending.pop().fulfill(json=broken)
    expect(picker.locator(".ui-picker-message")).to_contain_text("Your selections are retained")
    assert picker.get_by_role("button", name="Remove Report 59").count() == 1
    picker.get_by_role("button", name="Retry", exact=True).click()
    while not pending:
        picker.wait_for_timeout(20)
    old = pending.pop()
    picker.locator(".ui-picker-search").fill("new")
    while not pending:
        picker.wait_for_timeout(20)
    pending.pop().fulfill(json=payload(25))
    expect(picker.locator(".ui-picker-pager")).to_contain_text("Page 2")
    old.fulfill(json=payload())
    expect(picker.locator(".ui-picker-pager")).to_contain_text("Page 2")
    assert picker.locator('[name=reports]:checked').count() == 1


def test_remote_choices_during_save_keep_later_edits_dirty(picker):
    choices = []
    saves = []
    picker.route("https://example.test/choices**", lambda route: choices.append(route))
    picker.locator(".ui-picker-trigger").click()
    while not choices:
        picker.wait_for_timeout(20)
    choices.pop().fulfill(json=payload())
    picker.get_by_role("checkbox", name="Report 0", exact=True).check()
    picker.get_by_role("button", name="Next", exact=True).click()
    while not choices:
        picker.wait_for_timeout(20)
    picker.route("https://example.test/editor", lambda route: saves.append(route))
    picker.locator("form").evaluate("f => f.requestSubmit(f.querySelector('[name=action][value=studio_access]'))")
    while not saves:
        picker.wait_for_timeout(20)
    choices.pop().fulfill(json=payload(25))
    picker.get_by_role("checkbox", name="Report 26", exact=True).check()
    saves.pop().fulfill(json={"ok": True, "message": "Saved."})
    expect(picker.locator("#editor")).to_have_class("is-dirty")
    expect(picker.locator("[data-settings-dirty-status]")).to_contain_text("Newer changes")
    assert set(picker.locator("form").evaluate("f => new FormData(f).getAll('reports')")) == {"0", "26", "59"}
    picker.get_by_role("button", name="Done", exact=True).click()
    picker.on("dialog", lambda dialog: dialog.dismiss())
    picker.get_by_role("link", name="Leave", exact=True).click()
    expect(picker).to_have_url("https://example.test/editor")
    picker.get_by_role("button", name="Save", exact=True).click()
    while not saves:
        picker.wait_for_timeout(20)
    saves.pop().fulfill(status=400, json={"ok": False, "message": "Rejected", "errors": {"reports": ["Rejected"]}})
    assert set(picker.locator("form").evaluate("f => new FormData(f).getAll('reports')")) == {"0", "26", "59"}


@pytest.mark.parametrize("generic", [True, False])
def test_large_selections_use_csrf_json_read_post(picker, generic):
    values = [str(i) for i in range(1, 1001)]
    checks = ''.join(f'<label><input type="checkbox" name="{"reports" if generic else "recipients"}" value="{value}" checked>Choice {value}</label>' for value in values)
    mode = 'data-recipient-mode="choices" data-recipient-name="reports"' if generic else ''
    section = checks if generic else f'<div data-recipient-type="people">{checks}</div>'
    picker.locator('[data-recipient-picker]').evaluate('(f, html) => f.outerHTML=html', f'<fieldset data-recipient-picker data-picker-label="Choices" data-recipient-source="/choices" {mode}>{section}</fieldset>')
    picker.locator('form').evaluate("f=>{const csrf=document.createElement('input');csrf.name='csrfmiddlewaretoken';csrf.type='hidden';csrf.value='nearest-form-csrf';f.append(csrf);TrellumRecipients.init(f)}")
    seen = []
    def choices(route):
        seen.append(route.request)
        if generic:
            data = payload()
            data['selected'] = [{'value': value, 'label': f'Choice {value}'} for value in values]
        else:
            rows = [{'id': int(value), 'name': f'Choice {value}', 'email': ''} for value in values]
            data = {'members': rows[:25], 'groups': [], 'selected': {'members': rows, 'groups': []}, 'pagination': {'members': {'page': 1, 'total': 1000, 'has_previous': False, 'has_next': True}, 'groups': {'page': 1, 'total': 0, 'has_previous': False, 'has_next': False}}}
        route.fulfill(json=data)
    picker.route('https://example.test/choices**', choices)
    picker.locator('.ui-picker-trigger').click()
    expect(picker.locator('.ui-picker-pager')).to_contain_text('choices')
    assert seen[0].method == 'POST' and seen[0].url == 'https://example.test/choices'
    assert seen[0].headers['x-csrftoken'] == 'nearest-form-csrf'
    body = seen[0].post_data_json
    assert body['selected'] == values if generic else body['selected_people'].split(',') == values
    assert picker.locator('input[type=checkbox]:checked').count() == 1000
