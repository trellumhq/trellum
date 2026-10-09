"""Browser contracts for in-place repository completion and publish checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_SCRIPT = (ROOT / "static" / "settings-forms.js").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "repo-status.js").read_text(encoding="utf-8").replace(
    "interval = 2000", "interval = 25"
)


@pytest.fixture(scope="module")
def repo_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


def _html(*, queued=True, result="Initial results", extras="", dirty=False, configured=True, mode="manual", history=""):
    marker = f'<span data-repo-status data-status-url="/status" data-sync-requested="{str(queued).lower()}" data-publish-requested="false" data-publish-mode="{mode}" hidden></span>' if configured else ""
    return f"""<!doctype html><html><body>
<form id="outsideForm"><label>Unrelated draft <input id="outsideDraft" value="initial"></label></form>
<form id="repoConnectionForm" method="post" data-settings-form data-settings-save>
  <input name="repo_url" value="https://example.test/repo.git"><input type="hidden" name="publish_mode" value="{mode}">
  <button type="submit">Save connection</button></form>
<section id="repoResults" data-repo-results data-status-url="/status">
  {marker}
  <form id="repoSyncForm" method="post" data-settings-save><input type="hidden" name="csrfmiddlewaretoken" value="csrf-token">
    <button type="submit" id="repoSyncButton" data-idle-label="Sync now"{' disabled' if queued else ''}>{'Checking…' if queued else 'Sync now'}</button></form>
  <div id="dynamicResults"><p id="resultText">{result}</p>
    <form id="settings" data-settings-form{' class="is-dirty"' if dirty else ''}><label>Draft <input id="focusDraft" name="draft" value="kept"></label></form>
    {history}
    <details id="history" data-preserve-key="publish-1"><summary>History</summary><p>Saved history detail</p></details>
    {extras}
  </div>
  <div style="height:1400px"></div>
