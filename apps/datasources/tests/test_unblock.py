"""Unblocking waiting reports is admitted with the check and walked by the worker."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.datasources import status as status_mod
from apps.datasources import unblock
from apps.datasources.models import DataSource, RepoDataSource, UnblockRequest
from apps.datasources.testing import check_binding
from apps.reports.models import Report
from apps.runner.models import Run
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(studio_tree):
    return DataSource.objects.create(
        studio=studio_tree, name="wh", type="postgres", config={"host": "h"},
        credentials={"user": "u", "password": "p"},
    )


@pytest.fixture
def passing(monkeypatch):
    monkeypatch.setattr("apps.datasources.testing.test_datasource", lambda s: (True, "Connected."))


def drain():
    steps = 0
    while unblock.process_next():
        steps += 1
    return steps


def test_request_admits_without_walking(source, studio_tree, passing, monkeypatch):
    Report.objects.create(studio=studio_tree, slug="sales", config={"data_sources": ["wh"]})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("walk on the request"))  # noqa: E731
    monkeypatch.setattr(status_mod, "blocked_reports", boom)
    ok, _ = check_binding(source, studio_tree)
    assert ok
    request = UnblockRequest.objects.get(binding=source)
    assert request.status == UnblockRequest.PENDING and request.binding_updated_at == source.updated_at
    assert not Run.objects.exists()
    monkeypatch.undo()
    drain()
    assert Run.objects.filter(slug="sales", status="queued").count() == 1
    assert UnblockRequest.objects.get(binding=source).status == UnblockRequest.DONE


def test_failed_check_admits_nothing(source, studio_tree, monkeypatch):
    monkeypatch.setattr("apps.datasources.testing.test_datasource", lambda s: (False, "refused"))
    check_binding(source, studio_tree)
    assert not UnblockRequest.objects.exists()


def test_admission_commits_with_the_evidence(source, studio_tree, passing, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("crash while admitting")

    monkeypatch.setattr(unblock, "admit", fail)
    with pytest.raises(RuntimeError):
        check_binding(source, studio_tree)
    source.refresh_from_db()
    assert source.last_check_ok is None  # evidence and admission are one transaction
    assert not UnblockRequest.objects.exists()


def test_admission_survives_restart_and_a_second_check_resets_it(source, studio_tree, passing):
    check_binding(source, studio_tree)
    first = UnblockRequest.objects.get(binding=source)
    check_binding(source, studio_tree)
    assert UnblockRequest.objects.filter(binding=source).count() == 1
    assert UnblockRequest.objects.get(binding=source).pk == first.pk


def test_org_binding_walks_one_studio_per_step_and_resumes_after_crash(org, studio_tree, studio2, passing, monkeypatch):
    studio2.ensure_dirs()
    binding = DataSource.objects.create(
        org=org, name="shared", type="postgres", config={"host": "h"},
        credentials={"user": "u", "password": "p"},
    )
    for st in (studio_tree, studio2):
        Report.objects.create(studio=st, slug=f"r-{st.slug}", config={"data_sources": ["shared"]})
    check_binding(binding)
    now = timezone.now()
    assert unblock.process_next(now=now)
    request = UnblockRequest.objects.get(binding=binding)
    assert request.cursor_studio_id == min(studio_tree.pk, studio2.pk) and Run.objects.count() == 1

    # Crash mid-step: lease not yet expired -> nobody else takes it; expired -> resumes at the cursor.
    UnblockRequest.objects.filter(pk=request.pk).update(lease_until=now + timedelta(seconds=30))
    assert not unblock.process_next(now=now)
    original = unblock._walk_batch
    monkeypatch.setattr(unblock, "_walk_batch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("worker died")))
    with pytest.raises(RuntimeError):
        unblock.process_next(now=now + timedelta(seconds=61))
    monkeypatch.setattr(unblock, "_walk_batch", original)
    UnblockRequest.objects.filter(pk=request.pk).update(lease_until=now)
    assert drain() >= 1
    assert Run.objects.count() == 2  # no duplicate for the first studio, second one queued
    assert UnblockRequest.objects.get(binding=binding).status == UnblockRequest.DONE


def test_obsolete_request_enqueues_nothing(source, studio_tree, passing):
    Report.objects.create(studio=studio_tree, slug="sales", config={"data_sources": ["wh"]})
    check_binding(source, studio_tree)
    changed = DataSource.objects.get(pk=source.pk)
    changed.config = {"host": "elsewhere"}
    changed.save()
    drain()
    assert not Run.objects.exists()
    assert UnblockRequest.objects.get(binding=source).status == UnblockRequest.DONE


def test_declaration_change_invalidates(source, studio_tree, passing):
    decl = RepoDataSource.objects.create(studio=studio_tree, name="wh", type="postgres", config={"host": "h"})
    Report.objects.create(studio=studio_tree, slug="sales", config={"data_sources": ["wh"]})
    check_binding(source, studio_tree)
    decl.config = {"host": "new"}
    decl.save()
    drain()
    assert not Run.objects.exists()


def test_deleted_binding_cascades(source, studio_tree, passing):
    check_binding(source, studio_tree)
    source.delete()
    assert not unblock.process_next()


def test_poison_request_is_abandoned(source, studio_tree, passing, monkeypatch):
    check_binding(source, studio_tree)
    monkeypatch.setattr(unblock, "_walk_batch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    now = timezone.now()
    for i in range(unblock.MAX_ATTEMPTS):
        with pytest.raises(RuntimeError):
            unblock.process_next(now=now + timedelta(seconds=100 * (i + 1)))
    assert unblock.process_next(now=now + timedelta(seconds=1000))
    assert UnblockRequest.objects.get(binding=source).status == UnblockRequest.DONE
    assert not Studio.objects.none()


def test_a_step_examines_at_most_25_reports_and_resumes_from_the_cursor(source, studio_tree, passing):
    studio_tree.ensure_dirs()
    for i in range(60):
        Report.objects.create(studio=studio_tree, slug=f"r{i:02d}", config={"data_sources": ["wh"]})
    check_binding(source)
    assert unblock.process_next()
    request = UnblockRequest.objects.get(binding=source)
    assert Run.objects.count() == 25 and request.queued == 25 and request.cursor_studio_id == 0
    assert drain() >= 2
    assert Run.objects.count() == 60
    assert UnblockRequest.objects.get(binding=source).status == UnblockRequest.DONE
