"""Real HTTP + browser contract for the Operations page: it reads from the
bounded endpoints and looks and behaves as it always has."""
from pathlib import Path
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from apps.reports.models import Report
from apps.runner.models import Run

pytestmark = pytest.mark.django_db(transaction=True)


def _seed(studio, data_dir):
    reports = Report.objects.bulk_create([
        Report(studio=studio, name=f"Report {i:03}", slug=f"browser{i:03}", present_in_scan=True,
               category="Alpha" if i % 2 else "Beta") for i in range(60)])
    live_id = uuid4()
    directory = Path(data_dir) / "tmp" / "runs" / str(live_id)
    directory.mkdir(parents=True)
    (directory / "stdout.log").write_text("live line one\nlive line two 界\n")
    (directory / "stderr.log").write_text("")
    Run.objects.create(id=live_id, report=reports[0], studio=studio, slug=reports[0].slug, status=Run.RUNNING, log_dir=str(directory))
    done = Run.objects.create(report=reports[1], studio=studio, slug=reports[1].slug, status=Run.SUCCESS,
                              stdout_tail="persisted out\n", stderr_tail="")
    Run.objects.create(report=reports[2], studio=studio, slug=reports[2].slug, status=Run.ERROR,
                       stdout_tail="", stderr_tail="Traceback boom")
    return done


@pytest.mark.parametrize("engine,width", [("chromium", 1440), ("chromium", 390), ("webkit", 1440), ("webkit", 390)])
def test_operations_page(engine, width, live_server, login, org_admin, org, studio, settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    _seed(studio, settings.DATA_DIR)
    client = login(org_admin)
    host = live_server.url
    path = f"/s/{org.slug}/{studio.slug}/operations"
    artifacts = os.environ.get("TRELLUM_TEST_ARTIFACT_DIR")
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))

        def static(route):
            asset = settings.BASE_DIR / "static" / urlparse(route.request.url).path.split("/static/", 1)[1]
            route.fulfill(path=str(asset)) if asset.is_file() else route.continue_()
        page.route("**/static/**", static)
        page.goto(host + path)
        # Every report is listed at once: no pager, no extra page of rows.
        expect(page.locator("tr[data-ops-row]")).to_have_count(60)
        expect(page.locator(".ops-pager, .ops-native-pager")).to_have_count(0)
        expect(page.get_by_role("button", name="Next", exact=True)).to_have_count(0)
        assert not any("/api/registry" in url or "/api/system/status" in url for url in requests)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # The summary row, labels and filter chips are the ones the page always had.
        labels = page.locator(".ops-summary > .ops-stat .ops-stat-label").all_inner_texts()
        assert [label.lower() for label in labels] == [
            "reports", "running", "queued", "waiting for data source", "succeeded", "failed", "uptime"]
        chips = page.locator(".ops-filter-pill").evaluate_all("els => els.map(el => el.dataset.opsFilter)")
        assert chips == ["all", "running", "queued", "waiting", "error", "oom", "timeout", "stopped", "ok",
                         "not_run", "v_fail", "v_warn", "healthy"]
        expect(page.locator(".ops-filter-pill[data-ops-filter=all] .ops-filter-count")).to_have_text("60")
        expect(page.locator(".ops-filter-pill[data-ops-filter=not_run] .ops-filter-count")).to_have_text("57")
        # State chips apply at once, without a new read.
        before = len(requests)
        page.locator(".ops-filter-pill[data-ops-filter=ok]").click()
        expect(page.locator("tr[data-ops-row]:visible")).to_have_count(1)
        page.locator(".ops-filter-pill[data-ops-filter=all]").click()
        expect(page.locator("tr[data-ops-row]:visible")).to_have_count(60)
        assert len(requests) == before
        # Persisted log, with nothing added to the terminal.
        page.locator("tr[data-ops-row=browser001]").click()
        expect(page.locator("#opsTermBody_browser001")).to_contain_text("persisted out")
        expect(page.locator("#opsTerm_browser001 a")).to_have_count(0)
        # Live log from the bounded file reader.
        page.locator("tr[data-ops-row=browser000]").click()
        expect(page.locator("#opsTermBody_browser000")).to_contain_text("live line two 界")
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(artifacts) / f"ops-{engine}-{width}.png"), full_page=True)
        # Search applies after a typing pause, with no button.
        expect(page.locator(".folder-rail #searchInput")).to_be_visible()
        page.locator("#searchInput").fill("browser059")
        expect(page.locator("tr[data-ops-row]:visible")).to_have_count(1)
        assert errors == []
        context.close()
        browser.close()
