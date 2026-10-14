"""Real browser formdata and native bounded checkbox submission."""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("count", [25, 100, 1000])
@pytest.mark.parametrize("enhanced", [False, True])
def test_browser_complete_save_native_browse_and_enhanced_reset(engine, count, enhanced, live_server, login, org_admin, org, studio, make_group, settings, tmp_path):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    settings.DATA_DIR = tmp_path / "data"
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f"transport-{i:04}", name=f"Report {i:04}", present_in_scan=True) for i in range(count)])
    group = make_group("Transport browser")
    grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role="viewer", viewer_scope="selected")
    ReportPermissionGrant.objects.bulk_create([ReportPermissionGrant(grant=grant, report=report) for report in reports])
    client = login(org_admin)
    host = live_server.url
    path = f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access&studio={studio.pk}"
    def assignments():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: set(ReportPermissionGrant.objects.filter(grant_id=grant.pk).values_list("report_id", flat=True))).result()
    requests = []
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        context = browser.new_context(java_script_enabled=enhanced, viewport={"width": 390, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        def static(route):
            asset = settings.BASE_DIR / "static" / urlparse(route.request.url).path.split("/static/", 1)[1]
            route.fulfill(path=str(asset)) if asset.is_file() else route.continue_()
        page.route("**/static/**", static)
        page.on("request", lambda request: requests.append(request) if request.method == "POST" and "/report-choices" not in request.url else None)
        page.goto(host + path)
        form = page.locator("[data-studio-access]")
        expect(form).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") if enhanced else True
        if enhanced:
            assert form.locator('[name="reports"]:checked').count() == count
            # Reset must retain every offpage initial selection, and disabled
            # controls must be omitted from the packed successful values.
            packed = form.evaluate("""f => {
              const checks = [...f.querySelectorAll('[name="reports"]')];
              checks[0].checked = false;
              checks[0].dispatchEvent(new Event('input', {bubbles:true}));
              f.reset();
              checks[0].disabled = true;
              const data = new FormData(f);
              checks[0].disabled = false;
              return {ordinary: data.getAll('reports'), packet: JSON.parse(data.get('reports__selection')), count: checks.filter(c=>c.checked).length};
            }""")
            assert packed["ordinary"] == []
            assert packed["count"] == count
            assert set(packed["packet"]["selected"]) == {str(r.pk) for r in reports[1:]}
            form.evaluate("f => { const c=f.querySelector('[name=reports]'); c.checked=false; c.dispatchEvent(new Event('input',{bubbles:true})); }")
            save = form.get_by_role("button", name="Save changes", exact=True)
            save.scroll_into_view_if_needed()
            before_y = page.evaluate("scrollY")
            save.click()
            expect(form.locator("[data-settings-dirty-status]")).to_contain_text("Studio access updated.")
            expect(save).to_be_focused()
            assert abs(page.evaluate("scrollY") - before_y) <= 2
            expect(page).to_have_url(host + path)
        else:
            assert form.locator('[name="reports"]').count() == min(count, 25)
            form.locator('[name="reports"]').first.uncheck()
            if count > 25:
                form.get_by_role("button", name=f"Remove Report {count-1:04}", exact=False).click()
                form = page.locator("[data-studio-access]")
                assert assignments() == {r.pk for r in reports}
                assert form.locator('[name="reports"]').count() == 25
            form.get_by_role("button", name="Save changes", exact=True).click()
            expect(page.get_by_text("Studio access updated.", exact=True)).to_be_visible()
        expected = {r.pk for r in reports[1:]}
        if not enhanced and count > 25:
            expected.remove(reports[-1].pk)
        assert assignments() == expected
        assert requests
        # Count multipart parts rather than byte substrings in packed JSON.
        body = requests[-1].post_data_buffer
        assert body and len(body) < 32768
        if 'multipart/form-data' in requests[-1].headers.get('content-type', ''):
            fields = body.count(b'Content-Disposition: form-data;')
        else:
            fields = sum(len(values) for values in parse_qs(body.decode(), keep_blank_values=True).values())
        assert fields <= 64
        if enhanced:
            # An injected unavailable identity must remain rejected, retained
            # and visible instead of being lost when the packet is built.
            form.evaluate("f => { const c=document.createElement('input'); c.type='checkbox'; c.name='reports'; c.value='999999999'; c.checked=true; f.append(c); TrellumSettingsForms.trackControls(f,[c]); c.dispatchEvent(new Event('input',{bubbles:true})); }")
            form.get_by_role("button", name="Save changes", exact=True).click()
            expect(form.locator("[data-settings-error]")).to_contain_text("Select a valid choice")
            assert assignments() == expected
            assert form.locator('[name="reports"][value="999999999"]').is_checked()
        context.close()
        browser.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_compact_pending_snapshot_neighbor_and_unknown_outcome(engine):
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[3]
    rows = [{"value": str(i), "label": f"Report {i}"} for i in range(1, 1001)]
    html = '''<div style="height:900px"></div><form id="access" action="/save" method="post" data-settings-form data-settings-save>
      <fieldset data-recipient-picker data-recipient-mode="choices" data-recipient-name="reports" data-recipient-compact data-picker-label="Reports">
      <legend>Reports</legend><div data-compact-choices><input type="hidden" name="reports__selection" data-compact-packet value="{}">
      <script type="application/json">''' + json.dumps(rows) + '''</script>
      <div><label><input type="checkbox" name="reports" value="1" checked>Report 1</label></div></div></fieldset>
      <div class="ui-form-actions"><button type="submit">Save changes</button></div></form>
      <form id="neighbor" data-settings-form><input name="draft" value="Original"></form><div style="height:900px"></div>'''
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.route("https://example.test/editor", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://example.test/editor")
        for script in ("choice-picker.js", "settings-forms.js", "recipient-picker.js"):
            page.add_script_tag(content=(root / "static" / script).read_text())
        form = page.locator("#access")
        expect(form.locator('[name="reports"]:checked')).to_have_count(1000)
        page.locator('#neighbor input').fill("Neighbor draft")
        pending = []
        page.route("**/save", lambda route: pending.append(route))
        def uncheck(identity):
            form.locator(f'[name="reports"][value="{identity}"]').evaluate("c => {c.checked=false; c.dispatchEvent(new Event('input',{bubbles:true}));}")
        uncheck(1)
        save = form.get_by_role("button", name="Save changes")
        save.scroll_into_view_if_needed()
        before_y = page.evaluate("scrollY")
        save.click()
        expect(save).to_be_disabled()
        page.wait_for_timeout(30)
        assert len(pending) == 1
        body = pending[0].request.post_data_buffer
        assert b'"1000"' in body and len(body) < 32768
        # Duplicate submit and newer checkbox edits must preserve the submitted
        # snapshot while the shared handler acknowledges just this request.
        form.evaluate("f => f.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}))")
        uncheck(2)
        pending[0].fulfill(json={"ok": True, "message": "Saved."})
        expect(save).to_be_enabled()
        expect(form.locator('[data-settings-dirty-status]')).to_contain_text("Unsaved changes")
        assert len(pending) == 1
        assert not form.locator('[name="reports"][value="2"]').is_checked()
        expect(page.locator('#neighbor input')).to_have_value("Neighbor draft")
        assert abs(page.evaluate("scrollY") - before_y) <= 2
        expect(save).to_be_focused()
        # Shorten only the shared write timeout in this controlled test. The
        # dropped request remains unknown and must never be replayed.
        page.evaluate("""() => { const schedule=window.setTimeout; window.setTimeout=function(fn, delay) { return schedule(fn, delay===30000 ? 100 : delay); }; }""")
        save.click()
        expect(form.locator('[data-settings-dirty-status]')).to_contain_text("unknown", timeout=5000)
        expect(save).to_be_enabled()
        page.wait_for_timeout(200)
        assert len(pending) == 2
        assert not form.locator('[name="reports"][value="2"]').is_checked()
        expect(page.locator('#neighbor input')).to_have_value("Neighbor draft")
        browser.close()
