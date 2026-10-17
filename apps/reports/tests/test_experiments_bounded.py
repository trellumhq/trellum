"""Complete lifecycle/global result facts with bounded presentation and no I/O."""
import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.reports.models import Report, ReportSourceSummary, ReportExperimentSummary
from apps.reports.overviews.experiment_queries import overview
from apps.reports.report_experiment_projection import declared_rows, project_results

pytestmark = pytest.mark.django_db


@pytest.fixture
def experiment(studio):
    def make(slug='experiment', entries=None, payload=None, **report_fields):
        entries = entries if entries is not None else {'start_date': (timezone.now().date()-timedelta(days=10)).isoformat()}
        report = Report.objects.create(studio=studio, slug=slug, name=slug, config={'ab_test': entries}, **report_fields)
        if payload is None:
            rows, digest = declared_rows(report.config)
        else:
            rows, digest = project_results(report.config, payload, list(payload['components'].values()))
        summary, _ = ReportSourceSummary.objects.update_or_create(report=report, defaults=dict(schema_version=2, declaration_digest=digest, experiment_state='ready' if payload else 'missing', state='ready' if payload else 'missing', quality='observed', observed_at=timezone.now(), has_output=bool(payload)))
        summary.experiments.all().delete()
        rows = ReportExperimentSummary.objects.bulk_create([ReportExperimentSummary(summary=summary, **r) for r in rows])
        return report, summary, rows
    return make


def payload(delta=3, ci=(1, 5), higher=True, label='Revenue'):
    return {'components': {'first': {'type': 'ab_compare', 'header': {}, 'body': {'kpi_rows': [{'metric': label, 'delta_pct': delta, 'ci_pct': ci, 'higher_is_better': higher}]}}}}


@pytest.mark.parametrize('kind', ('unknown', 'scheduled', 'running', 'end-today', 'concluded', 'past_end', 'disabled', 'closed', 'invalid-date'))
def test_typed_global_lifecycle_matches_pure_rule(studio, experiment, kind):
    from apps.reports.overviews.experiments import derive_status
    today = timezone.now().date()
    cfg = {'start_date': (today-timedelta(days=10)).isoformat()}
    extra = {}
    if kind in ('concluded', 'past_end', 'disabled', 'closed'):
        cfg['end_date'] = (today-timedelta(days=2)).isoformat()
    if kind in ('past_end', 'disabled', 'closed'):
        extra['schedule_cron'] = '0 9 * * *'
    if kind == 'disabled':
        extra['disabled'] = True
    if kind == 'closed':
        cfg['closed'] = True
    if kind == 'unknown':
        cfg.pop('start_date')
    if kind == 'invalid-date':
        cfg['start_date'] = 'not-a-date'
    if kind == 'scheduled':
        cfg['start_date'] = (today+timedelta(days=1)).isoformat()
    if kind == 'end-today':
        cfg['end_date'] = today.isoformat()
    report, _, _ = experiment(entries=cfg, **extra)
    expected = derive_status(cfg, today, schedule_active=not report.disabled and bool(report.schedule_cron))
    data = overview(studio)
    assert data['rows'][0]['status'] == expected['status']
    status = expected['status']
    assert data['tiles']['running' if status == 'past_end' else status] == 1
    assert len(overview(studio, params={'status': status})['rows']) == 1


@pytest.mark.parametrize('delta,ci,higher,expected_win', [(3,(1,5),True,1),(-3,(-5,-1),False,1),(-3,(-5,-1),True,0),(3,(-1,5),True,0),(0,(-1,1),True,0),(0,None,True,0)])
def test_rate_counts_valid_zero_and_nonsignificant_results(studio, experiment, delta, ci, higher, expected_win):
    today = timezone.now().date()
    experiment(entries={'start_date': (today-timedelta(days=20)).isoformat(), 'end_date': (today-timedelta(days=2)).isoformat()}, payload=payload(delta, ci, higher, label=''))
    data = overview(studio)
    assert data['tiles']['concluded_with_metric'] == 1
    assert data['tiles']['wins'] == expected_win
    assert data['rows'][0]['primary']['delta_pct'] == delta


