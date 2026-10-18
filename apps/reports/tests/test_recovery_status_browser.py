"""Recovery evidence uses the existing Operations history, without replay actions."""
import os
import re

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from .test_operations_metrics_browser import _operations_fixture_html
from .test_portal_mobile_runtime import REPORT, _ops_log, _ops_respond


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_history_explains_recovery_and_unknown_outcomes(width):
    status = {"running": {}, "queue": []}
    run = {
        "status": "error", "trigger": "cron", "duration_seconds": 12,
        "created_at": "2026-10-19T09:00:00Z", "peak_memory_mb": 128,
        "diagnostics": {
            "version": 1, "stage": "fetch", "state": "failed", "transient": True,
            "timeout_seconds": 3600, "attempt": 3,
            "errors": [{"class": "OperationalError", "code": "08006"}],
            "timings": [{"stage": "fetch", "elapsed_ms": 12000}],
        },
        "recovery": {
            "state": "pending", "reason": "transient_connection", "attempt": 0,
            "due_at": "2026-10-19T09:01:04Z",
        },
    }

    def extra(route, path, query, reply):
        if path.endswith("/status") and "/api/reports/" in path:
            reply({"aggregates": {}, "history": [run]})
        elif path.startswith("/static/"):
            asset = settings.BASE_DIR / path.lstrip("/")
            if not asset.is_file():
                return False
            route.fulfill(path=str(asset))
        elif path.startswith("/_vendor/"):
            asset = settings.BASE_DIR / "trellum/static/vendor" / path.removeprefix("/_vendor/")
            route.fulfill(path=str(asset))
        else:
            return False
        return True

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", _ops_respond(
            lambda: [{**REPORT, "last_status": "error"}], lambda: status,
            log=lambda: _ops_log("error"), extra=extra, html=_operations_fixture_html(),
        ))
        page.goto("http://portal.test/s/acme/analytics/operations")
        page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()
        history = page.locator("#opsHist_revenue-overview")
        expect(history).to_contain_text("Automatic retry pending · 0 of 2 retries used")
        expect(history).to_contain_text("Last stage: fetch (failed)")
        expect(history).to_contain_text("Transient connection failure")
        expect(history).to_contain_text("Retry due:")
        evidence = history.locator("summary")
        evidence.focus()
        page.keyboard.press("Enter")
        expect(history.locator("details")).to_have_attribute("open", "")
        expect(history).to_contain_text("OperationalError · 08006")
        expect(history).to_contain_text("Build limit: 3600 seconds")
        expect(history).to_contain_text("Query attempt: 3")
        assert history.get_by_role("button", name=re.compile("Retry|Refresh|Apply")).count() == 0
        if os.environ.get("TRELLUM_CAPTURE_PORTAL") == "1":
            output = settings.BASE_DIR / ".artifacts/portal"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=output / f"recovery-status-{width}.png", full_page=True)
            # A frozen rendered fixture also permits collaborative preview inspection.
            snapshot = re.sub(r"<script\b[^>]*>.*?</script>", "", page.content(), flags=re.S)
            (output / f"recovery-status-{width}.html").write_text(snapshot)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("""() =>
            Array.from(document.querySelectorAll('body *')).filter(el => el.getBoundingClientRect().right > innerWidth)
            .map(el => [el.tagName, el.className, Math.round(el.getBoundingClientRect().width)]).slice(0, 30)""")

        def refresh():
            # Reopening also checks the initial history path for terminal states.
            page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()
            page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()

        run["recovery"] = {"state": "exhausted", "reason": "attempt_budget_exhausted", "attempt": 2}
        refresh()
        expect(history).to_contain_text("Automatic retries exhausted · 2 of 2 retries used")
        expect(history).not_to_contain_text("Retry due:")

        run["diagnostics"] = {"stage": "execute", "cause": '<img src=x onerror="window.injected=true">'}
        run["recovery"] = {"state": "unconfirmed", "reason": "operation_outcome_unconfirmed", "attempt": 0}
        refresh()
        expect(history).to_contain_text("Previous outcome unconfirmed; no automatic replay")
        expect(history).to_contain_text('<img src=x onerror="window.injected=true">')
        assert history.locator("img").count() == 0
        assert page.evaluate("window.injected !== true")

        run["diagnostics"] = {}
        run["recovery"] = {"state": "ineligible", "reason": "report_replay_not_safe", "attempt": 0}
        refresh()
        expect(history).to_contain_text("Cause unknown")
        expect(history).to_contain_text("Report has no safe replay contract")

        run["status"] = "success"
        run["recovery"] = {"state": "succeeded", "reason": "successful_build", "attempt": 1}
        refresh()
        expect(history).to_contain_text("Recovered automatically")
        expect(history).not_to_contain_text("Cause unknown")
        assert not errors
        browser.close()


