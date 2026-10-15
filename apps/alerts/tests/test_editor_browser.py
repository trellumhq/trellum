from pathlib import Path

import pytest
from django.core import mail
from django.test import Client
from playwright.sync_api import expect, sync_playwright

from apps.alerts.models import AlertRule, AlertRun
from apps.alerts.tests.test_views import _form
from apps.core import roles

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def prefix(rule):
    return f"/s/{rule.org.slug}/{rule.studio.slug}/alerts"


def test_new_draft_preview_is_native_and_never_creates_a_rule(login, owner, rule, prefix, fake_llm):
    from apps.alerts.tests.conftest import decide

    fake_llm(decide(True))
    before = AlertRule.objects.count()
    response = login(owner).post(f"{prefix}/new", {**_form(rule, name="Unsaved drain"), "action": "preview"})
    assert response.status_code == 200
    assert response.context["form"].is_bound and response.context["result"]["condition"] == "Met"
    assert "Preview only. No delivery or budget warning was sent." in response.content.decode()
    assert AlertRule.objects.count() == before
    assert not AlertRun.objects.filter(rule__isnull=True).exists()
    assert mail.outbox == []


def test_edit_preview_bills_editor_and_changes_only_test_timestamp(
    login, rule, prefix, make_user, grant_studio, fake_llm, monkeypatch
):
    from apps.alerts import evaluator
    from apps.alerts.tests.conftest import decide

    editor = make_user("editor@demo.example", org=rule.org)
    grant_studio(editor, rule.studio, roles.DEVELOPER)
    captured = {}
    original = evaluator.evaluate_snapshot

    def evaluate(snapshot, **kwargs):
        captured.update(kwargs)
        return original(snapshot, **kwargs)

    monkeypatch.setattr(evaluator, "evaluate_snapshot", evaluate)
    fake_llm(decide(True))
    rule.live_state = {"revision": rule.revision, "condition": "met", "pending": True,
                       "claim": {"run_id": 984, "token": "kept"}}
    rule.last_alerted_at = rule.created_at
    rule.last_run_at = rule.created_at
    rule.save(update_fields=["live_state", "last_alerted_at", "last_run_at"])
    rule.refresh_from_db()
    before = (rule.name, rule.instructions, rule.created_by_id, rule.live_state,
              rule.last_alerted_at, rule.last_run_at, rule.recipients.count())
    data = _form(rule, name="Unsaved edit", instructions="different condition")
    recipient = make_user("recipient@demo.example", org=rule.org)
    grant_studio(recipient, rule.studio, roles.VIEWER)
    data["recipients"] = [recipient.pk]
    response = login(editor).post(f"{prefix}/{rule.pk}/edit", {**data, "action": "preview"},
                                  HTTP_X_TRELLUM_FORM="1", HTTP_ACCEPT="application/json")
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["condition"] == "Met"
    assert captured["actor"] == editor and captured["billing_user"] == editor
    rule.refresh_from_db()
    assert (rule.name, rule.instructions, rule.created_by_id, rule.live_state,
            rule.last_alerted_at, rule.last_run_at, rule.recipients.count()) == before
    assert rule.last_test_at is not None
    run = AlertRun.objects.get(rule=rule, execution_mode=AlertRun.MODE_TEST)
    assert run.history_namespace.startswith(f"editor:{editor.pk}:")
    assert mail.outbox == []


def test_preview_validation_and_budget_error_return_draft_without_sending(
    login, owner, rule, prefix, monkeypatch
):
    from apps.assistant import budget

    client = login(owner)
    bad = client.post(f"{prefix}/new", {**_form(rule, name="Bad"), "timezone": "Mars/Olympus",
                                          "action": "preview"})
    assert bad.status_code == 200 and bad.context["form"].is_bound
    assert "Unknown IANA timezone" in bad.content.decode()
    warnings = []
    monkeypatch.setattr(budget, "precheck", lambda org, user: (False, "Budget is exhausted."))
    from apps.alerts import evaluator
    monkeypatch.setattr(evaluator, "_mail_owner_once", lambda *args: warnings.append(args))
    unavailable = client.post(f"{prefix}/new", {**_form(rule, name="Provider error"),
                                                   "action": "preview"},
                              HTTP_X_TRELLUM_FORM="1", HTTP_ACCEPT="application/json")
    assert unavailable.status_code == 200 and unavailable.json()["result"]["status"] == "Skipped: budget"
    assert "Budget is exhausted" in unavailable.json()["result"]["error"]
    assert warnings == [] and mail.outbox == []


