"""The catalog list view fits its content area at ordinary desktop widths, with
a paused-refresh badge in the Schedule cell, instead of scrolling sideways and
clipping the Last updated column."""
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.reports.models import Report
from apps.runner.models import Run
from apps.studios.models import Studio

from .test_registry_browser import open_page

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (1280, 1440)

# What the person sees of the list: how far its content runs past the area it
# is shown in, and how much of the last column is outside that area.
MEASURE = """() => {
    const view = document.querySelector('.list-view');
    const lastCell = view.querySelector('tbody tr[data-slug] td[data-label="Last updated"]').getBoundingClientRect();
    return {
        overflow: view.scrollWidth - view.clientWidth,
        clipped: Math.max(0, Math.round(lastCell.right - view.getBoundingClientRect().right)),
        page: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
    };
}"""


def seed(org, *, paused):
    """A realistic studio: long descriptions, several tags, a long studio name
    and, when asked, rows whose scheduled refreshes are paused."""
    studio = Studio.objects.create(org=org, slug="novaplayanalyticsemea", name="Nova Play Analytics EMEA")
    for number in range(8):
        report = Report.objects.create(
            studio=studio, slug=f"report-{number}", name=f"Player overview {number}", category="Overview",
            schedule_cron="0 6 * * *", tags=["demo", "engagement", "revenue", "retention"],
            description="Player KPIs, revenue by tier, geography, overlapping player behaviours and a long "
                        "explanatory ending that runs on well past what a single line can hold.",
            refresh_suspended_at=timezone.now() if paused and number % 2 == 0 else None,
        )
        finished = timezone.now() - timedelta(hours=2)
        Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.SUCCESS,
                           started_at=finished, finished_at=finished)
    return studio


@pytest.mark.parametrize("paused", [True, False], ids=["with-paused-badge", "without"])
def test_list_view_fits_without_sideways_scrolling(live_server, login, org_admin, org, paused):
    studio = seed(org, paused=paused)
    with open_page(live_server, login(org_admin)) as page:
        page.goto(f"{live_server.url}/s/{org.slug}/{studio.slug}/")
        expect(page.locator('tr[data-slug="report-0"]')).to_be_visible()
        schedule = page.locator('tr[data-slug="report-0"] td[data-label="Schedule"]')
        if paused:
            expect(schedule).to_contain_text("Auto-refresh paused")
        measured = {}
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            measured[width] = page.evaluate(MEASURE)
        # No scrollbar on the list, the last column fully inside it, nothing sticking out of the page.
        assert measured == {width: {"overflow": 0, "clipped": 0, "page": 0} for width in WIDTHS}
