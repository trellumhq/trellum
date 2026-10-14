"""Real-PG HTTP checks for the annotation browser transport (C13 B2/B3).

Lean on purpose: pagination completeness, filter/cursor binding, snapshot
safety and permissions. One Chromium check lives in test_annotation_browser.py.
"""
import json
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.reports import annotation_filter_state as filters
from apps.reports.annotation_projection import SCHEMA_VERSION
from apps.reports.models import AnnotationCatalogState, AnnotationEvent, AnnotationSourceState, Report
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def seeded(studio):
    assert connection.vendor == "postgresql"
    now = timezone.now()
    AnnotationCatalogState.objects.create(studio=studio, revision=1, published_revision=1, type_defaults={"campaign": False})
    source = AnnotationSourceState.objects.create(
        studio=studio, kind="global", owner_workspace_id="a" * 32, schema_version=SCHEMA_VERSION,
        attempted_schema_version=SCHEMA_VERSION, desired_generation=1, published_generation=1,
        status="ready", indexed_at=now, observed_at=now)
    kinds = ["release", "campaign", "incident"]
    AnnotationEvent.objects.bulk_create([
        AnnotationEvent(studio=studio, source=source, generation=1, ordinal=i, start_date=date(2026, 3, 1 + i % 28),
                        label=f"event {i:03}", event_type=kinds[i % 3], scope="shared" if i % 2 else "web", visible_default=True)
        for i in range(60)
    ])
    return studio


def base(studio):
    return f"/s/{studio.org.slug}/{studio.slug}/annotations"


def post(client, studio, body):
    return client.post(base(studio) + "/filter", json.dumps(body), content_type="application/json")


def make_state(client, studio, hidden=(), op="t"):
    keys = list(hidden)
    digest = filters.chain_digest(filters.EMPTY_DIGEST, keys) if keys else filters.EMPTY_DIGEST
    token = post(client, studio, {"op": "create", "operation_id": op, "mode": "explicit", "expected_items": len(keys),
                                  "expected_chain_digest": digest}).json()["result"]["token"]
    if keys:
        assert post(client, studio, {"op": "append", "state": token, "operation_id": op + "a", "chunk_index": 0, "keys": keys}).status_code == 200
    final = post(client, studio, {"op": "finalize", "state": token, "operation_id": op + "f"}).json()
    return token, final["result"]["revision"]


