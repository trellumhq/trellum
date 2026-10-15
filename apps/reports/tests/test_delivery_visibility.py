"""Report visibility predicates preserve caller query shape and access rules."""
import pytest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('ready', [True, False])
def test_visible_query_preserves_selected_private_and_caller_shape(
    org_admin, member, org, studio, make_user, make_group, attach_group, settings, ready,
):
    from django.db.models import Count
    from apps.core.report_access import visible_reports
    from apps.orgs.models import OrgMembership
    from apps.reports.models import Report, ReportPermissionGrant
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = 'proxy'
    private = Report.objects.create(studio=studio, slug='batch-private', name='A private', audience='private')
    public = Report.objects.create(studio=studio, slug='batch-public', name='B public')
    absent = Report.objects.create(studio=studio, slug='batch-absent', present_in_scan=False)
    group = make_group('Batch selected', grants=[(studio, 'viewer')])
    grant = group.grants.get()
    grant.viewer_scope = 'selected'; grant.save()
    attach_group(member, group)
    ReportPermissionGrant.objects.create(grant=grant, report=private)
    manager = make_user('batch-manager@example.test', org=org)
    attach_group(manager, make_group('Batch admins', org_role='admin'))
    outsider = make_user('batch-outsider@example.test')
    queryset = Report.objects.filter(present_in_scan=True, slug__startswith='batch-').annotate(schedules=Count('email_schedules')).order_by('name', 'pk')
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = ready
    expected = {org_admin.pk: [private.pk, public.pk], manager.pk: [private.pk, public.pk],
                member.pk: [private.pk] if ready else [], outsider.pk: []}
    for user in [org_admin, manager, member, outsider]:
        rows = visible_reports(user, queryset)
        assert list(rows.values_list('pk', flat=True)) == expected[user.pk]
        assert rows.count() == len(expected[user.pk])
        assert list(rows[:1].values_list('pk', flat=True)) == expected[user.pk][:1]
        assert all(row.schedules == 0 for row in rows)
    org.is_active = False; org.save(update_fields=['is_active'])
    assert not visible_reports(org_admin, queryset).exists()
