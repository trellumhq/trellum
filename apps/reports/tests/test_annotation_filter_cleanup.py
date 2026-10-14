"""Real PostgreSQL cleanup budgets, skip-lock races and interrupted transactions."""
import io
import multiprocessing
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections, transaction
from django.db.models.query import QuerySet
from django.test import RequestFactory
from django.utils import timezone

from apps.reports.annotation_filter_cleanup import CleanupWorker, cleanup_once
from apps.reports import annotation_filter_state as filters
from apps.reports.models import AnnotationFilterMembership, AnnotationFilterReceipt, AnnotationFilterState

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def bound(org_admin, studio):
    assert connection.vendor == "postgresql", "cleanup tests require actual PostgreSQL"
    request = RequestFactory().get("/")
    request.user, request.studio, request.org, request.session = org_admin, studio, studio.org, {}
    return filters.context_from_request(request)


def seed(context, size, operation, now):
    keys = [f"type{i}" for i in range(size)]
    chunks = [keys[i:i + 25] for i in range(0, size, 25)]
    digest = filters.EMPTY_DIGEST
    for chunk in chunks:
        digest = filters.chain_digest(digest, chunk)
    created = filters.create(context, operation_id=operation, mode="explicit", expected_items=size, expected_chain_digest=digest, now=now)
    for i, chunk in enumerate(chunks):
        filters.append(context, created["token"], operation_id=f"{operation}-{i}", chunk_index=i, keys=chunk, now=now)
    filters.finalize(context, created["token"], operation_id=f"{operation}-final", now=now)
    return AnnotationFilterState.objects.get(token=created["token"])


def rows():
    return AnnotationFilterState.objects.count() + AnnotationFilterMembership.objects.count() + AnnotationFilterReceipt.objects.count()


def test_global500_budget_includes_children_receipts_and_parents_and_resumes(bound):
    now = timezone.now()
    old = now - timedelta(hours=2)
    large = seed(bound, 1000, "large", old)
    small = seed(bound, 20, "small", old)
    live = seed(bound, 5, "live", now)
    before = rows()
    first = cleanup_once(now=now)
    assert first["total_deleted"] == 500 and rows() == before - 500
    assert AnnotationFilterState.objects.get(pk=large.pk).status == "retiring"
    assert AnnotationFilterState.objects.filter(pk=small.pk).exists()
    while AnnotationFilterState.objects.exclude(pk=live.pk).exists():
        before = rows()
        result = cleanup_once(now=now)
        assert 0 < result["total_deleted"] <= 500
        assert before - rows() == result["total_deleted"]
    assert AnnotationFilterMembership.objects.filter(state=live).count() == 5
    assert AnnotationFilterReceipt.objects.filter(state=live).count() == 3
    assert cleanup_once(now=now)["total_deleted"] == 0


def test_candidate25_bound_and_parent_deletion_never_collects_all_children(bound):
    now = timezone.now()
    for i in range(30):
        seed(bound, 0, f"empty{i}", now - timedelta(hours=1))
    with patch.object(QuerySet, "delete", side_effect=AssertionError("unbounded ORM cascade")):
        result = cleanup_once(now=now)
    assert result["candidates"] == result["visited"] == 25
    assert result["deleted"] == dict(memberships=0, receipts=50, states=25)
    assert AnnotationFilterState.objects.count() == 5
    assert cleanup_once(now=now)["deleted"]["states"] == 5


def test_one_row_budget_keeps_parent_until_children_are_empty(bound):
    now = timezone.now()
    state = seed(bound, 1, "one", now - timedelta(hours=1))
    for remaining in (4, 3, 2, 1, 0):
        before = rows()
        result = cleanup_once(now=now, max_rows=1)
        assert result["total_deleted"] == 1 and rows() == before - 1 == remaining
        assert AnnotationFilterState.objects.filter(pk=state.pk).exists() == (remaining > 0)


