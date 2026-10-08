import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("busy_flag", ["sync_requested", "publishing"])
@pytest.mark.parametrize("last_error", ["", "Repository authentication failed"])
def test_repo_status_polls_to_completion_and_reloads_once(busy_flag, last_error):
    script = (ROOT / "static" / "repo-status.js").read_text(encoding="utf-8")
    script = script.replace("interval = 2000", "interval = 25")
    requests, loads = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def route(route):
            path = route.request.url.rsplit("/", 1)[-1]
            if path == "status":
                requests.append(path)
                payload = {"sync_requested": False, "publishing": False,
                           "pending": None, "last_error": last_error}
                payload[busy_flag] = len(requests) == 1
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
            else:
                loads.append(True)
                queued = "true" if len(loads) == 1 else "false"
                result = "<h1>Refreshed pending changes and sync errors</h1>" if len(loads) == 2 else ""
                route.fulfill(status=200, content_type="text/html", body=f"""
                  <span data-repo-status data-status-url="/status" data-sync-requested="{queued}" data-publish-requested="false" hidden></span>
                  <button id="repoSyncButton" data-idle-label="Sync now" disabled>Checking for changes…</button>
                  <p id="repoPollStatus" role="status"></p>{result}
                  <script>{script}</script>""")
        page.route("http://repo.test/**", route)
        page.goto("http://repo.test/repo")
        page.wait_for_selector("text=Refreshed pending changes and sync errors", timeout=3000)
        # The completed document is idle: no more polls or reloads.
        page.wait_for_timeout(100)
        assert len(requests) == 2
        assert len(loads) == 2
        browser.close()


def test_repo_status_retries_transient_error_and_does_not_poll_when_idle():
    script = (ROOT / "static" / "repo-status.js").read_text(encoding="utf-8")
    script = script.replace("interval = 2000", "interval = 25")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        count = {"status": 0, "page": 0}

        def route(route):
            path = route.request.url.rsplit("/", 1)[-1]
            if path == "status":
                count["status"] += 1
                if count["status"] == 1:
                    route.fulfill(status=503, body="try again")
                else:
                    route.fulfill(status=200, content_type="application/json", body='{"sync_requested":false,"publishing":false}')
            else:
                count["page"] += 1
                queued = "true" if count["page"] == 2 else "false"
                route.fulfill(status=200, content_type="text/html", body=f"""
                  <span data-repo-status data-status-url="/status" data-sync-requested="{queued}" data-publish-requested="false" hidden></span>
                  <button id="repoSyncButton" data-idle-label="Sync now"></button><p id="repoPollStatus"></p>
                  <script>{script}</script>""")
        page.route("http://repo.test/**", route)
        page.goto("http://repo.test/repo")
        page.wait_for_load_state("load")
        page.wait_for_timeout(100)
        assert count["status"] == 0 and count["page"] == 1

        # Start a queued document and observe the first failed request, retry,
        # then its one completion reload.
        page.goto("http://repo.test/queued")
        page.wait_for_function("document.querySelector('[data-repo-status]').dataset.syncRequested === 'false'", timeout=3000)
        assert count["status"] == 2 and count["page"] == 3
        browser.close()


def test_repo_status_waits_for_dirty_settings_and_open_dialog():
    script = (ROOT / "static" / "repo-status.js").read_text(encoding="utf-8").replace("interval = 2000", "interval = 25")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        requests, loads = [], []

        def route(route):
            if route.request.url.endswith("/status"):
                requests.append(True)
                route.fulfill(status=200, content_type="application/json", body='{"sync_requested":false,"publishing":false}')
            else:
                loads.append(True)
                queued = "true" if len(loads) == 1 else "false"
                route.fulfill(status=200, content_type="text/html", body=f"""
                  <form data-settings-form class="is-dirty"></form><dialog open></dialog>
                  <span data-repo-status data-status-url="/status" data-sync-requested="{queued}" data-publish-requested="false" hidden></span>
                  <button id="repoSyncButton" data-idle-label="Check for changes"></button><p id="repoPollStatus"></p>
                  <script>{script}</script>""")
        page.route("http://repo.test/**", route)
        page.goto("http://repo.test/repo")
        page.wait_for_function("document.querySelector('#repoPollStatus').textContent.includes('Check finished')")
        assert len(requests) == 1 and len(loads) == 1
        page.locator("form").evaluate("form => form.classList.remove('is-dirty')")
        # An open dialog alone must still prevent the completion reload.
        page.wait_for_timeout(100)
        assert len(loads) == 1
        with page.expect_event("framenavigated"):
            page.locator("dialog").evaluate("dialog => dialog.close()")
        assert len(loads) == 2
        browser.close()


@pytest.mark.parametrize("idle_label", ["Check for changes", "Sync now"])
def test_repo_status_stops_after_repeated_failures_and_restores_retry(idle_label):
    script = (ROOT / "static" / "repo-status.js").read_text(encoding="utf-8").replace("interval = 2000", "interval = 25")
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def route(route):
            if route.request.url.endswith("/status"):
                requests.append(True)
                route.fulfill(status=503, body="Unavailable")
            else:
                route.fulfill(status=200, content_type="text/html", body=f"""
                  <span data-repo-status data-status-url="/status" data-sync-requested="true" hidden></span>
                  <button id="repoSyncButton" data-idle-label="{idle_label}" disabled>Checking…</button>
                  <p id="repoPollStatus"></p><script>{script}</script>""")

        page.route("http://repo.test/**", route)
        page.goto("http://repo.test/repo")
        page.wait_for_selector("text=Could not confirm completion", timeout=3000)
        assert page.locator("#repoSyncButton").is_enabled()
        assert page.locator("#repoSyncButton").text_content() == idle_label
        page.wait_for_timeout(100)
        assert len(requests) == 5
        browser.close()