</section>
<p id="repoPollStatus" role="status" aria-live="polite" tabindex="-1" hidden></p>
<script>{SETTINGS_SCRIPT}</script>
<script>{"var draft=document.getElementById('focusDraft');draft.value='dirty';draft.dispatchEvent(new Event('input',{bubbles:true}));" if dirty else ""}</script>
<script>{SCRIPT}</script>
</body></html>"""


def _track_navigation(page):
    navigations = []
    page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
    return navigations


def test_completion_replaces_only_results_and_preserves_scroll_focus_and_drafts(repo_browser):
    page = repo_browser.new_page(viewport={"width": 800, "height": 700})
    page.set_default_timeout(3000)
    status_calls, page_gets, navigations = [], [], []
    refreshed = _html(queued=False, result="Refreshed pending changes and sync errors")

    def route(route):
        if route.request.url.endswith("/status"):
            status_calls.append(True)
            busy = len(status_calls) == 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "sync_requested": busy, "publishing": False, "last_error": "Repository authentication failed"
            }))
        else:
            page_gets.append(route.request.headers.get("accept", ""))
            route.fulfill(status=200, content_type="text/html", body=_html() if len(page_gets) == 1 else refreshed)

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    navigations = _track_navigation(page)
    page.locator("#outsideDraft").fill("keep this unrelated draft")
    page.locator("#history summary").click()
    page.locator("#focusDraft").focus()
    page.evaluate("scrollTo(0, 500)")
    page.wait_for_selector("text=Refreshed pending changes and sync errors")
    assert page.locator("#outsideDraft").input_value() == "keep this unrelated draft"
    assert page.evaluate("document.activeElement.id") == "focusDraft"
    assert page.locator("#history").evaluate("el => el.open")
    assert page.evaluate("scrollY") == 500
    assert page.locator("#repoResults").count() == 1
    assert page.locator("#repoSyncForm").get_attribute("data-settings-initialized") == "1"
    assert page.locator("#repoSyncForm [data-settings-dirty-status]").count() == 1
    page.wait_for_timeout(100)
    assert len(status_calls) == 2 and len(page_gets) == 2, (status_calls, page_gets)
    assert navigations == []
    page.close()


def test_busy_to_complete_error_and_idle_does_not_poll(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    calls, pages = [], []

    def route(route):
        if route.request.url.endswith("/status"):
            calls.append(True)
            if len(calls) == 1:
                route.fulfill(status=503, body="retry")
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "sync_requested": len(calls) == 2, "publishing": False,
                    "last_error": "Repository authentication failed"
                }))
        else:
            pages.append(True)
            route.fulfill(status=200, content_type="text/html", body=_html(
                queued=len(pages) == 1,
                result="Initial sync status" if len(pages) == 1 else "Updated repository status with error details"
            ))

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.wait_for_selector("text=Updated repository status with error details")
    page.wait_for_timeout(75)
    assert len(calls) == 3 and len(pages) == 2
    page.close()

    idle = repo_browser.new_page()
    idle.set_default_timeout(3000)
    idle_calls = []
    idle.route("http://repo.test/**", lambda route: (
        idle_calls.append(route.request.url) or route.fulfill(
            status=200, content_type="text/html", body=_html(queued=False)
        )
    ))
    idle.goto("http://repo.test/repo")
    idle.wait_for_timeout(100)
    assert len(idle_calls) == 1
    idle.close()


def test_completion_waits_for_dirty_form_and_dialog_then_refreshes(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    status_calls, page_gets = [], []
    dirty_dialog = '<dialog id="editDialog" open><form method="dialog"><button>Close</button></form></dialog>'

    def route(route):
        if route.request.url.endswith("/status"):
            status_calls.append(True)
            route.fulfill(status=200, content_type="application/json", body='{"sync_requested":false,"publishing":false}')
        else:
            page_gets.append(True)
            route.fulfill(status=200, content_type="text/html", body=_html(
                queued=len(page_gets) == 1,
                result="Initial report state" if len(page_gets) == 1 else "Replacement applied", dirty=True,
                extras=dirty_dialog
            ))

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.wait_for_function("document.querySelector('#repoPollStatus').textContent.includes('Check finished')")
    assert len(status_calls) == 1 and len(page_gets) == 1
    page.evaluate("document.querySelector('#settings').classList.remove('is-dirty')")
    page.locator("#editDialog").evaluate("dialog => dialog.close()")
    page.wait_for_selector("text=Replacement applied")
    page.wait_for_timeout(75)
    assert len(page_gets) == 2
    page.close()


def test_poll_failure_restores_sync_button_without_page_reload(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    calls, navigations = [], []

    def route(route):
        if route.request.url.endswith("/status"):
            calls.append(True)
            route.fulfill(status=503, body="Unavailable")
        else:
            route.fulfill(status=200, content_type="text/html", body=_html())

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    navigations = _track_navigation(page)
    page.wait_for_selector("text=Could not confirm completion", timeout=3000)
    assert page.locator("#repoSyncButton").is_enabled()
    assert page.locator("#repoSyncButton").text_content() == "Sync now"
    assert len(calls) == 5 and navigations == []
    page.close()


def test_completed_sync_retries_failed_results_refresh(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    status_calls, page_gets = [], []

    def route(route):
        if route.request.url.endswith("/status"):
            status_calls.append(True)
            route.fulfill(status=200, content_type="application/json", body='{"sync_requested":false,"publishing":false}')
        else:
            page_gets.append(True)
            if len(page_gets) == 1:
                route.fulfill(status=200, content_type="text/html", body=_html(queued=True))
            elif len(page_gets) == 2:
                route.fulfill(status=503, content_type="text/plain", body="try again")
            else:
                route.fulfill(status=200, content_type="text/html", body=_html(
                    queued=False, result="Refresh retry succeeded"
                ))

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    navigations = _track_navigation(page)
    page.wait_for_selector("text=Refresh retry succeeded")
    assert len(status_calls) == 1 and len(page_gets) == 3 and navigations == []
    page.close()


def test_publish_confirmation_checks_head_and_posts_reviewed_sha(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    status_calls, page_gets, posts, navigations = [], [], [], []

    def status_response():
        return json.dumps({
            "sync_requested": len(status_calls) in (3,), "publishing": False,
            "remote_sha": "b" * 40 if len(status_calls) == 1 else "a" * 40,
        })

    def route(route):
        if route.request.url.endswith("/status"):
            status_calls.append(True)
            route.fulfill(status=200, content_type="application/json", body=status_response())
        elif route.request.url.endswith("/publish"):
            posts.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=202, content_type="application/json", body='{"ok":true,"scheduled":true}')
        else:
            page_gets.append(True)
            extra = '''<form id="modeForm" data-settings-form data-settings-save>
              <input type="radio" name="publish_mode" value="manual" checked>
              <input type="radio" name="publish_mode" value="auto"><button type="submit">Save mode</button></form>
              <button id="reviewBtn" data-remote="''' + "a" * 40 + '''">Review &amp; publish</button>
              <dialog id="modeConfirm"><button type="button" data-go>Switch and publish</button></dialog>
              <dialog id="publishDialog"><input type="checkbox" id="publishRebuild" checked>
                <p id="publishNote" role="alert" hidden></p><button type="button" id="publishGo" disabled>Publish</button></dialog>'''
            route.fulfill(status=200, content_type="text/html", body=_html(
                queued=False,
                result="Current commits" if len(page_gets) == 1 else "Published results refreshed",
                extras=extra
            ))

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    navigations = _track_navigation(page)
    page.locator("#reviewBtn").click()
    page.wait_for_function("document.querySelector('#publishNote').textContent.includes('New commits arrived')")
    assert page.locator("#publishGo").is_disabled()
    page.locator("#publishDialog").evaluate("dialog => dialog.close()")
    page.locator("#reviewBtn").click()
    page.wait_for_function("!document.querySelector('#publishGo').disabled")
    assert page.locator("#publishDialog").evaluate("el => el.open")
    page.locator("#publishGo").click()
    page.wait_for_selector("text=Published results refreshed")
    assert posts == [{"to": "a" * 40, "rebuild": True}]
    assert len(page_gets) == 2 and navigations == []
    page.close()


def test_switch_to_auto_requires_confirmation_before_shared_save(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    pages, status = [], []
    extra = '''<form method="post" id="modeForm" data-settings-form data-settings-save>
      <input type="hidden" name="csrfmiddlewaretoken" value="csrf-token">
      <input type="radio" name="publish_mode" value="manual" checked>
      <input type="radio" name="publish_mode" value="auto"><button type="submit">Save mode</button></form>
      <dialog id="modeConfirm"><button type="button" data-go>Switch and publish</button>
      <form method="dialog"><button>Cancel</button></form></dialog>'''

    def route(route):
        if route.request.url.endswith("/status"):
            status.append(True)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "sync_requested": len(status) < 4, "publishing": False
            }))
        elif route.request.method == "POST":
            route.fulfill(json={"ok": True, "sync_requested": True, "publish_requested": True, "publish_mode": "auto"})
        else:
            pages.append(True)
            route.fulfill(status=200, content_type="text/html", body=_html(
                queued=False, result="Mode saved" if len(pages) > 1 else "Mode initial", extras=extra, mode="auto" if len(pages) > 1 else "manual"
            ))

    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    navigations = _track_navigation(page)
    page.locator('#modeForm input[value="auto"]').check()
    page.locator('#modeForm button[type="submit"]').click()
    assert page.locator("#modeConfirm").evaluate("dialog => dialog.open")
    assert len(pages) == 1
    page.locator("#modeConfirm form button").click()
    assert not page.locator("#modeConfirm").evaluate("dialog => dialog.open")
    page.wait_for_function("document.querySelector('#modeForm input[value=manual]').checked")

    page.locator('#modeForm input[value="auto"]').check()
    page.locator('#modeForm button[type="submit"]').click()
    page.locator("#modeConfirm [data-go]").click()
    assert not page.locator("#modeConfirm").evaluate("dialog => dialog.open")
    page.wait_for_function("document.querySelector('#repoPollStatus').textContent.includes('Checking')")
    page.wait_for_timeout(50)
    assert page.locator('#modeForm input[value="auto"]').is_checked()
    page.wait_for_selector("text=Mode saved")
    assert len(pages) == 2 and len(status) == 4 and navigations == []
    page.close()


MODE_FORM = """<form method="post" id="modeForm" data-settings-form data-settings-save>
  <input type="hidden" name="set_mode" value="1">
  <input type="radio" name="publish_mode" value="manual" checked>
  <input type="radio" name="publish_mode" value="auto"><button type="submit">Save mode</button></form>
  <dialog id="modeConfirm"><button type="button" data-go>Switch and publish</button></dialog>"""
PUBLISH_CONTROLS = '<button id="reviewBtn" data-remote="' + 'a' * 40 + '">Review</button>' + """
  <dialog id="publishDialog"><p id="publishNote" hidden></p>
  <button type="button" id="publishGo" disabled>Publish</button></dialog>"""


def test_no_repo_initializes_without_polling_and_first_connection_refreshes(repo_browser):
    page = repo_browser.new_page()
    errors, reads, posts = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    def route(route):
        if route.request.method == "POST":
            posts.append(route.request)
            route.fulfill(json={"ok": True, "repo_configured": True, "publish_mode": "auto"})
        elif route.request.url.endswith("/status"):
            reads.append(route.request.url)
            route.fulfill(json={"sync_requested": False})
        else:
            configured = bool(posts)
            route.fulfill(content_type="text/html", body=_html(queued=False, configured=configured,
                mode="auto", result="First repo configured" if configured else "No repository"))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.wait_for_timeout(75)
    assert reads == [] and errors == []
    page.locator("#repoConnectionForm").evaluate("form => form.requestSubmit()")
    page.wait_for_selector("text=First repo configured")
    assert len(reads) == 1 and len(posts) == 1 and errors == []
    page.close()


def test_confirmed_mode_validation_failure_releases_refresh_and_controls(repo_browser):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    status_requests, gets, posts = [], [], []
    def route(route):
        if route.request.method == "POST":
            posts.append(route.request)
            route.fulfill(status=400, json={"ok": False, "message": "Mode rejected", "errors": {"publish_mode": ["Mode rejected"]}})
        elif route.request.url.endswith("/status"):
            status_requests.append(route)
        else:
            gets.append(True)
            route.fulfill(content_type="text/html", body=_html(queued=len(gets) == 1,
                extras=MODE_FORM, result="Mode recovered" if len(gets) > 1 else "Initial mode"))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.locator('#modeForm input[value="auto"]').check()
    assert len(status_requests) == 1
    status_requests[0].fulfill(json={"sync_requested": False})
    page.wait_for_function("document.querySelector('#repoPollStatus').textContent.includes('Check finished')")
    page.locator('#modeForm button[type="submit"]').click()
    page.locator('#modeConfirm [data-go]').click()
    page.wait_for_function("document.querySelector('#modeForm').dataset.settingsState === 'error'")
    assert page.locator('#modeForm button[type="submit"]').is_enabled()
    assert len(posts) == 1
    page.locator('#modeForm').evaluate("form => form.reset()")
    page.wait_for_selector("text=Mode recovered")
    assert len(gets) == 2
    page.close()


def test_mode_after_completed_sync_refreshes_and_connection_save_keeps_mode(repo_browser):
    from email.parser import BytesParser
    from email.policy import default
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    gets, posts = [], []
    saved_mode = "manual"
    def route(route):
        nonlocal saved_mode
        if route.request.method == "POST":
            request = route.request
            mime = BytesParser(policy=default).parsebytes(
                ("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode() + request.post_data_buffer)
            fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode() for part in mime.iter_parts()}
            posts.append(fields)
            saved_mode = fields["publish_mode"]
            route.fulfill(json={"ok": True, "publish_mode": saved_mode, "repo_configured": True})
        elif route.request.url.endswith("/status"):
            route.fulfill(json={"sync_requested": False})
        else:
            gets.append(True)
            route.fulfill(content_type="text/html", body=_html(queued=len(gets) == 1, mode=saved_mode,
                extras=MODE_FORM.replace('value="manual" checked', 'value="manual"').replace('value="auto"', 'value="auto" checked') if saved_mode == "auto" else MODE_FORM,
                result=f"Results version {len(gets)}"))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.wait_for_selector("text=Results version 2")
    page.locator('#modeForm input[value="auto"]').check()
    page.locator('#modeForm button[type="submit"]').click()
    page.locator('#modeConfirm [data-go]').click()
    page.wait_for_selector("text=Results version 3")
    assert page.locator('#repoConnectionForm input[name="publish_mode"]').input_value() == "auto"
    page.locator('#repoConnectionForm').evaluate("form => form.requestSubmit()")
    page.wait_for_selector("text=Results version 4")
    assert len(posts) == 2 and all(fields["publish_mode"] == "auto" for fields in posts)
    page.close()


@pytest.mark.parametrize("failure", ["network", "timeout"])
def test_unknown_publish_outcome_cannot_retry_without_fresh_status(repo_browser, failure):
    page = repo_browser.new_page()
    page.set_default_timeout(3000)
    page.add_init_script("""const timer=window.setTimeout;window.setTimeout=function(callback,delay){if(delay===10000)window.repoTimeout=callback;return timer(callback,delay);};""")
    posts, reads = [], []
    def route(route):
        if route.request.url.endswith("/publish"):
            posts.append(route)
            if failure == "network": route.abort()
        elif route.request.url.endswith("/status"):
            reads.append(True)
            route.fulfill(json={"remote_sha": "a" * 40, "last_synced_sha": "b" * 40, "sync_requested": False})
        else:
            route.fulfill(content_type="text/html", body=_html(queued=False, extras=PUBLISH_CONTROLS))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.locator('#reviewBtn').click()
    page.wait_for_function("!document.querySelector('#publishGo').disabled")
    page.locator('#publishGo').click()
    page.wait_for_timeout(30)
    if failure == "timeout": page.evaluate("repoTimeout()")
    page.wait_for_function("document.querySelector('#publishNote').textContent.includes('outcome is unknown')")
    assert page.locator('#publishGo').is_disabled()
    assert "Try again" not in page.locator('#publishNote').text_content()
    page.locator('#publishGo').dispatch_event("click")
    page.wait_for_timeout(50)
    assert len(posts) == 1 and len(reads) == 1
    page.locator('#publishDialog').evaluate("dialog => dialog.close()")
    page.locator('#reviewBtn').click()
    page.wait_for_function("!document.querySelector('#publishGo').disabled")
    assert len(posts) == 1 and len(reads) == 2
    page.close()


@pytest.mark.parametrize("phase", ["verification", "publish"])
def test_pagehide_ignores_late_publish_callbacks_and_pageshow_reconciles(repo_browser, phase):
    page = repo_browser.new_page()
    page.add_init_script("""
      window.repoRequests=[];const realFetch=window.fetch;
      window.fetch=function(url,options){
        if(String(url).endsWith('/status')||String(url).endsWith('/publish'))
          return new Promise(resolve=>repoRequests.push({url:String(url),resolve:resolve}));
        return realFetch(url,options);
      };
    """)
    page.route("http://repo.test/**", lambda route: route.fulfill(content_type="text/html", body=_html(queued=False, extras=PUBLISH_CONTROLS)))
    page.goto("http://repo.test/repo")
    page.locator('#reviewBtn').click()
    if phase == "publish":
        page.evaluate("repoRequests[0].resolve({ok:true,json:async()=>({remote_sha:'a'.repeat(40)})})")
        page.wait_for_function("!document.querySelector('#publishGo').disabled")
        page.locator('#publishGo').click()
    count = 1 if phase == "verification" else 2
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    page.evaluate("repoRequests[repoRequests.length-1].resolve({ok:true,json:async()=>({ok:true,remote_sha:'a'.repeat(40)})})")
    page.wait_for_timeout(75)
    assert page.evaluate("repoRequests.length") == count
    assert page.locator('#publishDialog').evaluate("dialog=>dialog.open")
    if phase == "verification": assert page.locator('#publishGo').is_disabled()
    assert page.locator('#repoPollStatus').text_content() != "Publishing…"
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
    page.wait_for_function(f"repoRequests.length === {count + 1}")
    assert page.locator('#publishGo').is_disabled()
    page.close()


def test_history_details_keep_stable_publish_key_after_new_row_and_time_change(repo_browser):
    page = repo_browser.new_page()
    pending = []
    gets = []
    old = '<table><tr><td>1 minute ago</td><td><details id="oldPublish" data-preserve-key="publish-42"><summary>1 commit</summary>Old commit</details></td></tr></table>'
    new = '<table><tr><td>just now</td><td><details id="newPublish" data-preserve-key="publish-43"><summary>1 commit</summary>New commit</details></td></tr><tr><td>2 minutes ago</td><td><details id="oldPublish" data-preserve-key="publish-42"><summary>1 commit</summary>Old commit</details></td></tr></table>'
    def route(route):
        if route.request.url.endswith("/status"): pending.append(route)
        else:
            gets.append(True)
            route.fulfill(content_type="text/html", body=_html(queued=len(gets)==1, history=old if len(gets)==1 else new))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.locator('#oldPublish summary').click()
    pending[0].fulfill(json={"sync_requested": False})
    page.wait_for_selector('#newPublish')
    assert page.locator('#oldPublish').evaluate("details=>details.open")
    assert not page.locator('#newPublish').evaluate("details=>details.open")
    page.close()



def test_failed_shared_sync_save_restores_button_and_allows_retry(repo_browser):
    page = repo_browser.new_page()
    posts = []
    def route(route):
        if route.request.method == "POST":
            posts.append(True)
            route.fulfill(status=400, json={"ok": False, "message": "Could not schedule sync"})
        else: route.fulfill(content_type="text/html", body=_html(queued=False))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.locator('#repoSyncButton').click()
    page.wait_for_function("document.querySelector('#repoSyncForm').dataset.settingsState === 'error'")
    assert page.locator('#repoSyncButton').is_enabled()
    assert page.locator('#repoSyncButton').text_content() == "Sync now"
    page.locator('#repoSyncButton').click()
    page.wait_for_function("document.querySelector('#repoSyncForm').dataset.settingsState === 'error'")
    assert len(posts) == 2
    page.close()


def test_connection_write_invalidates_older_results_read_before_save_finishes(repo_browser):
    page = repo_browser.new_page()
    page.add_init_script("""
      window.regionReads=[];const realFetch=window.fetch;
      window.fetch=function(url,options){
        if(String(url)===location.href&&(!options.method||options.method==='GET'))
          return new Promise(resolve=>regionReads.push(resolve));
        return realFetch(url,options);
      };
    """)
    writes = []
    def route(route):
        if route.request.method == "POST": writes.append(route)
        elif route.request.url.endswith("/status"): route.fulfill(json={"sync_requested": False})
        else: route.fulfill(content_type="text/html", body=_html(queued=True, mode="manual", result="Before new connection"))
    page.route("http://repo.test/**", route)
    page.goto("http://repo.test/repo")
    page.wait_for_function("regionReads.length === 1")
    page.locator('#repoConnectionForm input[name="repo_url"]').fill("https://example.test/new.git")
    page.locator('#repoConnectionForm').evaluate("form=>form.requestSubmit()")
    page.wait_for_function("document.querySelector('#repoConnectionForm').getAttribute('aria-busy') === 'true'")
    stale = _html(queued=False, mode="manual", result="Stale connection result")
    page.evaluate("html=>regionReads[0]({ok:true,headers:{get:()=> 'text/html'},text:async()=>html,url:location.href})", stale)
    page.wait_for_timeout(30)
    assert page.locator('#resultText').text_content() == "Before new connection"
    writes[0].fulfill(json={"ok": True, "publish_mode": "auto", "repo_configured": True})
    page.wait_for_function("regionReads.length === 2")
    fresh = _html(queued=False, mode="auto", result="Fresh connection result")
    page.evaluate("html=>regionReads[1]({ok:true,headers:{get:()=> 'text/html'},text:async()=>html,url:location.href})", fresh)
    page.wait_for_selector("text=Fresh connection result")
    assert page.locator('#repoConnectionForm input[name="repo_url"]').input_value() == "https://example.test/new.git"
    assert page.locator('#repoConnectionForm input[name="publish_mode"]').input_value() == "auto"
    page.close()