def test_skip_locked_cleanup_does_not_wait_and_touch_cannot_resurrect_retiring(bound):
    now = timezone.now()
    state = seed(bound, 10, "locked", now - timedelta(hours=1))
    locked, release = Event(), Event()
    def hold():
        connections.close_all()
        try:
            with transaction.atomic():
                AnnotationFilterState.objects.select_for_update().get(pk=state.pk)
                locked.set()
                assert release.wait(10)
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        assert locked.wait(10)
        try:
            result = cleanup_once(now=now)
            assert result["candidates"] == 1 and result["visited"] == result["total_deleted"] == 0
        finally:
            release.set()
        future.result(timeout=10)
    result = cleanup_once(now=now, max_rows=1)
    assert result["marked"] == 1
    with pytest.raises(filters.FilterStateError, match="expired"):
        filters.touch(bound, state.token, operation_id="resurrect", now=now)
    assert AnnotationFilterState.objects.get(pk=state.pk).status == "retiring"


def test_touch_wins_expiry_discovery_race_before_parent_lock(bound):
    now = timezone.now()
    state = seed(bound, 1, "touch-race", now - timedelta(minutes=29))
    original_first = QuerySet.first
    touched = False
    # Candidate discovery uses future cleanup now; test seam renews using a
    # valid actual activity timestamp before cleanup's row-lock lookup.
    def before_lock(queryset):
        nonlocal touched
        if queryset.model is AnnotationFilterState and queryset.query.select_for_update and queryset.query.select_for_update_skip_locked and not touched:
            touched = True
            filters.touch(bound, state.token, operation_id="renew", now=now)
        return original_first(queryset)
    with patch.object(QuerySet, "first", before_lock):
        result = cleanup_once(now=now + timedelta(minutes=2))
    assert touched and result["candidates"] == 1 and result["visited"] == 0
    assert AnnotationFilterState.objects.get(pk=state.pk).expires_at == now + timedelta(minutes=30)


def test_cleanup_failure_rolls_back_retirement_and_deletes(bound):
    now = timezone.now()
    state = seed(bound, 10, "rollback", now - timedelta(hours=1))
    before = rows()
    original = QuerySet._raw_delete
    def fail_receipts(queryset, using):
        if queryset.model is AnnotationFilterReceipt:
            raise RuntimeError("injected cleanup failure")
        return original(queryset, using)
    with patch.object(QuerySet, "_raw_delete", fail_receipts), pytest.raises(RuntimeError):
        cleanup_once(now=now)
    assert rows() == before and AnnotationFilterState.objects.get(pk=state.pk).status == "ready"
    assert cleanup_once(now=now)["total_deleted"] == before


def _interrupted_cleanup(channel, now):
    connections.close_all()
    original = QuerySet._raw_delete
    def after_delete(queryset, using):
        result = original(queryset, using)
        if queryset.model is AnnotationFilterMembership:
            channel.send("deleted-uncommitted")
            channel.recv()
        return result
    try:
        with patch.object(QuerySet, "_raw_delete", after_delete):
            cleanup_once(now=now)
    finally:
        connections.close_all()
        channel.close()


def test_sigkill_rolls_back_partial_tick_and_new_process_can_resume(bound):
    now = timezone.now()
    state = seed(bound, 40, "kill", now - timedelta(hours=1))
    before = rows()
    # Close the parent's sockets BEFORE fork; no PG socket is shared with the
    # child and its forced termination cannot corrupt the parent connection.
    connections.close_all()
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_interrupted_cleanup, args=(child, now))
    process.start()
    child.close()
    try:
        assert parent.poll(10) and parent.recv() == "deleted-uncommitted"
        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
        assert rows() == before and AnnotationFilterState.objects.get(pk=state.pk).status == "ready"
        # A new maintenance invocation resumes from only persisted state.
        assert cleanup_once(now=now)["total_deleted"] == before
        assert rows() == 0
    finally:
        if process.is_alive():
            process.kill(); process.join(10)
        parent.close()


def test_management_command_one_tick_and_invalid_budgets(bound):
    now = timezone.now()
    seed(bound, 5, "command", now - timedelta(hours=1))
    output = io.StringIO()
    call_command("cleanup_annotation_filters", max_rows=1, stdout=output)
    assert '"total_deleted": 1' in output.getvalue()
    for kwargs in (dict(max_rows=501), dict(max_candidates=26), dict(max_rows=0)):
        with pytest.raises(CommandError):
            call_command("cleanup_annotation_filters", stdout=io.StringIO(), **kwargs)


def test_worker_has_no_startup_tick_and_stops_while_waiting():
    with patch("apps.reports.annotation_filter_cleanup.cleanup_once") as tick:
        worker = CleanupWorker().start()
        assert worker.stop(timeout=5)
        tick.assert_not_called()
