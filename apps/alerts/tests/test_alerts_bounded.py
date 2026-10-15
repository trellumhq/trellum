"""Bounds on the Alerts pages are invisible: no paging, search or hint UI at realistic sizes."""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.alerts.models import AlertRule
from apps.alerts.tests.test_views import _form, _main
from apps.reports.models import Report
from apps.orgs.models import PermissionGroup, PermissionGroupMembership

pytestmark = pytest.mark.django_db


def test_rule_list_shows_every_rule_without_toolbar_or_pager(rule, login):
    AlertRule.objects.bulk_create([AlertRule(org=rule.org, studio=rule.studio, report=rule.report,
        created_by=rule.created_by, name=f'Rule {i:03}', recipient_roles=['developers']) for i in range(50)])
    response = login(rule.created_by).get(f'/s/{rule.org.slug}/{rule.studio.slug}/alerts')
    assert len(response.context['rows']) == 51
    html = _main(response)
    assert 'Rule 049' in html
    for text in ('Search alerts', 'rules</span>', 'Page 1 of', 'Previous', 'Next', 'ui-pager'):
        assert text not in html


def test_rule_list_has_a_silent_sql_cap(rule, login, monkeypatch):
    monkeypatch.setattr('apps.alerts.views.RULES_LIMIT', 10)
    AlertRule.objects.bulk_create([AlertRule(org=rule.org, studio=rule.studio, report=rule.report,
        created_by=rule.created_by, name=f'Rule {i:03}', recipient_roles=['developers']) for i in range(30)])
    response = login(rule.created_by).get(f'/s/{rule.org.slug}/{rule.studio.slug}/alerts')
    assert len(response.context['rows']) == 10
    assert 'Rule 000' in _main(response)


def test_report_choices_are_bounded_and_selected_is_independent(rule, login):
    Report.objects.bulk_create([Report(studio=rule.studio, slug=f'report-{i:03}', name=f'Report {i:03}') for i in range(60)])
    analysis = Report.objects.create(studio=rule.studio, slug='analysis', name='Analysis', kind='analysis')
    selected = Report.objects.filter(studio=rule.studio).order_by('-pk').exclude(pk=analysis.pk).first()
    url = f'/s/{rule.org.slug}/{rule.studio.slug}/alerts/report-choices'
    response = login(rule.created_by).get(url, {'selected': selected.pk, 'q': 'Report 000'})
    data = response.json()
    assert response['Cache-Control'] == 'private, no-store'
    assert len(data['results']) == 1
    assert data['selected']['value'] == str(selected.pk)
    assert data['pagination']['total'] == 1
    assert len(login(rule.created_by).get(url).json()['results']) == 25


def test_list_and_form_query_count_do_not_grow_per_rule_or_group(rule, login, make_user, grant_studio):
    people = []
    for i in range(100):
        person = make_user(f'grow-{i:03}@example.test', org=rule.org)
        grant_studio(person, rule.studio, 'viewer'); people.append(person)
        group = PermissionGroup.objects.create(org=rule.org, name=f'Group {i:03}')
        PermissionGroupMembership.objects.create(group=group, user=person)
    rules = [rule] + AlertRule.objects.bulk_create([AlertRule(org=rule.org, studio=rule.studio, report=rule.report,
        created_by=rule.created_by, name=f'Rule {i:03}', recipient_roles=['developers']) for i in range(49)])
    for item in rules:
        item.recipients.set(people)
    client = login(rule.created_by)
    prefix = f'/s/{rule.org.slug}/{rule.studio.slug}/alerts'
    for path, ceiling in [(prefix, 40), (prefix+'/new', 45), (prefix+f'/{rule.pk}/edit', 45), (prefix+f'/{rule.pk}', 40)]:
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(path)
        assert response.status_code == 200
        assert len(queries) <= ceiling, (path, len(queries))


def test_ordinary_studio_lists_every_choice_and_never_switches_to_remote_search(rule, login, make_user, grant_studio):
    for i in range(30):
        Report.objects.create(studio=rule.studio, slug=f'plain-{i:03}', name=f'Plain {i:03}')
        person = make_user(f'plain-{i:03}@example.test', org=rule.org)
        grant_studio(person, rule.studio, 'viewer')
        PermissionGroup.objects.create(org=rule.org, name=f'Plain group {i:03}')
    html = _main(login(rule.created_by).get(f'/s/{rule.org.slug}/{rule.studio.slug}/alerts/new'))
    assert html.count('type="checkbox" name="recipients"') >= 30 and html.count('name="recipient_groups"') == 30
    assert html.count('Plain 0') == 30
    for marker in ('data-choice-source', 'data-recipient-source', 'data-picker-fallback', 'data-report-native-choices',
                   'data-browse-choices', 'browse_choices', 'Search recipients', 'Browse choices', 'reportSearch'):
        assert marker not in html


def test_only_a_huge_studio_renders_a_capped_list_with_remote_sources(rule, login, make_user, grant_studio, monkeypatch):
    monkeypatch.setattr('apps.reports.recipient_choices.CHOICE_LIMIT', 5)
    Report.objects.bulk_create([Report(studio=rule.studio, slug=f'huge-{i:03}', name=f'Huge {i:03}') for i in range(20)])
    people = []
    for i in range(8):
        person = make_user(f'huge-{i:03}@example.test', org=rule.org)
        grant_studio(person, rule.studio, 'viewer'); people.append(person)
    late = Report.objects.get(slug='huge-019')
    prefix = f'/s/{rule.org.slug}/{rule.studio.slug}/alerts'
    client = login(rule.created_by)
    rule.report = late
    rule.save(update_fields=['report'])
    rule.recipients.set([people[-1]])
    html = _main(client.get(f'{prefix}/{rule.pk}/edit'))
    assert f'data-choice-source="{prefix}/report-choices"' in html
    assert f'data-recipient-source="{prefix}/recipient-choices"' in html
    assert html.count('type="checkbox" name="recipients"') == 6  # five rendered plus the selected person
    assert 'checked' in html.split(f'value="{people[-1].pk}"', 1)[1][:80]
    report_select = html.split('id="id_report"', 1)[1].split('</select>', 1)[0]
    assert f'<option value="{late.pk}" selected>' in report_select
    assert report_select.count('<option value="') == 6  # five rendered plus the selected report


def test_foreign_choices_are_rejected_without_leaking_their_labels(rule, login, make_user):
    from apps.orgs.models import Organization
    from apps.studios.models import Studio
    foreign_org = Organization.objects.create(slug='foreign', name='Foreign')
    foreign = make_user('secret-foreign@example.test', org=foreign_org)
    group = PermissionGroup.objects.create(org=foreign_org, name='Secret foreign group')
    foreign_studio = Studio.objects.create(org=foreign_org, slug='foreign', name='Foreign studio')
    report = Report.objects.create(studio=foreign_studio, slug='secret', name='Secret foreign report')
    response = login(rule.created_by).post(f'/s/{rule.org.slug}/{rule.studio.slug}/alerts/new',
        _form(rule, report=str(report.pk), recipients=[str(foreign.pk)], recipient_groups=[str(group.pk)]))
    assert response.status_code == 200
    html = response.content.decode()
    assert foreign.email not in html and group.name not in html and report.name not in html
    assert response.context['form'].errors
    assert not AlertRule.objects.filter(report=report).exists()