def test_preview_requires_csrf_and_role_access(login, owner, rule, prefix, make_user, grant_studio):
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    before = AlertRule.objects.count()
    response = client.post(f"{prefix}/new", {**_form(rule), "action": "preview"})
    assert response.status_code == 403 and AlertRule.objects.count() == before
    viewer = make_user("preview-viewer@demo.example", org=rule.org)
    grant_studio(viewer, rule.studio, roles.VIEWER)
    assert login(viewer).post(f"{prefix}/new", {**_form(rule), "action": "preview"}).status_code == 403


def test_browser_preview_keeps_draft_and_discards_result_after_edit(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/recipient-picker.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        pending = []
        page.route("http://example.test" + url, lambda route: pending.append(route))
        page.locator("[name=instructions]").fill("Draft A")
        page.locator("#alertForm").evaluate(
            "f => f.dispatchEvent(new SubmitEvent('submit', {bubbles:true, cancelable:true, submitter:f.querySelector('[data-alert-preview]')}))"
        )
        page.wait_for_timeout(40)
        assert len(pending) == 1
        page.locator("[name=instructions]").fill("Draft B")
        pending[0].fulfill(status=400, json={"ok": False, "message": "Old validation",
                                             "errors": {"instructions": ["Old error"]}})
        page.wait_for_timeout(30)
        expect(page.locator("#alertPreview")).to_be_hidden()
        expect(page.locator("[name=instructions]")).not_to_have_attribute("aria-invalid", "true")
        expect(page.locator("[name=instructions]")).to_have_value("Draft B")
        assert page.url == "http://example.test" + url
        browser.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_browser_recipient_and_timezone_pickers(engine, rule, login):
    from playwright.sync_api import Error

    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/new"
    html = login(rule.created_by).get(url).content.decode()
    needle = '</form>\n<section id="alertPreview"'
    injected = '''<script>
      const fields=document.querySelectorAll('[data-recipient-picker]');
      const field=fields[fields.length-1], checkbox=field.querySelector('input[type=checkbox]');
      const list=Array.from(field.children).find(child => child.contains(checkbox));
      for(let i=0;i<100;i++){const label=document.createElement('label');
        const input=document.createElement('input'); input.type='checkbox'; input.name='recipients';
        input.value='picker-test-'+i; label.append(input, ' Person '+i); list.append(label);}
    </script></form>\n<section id="alertPreview"'''
    html = html.replace(needle, injected, 1)
    with sync_playwright() as playwright:
        try:
            browser = getattr(playwright, engine).launch(headless=True)
        except Error as exc:
            pytest.skip(f"{engine} browser is unavailable: {exc}")
        context = browser.new_context(timezone_id="Europe/Amsterdam", viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/choice-picker.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/recipient-picker.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        assert page.locator("#id_timezone").input_value() == "Europe/Amsterdam"
        expect(page.locator("[data-timezone-default-help]")).to_contain_text("Europe/Amsterdam")
        expect(page.locator("[data-alert-preview-status]")).to_have_count(0)
        assert page.locator("#alertForm").get_attribute("data-browser-timezone-default") is not None
        page.locator('input[name="trigger"][value="schedule"]').check()
        timezone_picker = page.locator("#alertForm [data-choice-picker]").nth(1)
        timezone_picker.locator(".ui-picker-trigger").click()
        search = timezone_picker.locator(".ui-picker-search")
        expect(search).to_be_visible()
        search.fill("Europe/Lond")
        timezone_picker.locator(".ui-picker-option").filter(has_text="Europe/London").click()
        expect(page.locator("#id_timezone")).to_have_value("Europe/London")

        field = page.locator("[data-recipient-picker]")
        field.locator(".ui-picker-trigger").click()
        field.locator("[data-recipient-filter=people]").click()
        search = field.locator(".ui-picker-search")
        assert field.locator(".recipient-picker-option").evaluate_all(
            "labels => labels.filter(label => !label.hidden).length"
        ) == 101
        search.fill("person 99")
        assert field.locator(".recipient-picker-option").evaluate_all(
            "labels => labels.filter(label => !label.hidden).length"
        ) == 1
        choice = field.locator(".recipient-picker-option:visible input")
        choice.click()
        expect(choice).to_be_checked()
        search.fill("nobody")
        assert field.locator(".recipient-picker-option").evaluate_all(
            "labels => labels.filter(label => !label.hidden).length"
        ) == 0
        expect(field.locator(".ui-picker-message")).to_have_text("No matches.")
        expect(field.locator(".recipient-picker-selected")).to_contain_text("Person 99")
        assert page.locator("#alertForm").evaluate("form => new FormData(form).getAll('recipients')").count("picker-test-99") == 1
        field.locator(".recipient-picker-selected button").click()
        expect(field.locator(".ui-picker-trigger")).to_be_focused()
        assert page.locator("#alertForm").evaluate("form => new FormData(form).getAll('recipients')").count("picker-test-99") == 0

        unknown = context.new_page()
        unknown.add_init_script("Intl.DateTimeFormat.prototype.resolvedOptions = "
                                "() => ({timeZone: 'Mars/Olympus'});")
        unknown.route("**/*", lambda route: route.abort())
        unknown.route("http://example.test" + url,
                      lambda route: route.fulfill(body=html, content_type="text/html"))
        unknown.goto("http://example.test" + url)
        unknown.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        expect(unknown.locator("#id_timezone")).to_have_value("UTC")
        expect(unknown.locator("[data-timezone-default-help]")).to_contain_text("using UTC")
        browser.close()


def test_alert_editor_layout_desktop_mobile_and_dark(rule, login, tmp_path):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/new"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        for name in ("themes.generated.css", "ui.css", "console-shell.css", "choice-picker.css", "recipient-picker.css"):
            page.add_style_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
        page.locator('[data-recipient-type="people"]').evaluate("""group => {
          for (let i=0; i<120; i++) {
            const label=document.createElement('label'), input=document.createElement('input');
            input.type='checkbox'; input.name='recipients'; input.value='visual-'+i;
            input.dataset.recipientName='Person '+i; input.dataset.recipientIdentity='person'+i+'@example.test';
            label.append(input, document.createTextNode('Person '+i)); group.append(label);
          }
        }""")
        page.add_script_tag(content=(ROOT / "static/choice-picker.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/recipient-picker.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        page.locator("html").evaluate("node => node.setAttribute('data-theme', 'light')")
        page.locator('[data-console-shell]').evaluate_all("nodes => nodes.forEach(node => node.dataset.consoleTheme = 'light')")
        rail = page.locator("form .ui-form-rail")
        header = page.locator("header.ui-form-rail").bounding_box()
        assert header["width"] > 1000
        assert abs(rail.bounding_box()["x"] - header["x"]) < 2
        assert abs(rail.bounding_box()["width"] - header["width"]) < 2
        page.locator('input[name="trigger"][value="schedule"]').check()
        page.locator('[name="freq"]').select_option("weekly")
        expect(page.locator('[name="send_time"]')).to_be_visible()
        expect(page.locator('[name="weekday"]')).to_be_visible()
        expect(page.locator('[data-hourly]')).to_be_hidden()
        expect(page.locator('[data-freq="monthly"]')).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "alerts-desktop-light.png"), full_page=True)
        field = page.locator("[data-recipient-picker]")
        field.locator(".ui-picker-trigger").click()
        assert field.locator(".recipient-picker-option").count() >= 120
        assert field.locator(".ui-picker-options").evaluate("node => node.scrollHeight > node.clientHeight")
        page.screenshot(path=str(tmp_path / "alerts-desktop-picker.png"))

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator('[name="send_time"]').evaluate("node => node.getBoundingClientRect().height") >= 44
        page.wait_for_function("""() => {
          const popup = document.querySelector('[data-recipient-picker] .ui-picker-popup');
          if (!popup) return false;
          const r = popup.getBoundingClientRect();
          return !popup.hidden && r.width > 0 && r.height > 0 &&
            r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
        }""")
        popup_rect = field.locator(".ui-picker-popup").evaluate("""node => {
          const r=node.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom];
        }""")
        assert popup_rect[0] >= 0 and popup_rect[1] >= 0
        assert popup_rect[2] <= 390 and popup_rect[3] <= 844
        page.screenshot(path=str(tmp_path / "alerts-mobile-picker.png"))
        field.locator(".ui-picker-done").click()
        page.screenshot(path=str(tmp_path / "alerts-mobile-light.png"), full_page=True)

        page.locator("html").evaluate("node => node.setAttribute('data-theme', 'dark')")
        page.locator('[data-console-shell]').evaluate_all("nodes => nodes.forEach(node => node.dataset.consoleTheme = 'dark')")
        assert page.locator('[name="send_time"]').evaluate("node => getComputedStyle(node).colorScheme") == "dark"
        assert not page.locator('[data-choice-picker] .ui-picker-trigger').first.is_disabled()
        assert not field.locator(".ui-picker-trigger").is_disabled()
        expect(page.locator("[data-alert-preview]")).to_be_enabled()
        page.evaluate("scrollTo(0, 0)")
        page.screenshot(path=str(tmp_path / "alerts-mobile-dark.png"), full_page=True, animations="disabled")
        print(f"Alert layout screenshots: {tmp_path}")
        browser.close()


def test_change_during_preview_discards_stale_result_without_input_event(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        pending = []
        page.route("http://example.test" + url, lambda route: pending.append(route))
        page.locator("[data-alert-preview]").click()
        page.wait_for_timeout(40)
        assert len(pending) == 1
        page.locator("[name=instructions]").evaluate(
            "field => { field.value = 'Changed without input'; field.dispatchEvent(new Event('change', {bubbles:true})); }"
        )
        pending[0].fulfill(json={"ok": True, "message": "Stale result", "result": {
            "condition": "Met", "title": "Must be discarded", "evidence": [],
            "interpretation": {"window": "today", "timezone": "UTC", "scope": "sales", "freshness": "fresh"},
            "simulation": "would_notify", "simulation_reason": "Eligible", "cost": "0.01", "status": "OK"
        }})
        expect(page.locator("#alertPreview")).to_be_hidden()
        expect(page.locator("[data-alert-preview-status]")).to_contain_text("Draft changed")
        expect(page.locator("[name=instructions]")).to_have_value("Changed without input")
        browser.close()


def test_preview_button_preserves_focus_scroll_and_sends_once(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        for name in ("themes.generated.css", "ui.css", "console-shell.css"):
            page.add_style_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        page.locator("[name=instructions]").fill("Keep this draft")
        page.locator("[name=instructions]").focus()
        page.locator("[data-alert-preview]").scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, scrollY + 35)")
        before = page.evaluate("scrollY")
        pending = []
        page.route("http://example.test" + url, lambda route: pending.append(route))
        page.locator("[data-alert-preview]").click()
        page.locator("[data-alert-preview]").evaluate("button => button.click()")
        page.wait_for_timeout(40)
        assert len(pending) == 1
        pending[0].fulfill(json={"ok": True, "message": "Draft tested", "result": {
            "condition": "Unknown", "title": "<img src=x onerror=alert(1)>", "evidence": [],
            "interpretation": {"window": "48h", "timezone": "UTC", "scope": "sales", "freshness": "fresh"},
            "simulation": "unknown", "simulation_reason": "Insufficient evidence", "cost": "0.01", "status": "OK"
        }})
        expect(page.locator("#alertPreview")).to_contain_text("<img src=x onerror=alert(1)>")
        assert page.locator("#alertPreview img").count() == 0
        expect(page.locator("[name=instructions]")).to_have_value("Keep this draft")
        assert page.evaluate("document.activeElement.name") == "instructions"
        assert abs(page.evaluate("scrollY") - before) < 2
        page.locator("[data-alert-preview]").click()
        page.wait_for_timeout(40)
        assert len(pending) == 2
        page.locator("[name=name]").focus()
        page.evaluate("window.scrollTo(0, scrollY + 45)")
        changed_position = page.evaluate("scrollY")
        pending[1].fulfill(json={"ok": True, "message": "Second test", "result": {
            "condition": "Not met", "title": "Current result", "evidence": [],
            "interpretation": {"window": "today", "timezone": "UTC", "scope": "sales", "freshness": "fresh"},
            "simulation": "not_met", "simulation_reason": "Condition is not met", "cost": "0.01", "status": "OK"
        }})
        expect(page.locator("#alertPreview")).to_contain_text("Current result")
        assert page.evaluate("document.activeElement.name") == "name"
        assert abs(page.evaluate("scrollY") - changed_position) < 2
        browser.close()


def test_keyboard_preview_restores_focus_after_button_is_reenabled(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        pending = []
        page.route("http://example.test" + url, lambda route: pending.append(route))
        button = page.locator("[data-alert-preview]")
        button.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(40)
        assert len(pending) == 1
        pending[0].fulfill(json={"ok": True, "message": "Test complete", "result": {
            "condition": "Met", "title": "Keyboard result", "evidence": [],
            "interpretation": {"window": "today", "timezone": "UTC", "scope": "sales", "freshness": "fresh"},
            "simulation": "would_notify", "simulation_reason": "Eligible", "cost": "0.01", "status": "OK"
        }})
        expect(button).to_be_enabled()
        assert page.evaluate("document.activeElement === document.querySelector('[data-alert-preview]')")
        browser.close()


def test_preview_validation_and_network_errors_keep_draft(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        page.locator("[name=instructions]").fill("Draft survives errors")
        page.route("http://example.test" + url, lambda route: route.fulfill(status=400, json={
            "ok": False, "message": "Check the highlighted fields.",
            "errors": {"instructions": ["Add a coverage window."]}
        }))
        page.locator("[data-alert-preview]").click()
        expect(page.locator("[name=instructions]")).to_have_value("Draft survives errors")
        expect(page.locator("[name=instructions]")).to_have_attribute("aria-invalid", "true")
        expect(page.locator("[data-alert-error]")).to_have_text("Add a coverage window.")
        page.locator("[name=instructions]").fill("Draft still here")
        expect(page.locator("[name=instructions]")).not_to_have_attribute("aria-invalid", "true")
        page.unroute("http://example.test" + url)
        page.route("http://example.test" + url, lambda route: route.abort())
        page.locator("[data-alert-preview]").click()
        expect(page.locator("[data-alert-preview-status]")).to_contain_text("Connection interrupted")
        expect(page.locator("[name=instructions]")).to_have_value("Draft still here")
        browser.close()


def test_save_cancels_pending_preview_and_reenables_test_button(rule, login):
    url = f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit"
    html = login(rule.created_by).get(url).content.decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.route("http://example.test" + url, lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://example.test" + url)
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        previews, saves = [], []

        def intercept(route):
            payload = route.request.post_data or ""
            if 'name="action"' in payload and "preview" in payload:
                previews.append(route)
            else:
                saves.append(route)
                route.fulfill(json={"ok": True, "message": "Saved."})

        page.route("http://example.test" + url, intercept)
        page.locator("#alertForm").evaluate(
            "f => f.dispatchEvent(new SubmitEvent('submit', {bubbles:true, cancelable:true, submitter:f.querySelector('[data-alert-preview]')}))"
        )
        page.wait_for_timeout(30)
        assert len(previews) == 1
        page.locator("#alertForm").evaluate(
            "f => f.dispatchEvent(new SubmitEvent('submit', {bubbles:true, cancelable:true, submitter:f.querySelector('button[type=submit]:not([data-alert-preview])')}))"
        )
        page.wait_for_timeout(50)
        assert len(saves) == 1
        expect(page.locator("[data-alert-preview]")).to_be_enabled()
        expect(page.locator("#alertPreview")).to_be_hidden()
        assert page.locator("#alertPreview").get_attribute("aria-busy") == "false"
        previews[0].fulfill(status=400, json={"ok": False, "message": "Late error",
                                              "errors": {"instructions": ["Stale validation"]}})
        expect(page.locator("[name=instructions]")).not_to_have_attribute("aria-invalid", "true")
        expect(page.locator("#alertPreview")).to_be_hidden()
        browser.close()
