"""Bounded presentation never narrows authorized submitted choices."""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.alerts.models import AlertRule, AlertRun
from apps.alerts.tests.test_views import _form
from apps.reports.models import Report
from apps.orgs.models import PermissionGroup, PermissionGroupMembership

pytestmark = pytest.mark.django_db


def test_rule_list_pages_searches_and_keeps_latest_live_result(rule, login):
    AlertRule.objects.bulk_create([AlertRule(org=rule.org, studio=rule.studio, report=rule.report,
        created_by=rule.created_by, name=f'Rule {i:03}', recipient_roles=['developers']) for i in range(50)])
    client = login(rule.created_by)
    url = f'/s/{rule.org.slug}/{rule.studio.slug}/alerts'
    response = client.get(url)
    assert response.context['page'].paginator.count == 51
    assert len(response.context['rows']) == 25
    assert 'aria-label="Alert pages"' in response.content.decode().split('<main', 1)[1]
    assert len(client.get(url, {'page': 3}).context['rows']) == 1
    result = client.get(url, {'q': 'Rule 049'})
    assert result.context['page'].paginator.count == 1
    assert result.context['rows'][0]['rule'].name == 'Rule 049'


def test_form_browse_retains_off_page_draft_without_save_or_preview(rule, login, make_user, grant_studio, monkeypatch):
    Report.objects.bulk_create([Report(studio=rule.studio, slug=f'late-{i:03}', name=f'Late {i:03}') for i in range(50)])
    people = [make_user(f'late-{i:03}@example.test', org=rule.org) for i in range(50)]
    for person in people:
        grant_studio(person, rule.studio, 'viewer')
    report = Report.objects.filter(studio=rule.studio).order_by('-pk').first()
    def no_evaluation(*args, **kwargs):
        raise AssertionError('Browsing must not evaluate')
    monkeypatch.setattr('apps.alerts.evaluator.evaluate_snapshot', no_evaluation)
    url = f'/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/edit'
    body = _form(rule, name='Unsaved draft', instructions='Retain this draft', report=str(report.pk),
                 recipients=[str(people[-1].pk)], timezone='Europe/Amsterdam', action='browse_choices', people_page='2', report_page='2')
    response = login(rule.created_by).post(url, body)
    assert response.status_code == 200
    form = response.context['form']
    assert form.data['instructions'] == 'Retain this draft'
    assert len(form.fields['report'].choices) <= 26
    assert any(str(value) == str(report.pk) for value, label in form.fields['report'].choices)
    assert any(str(value) == str(people[-1].pk) for value, label in form.fields['recipients'].choices)
    rule.refresh_from_db()
    assert rule.name != 'Unsaved draft'
    assert not AlertRun.objects.exists()
    body['action'] = 'save'  # Real Save submitter wins even with retained page state.
    response = login(rule.created_by).post(url, body)
    assert response.status_code == 302
    rule.refresh_from_db()
    assert rule.report_id == report.pk and rule.timezone == 'Europe/Amsterdam'
    assert list(rule.recipients.values_list('pk', flat=True)) == [people[-1].pk]


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


def test_inactive_alert_choices_preserve_prior_studio_form_contract(rule, login, make_user, grant_studio):
    person = make_user('inactive-choice@example.test', org=rule.org)
    grant_studio(person, rule.studio, 'viewer')
    person.is_active = False; person.save(update_fields=['is_active'])
    client = login(rule.created_by)
    prefix = f'/s/{rule.org.slug}/{rule.studio.slug}'
    choices = client.get(prefix+'/alerts/recipient-choices', {'q': 'inactive-choice'}).json()
    assert [row['id'] for row in choices['members']] == [person.pk]
    report_choices = client.get(prefix+f'/api/reports/{rule.report.slug}/recipients', {'q': 'inactive-choice'}).json()
    assert report_choices['members'] == []
    response = client.post(prefix+f'/alerts/{rule.pk}/edit', _form(rule, recipients=[str(person.pk)], action='save', people_page='2'))
    assert response.status_code == 302
    assert rule.recipients.filter(pk=person.pk).exists()


def test_native_unavailable_selections_keep_ids_without_foreign_labels(rule, login, make_user):
    from apps.orgs.models import Organization
    foreign_org = Organization.objects.create(slug='foreign', name='Foreign')
    foreign = make_user('secret-foreign@example.test', org=foreign_org)
    group = PermissionGroup.objects.create(org=foreign_org, name='Secret foreign group')
    from apps.studios.models import Studio
    foreign_studio = Studio.objects.create(org=foreign_org, slug='foreign', name='Foreign studio')
    report = Report.objects.create(studio=foreign_studio, slug='secret', name='Secret foreign report')
    response = login(rule.created_by).post(f'/s/{rule.org.slug}/{rule.studio.slug}/alerts/new',
        _form(rule, report=str(report.pk), recipients=[str(foreign.pk)], recipient_groups=[str(group.pk)]))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'Selected person is unavailable' in html and 'Selected group is unavailable' in html and 'Selected report is unavailable' in html
    assert foreign.email not in html and group.name not in html and report.name not in html
    from html.parser import HTMLParser
    class Selection(HTMLParser):
        def __init__(self): super().__init__(); self.values=set(); self.options=set()
        def handle_starttag(self, tag, attrs):
            values=dict(attrs)
            if tag=='input' and 'checked' in values: self.values.add((values.get('name'),values.get('value')))
            if tag=='option' and 'selected' in values: self.options.add(values.get('value'))
    selected=Selection(); selected.feed(html)
    assert ('recipients',str(foreign.pk)) in selected.values
    assert ('recipient_groups',str(group.pk)) in selected.values
    assert str(report.pk) in selected.options
    assert response.context['form'].errors
