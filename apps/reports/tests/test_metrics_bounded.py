"""Bounded definitions and complete, bounded DB projections under live permissions."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.core import roles
from apps.reports.metrics_catalog import metric_definition_page
from apps.reports.models import MetricCatalogState, MetricDefinition, Report, ReportSourceSummary, ReportMetricClaim, ReportMetricPreview

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog(studio, member, grant_studio, login, monkeypatch):
    grant_studio(member, studio, roles.VIEWER)
    MetricCatalogState.objects.create(studio=studio, definition_revision=7)
    rows = [MetricDefinition(studio=studio, name=f"metric_{i:04}", label=f"Metric {i}", definition_hash="current") for i in range(2000)]
    rows[-1].description = "last-description"; rows[-1].owner = "last-owner"; rows[-1].tags = ["last-tag"]
    MetricDefinition.objects.bulk_create(rows)
    def forbidden(*args, **kwargs):
        raise AssertionError("Metrics requests must not inspect artifacts")
    monkeypatch.setattr("apps.core.storage.read_meta", forbidden)
    monkeypatch.setattr("apps.core.storage.output_root", forbidden)
    return studio, login(member), list(MetricDefinition.objects.filter(studio=studio).order_by("name"))


def _url(studio):
    return f"/s/{studio.org.slug}/{studio.slug}"


def _summary(catalog, **query):
    studio, client, rows = catalog
    return client.get(_url(studio) + "/api/metrics/summary", {"ids": ",".join(str(row.pk) for row in rows[:50]), "definition_revision": 7, **query})


def _source(studio, slug="report", **values):
    report = Report.objects.create(studio=studio, slug=slug, name=slug, tags=["metrics"] if slug == "metrics" else [])
    return ReportSourceSummary.objects.create(report=report, state="ready", quality="verified", observed_at=timezone.now(), **values)


def test_primary_global_search_and_bound(catalog):
    studio, client, rows = catalog
    with CaptureQueriesContext(connection) as captured:
        page = metric_definition_page(studio)
        assert len(page["metrics"]) == 500 and page["defined_count"] == 2000 and page["truncated"]
    assert len(captured) == 5
    for query in ("metric_1999", "Metric 1999", "last-description", "last-owner", "last-tag"):
        result = metric_definition_page(studio, query)
        assert result["matching_count"] == 1 and result["metrics"][0]["row"].pk == rows[-1].pk
        assert not result["truncated"]
    response = client.get(_url(studio) + "/metrics")
    assert response.status_code == 200
    html = response.content.decode()
    # One grid with a browser-side filter: no pager, no search button.
    assert html.count('class="met-card"') == 500
    assert "Showing the first 500 of 2000 matching metrics" in html and "Previous" not in html and "Next" not in html
    assert '<iframe' not in html and 'data-metric-search' not in html and '>Search</button>' not in html
    assert html.count('placeholder="Search metrics…"') == 1  # the one in-page field, applied as you type
    assert 'data-server-filter' in html
    filtered = client.get(_url(studio) + "/metrics", {"q": "last-owner"}).content.decode()
    assert filtered.count('class="met-card"') == 1 and "metric_1999" in filtered
    fragment = client.get(_url(studio) + "/metrics", {"q": "last-owner"}, HTTP_X_TRELLUM_NAVIGATION="1")
    assert fragment.json()["html"].count('class="met-card"') == 1


def test_small_catalog_is_one_grid_filtered_in_the_browser(studio, member, grant_studio, login):
    grant_studio(member, studio, roles.VIEWER)
    MetricDefinition.objects.bulk_create([MetricDefinition(studio=studio, name=f"m_{i}", definition_hash="h") for i in range(3)])
    html = login(member).get(_url(studio) + "/metrics").content.decode()
    assert html.count('class="met-card"') == 3
    assert 'data-server-filter' not in html and "Showing the first" not in html
    assert "3 defined, 0 claimed by reports" in html


def test_complete_counts_bounded_previews_and_points(catalog):
    studio, client, rows = catalog
    sources = [_source(studio, f"report-{i:04}") for i in range(200)]
    ReportMetricClaim.objects.bulk_create([ReportMetricClaim(summary=source, metric_name=row.name, definition_hash="current", version=1) for source in sources for row in rows[:50]])
    gen = _source(studio, "metrics", preview_state="ready")
    ReportMetricClaim.objects.bulk_create([ReportMetricClaim(summary=gen, metric_name=row.name, definition_hash="current") for row in rows[:50]])
    ReportMetricPreview.objects.bulk_create([ReportMetricPreview(summary=gen, metric_name=row.name, points=" ".join(f"{i},1" for i in range(60))) for row in rows[:50]])
    with CaptureQueriesContext(connection) as captured:
        response = _summary(catalog)
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["state"] == "ready" and len(data["metrics"]) == 50
    # Every claiming report is listed inline (201 < INLINE_LIMIT); nothing is left to page to.
    assert all(item["claim_count"] == 201 and len(item["claims"]) == 201 and len(item["spark"].split()) == 60 for item in data["metrics"])
    assert len(response.content) <= 4 * 1024 * 1024
    details = client.get(data["metrics"][0]["details_url"])
    assert details.status_code == 200 and b"report-0199" in details.content
    print("summary SQL", len(captured), "bytes", len(response.content))


def test_claims_past_the_inline_limit_link_to_a_searchable_list_without_paging(catalog, monkeypatch):
    studio, client, rows = catalog
    monkeypatch.setattr("apps.reports.metric_summaries.INLINE_LIMIT", 5)
    sources = [_source(studio, f"report-{i:04}") for i in range(8)]
    ReportMetricClaim.objects.bulk_create([ReportMetricClaim(summary=source, metric_name=rows[0].name, definition_hash="current", version=1) for source in sources])
    item = _summary(catalog).json()["metrics"][0]
    # The card gets the first INLINE_LIMIT claims and the true total, which is what links it on.
    assert item["claim_count"] == 8 and [claim["report"] for claim in item["claims"]] == [f"report-{i:04}" for i in range(5)]
    page = client.get(item["details_url"])
    html = page.content.decode()
    assert page.status_code == 200 and "Showing the first 5 of 8 matching reports" in html
    assert html.count("?display=console") == 5 and "report-0005" not in html
    assert "Previous" not in html and "Next" not in html and "Page " not in html and "Search</button>" not in html
    assert "data-auto-filter" in html and 'name="q"' in html  # search as you type, no button
    found = client.get(item["details_url"] + "&q=report-0006").content.decode()
    assert "report-0006" in found and "report-0005" not in found and "Showing the first" not in found
    assert "No claiming report matches" in client.get(item["details_url"] + "&q=nothing-like-it").content.decode()
    assert client.get(item["details_url"] + "&q=" + "x" * 201).status_code == 400


@pytest.mark.parametrize("state,quality", [("pending", "unknown"), ("uncertain", "verified"), ("unavailable", "verified"), ("ready", "observed"), ("missing", "observed")])
def test_incomplete_sources_never_complete_zero(catalog, state, quality):
    studio, _, _ = catalog
    source = _source(studio)
    source.state = state; source.quality = quality; source.save()
    data = _summary(catalog).json()
    assert data["state"] == "partial"
    assert all(item["claim_count"] == 0 and not item["count_complete"] and item["chart_state"] == "unbuilt" for item in data["metrics"])


def test_missing_summary_is_partial(catalog):
    studio, _, _ = catalog
    Report.objects.create(studio=studio, slug="unindexed")
    assert _summary(catalog).json()["state"] == "partial"


def test_pending_keeps_historical_claims_as_partial(catalog):
    studio, _, rows = catalog
    source = _source(studio)
    source.state = "uncertain"; source.save()
    ReportMetricClaim.objects.create(summary=source, metric_name=rows[0].name)
    data = _summary(catalog).json()
    assert data["state"] == "partial" and data["metrics"][0]["claim_count"] == 1
    assert data["metrics"][0]["claims"][0]["source_state"] == "uncertain"


def test_revision_and_identity_races(catalog, monkeypatch):
    studio, _, rows = catalog
    assert _summary(catalog, definition_revision=6).status_code == 409
    rows[0].present_in_scan = False; rows[0].save()
    assert _summary(catalog).status_code == 400
    rows[0].present_in_scan = True; rows[0].save()
    import apps.reports.metric_summaries as module
    original = module._revisions; calls = 0
    def racing(studio):
        nonlocal calls
        calls += 1
        if calls == 2:
            MetricCatalogState.objects.filter(studio=studio).update(summary_revision=1)
        return original(studio)
    monkeypatch.setattr(module, "_revisions", racing)
    assert _summary(catalog).status_code == 409


@pytest.mark.parametrize("ids", ["", "1,1", "bad", ",".join(str(n) for n in range(51))])
def test_summary_rejects_unbounded_or_malformed_ids(catalog, ids):
    assert _summary(catalog, ids=ids).status_code == 400


def test_foreign_definition_denied(catalog, other_studio):
    foreign = MetricDefinition.objects.create(studio=other_studio, name="secret")
    response = _summary(catalog, ids=foreign.pk)
    assert response.status_code == 400 and b"secret" not in response.content


def test_finite_viewer_denied(studio, member, make_group, attach_group, login, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    Report.objects.create(studio=studio, slug="private-report", audience=Report.AUDIENCE_PRIVATE)
    group = make_group("Selected", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get(studio=studio); grant.viewer_scope = "selected"; grant.save()
    attach_group(member, group)
    client = login(member)
    for suffix in ("/metrics", "/api/metrics/summary?ids=1&definition_revision=0", "/metrics/1/claims?definition_revision=0"):
        assert client.get(_url(studio) + suffix).status_code == 403


def test_stopped_worker_observations_are_historical(catalog):
    """Stale usage stays marked as such, but a chart never waits for the worker."""
    from datetime import timedelta
    studio, _, rows = catalog
    source = _source(studio, "metrics", preview_state="ready", workspace_version=1)
    source.observed_at = timezone.now() - timedelta(seconds=301); source.save()
    ReportMetricClaim.objects.create(summary=source, metric_name=rows[0].name, definition_hash="current")
    ReportMetricPreview.objects.create(summary=source, metric_name=rows[0].name, points="0,1 1,2")
    data = _summary(catalog).json()
    assert data["state"] == "partial"
    assert data["metrics"][0]["claim_count"] == 1 and not data["metrics"][0]["count_complete"]
    assert not data["metrics"][0]["claims"][0]["fresh"]
    assert data["metrics"][0]["spark"] == "0,1 1,2"
    assert data["metrics"][0]["chart_state"] == "ready" and data["metrics"][0]["embed_url"]


@pytest.mark.parametrize("state,quality,version,expected", [
    ("ready", "verified", 1, "ready"), ("missing", "verified", None, "unbuilt"),
    ("ready", "verified", 0, "legacy"), ("pending", "unknown", None, "unknown"),
    ("uncertain", "unknown", 1, "unknown"), ("ready", "verified", None, "unknown")])
def test_chart_state_follows_the_indexed_report_not_the_clock(catalog, state, quality, version, expected):
    from datetime import timedelta
    studio, _, rows = catalog
    source = _source(studio, "metrics", workspace_version=version)
    ReportSourceSummary.objects.filter(pk=source.pk).update(state=state, quality=quality, observed_at=timezone.now() - timedelta(hours=2))
    ReportMetricClaim.objects.create(summary=source, metric_name=rows[0].name, definition_hash="current")
    assert _summary(catalog).json()["metrics"][0]["chart_state"] == expected


def test_idle_backoff_never_ages_a_source_into_unknown():
    """The idle re-check interval must stay inside every reader's freshness window."""
    from apps.reports import catalog, metric_summaries, operations
    from apps.reports.source_reconciliation import POLL_SECONDS
    from apps.reports.source_summary import MAX_IDLE_SECONDS
    window = min(metric_summaries.SOURCE_FRESH_SECONDS, catalog.FRESH_SECONDS, operations.FRESH_SECONDS)
    assert MAX_IDLE_SECONDS + POLL_SECONDS < window


