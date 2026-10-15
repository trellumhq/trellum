"""Batched request events retain ordinary audit attribution and policy."""
from types import SimpleNamespace
import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from apps.core.audit import audit, audit_many
from apps.core.audit_actions import UnknownAuditAction
from apps.core.models import AuditLog
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


def test_batch_matches_single_request_metadata_and_identity(rf, member, superuser, org):
    request = rf.post('/audit-proof', HTTP_USER_AGENT='agent' * 100, REMOTE_ADDR='192.0.2.8')
    request.user, request.org = member, org
    request.session = {'impersonator_id': superuser.pk}
    request.api_key = SimpleNamespace(pk=123)
    metadata = {'group_id': 18, 'password': 'hidden', 'nested': {'secret': 'hidden'}, 'blob': 'x' * 10000}
    target = Report(pk=42)
    single = audit(request, 'group.report_grant', target=target, **metadata)
    with CaptureQueriesContext(connection) as queries:
        batch = audit_many(request, 'group.report_grant', [target, Report(pk=43)], **metadata)
    assert len(batch) == 2
    assert sum(q['sql'].startswith('SELECT') for q in queries) == 1
    fields = ('org_id', 'actor_id', 'impersonator_id', 'action', 'category', 'outcome', 'target_type', 'metadata', 'ip', 'user_agent')
    for row in batch:
        assert {f: getattr(row, f) for f in fields} == {f: getattr(single, f) for f in fields}
        assert row.pk and row.created_at
        assert row.metadata['password'] == '[redacted]'
        assert row.metadata['nested']['secret'] == '[redacted]'
        assert row.metadata['_truncated'] and row.metadata['api_key'] == 123
        assert len(row.user_agent) == 256
    assert {row.target_id for row in batch} == {'42', '43'}


def test_batches_keep_every_target_and_use_bounded_inserts(rf, member, org):
    request = rf.post('/audit-proof')
    request.user, request.org = member, org
    with CaptureQueriesContext(connection) as queries:
        rows = audit_many(request, 'group.report_ungrant', (Report(pk=i) for i in range(1000)), group_id=7)
    assert len(rows) == 1000
    assert sum(q['sql'].startswith('INSERT') for q in queries) == 4
    assert not any(q['sql'].startswith('SELECT') for q in queries)
    assert set(AuditLog.objects.filter(action='group.report_ungrant').values_list('target_id', flat=True)) == {str(i) for i in range(1000)}


def test_database_failure_does_not_poison_surrounding_transaction(rf, member, org):
    request = rf.post('/audit-proof')
    request.user, request.org = member, org
    with transaction.atomic():
        # Actual PostgreSQL column-length failure rather than a mocked error.
        assert audit_many(request, 'group.report_grant', [Report(pk=1)], outcome='invalid-long-outcome') is None
        org.name = 'Business operation remains usable'
        org.save(update_fields=['name'])
    org.refresh_from_db()
    assert org.name == 'Business operation remains usable'
    assert not AuditLog.objects.filter(action='group.report_grant').exists()


def test_empty_batch_does_no_database_work(rf, member, django_assert_num_queries):
    request = rf.post('/audit-proof')
    request.user = member
    with django_assert_num_queries(0):
        assert audit_many(request, 'group.report_grant', []) == []


def test_unknown_category_still_raises_in_debug(rf, settings):
    settings.DEBUG = True
    with pytest.raises(UnknownAuditAction):
        audit_many(rf.post('/audit-proof'), 'not-a-registered-action', [Report(pk=1)])
