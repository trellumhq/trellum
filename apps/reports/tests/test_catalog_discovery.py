"""Read-only discovery requires effective administration and normal access gates."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.core import roles
from apps.reports.catalog import registry_page_payload, registry_payload
from apps.reports.models import Report, ReportFavorite
from apps.reports.scan import sync_studio_registry

pytestmark = pytest.mark.django_db


@pytest.fixture
def rows(studio):
    now = timezone.now()
    return [Report.objects.create(studio=studio, slug=slug, name=slug, category='Discovery', **state)
            for slug, state in [
                ('ordinary', {}), ('hidden', {'present_in_scan': False, 'archived_in_scan': True}),
                ('disabled', {'disabled': True}), ('expired', {'expires_at': now - timedelta(days=1)}),
                ('retained', {'data_expired_at': now}), ('future', {'expires_at': now + timedelta(days=1)}),
                ('missing', {'present_in_scan': False}),
            ]]


def base(studio):
    return f'/s/{studio.org.slug}/{studio.slug}'


def slugs(response):
    assert response.status_code == 200, response.content
    return {r['slug'] for r in response.json()['reports']}


@pytest.mark.parametrize('grant_kind', ['studio', 'org', 'group-default', 'group-studio', 'group-org', 'superuser'])
def test_only_effective_admins_can_include_extra_rows(login, member, org_admin, superuser, studio, grant_studio,
                                                     make_group, attach_group, rows, grant_kind):
    user = member
    if grant_kind == 'studio':
        grant_studio(user, studio, roles.ADMIN)
    elif grant_kind == 'org':
        user = org_admin
    elif grant_kind == 'superuser':
        user = superuser
    else:
        group = make_group('Administrators', **{
            'group-default': {'default_studio_role': roles.ADMIN},
            'group-studio': {'grants': [(studio, roles.ADMIN)]},
            'group-org': {'org_role': roles.ORG_ADMIN},
        }[grant_kind])
        attach_group(user, group)
    client = login(user)
    for page in ['', '&page=1']:
        assert slugs(client.get(base(studio) + '/api/registry?kind=report' + page)) == {'ordinary', 'future'}
        result = client.get(base(studio) + '/api/registry?include_unavailable=1' + page)
        assert slugs(result) == {'ordinary', 'future', 'hidden', 'disabled', 'expired', 'retained'}
        by_slug = {r['slug']: r for r in result.json()['reports']}
        assert by_slug['hidden']['hidden'] and not by_slug['hidden']['disabled']
        assert by_slug['expired']['expired'] and not by_slug['expired']['data_expired_at']
        assert by_slug['retained']['data_expired_at'] and not by_slug['retained']['expired']
    assert b'id="showUnavailable"' in client.get(base(studio) + '/').content


@pytest.mark.parametrize('role', [roles.VIEWER, roles.DEVELOPER])
def test_forged_filters_cannot_expand_catalog(login, member, studio, grant_studio, rows, role):
    grant_studio(member, studio, role)
    client = login(member)
    for suffix in ['', '&page=1', '&page=1&q=hidden', '&page=1&tab=_recent&recent=hidden']:
        got = slugs(client.get(base(studio) + '/api/registry?include_unavailable=1' + suffix))
        assert not got & {'hidden', 'expired', 'disabled', 'retained'}
    assert b'id="showUnavailable"' not in client.get(base(studio) + '/').content


def test_search_favorites_recent_paging_and_invalid_filter(login, org_admin, studio, rows):
    client = login(org_admin)
    url = base(studio) + '/api/registry?include_unavailable=1&page=1'
    assert slugs(client.get(url + '&q=hidden')) == {'hidden'}
    ReportFavorite.objects.create(user=org_admin, report=rows[1])
    favorite = client.get(url + '&tab=_favorites')
    assert slugs(favorite) == {'hidden'}
    assert favorite.json()['reports'][0]['favorite'] is True
    assert slugs(client.get(url + '&tab=_recent&recent=retained')) == {'retained'}
    one = client.get(url + '&limit=5').json()
    two = client.get(url.replace('page=1', 'page=2') + '&limit=5').json()
    assert one['pagination']['total'] == 6 and len(one['reports']) == 5 and len(two['reports']) == 1
    assert client.get(base(studio) + '/api/registry?include_unavailable=yes').status_code == 400


def test_discovery_does_not_bypass_private_readiness_or_key_tenant(studio, org_admin, superuser, rows, settings, other_org):
    rows[1].audience = Report.AUDIENCE_PRIVATE
    rows[1].save(update_fields=['audience'])
    request = SimpleNamespace(user=org_admin, org=studio.org, studio=studio, GET={'include_unavailable': '1'})
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert 'hidden' not in {r['slug'] for r in registry_payload(request)['reports']}
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    assert 'hidden' in {r['slug'] for r in registry_payload(request)['reports']}
    request.user = superuser
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    for read in [lambda: registry_payload(request), lambda: registry_page_payload(request, {'include_unavailable': '1'})]:
        with pytest.raises(PermissionDenied):
            read()


def test_scan_repo_hidden_authority_and_removed_sources(studio_tree, write_report):
    directory = write_report('source-hidden', hidden=True)
    sync_studio_registry(studio_tree)
    row = Report.objects.get(studio=studio_tree, slug='source-hidden')
    row.hidden = False  # retained legacy override must have no effect
    row.save(update_fields=['hidden'])
    sync_studio_registry(studio_tree)
    row.refresh_from_db()
    assert row.is_hidden and row.archived_in_scan and not row.present_in_scan
    (directory / 'report.yaml').write_text('name: Visible\nhidden: false\n')
    sync_studio_registry(studio_tree)
    row.refresh_from_db()
    assert not row.is_hidden and row.present_in_scan
    (directory / 'report.yaml').unlink()
    sync_studio_registry(studio_tree)
    row.refresh_from_db()
    assert not row.present_in_scan and not row.archived_in_scan


def test_retired_routes_have_no_write_controls(login, org_admin, studio, rows):
    client = login(org_admin)
    for path in ['/hidden-expired', '/r/ordinary/availability']:
        assert client.get(base(studio) + path).status_code == 404
        assert client.post(base(studio) + path, {'hidden': '', 'expires_at': ''}).status_code in (404, 405)
    rows[0].refresh_from_db()
    assert rows[0].hidden is None and rows[0].expires_at is None
    assert b'/availability' not in client.get(base(studio) + '/r/ordinary/access').content


@pytest.mark.parametrize('role', [roles.ADMIN, roles.DEVELOPER, roles.VIEWER])
def test_bearer_keys_keep_role_and_tenant_boundaries(client, member, studio, org, other_org, rows, grant_studio, role):
    from apps.accounts.models import ApiKey
    grant_studio(member, studio, role)
    org.api_keys_enabled = True
    org.save(update_fields=['api_keys_enabled'])
    _, secret = ApiKey.mint(user=member, org=org, name='Discovery read', scopes=ApiKey.READ)
    headers = {'HTTP_AUTHORIZATION': f'Bearer {secret}'}
    for page in ['', '&page=1']:
        actual = slugs(client.get(base(studio) + '/api/registry?include_unavailable=1' + page, **headers))
        assert ('hidden' in actual) is (role == roles.ADMIN)
    # A superuser's key is also bound to the issuing organization.
    from django.contrib.auth import get_user_model
    root = get_user_model().objects.create_superuser(email='key-root@example.test', password='test')
    from apps.orgs.models import OrgMembership
    OrgMembership.objects.create(org=other_org, user=root, role=roles.ORG_ADMIN)
    other_org.api_keys_enabled = True
    other_org.save(update_fields=['api_keys_enabled'])
    _, foreign = ApiKey.mint(user=root, org=other_org, name='Other org', scopes=ApiKey.READ)
    for page in ['', '&page=1']:
        response = client.get(base(studio) + '/api/registry?include_unavailable=1' + page,
                              HTTP_AUTHORIZATION=f'Bearer {foreign}')
        assert response.status_code in (403, 404)