@pytest.mark.parametrize("query", [{"ids": str(2**63)}, {"definition_revision": str(2**63)}, {"ids": "9"*1000}])
def test_database_overflow_inputs_are_bad_request(catalog, query):
    assert _summary(catalog, **query).status_code == 400


def test_freshness_window_boundaries(catalog, monkeypatch):
    from datetime import timedelta
    from apps.reports import metric_summaries
    studio, _, _ = catalog
    source = _source(studio)
    now = timezone.now()
    monkeypatch.setattr(metric_summaries.timezone, "now", lambda: now)
    source.observed_at = now - timedelta(seconds=299); source.save()
    assert _summary(catalog).json()["state"] == "ready"
    source.observed_at = now - timedelta(seconds=301); source.save()
    data = _summary(catalog).json()
    assert data["state"] == "partial" and data["coverage"]["stale_sources"] == 1
    assert data["coverage"]["latest_observed_at"] is not None
    source.observed_at = now; source.save()  # a newer worker observation restores coverage
    assert _summary(catalog).json()["state"] == "ready"


def test_primary_generation_race_is_explicit(catalog, monkeypatch):
    from apps.reports.models import MetricDefinition
    studio, _, _ = catalog
    original = MetricDefinition.spec_text
    raced = False
    def spec(self):
        nonlocal raced
        if not raced:
            raced = True
            MetricCatalogState.objects.filter(studio=studio).update(definition_revision=8)
        return original(self)
    monkeypatch.setattr(MetricDefinition, "spec_text", spec)
    assert metric_definition_page(studio)["definition_changed"] is True


def test_claim_revision_change_refreshes_quietly_without_a_reload_button(catalog):
    studio, client, rows = catalog
    path = _url(studio) + f"/metrics/{rows[0].pk}/claims"
    stale = client.get(path, {"definition_revision": 6, "q": "daily"})
    assert stale.status_code == 302
    assert stale["Location"] == path + "?definition_revision=7&q=daily"
    assert client.get(stale["Location"]).status_code == 200
    # A metric that left the catalog since the link was built falls back to the Metrics page.
    gone = _url(studio) + "/metrics/999999999/claims"
    moved = client.get(gone, {"definition_revision": 6})
    assert moved.status_code == 302 and moved["Location"] == _url(studio) + "/metrics"
    assert client.get(gone, {"definition_revision": 7}).status_code == 404
    assert client.get(path).status_code == 400
