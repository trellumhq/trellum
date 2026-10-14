"""Real PostgreSQL tests for the bounded query layer (no HTTP/UI claims).

Run only after the coordinator grants the serial runtime lease. No SQLite
substitute: generate_series, grouped scalar defaults and coverage need PG.
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.reports.annotation_projection import SCHEMA_VERSION
from apps.reports.annotation_queries import (
    EventFilter, EventPosition, FacetPosition, catalog, coverage, day_counts,
    event_page, events, facet_page, month_data,
)
from apps.reports.models import AnnotationCatalogState, AnnotationEvent, AnnotationSourceState, Report

pytestmark = pytest.mark.django_db


@pytest.fixture
def projected(studio):
    now = timezone.now()
    state = AnnotationCatalogState.objects.create(studio=studio, revision=3, published_revision=3, type_defaults={"campaign": False})
    global_source = AnnotationSourceState.objects.create(
        studio=studio, kind="global", owner_workspace_id="a" * 32,
        schema_version=SCHEMA_VERSION, attempted_schema_version=SCHEMA_VERSION,
        desired_generation=2, published_generation=2, status="ready",
        indexed_at=now, observed_at=now,
    )

    def source(slug, **kwargs):
        report = Report.objects.create(studio=studio, slug=slug, disabled=True, config={"private": "not read"})
        values = dict(studio=studio, report=report, kind="report", schema_version=SCHEMA_VERSION,
                      attempted_schema_version=SCHEMA_VERSION, desired_generation=2,
                      published_generation=2, status="ready", indexed_at=now, observed_at=now)
        values.update(kwargs)
        return AnnotationSourceState.objects.create(**values)

    def event(*, source=global_source, ordinal=None, **kwargs):
        if ordinal is None:
            ordinal = AnnotationEvent.objects.filter(source=source).count()
        values = dict(studio=studio, source=source, generation=source.published_generation,
                      ordinal=ordinal, start_date=date(2023, 3, 3), label="old event",
                      event_type="release", scope="shared", visible_default=True)
        values.update(kwargs)
        return AnnotationEvent.objects.create(**values)

    return dict(studio=studio, now=now, catalog=state, global_source=global_source, source=source, event=event)


def require_postgresql():
    assert connection.vendor == "postgresql", "C13 query tests require actual PostgreSQL"


def test_unknown_is_not_verified_empty(studio):
    require_postgresql()
    assert catalog(studio) is None
    result = coverage(studio)
    assert result["expected"] == 1
    assert result["unknown"] == 1
    assert not result["complete"]
    assert event_page(events(studio)).rows == []


def test_fresh_missing_global_and_present_empty_report_are_known_empty(projected):
    p = projected
    p["global_source"].status = "missing"
    p["global_source"].save(update_fields=["status"])
    p["source"]("empty-unbuilt")
    result = coverage(p["studio"], now=p["now"])
    assert result["complete"] and result["current"] == result["expected"] == 2
    assert event_page(events(p["studio"], now=p["now"])).total == 0
    assert set(catalog(p["studio"])) == {"revision", "published_revision"}


@pytest.mark.parametrize("status", ["pending", "parse-error", "oversized", "unavailable"])
def test_compatible_last_success_remains_labeled_when_attempt_fails(projected, status):
    p = projected
    src = p["global_source"]
    src.status = status
    src.desired_generation += 1
    src.attempted_schema_version = SCHEMA_VERSION + 1
    src.error_code = "parse-error"
    src.save()
    ev = p["event"]()
    result = coverage(p["studio"], now=p["now"])
    assert result["retained"] == 1 and result["unknown"] == 0
    assert not result["complete"]
    row = event_page(events(p["studio"], now=p["now"])).rows[0]
    assert row["id"] == ev.pk and not row["source_current"]
    assert row["source__indexed_at"] == src.indexed_at


@pytest.mark.parametrize("schema", [0, SCHEMA_VERSION + 1])
def test_incompatible_success_is_unknown_not_retained(projected, schema):
    p = projected
    p["event"]()
    src = p["global_source"]
    src.schema_version = schema
    src.save(update_fields=["schema_version"])
    result = coverage(p["studio"], now=p["now"])
    assert result["unknown"] == 1 and result["retained"] == 0
    assert event_page(events(p["studio"])).total == 0
    local = p["source"]("local")
    p["event"](source=local, event_type="campaign")
    # Incompatible retained defaults cannot hide compatible report events.
    assert event_page(events(p["studio"], EventFilter(defaults=True))).total == 1


def test_global_freshness_expires_without_revision_and_reports_do_not_age(projected):
    p = projected
    local = p["source"]("old-local", observed_at=p["now"] - timedelta(days=300))
    p["event"](source=local)
    p["event"]()
    assert coverage(p["studio"], now=p["now"])["complete"]
    later = p["now"] + timedelta(seconds=301)
    result = coverage(p["studio"], now=later)
    assert result["current"] == 1 and result["retained"] == result["stale"] == 1
    rows = event_page(events(p["studio"], now=later)).rows
    assert {row["source__kind"]: row["source_current"] for row in rows} == {"global": False, "report": True}
    assert catalog(p["studio"])["revision"] == 3


def test_absent_removed_and_deleted_report_sources_do_not_fake_coverage(projected):
    p = projected
    Report.objects.create(studio=p["studio"], slug="unindexed")
    removed = p["source"]("removed", status="removed")
    p["event"](source=removed)
    deleted = p["source"]("gone")
    p["event"](source=deleted)
    Report.objects.filter(pk=deleted.report_id).update(present_in_scan=False)
    result = coverage(p["studio"], now=p["now"])
    assert result["expected"] == 3 and result["unknown"] == 2
    assert result["retained"] == 0 and not result["complete"]
    assert event_page(events(p["studio"])).total == 0


def test_ownerless_or_unpublished_global_is_not_current(projected):
    p = projected
    src = p["global_source"]
    src.owner_workspace_id = ""
    src.save(update_fields=["owner_workspace_id"])
    assert coverage(p["studio"], now=p["now"])["retained"] == 1
    src.published_generation = 0
    src.save(update_fields=["published_generation"])
    assert coverage(p["studio"], now=p["now"])["unknown"] == 1


def test_generation_tenant_history_duplicates_and_empty_scope(projected, other_studio):
    p = projected
    a = p["event"](label="duplicate")
    b = p["event"](label="duplicate")
    p["event"](label="unpublished", generation=1)
    local = p["source"]("never-built")
    empty = p["event"](source=local, scope="", event_type="event", visible_default=False)
    alien = AnnotationSourceState.objects.create(studio=other_studio, kind="global", status="ready",
        schema_version=SCHEMA_VERSION, indexed_at=p["now"], published_generation=1)
    AnnotationEvent.objects.create(studio=other_studio, source=alien, generation=1, ordinal=0,
        start_date=date(2023, 3, 3), label="alien", event_type="release", scope="shared")
    page = event_page(events(p["studio"]))
    assert {row["id"] for row in page.rows} == {a.pk, b.pk, empty.pk}
    assert page.total == 3
    assert event_page(events(p["studio"], EventFilter(scope=""))).rows[0]["id"] == empty.pk
    assert event_page(events(p["studio"], EventFilter(scope="shared"))).total == 2
    # visible_default is presentation data, not an additional event exclusion.
    assert not event_page(events(p["studio"], EventFilter(scope=""))).rows[0]["visible_default"]


def test_paged_full_history_search_finds_report_sources_types_scopes_and_labels(projected):
    p = projected
    src = p["source"]("needle-report")
    p["event"](source=src, label="matching title", event_type="odd_type", scope="odd_scope")
    p["event"](label="global")
    for search in ("matching", "odd_type", "odd_scope", "needle-report", "report: needle-report"):
        assert event_page(events(p["studio"], EventFilter(search=search))).total == 1
    assert event_page(events(p["studio"], EventFilter(search="events.yaml"))).total == 1
    assert event_page(events(p["studio"], EventFilter(search="no match"))).total == 0
    # Literal wildcard/quote input remains a parameter, not a SQL wildcard.
    p["event"](label="literal 100%_' value")
    assert event_page(events(p["studio"], EventFilter(search="%_'"))).total == 1


@pytest.mark.parametrize("ascending", [False, True])
def test_keyset_pages_keep_every_duplicate_without_offset(projected, ascending):
    p = projected
    for i in range(61):
        p["event"](ordinal=i, start_date=date(2023, 3, 1) + timedelta(days=i % 3), label="same")
    found, after = [], None
    while True:
        page = event_page(events(p["studio"]), after=after, ascending=ascending)
        assert page.total == 61 and len(page.rows) <= 25
        found.extend(row["id"] for row in page.rows)
        after = page.next_after
        if after is None:
            break
    assert len(found) == len(set(found)) == 61
    assert set(found) == set(AnnotationEvent.objects.values_list("id", flat=True))


def test_calendar_exact_counts_and_day_pages_include_ranges_not_reversed(projected):
    p = projected
    require_postgresql()
    start = date(2024, 1, 1)
    p["event"](start_date=start, end_date=None)
    p["event"](start_date=start, end_date=start)
    p["event"](start_date=start - timedelta(days=1000), end_date=start + timedelta(days=1000))
    p["event"](start_date=start + timedelta(days=1), end_date=start - timedelta(days=1))
    for i in range(30):
        p["event"](start_date=start, label=f"point{i}")
    qs = events(p["studio"])
    with CaptureQueriesContext(connection) as captured:
        buckets = day_counts(qs, start, start + timedelta(days=41))
    assert len(captured) == 1 and len(buckets) == 42
    assert buckets[0] == dict(day=start, point_count=32, range_count=1, count=33)
    assert buckets[1]["range_count"] == 1 and buckets[1]["point_count"] == 0
    first = event_page(qs, start=start, end=start)
    second = event_page(qs, start=start, end=start, after=first.next_after)
    assert first.total == second.total == 33 and len(first.rows) == 25 and len(second.rows) == 8
    assert event_page(qs).total == 34  # reversed declaration remains list-visible
    assert day_counts(qs, date(2030, 1, 1), date(2030, 1, 1))[0]["count"] == 0


def test_defaults_apply_at_type_level_and_compatible_retained_defaults_work(projected):
    p = projected
    p["event"](event_type="campaign", visible_default=True)
    p["event"](event_type="release", visible_default=False)
    assert event_page(events(p["studio"], EventFilter(defaults=True))).total == 1
    assert event_page(events(p["studio"], EventFilter(defaults=False))).total == 2
    p["global_source"].status = "parse-error"
    p["global_source"].save(update_fields=["status"])
    assert event_page(events(p["studio"], EventFilter(defaults=True))).total == 1
    facets = facet_page(events(p["studio"]), "event_type").rows
    assert {row["value"]: row["default_visible"] for row in facets} == {"campaign": False, "release": True}


@pytest.mark.parametrize("field", ["event_type", "scope"])
def test_high_cardinality_facets_are_complete_and_keep_hostile_or_empty_values(projected, field):
    p = projected
    names = [f"z{i:03}" for i in range(61)] + ["__proto__", "constructor", ""]
    names += ["campaign", "release"] if field == "event_type" else ["shared"]
    for name in names:
        p["event"](**{field: name})
    after, found = None, []
    while True:
        page = facet_page(events(p["studio"]), field, after=after)
        assert page.total == len(names) and len(page.rows) <= 25
        found.extend(row["value"] for row in page.rows)
        after = page.next_after
        if after is None:
            break
    assert len(found) == len(set(found)) == len(names) and set(found) == set(names)
    assert found[0] == ("campaign" if field == "event_type" else "shared")
    assert facet_page(events(p["studio"]), field, search="z060").rows[0]["value"] == "z060"


def test_hidden_membership_is_a_sql_subquery_without_selection_count_cap(projected):
    p = projected
    for i in range(130):
        p["event"](event_type=f"type{i:03}", scope=f"type{i:03}")
    p["event"](event_type="kept", scope="shared")
    # Scope values stand in for a future narrow persisted filter membership.
    # No model/storage/state producer is introduced by the query phase.
    hidden = AnnotationEvent.objects.filter(studio=p["studio"]).exclude(scope="shared").values("scope")
    qs = events(p["studio"], EventFilter(hidden_types=hidden))
    with CaptureQueriesContext(connection) as captured:
        page = event_page(qs)
    assert len(captured) == 2 and page.total == 1 and page.rows[0]["event_type"] == "kept"
    assert all("SELECT" in query["sql"] for query in captured)


def test_native_query_structural_increment_does_not_read_configs_or_files(projected):
    p = projected
    require_postgresql()
    for i in range(55):
        p["event"](label=f"event{i}")
    with patch("pathlib.Path.read_text", side_effect=AssertionError("file read")), CaptureQueriesContext(connection) as captured:
        state = catalog(p["studio"])
        cover = coverage(p["studio"], now=p["now"])
        qs = events(p["studio"], now=p["now"])
        page = event_page(qs)
        types = facet_page(qs, "event_type")
        scopes = facet_page(qs, "scope")
    # One catalog + one coverage + two event + one query per facet.
    assert len(captured) == 6
    assert state and cover["complete"] and len(page.rows) == 25
    assert types.total == scopes.total == 1
    assert all('"config"' not in query["sql"] for query in captured)
    assert all("OFFSET" not in query["sql"] for query in captured)
    assert not any('"type_defaults"' in query["sql"] for query in captured[:3])


@pytest.mark.parametrize("kwargs", [dict(search="x" * 201), dict(search="🦊" * 201), dict(scope="x" * 257), dict(defaults="true"), dict(hidden_types=["release"])])
def test_internal_filter_rejects_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        EventFilter(**kwargs)


def test_hidden_membership_must_be_one_text_column():
    for hidden in (AnnotationEvent.objects.all(), AnnotationEvent.objects.values("scope", "event_type"), AnnotationEvent.objects.values("id"), AnnotationEvent.objects.values("scope")[:1]):
        with pytest.raises(ValueError):
            EventFilter(hidden_types=hidden)


@pytest.mark.parametrize("start,end", [(date(2024, 1, 2), date(2024, 1, 1)), (date(2024, 1, 1), date(2024, 2, 12)), (datetime(2024, 1, 1), date(2024, 1, 1))])
def test_window_bounds_are_enforced_without_query(studio, start, end):
    with CaptureQueriesContext(connection) as captured, pytest.raises(ValueError):
        day_counts(events(studio), start, end)
    assert len(captured) == 0


def test_internal_cursor_and_facet_types_are_checked(studio):
    with pytest.raises(ValueError):
        facet_page(events(studio), "label")
    with pytest.raises(TypeError):
        event_page(events(studio), after={"id": 1})
    with pytest.raises(TypeError):
        facet_page(events(studio), "scope", after="unvalidated")


def test_month_composition_has_four_data_queries_and_bounded_preview(projected):
    p = projected
    require_postgresql()
    start = date(2024, 1, 1)
    for i in range(30):
        p["event"](start_date=start, end_date=start + timedelta(days=41))
    with CaptureQueriesContext(connection) as captured:
        state = catalog(p["studio"])
        cover = coverage(p["studio"], now=p["now"])
        month = month_data(events(p["studio"], now=p["now"]), start, start + timedelta(days=41))
    assert len(captured) == 4 and state and cover["complete"]
    assert len(month["days"]) == 42 and len(month["events"].rows) == 25
    assert month["events"].total == 30 and month["events"].next_after is not None
    assert all(bucket["count"] == 30 for bucket in month["days"])


def test_empty_or_exhausted_facet_page_keeps_exact_total(projected):
    p = projected
    assert facet_page(events(p["studio"]), "scope").total == 0
    p["event"]()
    exhausted = facet_page(events(p["studio"]), "scope", after=FacetPosition(10, "zzzz"))
    assert exhausted.total == 1 and exhausted.rows == [] and exhausted.next_after is None