def test_every_event_is_reachable_once_across_bounded_pages(login, seeded, org_admin):
    client = login(org_admin)
    seen, cursor, pages = [], "", 0
    while True:
        body = client.get(base(seeded) + "/data", {"mode": "list", **({"cursor": cursor} if cursor else {})}).json()
        assert len(body["rows"]) <= 25 and body["total"] == 60
        seen += [row["id"] for row in body["rows"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == len(set(seen)) == 60
    native = client.get(base(seeded) + "/browse")
    assert native.status_code == 200 and native.content.count(b'data-event-id=') == 25 and b"data-anno-next" in native.content


def test_filter_state_hides_types_and_binds_cursors(login, seeded, org_admin):
    client = login(org_admin)
    token, revision = make_state(client, seeded, hidden=["campaign"])
    q = {"mode": "list", "state": token, "revision": revision}
    body = client.get(base(seeded) + "/data", q).json()
    assert body["total"] == 40 and all(r["type"] != "campaign" for r in body["rows"])
    types = client.get(base(seeded) + "/data", {**q, "mode": "types"}).json()
    assert {r["value"]: r["hidden"] for r in types["rows"]} == {"release": False, "campaign": True, "incident": False}
    cursor = body["next_cursor"]
    assert client.get(base(seeded) + "/data", {**q, "cursor": cursor}).status_code == 200
    assert client.get(base(seeded) + "/data", {**q, "cursor": cursor + "x"}).status_code == 400
    assert client.get(base(seeded) + "/data", {**q, "cursor": cursor, "q": "event"}).status_code == 400
    # A concurrent change makes the old revision and its cursor stale, never rebased.
    delta = post(client, seeded, {"op": "delta", "state": token, "operation_id": "d1", "base_revision": revision, "changes": [["release", True]]})
    assert delta.status_code == 200
    assert client.get(base(seeded) + "/data", q).status_code == 409
    assert post(client, seeded, {"op": "delta", "state": token, "operation_id": "d2", "base_revision": revision, "changes": [["incident", True]]}).status_code == 409
    latest = client.get(base(seeded) + "/data", {"mode": "list", "state": token}).json()
    assert latest["total"] == 0 or all(r["type"] == "incident" for r in latest["rows"])
    status = client.get(base(seeded) + "/filter-status", {"state": token, "operation_id": "d1"}).json()
    assert status["action"]["state"] == "succeeded"
    assert client.get(base(seeded) + "/filter-status", {"state": token, "operation_id": "nope"}).json()["action"]["state"] == "unconfirmed"


def test_defaults_mode_with_shown_override_and_native_toggle_prg(login, seeded, org_admin):
    client = login(org_admin)
    plain = client.get(base(seeded) + "/data", {"mode": "list", "defaults": "1"}).json()
    assert plain["total"] == 40  # project default hides campaigns
    page = client.get(base(seeded) + "/browse", {"defaults": "1"})
    key = page.context["request_key"]
    response = client.post(base(seeded) + "/filter", {"request_key": key, "toggle": "0:campaign", "view": "list", "defaults": "1", "q": ""})
    assert response.status_code == 303 and "state=" in response["Location"] and "revision=2" in response["Location"]
    shown = client.get(response["Location"].replace("/browse", "/data").replace("view=list", "mode=list")).json()
    assert shown["total"] == 60
    again = client.post(base(seeded) + "/filter", {"request_key": key, "toggle": "0:campaign", "view": "list", "defaults": "1", "q": ""})
    assert again.status_code == 303  # same key is idempotent, not a second state


def test_native_conflict_recovers_to_latest_and_keeps_requested_change(login, seeded, org_admin):
    client = login(org_admin)
    token, revision = make_state(client, seeded)
    assert post(client, seeded, {"op": "delta", "state": token, "operation_id": "d1", "base_revision": revision, "changes": [["release", True]]}).status_code == 200
    response = client.post(base(seeded) + "/filter", {"request_key": "k" * 16, "toggle": "1:incident", "state": token, "revision": revision, "view": "list"})
    assert response.status_code == 303 and "notice=conflict" in response["Location"] and "pending=incident" in response["Location"]
    page = client.get(response["Location"])
    assert page.status_code == 200 and b"Apply again" in page.content and b"changed elsewhere" in page.content


def test_month_grid_is_bounded_and_ranges_are_validated(login, seeded, org_admin):
    client = login(org_admin)
    body = client.get(base(seeded) + "/data", {"mode": "month", "month": "2026-03"}).json()
    assert len(body["days"]) in (28, 35, 42) and len(body["rows"]) <= 25 and body["total"] == 60
    assert body["overflow"] == 35
    assert client.get(base(seeded) + "/data", {"mode": "month", "month": "0001-01"}).status_code == 400
    assert client.get(base(seeded) + "/data", {"mode": "day", "date": "2026-02-30"}).status_code == 400
    assert client.get(base(seeded) + "/data", {"mode": "list", "bogus": "1"}).status_code == 400
    assert client.get(base(seeded) + "/data", {"mode": "list", "q": "x\x00"}).status_code == 400
    assert client.get(base(seeded) + "/data?q=a&q=b").status_code == 400
    assert client.get(base(seeded) + "/browse?month=2026-03&view=month").content.count(b"<td") in (28, 35, 42)


def test_get_snapshot_is_read_only_and_query_bounded(login, seeded, org_admin):
    client = login(org_admin)
    client.get(base(seeded) + "/data?mode=list")  # warm session/theme caches outside the measured request
    with CaptureQueriesContext(connection) as queries:
        assert client.get(base(seeded) + "/data?mode=list").status_code == 200
    sql = [q["sql"] for q in queries.captured_queries]
    first = next(i for i, s in enumerate(sql) if "REPEATABLE READ READ ONLY" in s)
    assert not any("annotation" in s for s in sql[:first])  # snapshot opens before any feature SQL
    assert any("annotation" in s for s in sql[first:])
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sql)
    assert not any("events.yaml" in s for s in sql)


def test_permissions_follow_request_access_facts(login, seeded, org_admin, member, client, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    url = base(seeded) + "/data?mode=list"
    assert client.get(url).status_code in (302, 401, 403)
    assert client.post(base(seeded) + "/filter", "{}", content_type="application/json").status_code in (302, 401, 403)
    assert login(member).get(url).status_code == 404  # no studio grant hides the studio
    StudioMembership.objects.create(studio=seeded, user=member, role=roles.VIEWER)
    Report.objects.create(studio=seeded, slug="private", audience=Report.AUDIENCE_PRIVATE)
    assert login(member).get(url).status_code == 403  # viewer lacks full studio visibility
    assert login(member).get(base(seeded) + "/browse").status_code == 403
    assert login(member).post(base(seeded) + "/filter", "{}", content_type="application/json").status_code == 403


def test_oversized_and_malformed_state_bodies_are_rejected_before_state(login, seeded, org_admin):
    client = login(org_admin)
    assert client.post(base(seeded) + "/filter", "x" * 50000, content_type="application/json").status_code == 413
    assert post(client, seeded, {"op": "create"}).status_code == 400
    assert post(client, seeded, {"op": "append", "state": "0" * 64, "operation_id": "o", "chunk_index": 0, "keys": ["a"] * 26}).status_code in (400, 404)
    assert client.post(base(seeded) + "/filter", "not json", content_type="application/json").status_code == 400
