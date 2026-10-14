"""Real HTTP + browser contracts for the routed Operations surface."""
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
def test_operations_enhanced_and_native(engine, width, live_server, login, org_admin, org, studio, settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    done = _seed(studio, settings.DATA_DIR)
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
        # Enhanced content replaces the native block only after a ready fetch.
        expect(page.locator("[data-ops-native]")).to_have_count(0)
        expect(page.locator("tr[data-ops-row]")).to_have_count(25)
        assert not any("/api/registry" in url or "/api/system/status" in url for url in requests)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        expect(page.locator(".ops-filter-pill[data-ops-filter=all] .ops-filter-count")).to_have_text("60")
        # Server filter + paging.
        page.locator(".ops-filter-pill[data-ops-filter=ok]").click()
        expect(page.locator("tr[data-ops-row]")).to_have_count(1)
        page.locator(".ops-filter-pill[data-ops-filter=all]").click()
        expect(page.locator("tr[data-ops-row]")).to_have_count(25)
        page.get_by_role("button", name="Next", exact=True).first.click()
        expect(page.locator(".ops-pager").first).to_contain_text("Page 2 of 3")
        expect(page.locator("tr[data-ops-row]").first).to_be_visible()
        page.get_by_role("button", name="Previous", exact=True).first.click()
        expect(page.locator(".ops-pager").first).to_contain_text("Page 1 of 3")
        # Persisted log + bounded download link.
        page.locator("tr[data-ops-row=browser001]").click()
        expect(page.locator("#opsTermBody_browser001")).to_contain_text("persisted out")
        expect(page.locator("#opsTermDl_browser001")).to_have_attribute("href", f"{path.rsplit('/operations', 1)[0]}/api/operations/reports/browser001/log/download?run={done.pk}")
        # Live log from the bounded file reader.
        page.locator("tr[data-ops-row=browser000]").click()
        expect(page.locator("#opsTermBody_browser000")).to_contain_text("live line two 界")
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(artifacts) / f"ops-{engine}-{width}.png"), full_page=True)
        # Server-side search.
        if width < 768:
            page.locator("[data-console-search-toggle]").click()
        page.locator("#searchInput").fill("browser059")
        expect(page.locator("tr[data-ops-row]")).to_have_count(1)
        assert errors == []
        context.close()

        native = browser.new_context(java_script_enabled=False, viewport={"width": width, "height": 900})
        native.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = native.new_page()
        page.route("**/static/**", static)
        page.goto(host + path)
        expect(page.locator("tr[data-ops-row]")).to_have_count(25)
        expect(page.get_by_text("Run and Stop controls require JavaScript")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.get_by_role("link", name="Next", exact=True).click()
        expect(page.get_by_text("Page 2 of 3")).to_be_visible()
        page.goto(host + path + "?state=ok")
        page.get_by_role("link", name="Last run log").click()
        expect(page.locator("pre").first).to_contain_text("persisted out")
        expect(page.get_by_role("link", name="Download persisted tails")).to_be_visible()
        if artifacts:
            page.screenshot(path=str(Path(artifacts) / f"ops-native-log-{engine}-{width}.png"), full_page=True)
        native.close()
        browser.close()
