from pathlib import Path

import pytest
from django.test import Client
from django.core import mail
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
        input.value=i; label.append(input, ' Person '+i); list.append(label);}
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
        page.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        assert page.locator("#id_timezone").input_value() == "Europe/Amsterdam"
        assert page.locator("#alertForm").get_attribute("data-browser-timezone-default") is not None
        timezone = page.locator("#id_timezone")
        timezone.fill("Europe/Lond")
        timezone.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(timezone).to_have_value("Europe/London")

        field = page.locator("[data-recipient-picker]").last
        search = field.locator("input[type=search]")
        assert field.locator("[data-picker-results] label").evaluate_all(
            "labels => labels.filter(label => !label.hidden).length"
        ) == 50
        search.fill("person 99")
        assert field.locator("[data-picker-results] label").evaluate_all(
            "labels => labels.filter(label => !label.hidden).length"
        ) == 1
        field.locator("[data-picker-results] label:visible input").check()
        search.fill("nobody")
        assert field.locator("[data-picker-results] label").evaluate_all(
            "labels => labels.filter(label => !label.hidden).length"
        ) == 0
        expect(field.locator("[data-picker-selected]")).to_contain_text("Person 99")
        assert page.locator("#alertForm").evaluate("form => new FormData(form).getAll('recipients')").count("99") == 1
        field.locator("[data-picker-selected] button").click()
        assert page.locator("#alertForm").evaluate("form => new FormData(form).getAll('recipients')").count("99") == 0

        unknown = context.new_page()
        unknown.add_init_script("Intl.DateTimeFormat.prototype.resolvedOptions = "
                                "() => ({timeZone: 'Mars/Olympus'});")
        unknown.route("**/*", lambda route: route.abort())
        unknown.route("http://example.test" + url,
                      lambda route: route.fulfill(body=html, content_type="text/html"))
        unknown.goto("http://example.test" + url)
        unknown.add_script_tag(content=(ROOT / "static/alert-editor.js").read_text(encoding="utf-8"))
        expect(unknown.locator("#id_timezone")).to_have_value("UTC")
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