@pytest.mark.parametrize('mutation', ('expired', 'uncertain', 'unsupported', 'digest', 'payload', 'nan', 'infinity', 'bad-ci'))
def test_invalid_or_noncurrent_results_never_enter_denominator(studio, experiment, mutation):
    now = timezone.now()
    report, summary, rows = experiment(entries={'start_date': (now.date()-timedelta(days=20)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}, payload=payload())
    changes = {'expired': {'state':'expired'}, 'uncertain': {'state':'uncertain'}, 'unsupported': {'schema_version':3}, 'digest': {'declaration_digest':'changed'}}
    if mutation in changes:
        ReportSourceSummary.objects.filter(pk=summary.pk).update(**changes[mutation])
    else:
        updates = {'payload': {'payload_state':'invalid'}, 'nan': {'delta_pct':float('nan')}, 'infinity': {'delta_pct':float('inf')}, 'bad-ci': {'ci_low':10, 'ci_high':2}}
        ReportExperimentSummary.objects.filter(pk=rows[0].pk).update(**updates[mutation])
    data = overview(studio, now=now)
    assert data['tiles']['concluded'] == 1
    assert data['tiles']['concluded_with_metric'] == 0
    assert data['rows'][0]['primary'] is None


def test_missing_delta_is_shown_as_a_dash_but_never_judged(studio, experiment):
    now = timezone.now()
    _, _, rows = experiment(entries={'start_date': (now.date()-timedelta(days=20)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}, payload=payload())
    ReportExperimentSummary.objects.filter(pk=rows[0].pk).update(delta_pct=None)
    data = overview(studio, now=now)
    assert data['tiles']['concluded_with_metric'] == 0 and data['tiles']['win_rate'] is None
    assert data['rows'][0]['primary']['delta_pct'] is None
    assert data['rows'][0]['primary']['metric'] == 'Revenue'


def test_results_stay_visible_until_a_newer_observation_replaces_them(studio, experiment):
    """Like the latest build the page used to read: no freshness window."""
    now = timezone.now()
    _, summary, _ = experiment(entries={'start_date': (now.date()-timedelta(days=20)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}, payload=payload())
    ReportSourceSummary.objects.filter(pk=summary.pk).update(observed_at=now-timedelta(days=3))
    data = overview(studio, now=now)
    assert data['rows'][0]['primary']['delta_pct'] == 3
    assert data['tiles']['concluded_with_metric'] == 1 and data['tiles']['win_rate'] == 100


@pytest.mark.parametrize('mutation,built,pending', (('schema', True, True), ('digest', True, True), ('quality', True, True), ('pending', True, True), ('uncertain', True, True), ('expired', False, False)))
def test_unobserved_sources_are_not_claimed_as_unbuilt(studio, experiment, mutation, built, pending):
    _, summary, _ = experiment()
    row = overview(studio)['rows'][0]
    assert row['built'] is False and row['pending'] is False
    updates = {'schema': {'schema_version':3}, 'digest': {'declaration_digest':'changed'}, 'quality': {'quality':'unknown'}, 'pending': {'state':'pending'}, 'uncertain': {'state':'uncertain'}, 'expired': {'state':'expired'}}
    ReportSourceSummary.objects.filter(pk=summary.pk).update(**updates[mutation])
    row = overview(studio)['rows'][0]
    assert (row['built'], row['pending']) == (built, pending)


def test_coverage_never_claims_empty_when_unindexed_or_oversized(studio, experiment, member, grant_studio, login):
    report, summary, _ = experiment()
    summary.experiments.all().delete()
    summary.experiment_state = 'oversized'; summary.save()
    Report.objects.create(studio=studio, slug='historical-unindexed')
    data = overview(studio)
    assert data['row_count'] == 0
    assert data['coverage']['oversized'] == 1 and data['coverage']['unindexed'] == 1
    grant_studio(member, studio, roles.VIEWER)
    body = login(member).get(f'/s/{studio.org.slug}/{studio.slug}/experiments').content.decode()
    assert 'not fully indexed' in body and 'No A/B test reports in this studio' not in body


def test_coverage_notice_is_quiet_and_only_for_incomplete_indexes(studio, experiment, member, grant_studio, login):
    experiment('indexed', entries={'start_date': '2026-01-01'})
    grant_studio(member, studio, roles.VIEWER)
    client = login(member)
    url = f'/s/{studio.org.slug}/{studio.slug}/experiments'
    assert 'not indexed yet' not in client.get(url).content.decode()
    Report.objects.create(studio=studio, slug='historical-unindexed')
    body = client.get(url).content.decode()
    assert 'Some reports are not indexed yet, so this list may be incomplete.' in body
    assert 'Declaration coverage' not in body and 'indexed declarations' not in body


def test_a_normal_studio_shows_every_row_and_lane_on_one_page(studio, experiment):
    now = timezone.now()
    concluded = {'start_date': (now.date()-timedelta(days=30)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}
    for i in range(55):
        experiment(f'report-{i:03}', entries=concluded, payload=payload())
    data = overview(studio)
    assert len(data['rows']) == 55 and len(data['timeline']['lanes']) == 55
    assert data['tiles']['wins'] == 55 and data['tiles']['concluded_with_metric'] == 55
    assert data['page'].paginator.num_pages == 1 and data['timeline_truncated'] is False
    # Unsupported controls from the old UI are ignored, not interpreted.
    ignored = overview(studio, params={'q': 'report-054', 'timeline_page': '3'})
    assert len(ignored['rows']) == 55


def test_pager_and_lane_cap_only_apply_far_beyond_a_realistic_studio(studio, experiment, member, grant_studio, login, monkeypatch):
    from apps.reports.overviews import experiment_queries
    now = timezone.now()
    running = {'start_date': (now.date()-timedelta(days=30)).isoformat()}
    for i in range(23):
        experiment(f'report-{i:03}', entries=running, payload=payload())
    monkeypatch.setattr(experiment_queries, 'PAGE_SIZE', 10)
    monkeypatch.setattr(experiment_queries, 'TIMELINE_LANES', 7)
    first = overview(studio)
    assert len(first['rows']) == 10 and first['row_count'] == 23 and first['tiles']['running'] == 23
    assert len(first['timeline']['lanes']) == 7 and first['timeline_truncated'] is True
    assert first['page'].has_next() and not first['page'].has_previous()
    last = overview(studio, params={'page': '3'})
    assert [r['slug'] for r in last['rows']] == ['report-020', 'report-021', 'report-022']
    assert overview(studio, params={'page': '9'*5000})['page'].number == 1
    assert overview(studio, params={'page': '-99'})['page'].number == 1
    filtered = overview(studio, params={'status': 'running', 'page': '3'})
    assert len(filtered['rows']) == 3 and filtered['page'].paginator.count == 23
    grant_studio(member, studio, roles.VIEWER)
    body = login(member).get(f'/s/{studio.org.slug}/{studio.slug}/experiments?status=running&page=2').content.decode()
    assert 'class="ui-pager"' in body and '11–20 of 23' in body and 'status=running' in body
    assert 'Showing the first 7 lanes.' in body


def test_page_has_main_controls_and_no_search_apply_or_paging_rows(studio, experiment, member, grant_studio, login):
    now = timezone.now()
    experiment('running-one', entries={'start_date': (now.date()-timedelta(days=5)).isoformat()}, payload=payload())
    experiment('scheduled-one', entries={'start_date': (now.date()+timedelta(days=5)).isoformat()})
    experiment('done-one', entries={'start_date': (now.date()-timedelta(days=30)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}, payload=payload())
    grant_studio(member, studio, roles.VIEWER)
    page = login(member).get(f'/s/{studio.org.slug}/{studio.slug}/experiments').content.decode()
    body = page.split('<main', 1)[1].split('</main>', 1)[0]  # the shell has its own search box
    pills = re.findall(r'<a class="ui-pill[^"]*"(?: data-console-nav)? href="([^"]*)">([A-Za-z ]+) <span class="n">(\d+)</span>', body)
    assert pills == [('?', 'All', '3'), ('?status=running', 'Running', '1'), ('?status=concluded', 'Concluded', '1')]
    for absent in ('type="search"', '<button', 'Search all experiments', 'matching experiments', 'Page 1 of', 'indexed declarations', 'Declaration coverage', 'ui-pager', 'Observation unavailable'):
        assert absent not in body, absent
    assert 'this studio, all time' in body and '1 of 1 concluded were wins' in body
    assert 'how experiments are read' in body and 'class="design-note"' in body
    assert '>scheduled<' in body  # still a row badge even without a pill


def test_multideclaration_names_and_current_digest(studio, experiment):
    _, summary, rows = experiment(entries=[{'name':'Z named', 'test_name':'first'}, {'test_name':'A declared'}, {}], payload={'components':{}})
    data = overview(studio)
    assert {r['name'] for r in data['rows']} == {'A declared','Z named','experiment'}
    assert all(r['primary'] is None for r in data['rows'])


def test_deleted_not_present_and_cross_tenant_excluded(studio, experiment, org):
    from apps.studios.models import Studio
    experiment('visible')
    report, _, _ = experiment('removed'); report.present_in_scan=False; report.save()
    report, _, _ = experiment('deleted'); report.delete()
    foreign = Studio.objects.create(org=org, slug='foreign', name='Foreign')
    report, _, _ = experiment('foreign'); report.studio=foreign; report.save()
    assert [r['slug'] for r in overview(studio)['rows']] == ['visible']


@pytest.mark.parametrize('count', (20, 200, 1000))
def test_whole_middleware_bounded_no_heavy_columns_or_storage(studio, member, grant_studio, login, monkeypatch, count):
    now = timezone.now()
    reports = Report.objects.bulk_create([Report(studio=studio, slug=f'report-{i:04}', name=f'Report {i:04}', config={'large':'x'*10000}) for i in range(count)])
    summaries = ReportSourceSummary.objects.bulk_create([ReportSourceSummary(report=r, declaration_digest='digest', experiment_state='missing', quality='observed', state='missing', has_output=False, observed_at=now) for r in reports])
    ReportExperimentSummary.objects.bulk_create([ReportExperimentSummary(summary=s, ordinal=0, declaration_digest='digest', start_date=now.date()-timedelta(days=5)) for s in summaries])
    monkeypatch.setattr('apps.core.storage.output_root', lambda *args: pytest.fail('GET storage read'))
    monkeypatch.setattr('apps.core.storage.read_meta', lambda *args: pytest.fail('GET metadata read'))
    from html.parser import HTMLParser
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes=self.rows=self.lanes=0
            self.in_body=False
        def handle_starttag(self,tag,attrs):
            self.nodes+=1
            if tag=='tbody':
                self.in_body=True
            if tag=='tr' and self.in_body:
                self.rows+=1
            if 'tl-lane' in dict(attrs).get('class','').split():
                self.lanes+=1
        def handle_endtag(self,tag):
            if tag=='tbody':
                self.in_body=False
    for role, budget in ((roles.ADMIN,24), (roles.VIEWER,30)):
        from apps.studios.models import StudioMembership
        StudioMembership.objects.update_or_create(user=member, studio=studio, defaults={"role":role})
        client = login(member)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(f'/s/{studio.org.slug}/{studio.slug}/experiments')
        assert response.status_code == 200
        assert len(queries) <= budget
        assert len(response.content) <= 640*1024
        elements=Elements();elements.feed(response.content.decode())
        assert elements.rows<=200 and elements.lanes<=200 and elements.nodes<=5500
        read_sql = '\n'.join(q['sql'] for q in queries if q['sql'].lstrip().upper().startswith('SELECT'))
        assert '"reports_report"."config"' not in read_sql
        assert '"reports_reportsourcesummary"."runtime"' not in read_sql
        assert '"reports_report"."description"' not in read_sql


def test_current_revocation_denies_before_projection(studio, member, grant_studio, login, experiment):
    from apps.studios.models import StudioMembership
    experiment()
    grant_studio(member, studio, roles.VIEWER)
    client=login(member)
    url=f'/s/{studio.org.slug}/{studio.slug}/experiments'
    assert client.get(url).status_code == 200
    StudioMembership.objects.filter(studio=studio, user=member).delete()
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 404 and len(queries) <= 20
    assert not any('reports_reportexperimentsummary' in q['sql'] for q in queries)


def test_missing_payload_no_kpi_and_pending_are_distinct(studio, experiment):
    experiment('missing-payload', payload={'components':{}})
    experiment('no-kpi', payload={'components':{'one':{'type':'ab_compare','header':{},'body':{'kpi_rows':[]}}}})
    _, pending, _ = experiment('pending', payload=payload())
    ReportSourceSummary.objects.filter(pk=pending.pk).update(state='pending')
    by_slug={r['slug']:r for r in overview(studio)['rows']}
    assert by_slug['missing-payload']['payload_missing'] is True and by_slug['missing-payload']['built'] is True
    assert by_slug['no-kpi']['result_state']=='No KPI rows in the payload' and by_slug['no-kpi']['primary'] is None
    assert by_slug['no-kpi']['payload_missing'] is False and not by_slug['no-kpi']['pending']
    assert by_slug['pending']['pending'] is True and by_slug['pending']['primary'] is None


def test_running_filter_includes_past_end_and_timeline_excludes_backward_bars(studio, experiment):
    today=timezone.now().date()
    experiment('past-end', entries={'start_date':(today-timedelta(days=20)).isoformat(),'end_date':(today-timedelta(days=2)).isoformat()}, schedule_cron='0 9 * * *')
    experiment('running', entries={'start_date':(today-timedelta(days=5)).isoformat()})
    experiment('backward', entries={'start_date':(today-timedelta(days=5)).isoformat(),'end_date':(today-timedelta(days=10)).isoformat()})
    experiment('scheduled', entries={'start_date':(today+timedelta(days=5)).isoformat()})
    data=overview(studio,params={'status':'running'})
    assert len(data['rows'])==2 and data['tiles']['running']==2
    assert {r['slug'] for r in data['rows']}=={'past-end','running'}
    all_data=overview(studio)
    assert {lane['slug'] for lane in all_data['timeline']['lanes']}=={'past-end','running'}


def test_selected_viewer_cannot_access_complete_studio_projection(studio, member, make_group, attach_group, login, settings, experiment):
    from apps.orgs.models import PermissionGroupGrant
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY=True
    experiment()
    group=make_group('Limited')
    attach_group(member,group)
    PermissionGroupGrant.objects.create(group=group,studio=studio,role=roles.VIEWER,viewer_scope='selected')
    client=login(member)
    with CaptureQueriesContext(connection) as queries:
        response=client.get(f'/s/{studio.org.slug}/{studio.slug}/experiments')
    assert response.status_code==403 and len(queries)<=20
    assert not any('reports_reportexperimentsummary' in q['sql'] for q in queries)


@pytest.mark.parametrize('result', (True, float('nan'), float('inf'), '3'))
def test_producer_invalid_delta_fixtures_remain_ineligible(studio, experiment, result):
    today=timezone.now().date()
    experiment(entries={'start_date':(today-timedelta(days=20)).isoformat(),'end_date':(today-timedelta(days=2)).isoformat()},payload=payload(result))
    data=overview(studio)
    assert data['tiles']['concluded']==1 and data['tiles']['concluded_with_metric']==0
    assert data['rows'][0]['primary'] is None


def test_rows_follow_the_studio_report_order_then_declared_ordinal(studio, experiment):
    entries=[{'name':'Shared name','test_name':'first'},{'name':'Shared name','test_name':'second'}]
    experiment('z-created-first',entries=entries)
    experiment('a-created-second',entries=entries)
    experiment('prioritised',entries={'start_date':'2026-01-01'},priority=-1)
    data=overview(studio)
    assert [(r['slug'],r['test_name']) for r in data['rows']]==[
        ('prioritised','prioritised'),
        ('a-created-second','first'),('a-created-second','second'),
        ('z-created-first','first'),('z-created-first','second'),
    ]
