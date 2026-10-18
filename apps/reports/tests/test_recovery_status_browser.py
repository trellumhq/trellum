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
            page.evaluate("_opsLoadHistory('revenue-overview')")

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
