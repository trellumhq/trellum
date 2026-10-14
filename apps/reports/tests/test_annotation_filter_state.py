"""Authored real-PG B1 tests. No HTTP/browser or middleware budget claim."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.orgs.models import OrgMembership
from apps.studios.models import StudioMembership
from apps.reports import annotation_filter_state as filters
from apps.reports.models import AnnotationFilterMembership, AnnotationFilterReceipt, AnnotationFilterState, Report

pytestmark = pytest.mark.django_db(transaction=True)


def request_context(user, studio):
    request = RequestFactory().get("/")
    request.user, request.studio, request.org = user, studio, studio.org
    request.session = {}
    return filters.context_from_request(request)


@pytest.fixture
def bound(org_admin, studio):
    assert connection.vendor == "postgresql", "B1 requires actual PostgreSQL"
    return request_context(org_admin, studio)


def manifest(keys):
    chunks = [keys[i:i + 25] for i in range(0, len(keys), 25)]
    digest = filters.EMPTY_DIGEST
    for chunk in chunks:
        digest = filters.chain_digest(digest, chunk)
    return chunks, digest


def import_set(context, keys, *, operation="import", mode="explicit", now=None):
    chunks, digest = manifest(keys)
    result = filters.create(context, operation_id=operation, mode=mode, expected_items=len(keys), expected_chain_digest=digest, now=now)
    token = result["token"]
    for i, chunk in enumerate(chunks):
        filters.append(context, token, operation_id=f"{operation}-chunk{i}", chunk_index=i, keys=chunk, now=now)
    return filters.finalize(context, token, operation_id=f"{operation}-final", now=now)


@pytest.mark.parametrize("size", [0, 130, 1000])
def test_complete_unknown_saved_set_has_no_selection_count_cap(bound, size):
    keys = [f"unknown_type_{i}" for i in range(size)]
    keys += ["", "__proto__", "constructor"] if size else []
    result = import_set(bound, keys)
    assert filters.read(bound, result["token"], revision=1)["state"] == "ready"
    assert set(filters.membership(bound, result["token"], revision=1, hidden=True).values_list("event_type", flat=True)) == set(keys)
    assert result["accepted_items"] == len(keys)
    assert result["accepted_distinct"] == len(set(keys))


def test_partial_import_is_not_visible_and_finalize_uses_scalar_counters(bound):
    keys = [f"type{i}" for i in range(30)]
    chunks, digest = manifest(keys)
    created = filters.create(bound, operation_id="create", mode="explicit", expected_items=30, expected_chain_digest=digest)
    token = created["token"]
    filters.append(bound, token, operation_id="chunk0", chunk_index=0, keys=chunks[0])
    with pytest.raises(filters.FilterStateError, match="revision_conflict"):
        filters.read(bound, token, revision=0)
    with pytest.raises(filters.FilterStateError, match="incomplete_import"):
        filters.finalize(bound, token, operation_id="final")
    filters.append(bound, token, operation_id="chunk1", chunk_index=1, keys=chunks[1])
    with CaptureQueriesContext(connection) as captured:
        result = filters.finalize(bound, token, operation_id="final")
    assert result["revision"] == 1
    assert all('"reports_annotationfiltermembership"' not in q["sql"] for q in captured)
    assert not any("COUNT(" in q["sql"].upper() for q in captured)


def test_create_append_finalize_and_delta_exact_replays_are_idempotent(bound):
    keys = ["duplicate"] * 27 + ["other"] * 3
    chunks, digest = manifest(keys)
    args = dict(operation_id="create", mode="explicit", expected_items=30, expected_chain_digest=digest)
    first = filters.create(bound, **args)
    assert filters.create(bound, **args) == first
    token = first["token"]
    receipt0 = filters.append(bound, token, operation_id="chunk0", chunk_index=0, keys=chunks[0])
    receipt1 = filters.append(bound, token, operation_id="chunk1", chunk_index=1, keys=chunks[1])
    assert filters.append(bound, token, operation_id="chunk0", chunk_index=0, keys=chunks[0]) == receipt0
    assert receipt1["accepted_distinct"] == 2
    ready = filters.finalize(bound, token, operation_id="finish")
    assert filters.finalize(bound, token, operation_id="finish") == ready
    args = dict(operation_id="delta", base_revision=1, changes=[("other", False), ("new", True)])
    changed = filters.delta(bound, token, **args)
    assert changed["revision"] == 2
    assert filters.delta(bound, token, **args) == changed
    assert filters.observe(bound, token, operation_id="delta") == changed
    assert filters.observe(bound, token, operation_id="missing")["status"] == "unconfirmed"
    assert AnnotationFilterMembership.objects.count() == 2
    assert AnnotationFilterReceipt.objects.filter(operation_id="delta").count() == 1


def test_body_mismatch_chunk_order_bad_digest_and_busy_create_preserve_draft(bound):
    keys = [f"type{i}" for i in range(26)]
    chunks, digest = manifest(keys)
    first = filters.create(bound, operation_id="create", mode="explicit", expected_items=26, expected_chain_digest=digest)
    token = first["token"]
    with pytest.raises(filters.FilterStateError, match="operation_conflict"):
        filters.create(bound, operation_id="create", mode="defaults", expected_items=26, expected_chain_digest=digest)
    with pytest.raises(filters.FilterStateError, match="busy"):
        filters.create(bound, operation_id="other", mode="explicit", expected_items=0, expected_chain_digest=filters.EMPTY_DIGEST)
    with pytest.raises(filters.FilterStateError, match="chunk_conflict"):
        filters.append(bound, token, operation_id="wrong-order", chunk_index=1, keys=chunks[1])
    filters.append(bound, token, operation_id="chunk0", chunk_index=0, keys=chunks[0])
    with pytest.raises(filters.FilterStateError, match="operation_conflict"):
        filters.append(bound, token, operation_id="chunk0", chunk_index=0, keys=["changed"] * 25)
    filters.append(bound, token, operation_id="chunk1", chunk_index=1, keys=["wrong-final-key"])
    with pytest.raises(filters.FilterStateError, match="incomplete_import"):
        filters.finalize(bound, token, operation_id="final")
    state = AnnotationFilterState.objects.get(token=token)
    assert state.status == "draft" and state.revision == 0
    assert not AnnotationFilterReceipt.objects.filter(operation_id="final").exists()


def test_delta_revision_conflict_no_op_defaults_and_bounded_transfer(bound):
    ready = import_set(bound, [f"type{i}" for i in range(1000)])
    token = ready["token"]
    changes = [(f"type{i}", False) for i in range(25)]
    with CaptureQueriesContext(connection) as captured:
        result = filters.delta(bound, token, operation_id="remove", base_revision=1, changes=changes)
    assert result["revision"] == 2
    assert AnnotationFilterMembership.objects.count() == 975
    selection_queries = [q["sql"] for q in captured if q["sql"].lstrip().startswith("SELECT") and '"reports_annotationfiltermembership"' in q["sql"]]
    assert len(selection_queries) == 1 and " IN " in selection_queries[0]
    with pytest.raises(filters.FilterStateError, match="revision_conflict"):
        filters.delta(bound, token, operation_id="stale", base_revision=1, changes=[("other", True)])
    assert not AnnotationFilterReceipt.objects.filter(operation_id="stale").exists()
    assert filters.delta(bound, token, operation_id="noop", base_revision=2, changes=[("type999", True)])["revision"] == 2
    defaults = import_set(bound, [], operation="defaults", mode="defaults")
    show = filters.delta(bound, defaults["token"], operation_id="show", base_revision=1, changes=[("campaign", False)])
    assert show["revision"] == 2
    assert list(filters.membership(bound, defaults["token"], revision=2, hidden=False).values_list("event_type", flat=True)) == ["campaign"]


def test_mutation_receipt_failure_and_outer_rollback_preserve_exact_prior_state(bound):
    ready = import_set(bound, ["old"])
    token = ready["token"]
    with patch.object(filters, "_record", side_effect=RuntimeError("injected receipt failure")), pytest.raises(RuntimeError):
        filters.delta(bound, token, operation_id="failed", base_revision=1, changes=[("old", False), ("new", True)])
    assert filters.read(bound, token, revision=1)["revision"] == 1
    assert set(AnnotationFilterMembership.objects.values_list("event_type", flat=True)) == {"old"}
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            filters.delta(bound, token, operation_id="rolled-back", base_revision=1, changes=[("new", True)])
            raise RuntimeError("rollback")
    assert not AnnotationFilterReceipt.objects.filter(operation_id="rolled-back").exists()
    assert AnnotationFilterState.objects.get(token=token).revision == 1


def test_ownership_context_and_fresh_request_revocation(bound, org_admin, studio, studio2, make_user, org):
    ready = import_set(bound, ["hidden"])
    token = ready["token"]
    other = make_user("other@example.test", org=org, org_role=roles.ORG_ADMIN)
    for ctx in (request_context(other, studio), request_context(org_admin, studio2)):
        with pytest.raises(filters.FilterStateError, match="not_found"):
            filters.read(ctx, token, revision=1)
    with pytest.raises(PermissionDenied):
        filters.read(filters.FilterContext(org_admin.pk, studio.pk, bound.actor_fingerprint), token, revision=1)
    with pytest.raises(PermissionDenied):
        filters.read(replace(bound, studio_id=studio2.pk), token, revision=1)
    with pytest.raises(PermissionDenied):
        filters.read(replace(bound, user_id=other.pk), token, revision=1)
    request = RequestFactory().get("/")
    request.user, request.studio, request.org, request.api_key = org_admin, studio, org, object()
    with pytest.raises(PermissionDenied):
        filters.context_from_request(request)
    OrgMembership.objects.filter(user=org_admin, org=org).delete()
    with pytest.raises(PermissionDenied):
        request_context(org_admin, studio)
    # An already-minted request context retains request-time semantics; the
    # next HTTP request must mint a fresh one and is denied as tested above.


def test_selected_report_and_inactive_actor_cannot_mint_context(member, studio, org):
    StudioMembership.objects.create(user=member, studio=studio, role=roles.VIEWER)
    assert request_context(member, studio)
    Report.objects.create(studio=studio, slug="private", audience=Report.AUDIENCE_PRIVATE)
    with pytest.raises(PermissionDenied):
        request_context(member, studio)
    member.is_active = False
    with pytest.raises(PermissionDenied):
        request_context(member, studio)


def test_expiry_touch_throttle_and_read_status_never_renew(bound):
    now = timezone.now()
    ready = import_set(bound, [], now=now)
    token = ready["token"]
    filters.read(bound, token, revision=1, now=now + timedelta(minutes=1))
    filters.observe(bound, token, operation_id="import-final", now=now + timedelta(minutes=2))
    assert AnnotationFilterState.objects.get(token=token).expires_at == now + filters.LIFETIME
    with CaptureQueriesContext(connection) as captured:
        assert filters.touch(bound, token, operation_id="early", now=now + timedelta(minutes=3))["status"] == "throttled"
    assert not any(q["sql"].startswith(("INSERT", "UPDATE", "DELETE")) for q in captured)
    touched = filters.touch(bound, token, operation_id="touch", now=now + timedelta(minutes=6))
    assert filters.touch(bound, token, operation_id="touch", now=now + timedelta(minutes=7)) == touched
    assert AnnotationFilterState.objects.get(token=token).expires_at == now + timedelta(minutes=36)
    for func, kwargs in ((filters.read, dict(revision=1)), (filters.touch, dict(operation_id="late")), (filters.observe, dict(operation_id="touch"))):
        with pytest.raises(filters.FilterStateError, match="expired"):
            func(bound, token, now=now + timedelta(minutes=36), **kwargs)


@pytest.mark.parametrize("keys", [["x"] * 26, ["nul\x00"], ["\ud800"], ["x" * 257], [], [True]])
def test_invalid_keys_are_rejected_before_state_mutation(bound, keys):
    ready = filters.create(bound, operation_id="create", mode="explicit", expected_items=25, expected_chain_digest="a" * 64)
    with CaptureQueriesContext(connection) as captured, pytest.raises(filters.FilterStateError):
        filters.append(bound, ready["token"], operation_id="bad", chunk_index=0, keys=keys)
    assert not any(q["sql"].startswith(("INSERT", "UPDATE", "DELETE")) for q in captured)
    assert AnnotationFilterMembership.objects.count() == 0


def test_utf8_escape_byte_budget_and_invalid_delta_manifest(bound):
    # JSON escaping of25 maximum-length control strings exceeds32KiB.
    with pytest.raises(filters.FilterStateError, match="oversized_body"):
        filters.chain_digest(filters.EMPTY_DIGEST, ["\x01" * 256] * 25)
    for items in (-1, True, 1 << 63):
        with pytest.raises(filters.FilterStateError, match="invalid_counter"):
            filters.create(bound, operation_id="bad", mode="explicit", expected_items=items, expected_chain_digest=filters.EMPTY_DIGEST)
    ready = import_set(bound, [])
    for changes in ([('same', True), ('same', False)], [("bad", "true")], [("x", True)] * 26):
        with pytest.raises(filters.FilterStateError):
            filters.delta(bound, ready["token"], operation_id="bad", base_revision=1, changes=changes)


def test_nonblocking_admission_and_state_row_locks_on_other_connection(bound):
    token = import_set(bound, ["old"])["token"]
    locked, release = Event(), Event()
    def hold(kind):
        connections.close_all()
        try:
            with transaction.atomic():
                if kind == "admission":
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [filters.admission_lock_key(bound)])
                else:
                    AnnotationFilterState.objects.select_for_update().get(token=token)
                locked.set()
                assert release.wait(10)
        finally:
            connections.close_all()
    for kind in ("admission", "row"):
        locked.clear(); release.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(hold, kind)
            assert locked.wait(10)
            try:
                with pytest.raises(filters.FilterStateError, match="busy") as error:
                    if kind == "admission":
                        filters.create(bound, operation_id="blocked", mode="explicit", expected_items=0, expected_chain_digest=filters.EMPTY_DIGEST)
                    else:
                        filters.delta(bound, token, operation_id="blocked", base_revision=1, changes=[("new", True)])
                assert error.value.status == 423
            finally:
                release.set()
            task.result(timeout=10)
    assert filters.read(bound, token, revision=1)


def test_repeatable_read_membership_snapshot_and_stale_revision(bound):
    token = import_set(bound, ["old"])["token"]
    def mutate():
        connections.close_all()
        try:
            return filters.delta(bound, token, operation_id="concurrent", base_revision=1, changes=[("old", False), ("new", True)])
        finally:
            connections.close_all()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert filters.read(bound, token, revision=1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(mutate).result(timeout=10)["revision"] == 2
        assert list(filters.membership(bound, token, revision=1, hidden=True).values_list("event_type", flat=True)) == ["old"]
    with pytest.raises(filters.FilterStateError, match="revision_conflict"):
        filters.read(bound, token, revision=1)
    assert list(filters.membership(bound, token, revision=2, hidden=True).values_list("event_type", flat=True)) == ["new"]


def test_migration_from_producer_baseline_and_round_trip():
    executor = MigrationExecutor(connection)
    target = ("reports", "0030_annotation_filter_state")
    try:
        executor.migrate([("reports", "0029_annotation_projection")])
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        apps = executor.loader.project_state([target]).apps
        state_model = apps.get_model("reports", "AnnotationFilterState")
        assert state_model._meta.get_field("revision")
        assert {c.name for c in apps.get_model("reports", "AnnotationFilterMembership")._meta.constraints} == {"anno_filter_type"}
        assert "reports_annotationfilterreceipt" in connection.introspection.table_names()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_concurrent_create_admission_never_publishes_two_drafts(bound):
    barrier = Barrier(2)
    def create(operation):
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            try:
                return filters.create(bound, operation_id=operation, mode="explicit", expected_items=0, expected_chain_digest=filters.EMPTY_DIGEST)
            except filters.FilterStateError as error:
                return error.code
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(create, "one"), pool.submit(create, "two")
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "busy" in results and AnnotationFilterState.objects.count() == 1


def test_duplicate_concurrent_delta_is_once_and_retry_recovers_receipt(bound):
    token = import_set(bound, ["old"])["token"]
    barrier = Barrier(2)
    args = dict(operation_id="concurrent-id", base_revision=1, changes=[("new", True)])
    def change():
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            try:
                return filters.delta(bound, token, **args)
            except filters.FilterStateError as error:
                return error.code
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(change), pool.submit(change)
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert all(isinstance(result, dict) or result == "busy" for result in results)
    assert any(isinstance(result, dict) for result in results)
    recovered = filters.delta(bound, token, **args)
    assert recovered["revision"] == 2
    assert AnnotationFilterReceipt.objects.filter(operation_id="concurrent-id").count() == 1
    assert AnnotationFilterMembership.objects.count() == 2


def test_busy_recovery_only_exposes_one_owned_draft(bound, org_admin, studio2):
    token = filters.create(bound, operation_id="draft", mode="explicit", expected_items=0, expected_chain_digest=filters.EMPTY_DIGEST)["token"]
    recovered = filters.active_import(bound)
    assert recovered["token"] == token and recovered["operation_id"] == "draft"
    assert filters.active_import(request_context(org_admin, studio2)) is None
    filters.finalize(bound, token, operation_id="finished")
    assert filters.active_import(bound) is None
