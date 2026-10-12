"""Delivery widget integration with native fields, shared pickers and isolated requests."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "/s/example/studio"


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def browser(request):
    with sync_playwright() as playwright:
        instance = getattr(playwright, request.param).launch(headless=True)
        yield instance
        instance.close()


def schedule(id=1):
    return {"id": id, "freq": "daily", "send_hour": 9, "send_minute": 37,
            "weekday": 0, "month_day": 1, "timezone": "America/New_York", "attach_pdf": False,
            "enabled": True, "editable": True, "recipient_roles": ["admins"],
            "recipients": [{"id": 99, "email": "saved@example.test"}],
            "recipient_groups": [{"id": 3, "name": "Finance & audit"}], "recipient_summary": "Saved recipients"}


def members():
    return {"studio_name": "Studio <script>unsafe()</script>", "roles": {"admins": 2},
            "members": [{"id": i, "name": f"Person {i}", "email": f"person{i}@example.test"} for i in range(120)]
            + [{"id": 200, "name": '<img src=x onerror="unsafe()">', "email": "quoted@example.test"}],
            "groups": [{"id": 3, "name": "Finance & audit"}]}


def page_for(browser, *, width=1440, saved=True, can_manage=True, resolved=False, portal_css=False):
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Europe/Amsterdam")
    page = context.new_page()
    requests, pending = [], {}
    behavior = {"members_fail": False, "save_fail": False, "hold_schedules": set(), "hold_members": set(), "hold_saves": set()}

    def payload(slug):
        result = {"schedules": [schedule()] if saved and slug == "first" else [], "can_manage": can_manage,
                  "default_timezone": "UTC", "default_enabled": True, "subscribed_count": 0}
        if resolved:
            result["picker_assets"] = {key: "/static/" + name.replace(".", ".resolved.", 1) for key, name in [
                ("choice_css", "choice-picker.css"), ("choice_js", "choice-picker.js"),
                ("recipient_css", "recipient-picker.css"), ("recipient_js", "recipient-picker.js")]}
        return result

    def respond(route):
        path = urlsplit(route.request.url).path
        requests.append((path, route.request.method))
        if path.startswith("/static/"):
            name = path.rsplit("/", 1)[-1].replace(".resolved.", ".")
            route.fulfill(body=(ROOT / "static" / name).read_text(encoding="utf-8"),
                          content_type="text/css" if name.endswith(".css") else "application/javascript")
            return
        if "/api/reports/" in path:
            slug = path.split("/api/reports/", 1)[1].split("/", 1)[0]
            kind = "members" if path.endswith("/recipients") else "saves" if route.request.method == "POST" else "schedules"
            if slug in behavior["hold_" + kind]:
                pending[(kind, slug)] = route
                return
            if kind == "members":
                route.fulfill(status=503 if behavior["members_fail"] else 200,
                              json={"error": "Unavailable"} if behavior["members_fail"] else members())
            elif kind == "saves":
                if behavior["save_fail"]:
                    route.fulfill(status=400, json={"error": "Check the draft", "errors": {"send_minute": ["Rejected minute"]}})
                else:
                    result = schedule()
                    result.update(route.request.post_data_json)
                    result["id"] = 1
                    route.fulfill(json={"ok": True, "schedule": result})
            else:
                route.fulfill(json=payload(slug))
            return
        route.fulfill(body='<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'
                           '<button id="first">First report</button><button id="second">Second report</button></body></html>',
                      content_type="text/html")

    page.route("**/*", respond)
    page.goto("http://test.local/")
    page.clock.install(time="2026-10-12T12:00:00Z")
    page.evaluate("window.PORTAL_CTX={};window.unsafe=()=>window.unsafeCalled=true;window.unsafeCalled=false;")
    if portal_css:
        page.add_style_tag(content=(ROOT / "static/ui.css").read_text(encoding="utf-8"))
        page.evaluate("document.body.className='mgmt'")
    page.add_script_tag(content=(ROOT / "static/report_delivery.js").read_text(encoding="utf-8"))
    page.evaluate(f"""['first','second'].forEach(slug=>document.getElementById(slug).onclick=()=>ReportDelivery.open('{PREFIX}',slug,slug,document.getElementById(slug)))""")
    return context, page, requests, pending, behavior, payload


def edit(page):
    page.locator("#first").click()
    page.locator("#rdwScheduleList [data-edit]").click()
    expect(page.locator("#rdwFormWrap button[type=submit]")).to_be_enabled()


@pytest.mark.parametrize("width", [1440, 390])
def test_saved_edit_uses_shared_widgets_real_time_and_preserves_failed_draft(browser, width):
    context, page, requests, pending, behavior, _ = page_for(browser, width=width, portal_css=True)
    edit(page)
    root = page.locator("#rdwFormWrap")
    expect(root.locator('[data-f="time"]')).to_have_attribute("type", "time")
    expect(root.locator('[data-f="time"]')).to_have_value("09:37")
    expect(root.locator('[data-f="timezone"]')).to_have_value("America/New_York")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Person 99")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Group: Finance & audit")
    root.locator("[data-recipient-picker] .ui-picker-trigger").click()
    expect(root.locator(".recipient-picker-option")).to_have_count(125)
    root.locator('[data-recipient-filter="people"]').click()
    root.locator("[data-recipient-picker] .ui-picker-search").fill("quoted@")
    root.locator('input[value="200"]').check()
    expect(root.locator(".recipient-picker-selected")).to_contain_text('<img src=x onerror="unsafe()">')
    assert not page.evaluate("Boolean(window.unsafeCalled)")
    root.get_by_role("button", name="Done", exact=True).click()
    root.locator('[data-f="time"]').fill("11:43")
    behavior["save_fail"] = True
    root.locator('button[type="submit"]').click()
    expect(root.locator('[data-f="time"]').locator('..')).to_contain_text("Rejected minute")
    expect(root.locator('[data-f="time"]')).to_have_attribute("aria-invalid", "true")
    expect(root.locator('[data-f="time"]')).to_have_value("11:43")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Person 99")
    expect(root.locator('button[type="submit"]')).to_be_enabled()
    expect(root.locator('[data-recipient-retry]')).to_be_hidden()
    assert page.evaluate("document.querySelectorAll('#rdwFormWrap img').length") == 0
    root.locator('[data-f="time"]').fill("10:19")
    behavior["save_fail"] = False
    captured = []
    page.on("request", lambda request: captured.append(request.post_data_json) if request.method == "POST" else None)
    root.locator('button[type="submit"]').click()
    expect(root.locator("[data-save-status]")).to_have_text("Delivery saved.")
    assert captured[-1]["send_hour"] == 10 and captured[-1]["send_minute"] == 19
    assert captured[-1]["recipient_ids"] == [99, 200]
    assert captured[-1]["recipient_roles"] == ["admins"]
    assert captured[-1]["recipient_group_ids"] == [3]
    assert "local_time" not in captured[-1]
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.screenshot(path=str(ROOT / ".artifacts" / f"delivery-drawer-{browser.browser_type.name}-{width}.png"))
    context.close()


def test_new_browser_zone_preserves_clock_and_manual_zone_converts_arbitrary_minute(browser):
    context, page, requests, pending, behavior, _ = page_for(browser, saved=False, resolved=True)
    page.locator("#first").click()
    page.locator("#rdwNewSchedule").click()
    root = page.locator("#rdwFormWrap")
    expect(root.locator('button[type="submit"]')).to_be_enabled()
    expect(root.locator('[data-f="timezone"]')).to_have_value("Europe/Amsterdam")
    expect(root.locator('[data-f="time"]')).to_have_value("08:00")
    expect(root).to_contain_text("Defaulted to your browser timezone")
    expect(root).to_contain_text("Changing timezone converts local time")
    root.locator('[data-f="time"]').fill("09:43")
    root.locator(".ui-choice-picker .ui-picker-trigger").click()
    root.locator(".ui-choice-picker .ui-picker-search").fill("UTC")
    root.locator(".ui-choice-picker .ui-picker-option").filter(has_text="UTC").click()
    expect(root.locator('[data-f="timezone"]')).to_have_value("UTC")
    expect(root.locator('[data-f="time"]')).to_have_value("07:43")
    root.locator("[data-recipient-picker] .ui-picker-trigger").click()
    root.locator('[data-recipient-filter="roles"]').click()
    root.locator('input[value="admins"]').check()
    root.get_by_role("button", name="Done", exact=True).click()
    root.locator('button[type="submit"]').click()
    expect(page.locator("#rdwScheduleList")).to_contain_text("07:43 UTC")
    assert not page.locator("#rdwFormWrap form").count()
    assert any("choice-picker.resolved.js" in path for path, _ in requests)
    assert not any("/static/choice-picker.js" == path for path, _ in requests)
    assert len([path for path, _ in requests if "choice-picker.resolved.js" in path]) == 1
    context.close()


def test_recipient_failure_keeps_saved_chips_and_blocks_save_until_retry(browser):
    context, page, requests, pending, behavior, _ = page_for(browser)
    behavior["members_fail"] = True
    page.locator("#first").click()
    page.locator("#rdwScheduleList [data-edit]").click()
    root = page.locator("#rdwFormWrap")
    expect(root.locator("[data-recipient-status]")).to_contain_text("Saved selections are kept")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("saved@example.test")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Finance & audit")
    expect(root.locator('button[type="submit"]')).to_be_disabled()
    root.locator('[data-f="time"]').fill("12:29")
    behavior["members_fail"] = False
    root.locator("[data-recipient-retry]").click()
    expect(root.locator('button[type="submit"]')).to_be_enabled()
    expect(root.locator('[data-f="time"]')).to_have_value("12:29")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Person 99")
    assert not any(method == "POST" for _, method in requests)
    context.close()


@pytest.mark.parametrize("kind", ["schedules", "members", "saves"])
def test_late_responses_cannot_replace_another_report_or_reopened_drawer(browser, kind):
    context, page, requests, pending, behavior, payload = page_for(browser)
    behavior["hold_" + kind].add("first")
    page.locator("#first").click()
    if kind in ("members", "saves"):
        page.locator("#rdwScheduleList [data-edit]").click()
    if kind == "saves":
        expect(page.locator("#rdwFormWrap button[type=submit]")).to_be_enabled()
        page.locator('[data-f="time"]').fill("11:13")
        page.locator("#rdwFormWrap button[type=submit]").click()
    page.wait_for_function("document.querySelector('#rdwDrawer').classList.contains('open')")
    page.on("dialog", lambda dialog: dialog.accept())
    page.evaluate(f"ReportDelivery.open('{PREFIX}','second','Second',document.querySelector('#second'))")
    expect(page.locator("#rdwSubtitle")).to_have_text("Second")
    expect(page.locator("#rdwScheduleList")).to_contain_text("No scheduled deliveries yet")
    page.wait_for_timeout(10)
    route = pending[(kind, "first")]
    if kind == "schedules": route.fulfill(json=payload("first"))
    elif kind == "members": route.fulfill(json=members())
    else: route.fulfill(json={"ok": True, "schedule": schedule()})
    page.wait_for_timeout(50)
    expect(page.locator("#rdwSubtitle")).to_have_text("Second")
    expect(page.locator("#rdwScheduleList")).to_contain_text("No scheduled deliveries yet")
    assert not page.locator("#rdwFormWrap form").count()
    page.evaluate("ReportDelivery.close()")
    behavior["hold_" + kind].clear()
    page.locator("#second").click()
    expect(page.locator("#rdwScheduleList")).to_contain_text("No scheduled deliveries yet")
    context.close()


def test_save_snapshot_keeps_edits_made_during_request_and_nested_escape_returns_focus(browser):
    context, page, requests, pending, behavior, _ = page_for(browser)
    edit(page)
    root = page.locator("#rdwFormWrap")
    root.locator("[data-recipient-picker] .ui-picker-trigger").click()
    root.locator("[data-recipient-picker] .ui-picker-search").press("Escape")
    expect(root.locator("[data-recipient-picker] .ui-picker-trigger")).to_be_focused()
    expect(page.locator("#rdwDrawer")).to_have_class("open")
    behavior["hold_saves"].add("first")
    root.locator('[data-f="time"]').fill("10:11")
    root.locator('button[type="submit"]').click()
    root.locator('[data-f="time"]').fill("12:49")
    page.wait_for_timeout(20)
    pending[("saves", "first")].fulfill(json={"ok": True, "schedule": {**schedule(), "send_hour": 10, "send_minute": 11}})
    expect(root.locator("[data-save-status]")).to_have_text("Submitted changes saved; newer edits are still unsaved.")
    expect(root.locator('[data-f="time"]')).to_have_value("12:49")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#rdwClose").click()
    expect(page.locator("#rdwDrawer")).to_have_class("open")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#rdwClose").click()
    expect(page.locator("#first")).to_be_focused()
    context.close()


def test_viewer_reads_list_without_loading_recipient_choices_or_edit_actions(browser):
    context, page, requests, pending, behavior, _ = page_for(browser, can_manage=False)
    # The server decides which schedules are editable.
    page.route("**/first/schedules", lambda route: route.fulfill(json={"schedules": [{**schedule(), "editable": False}], "can_manage": False}))
    page.locator("#first").click()
    expect(page.locator("#rdwBody")).to_contain_text("Read only")
    assert not page.locator("#rdwNewSchedule,[data-edit]").count()
    assert not any(path.endswith("/recipients") for path, _ in requests)
    context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_legacy_center_reuses_controls_with_management_css_and_idempotent_mount(browser, width):
    context, page, requests, pending, behavior, _ = page_for(browser, width=width, portal_css=True)
    page.evaluate("""saved => {
        document.body.insertAdjacentHTML('beforeend','<main><div class="ui-table-scroll"><table class="ui-table delivery-list" data-mobile-cards><tbody><tr class="delivery-row" data-delivery-row data-prefix="/s/example/studio" data-slug="first"><td data-field="cadence"></td><td data-field="format"></td><td data-field="recipients"></td><td data-field="status"></td><td><button data-toggle-edit class="ui-btn">Edit</button></td></tr><tr class="delivery-row-detail"><td colspan="5"><div data-flash-slot></div><div data-form-slot></div><script type="application/json" data-schedule-json></script></td></tr></tbody></table></div></main>');
        document.querySelector('[data-schedule-json]').textContent=JSON.stringify(saved);
        ReportDelivery.mountCenter(document);ReportDelivery.mountCenter(document);
    }""", schedule())
    page.locator('[data-toggle-edit]').click()
    root = page.locator('[data-form-slot]')
    expect(root.locator('button[type=submit]')).to_be_enabled()
    expect(root.locator('[data-f="time"]')).to_have_value('09:37')
    expect(root.locator('.ui-choice-picker')).to_have_count(1)
    expect(root.locator('.recipient-picker')).to_have_count(1)
    root.locator('[data-recipient-picker] .ui-picker-trigger').click()
    root.locator('[data-recipient-picker] .ui-picker-search').press('Escape')
    expect(root.locator('[data-recipient-picker] .ui-picker-trigger')).to_be_focused()
    assert page.locator('#rdwDrawer.open').count() == 0
    root.locator('[data-f="time"]').fill('06:59')
    root.locator('button[type=submit]').click()
    expect(root.locator('[data-save-status]')).to_have_text('Delivery saved.')
    expect(page.locator('[data-field="cadence"]')).to_contain_text('06:59')
    assert len([path for path, method in requests if method == 'POST']) == 1
    if width == 390:
        assert root.locator('[data-f="time"]').evaluate('node=>getComputedStyle(node).fontSize') == '16px'
        assert root.locator('button[type=submit]').bounding_box()['height'] >= 44
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(ROOT / '.artifacts' / f'delivery-legacy-{browser.browser_type.name}-{width}.png'))
    context.close()


def test_close_reopen_invalidates_old_recipient_read_and_closes_popovers(browser):
    context, page, requests, pending, behavior, _ = page_for(browser)
    behavior['hold_members'].add('first')
    page.locator('#first').click()
    page.locator('#rdwScheduleList [data-edit]').click()
    page.wait_for_timeout(30)
    old = pending[('members', 'first')]
    page.evaluate('ReportDelivery.close()')
    behavior['hold_members'].clear()
    edit(page)
    old.fulfill(json={'members': [{'id': 999, 'name': 'Obsolete person', 'email': 'obsolete@example.test'}], 'roles': {}, 'groups': []})
    page.wait_for_timeout(50)
    root = page.locator('#rdwFormWrap')
    assert not root.locator('input[value="999"]').count()
    root.locator('[data-recipient-picker] .ui-picker-trigger').click()
    expect(root.locator('[data-recipient-picker] .ui-picker-popup')).to_be_visible()
    page.evaluate('ReportDelivery.close()')
    expect(root.locator('[data-recipient-picker] .ui-picker-popup')).to_be_hidden()
    expect(page.locator('#first')).to_be_focused()
    context.close()


def test_asset_failure_retry_and_empty_audience_are_separate_states(browser):
    context, page, requests, pending, behavior, _ = page_for(browser, saved=False)
    page.route('**/choice-picker.css', lambda route: route.abort())
    page.route('**/first/recipients', lambda route: route.fulfill(json={'members': [], 'roles': {}, 'groups': [], 'studio_name': 'Empty studio'}))
    page.locator('#first').click()
    page.locator('#rdwNewSchedule').click()
    root = page.locator('#rdwFormWrap')
    expect(root.locator('[data-recipient-status]')).to_contain_text('Shared controls could not load')
    expect(root.locator('button[type=submit]')).to_be_disabled()
    page.unroute('**/choice-picker.css')
    root.locator('[data-recipient-retry]').click()
    expect(root.locator('button[type=submit]')).to_be_enabled()
    expect(root.locator('[data-recipient-status]')).to_contain_text('No eligible people or groups')
    expect(root.locator('[data-recipient-retry]')).to_be_hidden()
    root.locator('[data-recipient-picker] .ui-picker-trigger').click()
    root.locator('[data-recipient-filter="people"]').click()
    expect(root.locator('[data-recipient-picker] .ui-picker-message')).to_have_text('No people are available.')
    context.close()


def test_standalone_dark_widget_styles_do_not_change_report_content(browser):
    context, page, requests, pending, behavior, _ = page_for(browser, width=390)
    page.emulate_media(color_scheme='light')
    page.evaluate("""document.body.style.cssText='--bg-main:#181b1f;--text-main:#f3f4f6;--text-secondary:#a7adb8;--border-color:#30363d';
        document.body.insertAdjacentHTML('beforeend','<div id="report-content" style="color:rgb(12,34,56);font-size:23px">Report content</div>');""")
    edit(page)
    root = page.locator('#rdwFormWrap')
    assert root.locator('[data-f="time"]').evaluate('node=>getComputedStyle(node).colorScheme') == 'dark'
    assert root.locator('.rdw-label').first.evaluate('node=>getComputedStyle(node).color') == 'rgb(167, 173, 184)'
    assert page.locator('#rdwScheduleList .rdw-btn').first.evaluate('node=>getComputedStyle(node).backgroundColor') == 'rgb(24, 27, 31)'
    assert page.locator('#rdwScheduleList .rdw-schedule-row').first.evaluate('node=>getComputedStyle(node).borderTopColor') == 'rgb(48, 54, 61)'
    expect(root.locator('.recipient-picker-selected')).to_contain_text('Person 99')
    assert page.locator('#report-content').evaluate('node=>getComputedStyle(node).fontSize') == '23px'
    assert page.locator('#report-content').evaluate('node=>getComputedStyle(node).color') == 'rgb(12, 34, 56)'
    assert not any(path.endswith('/ui.css') for path, _ in requests)
    root.locator('.ui-choice-picker .ui-picker-trigger').click()
    trigger = root.locator('.ui-choice-picker .ui-picker-trigger')
    assert trigger.evaluate('node=>getComputedStyle(node).color') == 'rgb(243, 244, 246)'
    assert root.locator('.ui-choice-picker .ui-picker-popup').evaluate('node=>getComputedStyle(node).backgroundColor') == 'rgb(24, 27, 31)'
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(ROOT / '.artifacts' / f'delivery-standalone-dark-{browser.browser_type.name}.png'))
    page.evaluate("document.body.style.setProperty('--bg-main','#ffffff')")
    expect(root.locator('[data-f="time"]')).to_have_css('color-scheme', 'light')
    context.close()