def test_existing_poll_refreshes_recovery_preserving_evidence_and_focus():
    status = {"running": {}, "queue": []}
    history_calls = []
    delayed = []
    hold = True
    run = {
        "created_at": "2026-10-18T09:00:00Z", "status": "error", "trigger": "schedule",
        "diagnostics": {"stage": "fetch", "state": "failed", "timeout_seconds": 3600},
        "recovery": {"state": "pending", "attempt": 0},
    }

    def extra(route, path, query, reply):
        if path.endswith("/status") and "/api/reports/" in path:
            history_calls.append(route.request.url)
            if hold:
                delayed.append(reply)
            else:
                reply({"history": [run]})
            return True
        if path.startswith("/static/"):
            asset = settings.BASE_DIR / path.lstrip("/")
            if asset.is_file():
                route.fulfill(path=str(asset))
                return True
        if path.startswith("/_vendor/"):
            asset = settings.BASE_DIR / "trellum/static/vendor" / path.removeprefix("/_vendor/")
            route.fulfill(path=str(asset))
            return True
        return False

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/*", _ops_respond(
            lambda: [{**REPORT, "last_status": "error"}], lambda: status,
            extra=extra, html=_operations_fixture_html(),
        ))
        page.goto("http://portal.test/s/acme/analytics/operations")
        page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()
        history = page.locator("#opsHist_revenue-overview")
        expect(history).to_contain_text("Loading run history")
        page.evaluate("clearTimeout(_opsStatusTimer); _opsStatusRevision++")
        # A newer action invalidates this delayed initial read on an idle row.
        assert len(delayed) == 1
        delayed.pop()({"history": [run]})
        page.wait_for_function("!_opsHistoryInFlight['revenue-overview']")
        expect(history).to_contain_text("Loading run history")
        hold = False
        page.evaluate("status => _opsApplyStatus(status, _opsStatusRevision, _opsStatusCycle)", status)
        expect(history).to_contain_text("Automatic retry pending")
        assert len(history_calls) == 2
        # Avoid wall-clock timing in this test; drive the shipped poll's apply path.
        page.evaluate("clearTimeout(_opsStatusTimer)")

        def poll():
            page.evaluate("status => _opsApplyStatus(status, _opsStatusRevision, _opsStatusCycle)", status)

        evidence = history.locator("summary")
        evidence.focus()
        page.keyboard.press("Enter")
        page.evaluate("window.__evidenceNode = document.activeElement; window.__historyScroll = scrollY")
        run["recovery"] = {"state": "running", "attempt": 1}
        status["running"] = {REPORT["slug"]: {"elapsed_seconds": 1}}
        poll()
        # A changed response is deferred while a person is using the evidence.
        expect(history).to_contain_text("Automatic retry pending")
        assert page.evaluate("document.activeElement === window.__evidenceNode")
        page.locator("#searchInput").focus()
        poll()
        expect(history).to_contain_text("Automatic retry running")
        expect(history.locator("details")).to_have_attribute("open", "")
        page.evaluate("window.__stableDetails = document.querySelector('#opsHist_revenue-overview details')")
        poll()
        assert page.evaluate("window.__stableDetails === document.querySelector('#opsHist_revenue-overview details')")

        run["recovery"] = {"state": "unconfirmed", "attempt": 1, "reason": "prior_process_not_confirmed_stopped"}
        poll()
        expect(history).to_contain_text("Previous outcome unconfirmed; no automatic replay")
        expect(history.locator("details")).to_have_attribute("open", "")

        # The existing cycle stops history reads while the document is hidden.
        before = len(history_calls)
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange'))")
        poll()
        assert len(history_calls) == before
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, value:false}); document.dispatchEvent(new Event('visibilitychange'))")

        hold = True
        page.evaluate("_opsApplyStatus(window._opsLatestStatus, _opsStatusRevision, _opsStatusCycle); undefined")
        page.wait_for_function("!!_opsHistoryInFlight['revenue-overview']")
        # A second cycle reuses the one pending read.
        page.evaluate("_opsApplyStatus(window._opsLatestStatus, _opsStatusRevision, _opsStatusCycle); undefined")
        assert len(history_calls) == before + 1
        page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()
        page.wait_for_function("!_opsHistoryInFlight['revenue-overview']")
        poll()
        assert len(history_calls) == before + 1

        hold = False
        page.locator('[data-ops-row="revenue-overview"] [data-label="Report"]').click()
        expect(history).to_contain_text("Previous outcome unconfirmed")
        hold = True
        page.evaluate("_opsApplyStatus(window._opsLatestStatus, _opsStatusRevision, _opsStatusCycle); undefined")
        page.wait_for_function("!!_opsHistoryInFlight['revenue-overview']")
        before = len(history_calls)
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("!_opsHistoryInFlight['revenue-overview']")
        poll()
        assert len(history_calls) == before
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, value:false}); document.dispatchEvent(new Event('visibilitychange'))")
        page.evaluate("_opsApplyStatus(window._opsLatestStatus, _opsStatusRevision, _opsStatusCycle); undefined")
        page.wait_for_function("!!_opsHistoryInFlight['revenue-overview']")
        before = len(history_calls)
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        page.wait_for_function("!_opsHistoryInFlight['revenue-overview']")
        poll()
        assert len(history_calls) == before

        # Resuming eventually settles to idle history, which needs no extra reads.
        hold = False
        run["status"] = "success"
        run["recovery"] = {"state": "succeeded", "attempt": 1}
        status["running"] = {}
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true})); clearTimeout(_opsStatusTimer)")
        poll()
        expect(history).to_contain_text("Recovered automatically")
        before = len(history_calls)
        poll()
        assert len(history_calls) == before
        browser.close()
