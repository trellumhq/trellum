"""PostgreSQL catalog semantics, global scope and bounded presentation."""
from datetime import timedelta
from types import SimpleNamespace
import json

import pytest
from django.db import connection
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles, storage
from apps.orgs.models import PermissionGroupGrant
from apps.reports.catalog import catalog_categories_payload, catalog_payload, catalog_personal_payload
from apps.reports.catalog_details import catalog_details_payload
from apps.reports.models import Report, ReportFavorite, ReportPermissionGrant, ReportSourceSummary

pytestmark = pytest.mark.django_db


def request(user, studio):
    return SimpleNamespace(user=user, studio=studio, org=studio.org)


@pytest.mark.parametrize("size", [20, 200, 1000])
def test_growth_global_search_and_paging_are_database_only(studio, org_admin, monkeypatch, size):
    rows = Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"Report {i:04}", category=f"Category {i%40:02}", config={"secret": "large"*1000}) for i in range(size)])
    def forbidden(*args, **kwargs):
        raise AssertionError("Catalog must not read artifacts")
    monkeypatch.setattr(storage, "read_meta", forbidden)
    monkeypatch.setattr(storage, "html_index", forbidden)
    req = request(org_admin, studio)
    with CaptureQueriesContext(connection) as queries:
        result = catalog_payload(req, {})
    assert len(result["rows"]) == min(25, size)
    assert result["pagination"]["total"] == size
    assert len(result["categories"]["categories"]) <= 25
    assert len(queries) <= 12
    assert all('"config"' not in q["sql"] for q in queries)
    for helper, params in ((catalog_categories_payload, {"tab": "missing category"}),
                           (catalog_personal_payload, {})):
        with CaptureQueriesContext(connection) as extra_queries:
            helper(req, params)
        assert all('"config"' not in q["sql"] for q in extra_queries)
    assert catalog_payload(req, {"q": rows[-1].name})["rows"][0]["id"] == rows[-1].pk
    seen = []
    for page in range(1, (size+24)//25+1):
        seen += [r["id"] for r in catalog_payload(req, {"page": str(page)})["rows"]]
    assert len(seen) == len(set(seen)) == size


def test_group_first_occurrence_and_search_overrides_folder(studio, org_admin):
    for slug, name, category in [("one", "A", "X"), ("two", "B", "Y"), ("three", "C", "X")]:
        Report.objects.create(studio=studio, slug=slug, name=name, category=category)
    req = request(org_admin, studio)
    result = catalog_payload(req, {"csort": "name"})
    assert [r["name"] for r in result["rows"]] == ["A", "C", "B"]
    assert [r["name"] for r in catalog_payload(req, {"csort": "name", "q": "b", "tab": "X"})["rows"]] == ["B"]


def test_search_exact_cross_field_and_joined_tag_substrings(studio, org_admin):
    row = Report.objects.create(studio=studio, slug="test", name="Alpha", description="Beta", tags=["Gamma", "Delta"], category="Epsilon")
    req = request(org_admin, studio)
    for q in ("alpha beta", "beta gamma", "gamma delta", "delta epsilon", f"epsilon {studio.slug}"):
        assert [r["id"] for r in catalog_payload(req, {"q": q})["rows"]] == [row.pk]
    assert catalog_payload(req, {"q": 'gamma", "delta'})["rows"] == []


def test_complete_favorite_pages_recent_order_and_kind(studio, org_admin):
    rows = Report.objects.bulk_create([Report(studio=studio, slug=f"r{i:02}", name=f"Report {i:02}") for i in range(60)])
    ReportFavorite.objects.bulk_create([ReportFavorite(user=org_admin, report=row) for row in rows])
    req = request(org_admin, studio)
    recent = [rows[i].slug for i in (59, 20, 10, 9, 8, 7, 6, 5)]
    seen = []
    for page in (1, 2, 3):
        result = catalog_personal_payload(req, {"favorite_page": str(page), "recent": recent})
        seen += [r["id"] for r in result["favorites"]]
        assert [r["slug"] for r in result["recent"]] == recent
        assert result["pagination"]["total"] == 60
    assert len(seen) == len(set(seen)) == 60
    Report.objects.create(studio=studio, slug="article", kind="analysis")
    Report.objects.create(studio=studio, slug="metrics", tags=["metrics"])
    assert catalog_payload(req, {})["pagination"]["total"] == 60
    assert catalog_payload(req, {}, kind="analysis")["pagination"]["total"] == 1


def test_unknown_output_does_not_hide_authoritative_failed_attempt(studio, org_admin):
    now = timezone.now()
    failed = Report.objects.create(studio=studio, slug="failed")
    stale = Report.objects.create(studio=studio, slug="stale")
    known = Report.objects.create(studio=studio, slug="known")
    ReportSourceSummary.objects.create(report=failed, state="uncertain", runtime_state="unavailable", observed_at=now, attempt_at=now, attempt_status="error")
    ReportSourceSummary.objects.create(report=stale, state="missing", runtime_state="missing", quality="observed", observed_at=now-timedelta(seconds=301), has_output=False)
    ReportSourceSummary.objects.create(report=known, state="ready", runtime_state="ready", quality="observed", observed_at=now, attempt_status="success", has_output=True, html_entry="report.html")
    req = request(org_admin, studio)
    rows = catalog_payload(req, {"csort": "status"}, now=now)["rows"]
    assert [r["slug"] for r in rows] == ["known", "failed", "stale"]
    assert rows[1]["last_status"] == "error" and rows[1]["has_output"] is None
    assert rows[2]["output_state"] == "stale" and rows[2]["has_output"] is None
    assert rows[0]["html_entry"] == "report.html"


def test_selected_private_and_revoked_scopes_precede_catalog_data(studio, member, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    chosen = Report.objects.create(studio=studio, slug="chosen", audience="private")
    hidden = Report.objects.create(studio=studio, slug="hidden", description="hiddenneedle")
    group = make_group("selected", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    ReportPermissionGrant.objects.create(grant=grant, report=chosen)
    membership = attach_group(member, group)
    req = request(member, studio)
    assert [r["id"] for r in catalog_payload(req, {})["rows"]] == [chosen.pk]
    assert catalog_payload(req, {"q": "hiddenneedle"})["pagination"]["total"] == 0
    with pytest.raises(Http404):
        catalog_details_payload(req, hidden.pk, {"metadata": "0"})
    membership.delete()
    assert catalog_payload(req, {})["pagination"]["total"] == 0


def test_categories_are_browsable_and_selected_facet_is_pinned(studio, org_admin):
    Report.objects.bulk_create([Report(studio=studio, slug=f"r{i}", category=f"Category {i:03}") for i in range(60)])
    req = request(org_admin, studio)
    first = catalog_categories_payload(req, {"tab": "Category 059"})
    assert len(first["categories"]) == 25 and first["selected"]["count"] == 1
    assert catalog_categories_payload(req, {"category_q": "059"})["categories"] == [{"display_category": "Category 059", "count": 1}]


def test_full_description_and_long_tag_native_chunks(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="long", description="d"*1300, tags=["t"*1300]+[f"tag{i}" for i in range(40)])
    req = request(org_admin, studio)
    result = catalog_details_payload(req, report.pk, {"metadata": "0", "description_page": "3", "tag_page": "2", "tag_index": "0", "text_page": "3"})
    assert result["declaration"]["description_text"] == "d"*100
    assert result["declaration"]["tag_text"]["text"] == "t"*100
    assert len(result["declaration"]["tag_rows"]) == 16
    assert result["declaration"]["tag_pagination"]["total"] == 41


@pytest.mark.parametrize("params", [{"q": "\x00"}, {"category_q": "name\x00"}, {"recent": ["bad\x00"]}, {"q": "x"*1001}, {"tab": "x"*201}, {"page": "0"}, {"recent": ["x"]*9}, {"sort": "name;DROP"}, {"view": "ops"}])
def test_invalid_state_is_rejected_before_database_work(studio, org_admin, params):
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError):
        catalog_payload(request(org_admin, studio), params)
    assert len(queries) == 0


def test_database_text_order_and_stable_ties(studio, org_admin):
    for i, name in enumerate(("á", "A", "a", "-", "Z", "ä", "z")):
        Report.objects.create(studio=studio, slug=f"r{i}", name=name)
    from django.db.models.functions import Lower
    expected = list(Report.objects.filter(studio=studio).order_by(Lower("name"), "priority", "slug", "pk").values_list("pk", flat=True))
    assert [r["id"] for r in catalog_payload(request(org_admin, studio), {"q": "", "tab": "Uncategorized", "csort": "name"})["rows"]] == expected


def test_escaped_unicode_byte_ceiling_is_explicit_without_false_empty(studio, org_admin):
    Report.objects.bulk_create([Report(studio=studio, slug=f"unicode-{i}", name="名"*300, category="類"*200, description="😀"*1000, tags=["😀"*1000]*8) for i in range(25)])
    payload = catalog_payload(request(org_admin, studio), {})
    assert payload["state"] == "oversized"
    assert payload["pagination"]["total"] == 25
    assert "rows" not in payload and len(json.dumps(payload).encode()) <= 128*1024
    row = catalog_payload(request(org_admin, studio), {"q": "unicode-0"})
    # Fallback name is not the slug when an explicit name exists; a narrowed
    # category request still returns an explicit oversized result if needed.
    assert row["pagination"]["total"] == 0


def test_bounded_tag_preview_keeps_complete_search_and_detail(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="long-tag", tags=["a"*80+"offpage-tag-needle"])
    req = request(org_admin, studio)
    row = catalog_payload(req, {"q": "offpage-tag-needle"})["rows"][0]
    assert row["tags"] == ["a"*80] and row["tags_more"]
    detail = catalog_details_payload(req, report.pk, {"metadata": "0", "tag_index": "0"})
    assert detail["declaration"]["tag_text"]["text"].endswith("offpage-tag-needle")


def test_bearer_org_scope_is_checked_before_database_work(studio, org_admin):
    req = request(org_admin, studio)
    req.api_key = SimpleNamespace(org_id=studio.org_id+1)
    with CaptureQueriesContext(connection) as queries, pytest.raises(PermissionDenied):
        catalog_payload(req, {})
    assert len(queries) == 0
