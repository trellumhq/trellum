"""The console header's jump list: what a viewer may open, bounded, never paged."""
import pytest

from apps.core import roles
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def scoped_access(settings):
    # Private audiences are enforced once scoped access is ready, as in production.
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"


def _url(studio):
    return f"/s/{studio.org.slug}/{studio.slug}/api/jump"


def _make(studio, count, prefix="Report", **extra):
    Report.objects.bulk_create(
        [Report(studio=studio, slug=f"{prefix.lower()}-{i:03}", name=f"{prefix} {i:03}", **extra) for i in range(count)]
    )


def test_lists_reports_and_analyses_with_console_links(login, studio, org_admin):
    Report.objects.bulk_create([
        Report(studio=studio, slug="cart-funnel", name="Cart Funnel", category="Product"),
        Report(studio=studio, slug="where", name="Where we lose buyers", kind=Report.KIND_ANALYSIS, category="Uncategorized"),
        Report(studio=studio, slug="plain", name="", category="Overview"),
        Report(studio=studio, slug="gone", name="Gone", present_in_scan=False),
        Report(studio=studio, slug="metrics", name="Metrics", tags=["metrics"]),
    ])
    data = login(org_admin).get(_url(studio)).json()
    assert data["pagination"] == {"has_next": False}
    assert [(r["label"], r["hint"]) for r in data["results"]] == [
        ("Cart Funnel", "Report · Product"), ("plain", "Report · Overview"), ("Where we lose buyers", "Analysis"),
    ]
    base = f"/s/{studio.org.slug}/{studio.slug}/r"
    assert data["results"][0]["href"] == f"{base}/cart-funnel/index.html?display=console"
    assert data["results"][0]["value"] == "cart-funnel"


def test_uses_the_built_entry_page(login, studio, org_admin):
    from apps.reports.models import ReportSourceSummary

    report = Report.objects.create(studio=studio, slug="entry", name="Entry")
    ReportSourceSummary.objects.update_or_create(report=report, defaults={"html_entry": "my page.html"})
    href = login(org_admin).get(_url(studio)).json()["results"][0]["href"]
    assert href.endswith("/r/entry/my%20page.html?display=console")


def test_a_viewer_only_sees_reports_they_may_open(client, login, make_user, grant_studio, org, studio, studio2, org_admin):
    viewer = make_user("viewer@demo.example", org=org)
    grant_studio(viewer, studio, roles.VIEWER)
    Report.objects.bulk_create([
        Report(studio=studio, slug="open", name="Open Report"),
        Report(studio=studio, slug="secret", name="Secret Plan", audience=Report.AUDIENCE_PRIVATE),
        Report(studio=studio2, slug="elsewhere", name="Other Studio Report"),
    ])
    names = lambda who: [r["label"] for r in login(who).get(_url(studio)).json()["results"]]  # noqa: E731
    assert names(org_admin) == ["Open Report", "Secret Plan"]
    assert names(viewer) == ["Open Report"]
    # Another studio's list is not reachable without a role there.
    assert login(viewer).get(_url(studio2)).status_code in (403, 404)
    # And searching cannot surface what the list hides.
    assert login(viewer).get(_url(studio), {"q": "secret"}).json()["results"] == []


def test_anonymous_and_non_members_are_refused(client, studio, make_user, other_org, login):
    assert client.get(_url(studio)).status_code in (302, 401, 403, 404)
    stranger = make_user("stranger@rival.example", org=other_org)
    assert login(stranger).get(_url(studio)).status_code in (403, 404)


def test_everything_up_to_the_inline_limit_arrives_at_once(login, studio, org_admin):
    _make(studio, 500)
    data = login(org_admin).get(_url(studio)).json()
    assert len(data["results"]) == 500 and data["pagination"]["has_next"] is False


def test_beyond_the_limit_a_preview_then_search_as_you_type(login, studio, org_admin):
    _make(studio, 501)
    client = login(org_admin)
    first = client.get(_url(studio)).json()
    assert len(first["results"]) == 25 and first["pagination"]["has_next"] is True
    hit = client.get(_url(studio), {"q": "report 417"}).json()
    assert [r["label"] for r in hit["results"]] == ["Report 417"] and hit["pagination"]["has_next"] is False
    many = client.get(_url(studio), {"q": "report 1"}).json()
    assert len(many["results"]) == 25 and many["pagination"]["has_next"] is True  # narrower query, no page 2
    assert "page" not in many["pagination"]


def test_search_matches_name_slug_and_category_and_rejects_bad_input(login, studio, org_admin):
    Report.objects.bulk_create([
        Report(studio=studio, slug="alpha-slug", name="First", category="Revenue"),
        Report(studio=studio, slug="beta", name="Second", category="Other"),
    ])
    client = login(org_admin)
    for q, expected in (("first", ["First"]), ("ALPHA-SLUG", ["First"]), ("reven", ["First"]), ("zzz", [])):
        assert [r["label"] for r in client.get(_url(studio), {"q": q}).json()["results"]] == expected, q
    assert client.get(_url(studio), {"q": "x" * 201}).status_code == 400
    assert client.get(_url(studio), {"q": "a\x00b"}).status_code == 400


def test_the_response_is_private_and_not_cached(login, studio, org_admin):
    response = login(org_admin).get(_url(studio))
    assert response["Cache-Control"] == "private, no-store"
    assert login(org_admin).post(_url(studio)).status_code == 405
