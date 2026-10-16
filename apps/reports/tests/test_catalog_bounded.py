"""PostgreSQL registry semantics, global scope and bounded presentation."""
from types import SimpleNamespace
import json

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core import roles, storage
from apps.orgs.models import PermissionGroupGrant
from apps.reports.catalog import registry_page_payload
from apps.reports.models import Report, ReportFavorite, ReportPermissionGrant

pytestmark = pytest.mark.django_db


def request(user, studio):
    return SimpleNamespace(user=user, studio=studio, org=studio.org)


@pytest.mark.parametrize("size", [20, 200, 1000])
def test_growth_global_search_and_paging_are_database_only(studio, org_admin, monkeypatch, size):
    rows = Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"Report {i:04}", category=f"Category {i%40:02}") for i in range(size)])

    def forbidden(*args, **kwargs):
        raise AssertionError("The registry must not read artifacts")

    monkeypatch.setattr(storage, "read_meta", forbidden)
    monkeypatch.setattr(storage, "html_index", forbidden)
    req = request(org_admin, studio)
    with CaptureQueriesContext(connection) as queries:
        result = registry_page_payload(req, {})
    assert len(result["reports"]) == min(50, size)
    assert result["pagination"]["total"] == size
    assert len(queries) <= 14
    assert registry_page_payload(req, {"q": rows[-1].name})["reports"][0]["id"] == rows[-1].pk
    seen = []
    for page in range(1, (size + 49) // 50 + 1):
        seen += [r["id"] for r in registry_page_payload(req, {"page": str(page)})["reports"]]
    assert len(seen) == len(set(seen)) == size


def test_search_exact_cross_field_and_joined_tag_substrings(studio, org_admin):
    row = Report.objects.create(studio=studio, slug="test", name="Alpha", description="Beta", tags=["Gamma", "Delta"], category="Epsilon")
    req = request(org_admin, studio)
    for q in ("alpha beta", "beta gamma", "gamma delta", "delta epsilon", f"epsilon {studio.slug}"):
        assert [r["id"] for r in registry_page_payload(req, {"q": q})["reports"]] == [row.pk]
    assert registry_page_payload(req, {"q": 'gamma", "delta'})["reports"] == []


def test_search_overrides_the_tab(studio, org_admin):
    for slug, name in [("one", "A"), ("two", "B")]:
        Report.objects.create(studio=studio, slug=slug, name=name)
    req = request(org_admin, studio)
    assert [r["name"] for r in registry_page_payload(req, {"q": "b", "tab": "_recent", "recent": ["one"]})["reports"]] == ["B"]


def test_favorites_and_recent_tabs_narrow_the_registry(studio, org_admin):
    rows = Report.objects.bulk_create([Report(studio=studio, slug=f"r{i:02}", name=f"Report {i:02}") for i in range(60)])
    ReportFavorite.objects.bulk_create([ReportFavorite(user=org_admin, report=row) for row in rows])
    req = request(org_admin, studio)
    recent = [rows[i].slug for i in (59, 20, 10, 9, 8, 7, 6, 5)]
    # Registry order; the client orders recent reports by when each was opened.
    assert [r["slug"] for r in registry_page_payload(req, {"tab": "_recent", "recent": recent})["reports"]] == sorted(recent)
    seen = []
    for page in (1, 2):
        result = registry_page_payload(req, {"tab": "_favorites", "page": str(page)})
        seen += [r["id"] for r in result["reports"]]
        assert result["pagination"]["total"] == 60
    assert len(seen) == len(set(seen)) == 60
    Report.objects.create(studio=studio, slug="article", kind="analysis")
    assert registry_page_payload(req, {}, kind="report")["pagination"]["total"] == 60
    assert registry_page_payload(req, {}, kind="analysis")["pagination"]["total"] == 1
    assert registry_page_payload(req, {})["pagination"]["total"] == 61


def test_the_generated_metrics_report_is_listed_and_flagged(studio, org_admin):
    Report.objects.create(studio=studio, slug="metrics", tags=["metrics"])
    Report.objects.create(studio=studio, slug="metrics-guide", tags=["metrics"])
    rows = {r["slug"]: r for r in registry_page_payload(request(org_admin, studio), {})["reports"]}
    assert rows["metrics"]["metrics_report"] is True and rows["metrics-guide"]["metrics_report"] is False


def test_selected_private_and_revoked_scopes_precede_registry_data(studio, member, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    chosen = Report.objects.create(studio=studio, slug="chosen", audience="private")
    Report.objects.create(studio=studio, slug="hidden", description="hiddenneedle")
    group = make_group("selected", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    ReportPermissionGrant.objects.create(grant=grant, report=chosen)
    membership = attach_group(member, group)
    req = request(member, studio)
    assert [r["id"] for r in registry_page_payload(req, {})["reports"]] == [chosen.pk]
    assert registry_page_payload(req, {"q": "hiddenneedle"})["pagination"]["total"] == 0
    membership.delete()
    assert registry_page_payload(req, {})["pagination"]["total"] == 0


@pytest.mark.parametrize("params", [{"q": "\x00"}, {"recent": ["bad\x00"]}, {"q": "x"*1001}, {"tab": "x"*201}, {"tab": "Revenue"},
                                    {"page": "0"}, {"page": "1000001"}, {"limit": "4"}, {"limit": "51"}, {"recent": ["x"]*9}])
def test_invalid_state_is_rejected_before_database_work(studio, org_admin, params):
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError):
        registry_page_payload(request(org_admin, studio), params)
    assert len(queries) == 0


def test_stable_order_is_priority_then_slug(studio, org_admin):
    for i, (slug, priority) in enumerate((("b", 5), ("a", 5), ("z", 1), ("c", 99))):
        Report.objects.create(studio=studio, slug=slug, name=f"N{i}", priority=priority)
    assert [r["slug"] for r in registry_page_payload(request(org_admin, studio), {})["reports"]] == ["z", "a", "b", "c"]


def test_escaped_unicode_byte_ceiling_is_explicit_without_false_empty(studio, org_admin):
    Report.objects.bulk_create([Report(studio=studio, slug=f"unicode-{i}", name="名"*300, category="類"*200, description="😀"*3000, tags=["😀"*1000]*8) for i in range(25)])
    payload = registry_page_payload(request(org_admin, studio), {})
    assert payload["state"] == "oversized"
    assert payload["pagination"]["total"] == 25
    assert "reports" not in payload and len(json.dumps(payload).encode()) <= 256*1024
    # Asked again as smaller pages, the same rows arrive.
    small = registry_page_payload(request(org_admin, studio), {"limit": "5"})
    assert small["state"] == "oversized" or len(small["reports"]) == 5


def test_bearer_org_scope_is_checked_before_database_work(studio, org_admin):
    req = request(org_admin, studio)
    req.api_key = SimpleNamespace(org_id=studio.org_id+1)
    with CaptureQueriesContext(connection) as queries, pytest.raises(PermissionDenied):
        registry_page_payload(req, {})
    assert len(queries) == 0
