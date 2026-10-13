"""Delivery batches preserve live access and bounded page costs."""
import pytest
from apps.core.report_access import ReportAccessBatch, bulk_can_view_report
from apps.reports.models import EmailSchedule
from apps.reports.recipient_access import DeliveryPresentationBatch

pytestmark = pytest.mark.django_db


def test_batch_scope_and_compatibility(org_admin, report_row, other_studio):
    from apps.reports.models import Report
    duplicate = type(org_admin).objects.get(pk=org_admin.pk)
    assert bulk_can_view_report([org_admin, duplicate], report_row) == {org_admin.pk: True}
    assert bulk_can_view_report([], object()) == {}
    batch = ReportAccessBatch([org_admin], report_row.studio.org)
    with pytest.raises(ValueError):
        batch.can_view(duplicate, report_row)
    foreign = Report.objects.create(studio=other_studio, slug='foreign-batch')
    assert other_studio.org_id != report_row.studio.org_id
    with pytest.raises(ValueError):
        batch.can_view(org_admin, foreign)


def test_inactive_recipient_is_filtered_and_second_batch_revokes(org_admin, report_row):
    schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
    schedule.recipients.add(org_admin)
    def fresh():
        return EmailSchedule.objects.select_related('report__studio__org').prefetch_related('recipients', 'recipient_groups').get(pk=schedule.pk)
    row = fresh()
    assert len(DeliveryPresentationBatch([row]).eligible(row)) == 1
    org_admin.is_active = False
    org_admin.save(update_fields=['is_active'])
    row = fresh()
    assert DeliveryPresentationBatch([row]).eligible(row) == []


def test_empty_report_scope_denies_without_an_impossible_lookup(studio, django_assert_num_queries):
    from types import SimpleNamespace
    from django.http import Http404
    from apps.core.permissions import EffectiveRoles
    from apps.reports.views import _get_report
    request = SimpleNamespace(studio=studio, org_roles=EffectiveRoles())
    with django_assert_num_queries(0), pytest.raises(Http404):
        _get_report(request, 'inaccessible')


def test_shared_group_batch_keeps_defaults_grants_and_revocation(
    org, studio, studio2, org_admin, make_user, make_group, attach_group,
    settings, django_assert_num_queries,
):
    from apps.core.permissions import bulk_effective_roles
    from apps.orgs.models import PermissionGroupMembership
    from apps.reports.models import Report, ReportPermissionGrant
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = 'proxy'
    public = Report.objects.create(studio=studio, slug='shared-group-public')
    private = Report.objects.create(studio=studio, slug='shared-group-private', audience='private')
    first = make_user('shared-first@example.test', org=org)
    second = make_user('shared-second@example.test', org=org)
    manager = make_user('shared-manager@example.test', org=org)
    selected = make_group('Shared selected', grants=[(studio, 'viewer')])
    grant = selected.grants.get()
    grant.viewer_scope = 'selected'; grant.save()
    report_grant = ReportPermissionGrant.objects.create(grant=grant, report=private)
    attach_group(first, selected); attach_group(second, selected)
    attach_group(first, make_group('Shared default', default_studio_role='viewer'))
    attach_group(first, make_group('Shared scoped', grants=[(studio2, 'developer')]))
    attach_group(manager, make_group('Shared admin', org_role='admin'))
    users = [org_admin, first, second, manager]
    with django_assert_num_queries(6):
        resolved = bulk_effective_roles(users, org)
        assert resolved[first.pk].role_for(studio2) == 'developer'
        assert resolved[first.pk].report_scope_for(studio) == frozenset([public.pk, private.pk])
        assert resolved[second.pk].report_scope_for(studio) == frozenset([private.pk])
        assert resolved[manager.pk].is_org_admin
    assert all(bulk_can_view_report(users, private).values())
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert not any(bulk_can_view_report(users, private).values())
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    PermissionGroupMembership.objects.filter(user=second, group=selected).delete()
    report_grant.delete()
    refreshed = bulk_effective_roles(users, org)
    assert refreshed[second.pk].role_for(studio) is None
    assert refreshed[first.pk].report_scope_for(studio) == frozenset([public.pk])
