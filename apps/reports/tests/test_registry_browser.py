"""The real dashboard and Operations page on the one registry row: every tag and
the paused badge on the cards, the same search in Operations, Download only for a
stored file, and no cap on open live terminals."""
from contextlib import contextmanager
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from django.conf import settings
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from apps.datasources.models import DataSource, RepoDataSource
from apps.reports.models import Report
from apps.runner.models import Run

pytestmark = pytest.mark.django_db(transaction=True)

TAGS = [f"tag-{i:02}" for i in range(12)]


@contextmanager
def open_page(live_server, client):
    """A signed-in Chromium page; seed the database before entering, as the
    Playwright loop forbids synchronous ORM calls while it runs."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}])
        tab = context.new_page()
        errors = []
        tab.on("pageerror", lambda error: errors.append(str(error)))

        def static(route):
            asset = settings.BASE_DIR / "static" / urlparse(route.request.url).path.split("/static/", 1)[1]
            route.fulfill(path=str(asset)) if asset.is_file() else route.continue_()

        tab.route("**/static/**", static)
        try:
            yield tab
            assert errors == []
        finally:
            context.close()
            browser.close()


def test_cards_show_every_tag_the_paused_badge_and_search_reaches_them(live_server, login, org_admin, org, studio):
    Report.objects.create(
        studio=studio, slug="paused", name="Paused report", schedule_cron="0 6 * * *",
        refresh_suspended_at=timezone.now(), tags=TAGS, description="start " + "x" * 900 + " description-tail-needle",
    )
    Report.objects.create(studio=studio, slug="plain", name="Plain report")
    with open_page(live_server, login(org_admin)) as page:
        page.goto(f"{live_server.url}/s/{org.slug}/{studio.slug}/")
        # The list: every tag, and the pause beside the schedule.
        row = page.locator('tr[data-slug="paused"]')
        expect(row).to_be_visible()
        expect(row.locator(".list-tags .tag")).to_have_count(len(TAGS))
        expect(row.locator('td[data-label="Schedule"]')).to_contain_text("Auto-refresh paused")
        expect(page.locator('tr[data-slug="plain"]')).not_to_contain_text("Auto-refresh paused")
        # The cards: the same.
        page.locator('[data-setview="cards"]').click()
        card = page.locator('a.card[data-slug="paused"]')
        expect(card).to_be_visible()
        expect(card.locator(".tag")).to_have_count(len(TAGS))
        expect(card.locator(".ui-badge", has_text="Auto-refresh paused")).to_have_count(1)
        expect(page.locator('a.card[data-slug="plain"] .ui-badge', has_text="Auto-refresh paused")).to_have_count(0)
        # The whole description and every tag are searched, instantly and locally.
        for term in (TAGS[-1], "description-tail-needle"):
            page.locator("#searchInput").fill(term)
            expect(page.locator("a.card")).to_have_count(1)
            expect(page.locator('a.card[data-slug="paused"]')).to_be_visible()
        page.locator("#searchInput").fill("")
        expect(page.locator("a.card")).to_have_count(2)


def test_operations_search_matches_tags_category_and_description(live_server, login, org_admin, org, studio):
    Report.objects.create(studio=studio, slug="alpha", name="Alpha", tags=["gamma"], category="Finance", description="beta words")
    Report.objects.create(studio=studio, slug="other", name="Other", category="Ops")
    with open_page(live_server, login(org_admin)) as page:
        page.goto(f"{live_server.url}/s/{org.slug}/{studio.slug}/operations")
        expect(page.locator("tr[data-ops-row]")).to_have_count(2)
        for term in ("gamma", "finance", "beta words", "alpha"):
            page.locator("#searchInput").fill(term)
            expect(page.locator("tr[data-ops-row]:visible")).to_have_count(1)
            expect(page.locator("tr[data-ops-row=alpha]")).to_be_visible()
        page.locator("#searchInput").fill("")
        expect(page.locator("tr[data-ops-row]:visible")).to_have_count(2)


def test_download_is_offered_only_for_a_stored_file(live_server, login, org_admin, org, studio):
    for name in ("stored", "empty"):
        RepoDataSource.objects.create(studio=studio, name=name, type="file", config={"upload": True}, source_file="data-sources/config.yaml")
        Report.objects.create(studio=studio, slug=f"uses-{name}", config={"data_sources": [name]})
    DataSource.objects.create(studio=studio, name="stored", type="file", config={"upload": True, "path": "files/stored.csv"})
    DataSource.objects.create(studio=studio, name="empty", type="file", config={"upload": True})
    with open_page(live_server, login(org_admin)) as page:
        page.goto(f"{live_server.url}/s/{org.slug}/{studio.slug}/operations")
        expect(page.locator("tr[data-ops-row]")).to_have_count(2)
        if not page.locator("#dsBody").is_visible():
            page.locator(".ds-header").click()
        expect(page.locator("#dsBody tbody tr")).to_have_count(2)

        def row(name):
            return page.locator("#dsBody tbody tr", has=page.locator(".ds-name", has_text=name))

        expect(row("stored").get_by_role("button", name="Download")).to_have_count(1)
        expect(row("empty").get_by_role("button", name="Download")).to_have_count(0)


def test_every_open_terminal_follows_its_build(live_server, login, org_admin, org, studio, settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    slugs = [f"live{i}" for i in range(6)]
    for slug in slugs:
        report = Report.objects.create(studio=studio, slug=slug, name=slug)
        identity = uuid4()
        directory = settings.DATA_DIR / "tmp" / "runs" / str(identity)
        directory.mkdir(parents=True)
        (directory / "stdout.log").write_text(f"{slug} output line\n")
        (directory / "stderr.log").write_text("")
        Run.objects.create(id=identity, report=report, studio=studio, slug=slug, status=Run.RUNNING, log_dir=str(directory))
    with open_page(live_server, login(org_admin)) as page:
        page.goto(f"{live_server.url}/s/{org.slug}/{studio.slug}/operations")
        expect(page.locator("tr[data-ops-row]")).to_have_count(6)
        for slug in slugs:
            page.locator(f"tr[data-ops-row={slug}] [data-label=Report]").click()
        for slug in slugs:
            expect(page.locator(f"#opsTermBody_{slug}")).to_contain_text(f"{slug} output line")
        assert "limited to" not in page.locator("#content").inner_text()
