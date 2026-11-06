"""Report availability is checked against the clock, current roles and tenant."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.core import roles
from apps.core.report_access import ReportAccessBatch, can_view_report
from apps.reports.catalog import registry_page_payload
from apps.reports.models import Report, ReportFavorite, ReportPermissionGrant, ShareLink, OrgSharePolicy
from apps.reports.scan import sync_studio_registry

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 2, 12, tzinfo=dt_timezone.utc)


@pytest.fixture(autouse=True)
def proxy(settings):
    settings.TRELLUM_REPORT_ACCESS_MODEL = 'proxy'
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True


@pytest.fixture
def viewer(member, studio, grant_studio):
    grant_studio(member, studio, roles.VIEWER)
    return member


@pytest.fixture
def developer(make_user, org, studio, grant_studio):
    user = make_user('expiry-dev@example.test', org=org)
    grant_studio(user, studio, roles.DEVELOPER)
    return user


def prefix(studio):
    return f'/s/{studio.org.slug}/{studio.slug}'


def payload(report, user, **params):
    request = SimpleNamespace(user=user, org=report.studio.org, studio=report.studio)
    return registry_page_payload(request, params)


@pytest.mark.parametrize('delta,available', [(None, True), (1, True), (0, False), (-1, False)])
def test_deadline_is_inclusive_for_catalog_content_and_batch(report_row, viewer, org_admin, monkeypatch, delta, available):
    monkeypatch.setattr(timezone, 'now', lambda: NOW)
    report_row.expires_at = NOW + timedelta(microseconds=delta) if delta is not None else None
    report_row.save(update_fields=['expires_at'])
    for user in (viewer, org_admin):
        assert bool(payload(report_row, user)['reports']) is available
    assert can_view_report(viewer, report_row) is available
    assert ReportAccessBatch([viewer], report_row.studio.org).can_view(viewer, report_row) is available
    assert can_view_report(org_admin, report_row)
    assert ReportAccessBatch([org_admin], report_row.studio.org).can_view(org_admin, report_row)


def test_catalog_favorites_recent_search_and_jump_omit_both_even_for_admin(login, org_admin, report_row):
    studio = report_row.studio
    rows = [Report.objects.create(studio=studio, slug=slug, name=slug, category='Archive category', **fields)
            for slug, fields in [('hiddenneedle', {'archived_in_scan': True}), ('expiredneedle', {'expires_at': NOW})]]
    for row in rows:
        ReportFavorite.objects.create(user=org_admin, report=row)
    ReportFavorite.objects.create(user=org_admin, report=report_row)
    client = login(org_admin)
    for params in ({}, {'tab': '_favorites'}, {'tab': '_recent', 'recent': [r.slug for r in rows]}, {'q': 'needle'}):
        result = payload(report_row, org_admin, **params)
        assert not {r['slug'] for r in result['reports']} & {r.slug for r in rows}
    assert [f['slug'] for f in client.get('/api/me/favorites').json()['favorites']] == [report_row.slug]
    assert not {r['value'] for r in client.get(prefix(studio) + '/api/jump').json()['results']} & {r.slug for r in rows}


def test_private_audience_is_not_hidden_and_stays_restricted(login, viewer, org_admin, report_row, settings):
    report_row.audience = Report.AUDIENCE_PRIVATE
    report_row.save(update_fields=['audience'])
    assert not payload(report_row, viewer)['reports']
    assert payload(report_row, org_admin)['reports']
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    assert not can_view_report(viewer, report_row)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert not can_view_report(org_admin, report_row)  # expiry does not bypass Private readiness


def test_direct_assets_live_queries_shares_and_embeds_cannot_bypass_expiry(login, viewer, org_admin, report_row, settings):
    out = report_row.studio.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.html').write_text('<html><head></head><body>secret report</body></html>')
    (out / 'data.json').write_text('{"secret": 42}')
    OrgSharePolicy.objects.create(org=report_row.studio.org, share_links_enabled=True, embed_links_enabled=True)
    links = [ShareLink.objects.create(report=report_row, created_by=org_admin, embed=embed) for embed in (False, True)]
    client = login(viewer)
    base = prefix(report_row.studio)
    assert client.get(f'{base}/r/{report_row.slug}/data.json').status_code == 200
    report_row.expires_at = timezone.now()
    report_row.save(update_fields=['expires_at'])
    for path in (f'/r/{report_row.slug}/', f'/r/{report_row.slug}/index.html', f'/r/{report_row.slug}/data.json',
                 f'/api/reports/{report_row.slug}/metrics', f'/api/report-shell?report={report_row.slug}'):
        assert client.get(base + path).status_code == 404, path
    assert client.post(f'{base}/api/reports/{report_row.slug}/live-query', '{}', content_type='application/json').status_code == 404
    for link in links:
        for suffix in ('', 'data.json'):
            assert client.get(f'/share/{link.token}/{suffix}').status_code == 410
    assert login(org_admin).get(f'{base}/r/{report_row.slug}/data.json').status_code == 200
    assert (out / 'data.json').is_file() and report_row.data_expired_at is None


def test_developer_keeps_expired_report_management_but_not_content(login, developer, report_row):
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    client = login(developer)
    base = prefix(report_row.studio)
    assert client.get(f'{base}/api/reports/{report_row.slug}/status').status_code == 200
    assert client.get(f'{base}/r/{report_row.slug}/').status_code == 404
    assert client.get(base + '/api/operations/catalog').json()['rows'][0]['slug'] == report_row.slug


def test_scan_indexes_hidden_reports_but_not_incomplete_drafts(studio_tree, write_report):
    write_report('hidden', hidden=True)
    draft = write_report('draft', hidden=True)
    (draft / 'generator.py').unlink()
    write_report('disabled', disabled=True)
    from trellum.runner import scan_report_configs
    assert scan_report_configs(str(studio_tree.reports_dir)) == []
    assert sync_studio_registry(studio_tree) == 2
    assert studio_tree.reports.get(slug='disabled').disabled
    assert not studio_tree.reports.filter(slug='draft').exists()
    row = studio_tree.reports.get(slug='hidden')
    assert not row.present_in_scan and row.archived_in_scan and row.is_hidden
    row.hidden = False
    row.expires_at = NOW
    row.save(update_fields=['hidden', 'expires_at'])
    sync_studio_registry(studio_tree)
    row.refresh_from_db()
    assert row.is_hidden and row.expires_at == NOW


@pytest.mark.parametrize('model', ['edge-signed', 'edge-external'])
def test_edge_modes_reject_expiry_and_fail_closed_when_configured(login, org_admin, report_row, settings, model):
    settings.TRELLUM_REPORT_ACCESS_MODEL = model
    report_row.expires_at = timezone.now() + timedelta(days=1)
    report_row.save(update_fields=['expires_at'])
    assert not can_view_report(org_admin, report_row)
    assert not ReportAccessBatch([org_admin], report_row.studio.org).can_view(org_admin, report_row)
    client = login(org_admin)
    base = prefix(report_row.studio)
    assert client.get(f'{base}/r/{report_row.slug}/').status_code == 503
    assert client.get(f'{base}/r/{report_row.slug}/data.json').status_code == 503


def test_hidden_keeps_portal_restriction_but_does_not_revoke_existing_share(login, viewer, org_admin, report_row):
    report_row.archived_in_scan = True
    report_row.save(update_fields=['archived_in_scan'])
    out = report_row.studio.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / 'data.json').write_text('{"original": 1}')
    policy = OrgSharePolicy.objects.create(org=report_row.studio.org, share_links_enabled=True)
    link = ShareLink.objects.create(report=report_row, created_by=org_admin)
    client = login(viewer)
    assert client.get(prefix(report_row.studio) + f'/r/{report_row.slug}/data.json').status_code == 404
    assert link.is_active_under(policy)
    assert client.get(f'/share/{link.token}/data.json').status_code == 200
    assert login(org_admin).get(prefix(report_row.studio) + f'/r/{report_row.slug}/data.json').status_code == 200


def test_selected_private_viewer_loses_direct_and_tool_access_at_expiry(login, member, report_row, make_group, attach_group):
    from apps.assistant.tools import AssistantToolbox

    report_row.audience = Report.AUDIENCE_PRIVATE
    report_row.save(update_fields=['audience'])
    group = make_group('Assigned', grants=[(report_row.studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=['viewer_scope'])
    ReportPermissionGrant.objects.create(report=report_row, grant=grant)
    attach_group(member, group)
    assert can_view_report(member, report_row)
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    assert not can_view_report(member, report_row)
    assert login(member).get(prefix(report_row.studio) + f'/r/{report_row.slug}/').status_code == 404
    toolbox = AssistantToolbox(report_row.studio, actor=member, scope='report', report=report_row)
    assert toolbox._access_error(report_row.slug)


def test_full_studio_assistant_cannot_read_expired_report_data(viewer, org_admin, report_row):
    from apps.assistant.tools import AssistantToolbox

    out = report_row.studio.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / 'data.json').write_text('{"secret": 42}')
    toolbox = AssistantToolbox(report_row.studio, actor=viewer, scope='full')
    assert toolbox._access_error(report_row.slug) is None
    # Cached/prechecked execution must still check the current report deadline
    # at each file read, including in a full-studio conversation.
    toolbox._executing_prechecked = True
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    assert toolbox._access_error(report_row.slug)
    assert toolbox.read_data(out / 'data.json') is None
    assert not toolbox.reports()
    assert AssistantToolbox(report_row.studio, actor=org_admin, scope='full')._access_error(report_row.slug) is None


def test_expiry_keeps_developer_delivery_cleanup_controls(login, developer, viewer, report_row):
    from apps.reports.models import EmailSchedule

    schedule = EmailSchedule.objects.create(report=report_row, created_by=developer)
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    base = prefix(report_row.studio)
    url = f'{base}/api/reports/{report_row.slug}/schedules/{schedule.pk}'
    assert login(viewer).delete(url).status_code == 404
    client = login(developer)
    assert client.get(f'{base}/api/reports/{report_row.slug}/schedules').status_code == 200
    assert client.delete(url).status_code == 200
    assert not EmailSchedule.objects.filter(pk=schedule.pk).exists()


def test_source_analysis_cannot_bypass_expiry_with_an_alias_directory(viewer, report_row):
    from apps.assistant.tools import AssistantToolbox

    source = report_row.studio.reports_dir / 'source-alias'
    source.mkdir(parents=True)
    (source / 'report.yaml').write_text(f'slug: {report_row.slug}\nkind: analysis\n')
    (source / 'content.md').write_text('Confidential analysis')
    tool = AssistantToolbox(report_row.studio, actor=viewer, scope='full')
    assert 'Confidential analysis' in tool._read_doc({'path': 'reports/source-alias/content.md'})
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    assert 'Confidential analysis' not in tool._read_doc({'path': 'reports/source-alias/content.md'})


def test_manual_hiding_keeps_full_studio_source_policy(viewer, report_row):
    from apps.assistant.tools import AssistantToolbox

    source = report_row.studio.reports_dir / report_row.slug
    (source / 'content.md').write_text('Studio source under its existing source-sharing policy')
    report_row.archived_in_scan = True
    report_row.present_in_scan = False
    report_row.save(update_fields=['archived_in_scan', 'present_in_scan'])
    tool = AssistantToolbox(report_row.studio, actor=viewer, scope='full')
    assert 'Studio source' in tool._read_doc({'path': f'reports/{report_row.slug}/content.md'})
    report_row.expires_at = NOW
    report_row.save(update_fields=['expires_at'])
    assert 'Studio source' not in tool._read_doc({'path': f'reports/{report_row.slug}/content.md'})
