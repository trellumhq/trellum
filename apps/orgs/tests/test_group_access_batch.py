"""Large service writes preserve validation, assignments and audit rows.

These deliberately bypass the HTTP parser; no 1,000-field HTTP success is implied.
"""
from collections import Counter
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from apps.core.models import AuditLog
from apps.orgs.groups.forms import StudioAccessForm
from apps.orgs.groups.views import _save_group_access
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def selected_access(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = 'proxy'


def save(request, group, studio, reports, *, role='viewer', scope='selected'):
    data = QueryDict('', mutable=True)
    data.update(studio=str(studio.pk), role=role, viewer_scope=scope)
    data.setlist('reports', [str(report.pk) for report in reports])
    form = StudioAccessForm(data, org=request.org)
    return _save_group_access(request, group, form), form


@pytest.fixture
def request_actor(rf, org_admin, org):
    request = rf.post('/service-proof')
    request.user, request.org = org_admin, org
    return request


@pytest.mark.parametrize('count', (25, 100, 1000))
def test_bounded_add_and_remove_keep_exact_audit_multiset(count, request_actor, make_group, studio):
    group = make_group(f'Batch {count}')
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f'r-{i}', config={'large': 'x' * 1000}) for i in range(count)])
    for targets, event in ((reports, 'group.report_grant'), ([], 'group.report_ungrant')):
        before = AuditLog.objects.count()
        with CaptureQueriesContext(connection) as queries:
            success, form = save(request_actor, group, studio, targets)
        assert success, form.errors
        assert len(queries) <= 50
        selects = [q['sql'] for q in queries if q['sql'].startswith('SELECT')]
        assert len(selects) <= 20
        assert not any('"reports_report"."config"' in sql for sql in selects)
        grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
        assert set(grant.report_grants.values_list('report_id', flat=True)) == {r.pk for r in targets}
        events = AuditLog.objects.order_by('id')[before:]
        assert Counter(row.action for row in events) == {'group.grant': 1, event: count}
        assert {row.target_id for row in events if row.action == event} == {str(r.pk) for r in reports}
        assert all(row.actor_id == request_actor.user.pk and row.org_id == request_actor.org.pk for row in events)
        assert all(row.metadata == {'group_id': group.pk} for row in events if row.action == event)


def test_model_validation_failure_rolls_back_entire_change(request_actor, make_group, studio, monkeypatch):
    group = make_group('Atomic')
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f'r-{i}') for i in range(3)])
    assert save(request_actor, group, studio, [reports[0]])[0]
    before = list(AuditLog.objects.values_list('id', flat=True))
    original = ReportPermissionGrant.clean
    def reject(self):
        original(self)
        if self.report_id == reports[2].pk:
            raise ValidationError('Assignment changed during validation')
    monkeypatch.setattr(ReportPermissionGrant, 'clean', reject)
    success, form = save(request_actor, group, studio, reports[1:])
    assert not success and 'Assignment changed' in str(form.errors)
    assert list(ReportPermissionGrant.objects.filter(grant__group=group).values_list('report_id', flat=True)) == [reports[0].pk]
    assert list(AuditLog.objects.values_list('id', flat=True)) == before


def test_bulk_constraint_race_becomes_validation_error_and_rolls_back(request_actor, make_group, studio, monkeypatch):
    group = make_group('Constraint race')
    report = Report.objects.create(studio=studio, slug='one')
    def conflict(*args, **kwargs):
        raise IntegrityError('concurrent assignment')
    monkeypatch.setattr(ReportPermissionGrant.objects, 'bulk_create', conflict)
    success, form = save(request_actor, group, studio, [report])
    assert not success and 'Selected reports changed' in str(form.errors)
    assert not PermissionGroupGrant.objects.filter(group=group).exists()
    assert not AuditLog.objects.filter(action='group.grant', target_id=str(group.pk)).exists()


def test_foreign_report_is_rejected_without_write(request_actor, make_group, studio, other_studio):
    group = make_group('Foreign')
    foreign = Report.objects.create(studio=other_studio, slug='foreign')
    success, form = save(request_actor, group, studio, [foreign])
    assert not success and 'reports' in form.errors
    assert not PermissionGroupGrant.objects.filter(group=group).exists()


def test_private_assignment_survives_all_scope_then_clears_on_developer(request_actor, make_group, studio):
    group = make_group('Private')
    private = Report.objects.create(studio=studio, slug='private', audience=Report.AUDIENCE_PRIVATE)
    public = Report.objects.create(studio=studio, slug='public')
    assert save(request_actor, group, studio, [private, public])[0]
    assert save(request_actor, group, studio, [], scope='all')[0]
    assert list(ReportPermissionGrant.objects.filter(grant__group=group).values_list('report_id', flat=True)) == [private.pk]
    assert save(request_actor, group, studio, [], role='developer')[0]
    assert not ReportPermissionGrant.objects.filter(grant__group=group).exists()


def test_readiness_failure_preserves_existing_assignment(request_actor, make_group, studio, settings):
    group = make_group('Readiness')
    report = Report.objects.create(studio=studio, slug='one')
    assert save(request_actor, group, studio, [report])[0]
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    success, form = save(request_actor, group, studio, [])
    assert not success and form.non_field_errors()
    assert ReportPermissionGrant.objects.filter(grant__group=group, report=report).exists()


def test_deletion_after_form_validation_is_not_silently_dropped(request_actor, make_group, studio):
    group = make_group('Deleted between validation and lock')
    old = Report.objects.create(studio=studio, slug='old')
    requested = Report.objects.create(studio=studio, slug='requested')
    assert save(request_actor, group, studio, [old])[0]
    form = StudioAccessForm({'studio': studio.pk, 'role': 'viewer',
                             'viewer_scope': 'selected', 'reports': [str(requested.pk)]},
                            org=request_actor.org)
    assert form.is_valid()
    requested.delete()
    before = AuditLog.objects.count()
    assert not _save_group_access(request_actor, group, form)
    assert 'Selected reports changed' in str(form.errors)
    assert list(ReportPermissionGrant.objects.filter(grant__group=group).values_list('report_id', flat=True)) == [old.pk]
    assert AuditLog.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_concurrent_replacements_serialize_and_keep_complete_audits(request_actor, make_group, studio):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connections

    group = make_group('Concurrent replacements')
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f'r-{i}') for i in range(50)])
    ready = Barrier(2)

    def replace(targets):
        try:
            ready.wait(timeout=10)
            success, form = save(request_actor, group, studio, targets)
            assert success, form.errors
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(replace, reports[:25]), pool.submit(replace, reports[25:])]
        for future in futures:
            future.result(timeout=20)
    persisted = set(ReportPermissionGrant.objects.filter(grant__group=group).values_list('report_id', flat=True))
    assert persisted in ({r.pk for r in reports[:25]}, {r.pk for r in reports[25:]})
    actions = Counter(AuditLog.objects.filter(org=request_actor.org).values_list('action', flat=True))
    assert actions == {'group.grant': 2, 'group.report_grant': 50, 'group.report_ungrant': 25}
