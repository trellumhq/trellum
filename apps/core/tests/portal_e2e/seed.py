"""Deterministic isolated baseline data; no provider credentials."""
from django.contrib.auth import get_user_model
from apps.orgs.models import Organization, OrgMembership, PermissionGroup, PermissionGroupMembership, PermissionGroupGrant
from apps.studios.models import Studio, StudioMembership
from apps.reports.models import Report, ReportPermissionGrant

SEED_VERSION = 'portal-baseline-v1'
PASSWORD = 'Disposable-browser-password-42!'

def seed(report_count=20, member_count=3, studio_count=1):
    User = get_user_model()
    for slug in ('alpha', 'beta'):
        org = Organization.objects.create(slug=slug, name=slug.title())
        for index in range(studio_count):
            studio = Studio.objects.create(org=org, slug=f'studio-{index}', name=f'Studio {index}')
            studio.ensure_dirs()
            Report.objects.bulk_create([Report(studio=studio, slug=f'report-{n:04}', name=f'Report {n}') for n in range(report_count)])
            for role in ('admin', 'developer', 'viewer'):
                user, _ = User.objects.get_or_create(email=f'{slug}-{role}@example.invalid')
                user.set_password(PASSWORD)
                user.save()
                OrgMembership.objects.get_or_create(org=org, user=user, defaults={'role': 'admin' if role == 'admin' else 'member'})
                StudioMembership.objects.create(studio=studio, user=user, role=role)
        for role in ('member', 'admin', 'selected'):
            user = User.objects.create_user(f'{slug}-group-{role}@example.invalid', PASSWORD)
            OrgMembership.objects.create(org=org, user=user, role='member')
            group = PermissionGroup.objects.create(org=org, name=role, org_role='admin' if role == 'admin' else '')
            PermissionGroupMembership.objects.create(group=group, user=user)
            first = org.studios.get(slug='studio-0')
            grant = PermissionGroupGrant.objects.create(group=group, studio=first, role='viewer', viewer_scope='selected' if role == 'selected' else 'all')
            if role == 'selected':
                ReportPermissionGrant.objects.create(grant=grant, report=first.reports.first())
        User.objects.create_user(f'{slug}-revoked@example.invalid', PASSWORD)
        impersonated = User.objects.create_user(f'{slug}-impersonated@example.invalid', PASSWORD)
        OrgMembership.objects.create(org=org, user=impersonated, role='member')
        for n in range(member_count):
            user = User.objects.create_user(f'{slug}-member-{n}@example.invalid', PASSWORD)
            OrgMembership.objects.create(org=org, user=user, role='member')
    User.objects.create_superuser('operator@example.invalid', PASSWORD)
    User.objects.create_user('inactive@example.invalid', PASSWORD, is_active=False)
