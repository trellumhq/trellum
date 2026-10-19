"""The registry scan and the summary writers take row locks in one order.

``sync_studio_registry`` locks a Report row (``update_or_create``) and then
creates or locks its ``ReportSourceSummary``. Every summary writer must lock the
Report first too. Created the other way round, a summary insert holds its new
row while its foreign key check (verified at commit, ``KEY SHARE`` on the
report) waits behind the scan's ``FOR UPDATE``; the scan then waits for that
uncommitted row. PostgreSQL ends the cycle by aborting one side, which the
worker logged as ``source reconciliation batch failed ... deadlock detected``.

These tests drive both sides on separate connections and check the sequence is
pinned (not timing-dependent): each waits until the other is parked on a lock.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection, connections
from django.utils import timezone

from apps.reports import report_experiment_projection as projection
from apps.reports import source_summary
from apps.reports.models import Report, ReportSourceSummary
from apps.reports.scan import sync_studio_registry
from apps.reports.source_summary import (
    _reconcile_leased,
    acquire,
    begin_attempt,
    begin_expiry,
    finish_expiry,
    reconcile,
)

pytestmark = pytest.mark.django_db(transaction=True)

WAIT = 15


@pytest.fixture(autouse=True)
def postgres_only():
    if connection.vendor != "postgresql":
        pytest.skip("row-lock ordering is a PostgreSQL behaviour")


def blocked_sessions():
    """Sessions of this database currently parked on a lock."""
    with connection.cursor() as cursor:
        # PostgreSQL caches pg_stat_activity for the rest of a transaction. The
        # scan polls from inside its own, so without this the first (empty)
        # answer is final and every poll burns the whole WAIT.
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'")
        return cursor.fetchone()[0]


def wait_for(predicate, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def spawn(function, name="worker"):
    """Run ``function`` on its own connection; ``box`` gets its result or error."""
    box = {}

    def target():
        try:
            box["result"] = function()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            box["error"] = exc
        finally:
            connections.close_all()

    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread, box


def finish(*threads):
    for thread, box in threads:
        thread.join(WAIT * 2)
        assert not thread.is_alive(), "a worker thread never finished"
        assert "error" not in box, f"{type(box['error']).__name__}: {box['error']}"


def make_report(studio_tree, write_report):
    write_report("one")
    return Report.objects.create(studio=studio_tree, slug="one")


def seed_expiring(row):
    ReportSourceSummary.objects.create(
        report=row, operation="expiry:op", state="expiring",
        next_check_at=timezone.now() - timedelta(seconds=1))


def _first_lease(row):
    return None, lambda: reconcile(row.pk)


def _begin_attempt(row):
    return None, lambda: begin_attempt(row.pk, uuid.uuid4())


def _begin_expiry(row):
    return None, lambda: begin_expiry(row.pk)


def _finish_expiry(row):
    seed_expiring(row)
    return None, lambda: finish_expiry(row.pk, "expiry:op", expired_at=timezone.now())


def _expired_publication(row):
    seed_expiring(row)
    lease = acquire(row.pk)
    assert lease is not None
    return lease, lambda: _reconcile_leased(row.pk, *lease)


@pytest.mark.parametrize("scenario", [_first_lease, _begin_attempt, _begin_expiry, _finish_expiry, _expired_publication],
                         ids=lambda s: s.__name__.strip("_"))
def test_rescan_holding_a_report_never_deadlocks_with_a_summary_writer(
        studio_tree, write_report, monkeypatch, scenario):
    """Registry reload mid-flight: the scan holds the report, the writer arrives."""
    row = make_report(studio_tree, write_report)
    _, write = scenario(row)

    scan_holds_report = threading.Event()
    writer_settled = threading.Event()
    real = projection.sync_declarations

    def sync_after_writer_arrives(report, *args, **kwargs):
        # ``update_or_create`` has the report locked. Let the writer run into it:
        # it either finishes (skipping the busy report) or parks on the lock.
        scan_holds_report.set()
        wait_for(lambda: writer_settled.is_set() or blocked_sessions())
        return real(report, *args, **kwargs)

    monkeypatch.setattr(projection, "sync_declarations", sync_after_writer_arrives)

    def writer():
        assert scan_holds_report.wait(WAIT)
        try:
            return write()
        finally:
            writer_settled.set()

    scan = spawn(lambda: sync_studio_registry(studio_tree))
    other = spawn(writer)
    finish(scan, other)

    assert ReportSourceSummary.objects.filter(report=row).count() == 1
    summary = ReportSourceSummary.objects.get(report=row)
    # The scan's declaration sync was not lost: it stamped the summary.
    assert summary.declaration_digest
    # Nothing is wedged: the report can still be leased once the dust settles
    # (an expired report is correctly ineligible, a live one is due again).
    ReportSourceSummary.objects.filter(report=row).update(
        lease_until=None, lease_token="", next_check_at=timezone.now() - timedelta(seconds=1))
    if scenario in (_first_lease, _begin_attempt):
        assert acquire(row.pk) is not None


def test_summary_insert_in_flight_never_deadlocks_with_a_rescan(studio_tree, write_report, monkeypatch):
    """The other interleaving: the lease transaction is open when the scan arrives."""
    row = make_report(studio_tree, write_report)
    lease_open = threading.Event()
    release_lease = threading.Event()
    real_uuid = uuid

    def pause_with_lease_transaction_open():
        # Reached inside ``acquire``'s transaction, after the summary insert.
        if threading.current_thread().name == "lease":
            lease_open.set()
            release_lease.wait(WAIT)
        return real_uuid.uuid4()

    monkeypatch.setattr(source_summary, "uuid", SimpleNamespace(uuid4=pause_with_lease_transaction_open, UUID=uuid.UUID))

    lease = spawn(lambda: acquire(row.pk), name="lease")
    assert wait_for(lease_open.is_set), "the lease transaction never reached its pause"
    scan = spawn(lambda: sync_studio_registry(studio_tree))
    # The scan is now parked behind the open lease transaction (or, without the
    # fix, on the summary row the lease inserted): either way release the lease.
    assert wait_for(blocked_sessions), "the rescan never reached the open lease transaction"
    release_lease.set()
    finish(lease, scan)

    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.declaration_digest
    # The scan bumped the generation and cancelled the lease it overtook, so a
    # late publication from the old lease is fenced rather than applied.
    assert summary.lease_token == ""
    assert reconcile(row.pk)["status"] != "ineligible"
