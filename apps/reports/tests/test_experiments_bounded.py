"""Complete lifecycle/global result facts with bounded presentation and no I/O."""
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
    assert overview(studio, params={'status': status})['filtered_count'] == 1


@pytest.mark.parametrize('delta,ci,higher,expected_win', [(3,(1,5),True,1),(-3,(-5,-1),False,1),(-3,(-5,-1),True,0),(3,(-1,5),True,0),(0,(-1,1),True,0),(0,None,True,0)])
def test_rate_counts_valid_zero_and_nonsignificant_results(studio, experiment, delta, ci, higher, expected_win):
    today = timezone.now().date()
    experiment(entries={'start_date': (today-timedelta(days=20)).isoformat(), 'end_date': (today-timedelta(days=2)).isoformat()}, payload=payload(delta, ci, higher, label=''))
    data = overview(studio)
    assert data['tiles']['concluded_with_metric'] == 1
    assert data['tiles']['wins'] == expected_win
    assert data['rows'][0]['primary']['delta_pct'] == delta


@pytest.mark.parametrize('mutation', ('expired', 'uncertain', 'unsupported', 'stale', 'future', 'digest', 'payload', 'nan', 'infinity', 'bad-ci', 'missing-delta'))
def test_invalid_or_noncurrent_results_never_enter_denominator(studio, experiment, mutation):
    now = timezone.now()
    report, summary, rows = experiment(entries={'start_date': (now.date()-timedelta(days=20)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}, payload=payload())
    changes = {'expired': {'state':'expired'}, 'uncertain': {'state':'uncertain'}, 'unsupported': {'schema_version':3}, 'stale': {'observed_at':now-timedelta(seconds=301)}, 'future': {'observed_at':now+timedelta(seconds=5)}, 'digest': {'declaration_digest':'changed'}}
    if mutation in changes:
        ReportSourceSummary.objects.filter(pk=summary.pk).update(**changes[mutation])
    else:
        updates = {'payload': {'payload_state':'invalid'}, 'nan': {'delta_pct':float('nan')}, 'infinity': {'delta_pct':float('inf')}, 'bad-ci': {'ci_low':10, 'ci_high':2}, 'missing-delta': {'delta_pct':None}}
        ReportExperimentSummary.objects.filter(pk=rows[0].pk).update(**updates[mutation])
    data = overview(studio, now=now)
    assert data['tiles']['concluded'] == 1
    assert data['tiles']['concluded_with_metric'] == 0
    assert data['tiles']['excluded'] == 1
    assert data['rows'][0]['primary'] is None


@pytest.mark.parametrize('mutation', ('schema', 'stale', 'future', 'digest', 'quality', 'state'))
def test_not_built_requires_supported_fresh_observation(studio, experiment, mutation):
    _, summary, _ = experiment()
    assert overview(studio)['rows'][0]['result_state'] == 'Not built yet'
    updates = {'schema': {'schema_version':3}, 'stale': {'observed_at':timezone.now()-timedelta(seconds=301)}, 'future': {'observed_at':timezone.now()+timedelta(seconds=2)}, 'digest': {'declaration_digest':'changed'}, 'quality': {'quality':'unknown'}, 'state': {'state':'expired'}}
    ReportSourceSummary.objects.filter(pk=summary.pk).update(**updates[mutation])
    assert overview(studio)['rows'][0]['result_state'] == 'Observation unavailable'


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


def test_offpage_global_totals_and_independent_url_pages(studio, experiment):
    now = timezone.now()
    concluded = {'start_date': (now.date()-timedelta(days=30)).isoformat(), 'end_date': (now.date()-timedelta(days=2)).isoformat()}
    for i in range(55):
        experiment(f'report-{i:03}', entries=concluded, payload=payload())
    data = overview(studio, params={'page':'2', 'timeline_page':'3'})
    assert len(data['rows']) == 25 and len(data['timeline']['lanes']) == 5
    assert data['tiles']['wins'] == 55 and data['tiles']['concluded_with_metric'] == 55
    assert data['filtered_count'] == 55 and data['timeline_page']['total'] == 55
    assert 'timeline_page=3' in data['table_page']['next'] and 'page=2' in data['timeline_page']['previous']
    searched = overview(studio, params={'q':'report-054'})
    assert searched['filtered_count'] == 1 and searched['rows'][0]['slug'] == 'report-054'
    assert searched['tiles']['wins'] == 55
    assert all('page=' not in f['url'] for f in data['filters'])
    pathological = overview(studio, params={'page':'9'*5000, 'timeline_page':'-99'})
    assert pathological['table_page']['number'] == pathological['timeline_page']['number'] == 1


def test_multideclaration_names_global_search_and_current_digest(studio, experiment):
    _, summary, rows = experiment(entries=[{'name':'Z named', 'test_name':'first'}, {'test_name':'A declared'}, {}], payload={'components':{}})
    data = overview(studio)
    assert {r['name'] for r in data['rows']} == {'A declared','Z named','experiment'}
    assert overview(studio, params={'q':'named'})['filtered_count'] == 1
    assert overview(studio, params={'q':'first'})['filtered_count'] == 1
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
        assert len(response.content) <= 160*1024
        elements=Elements();elements.feed(response.content.decode())
        assert elements.rows<=25 and elements.lanes<=25 and elements.nodes<=1800
        read_sql = '\n'.join(q['sql'] for q in queries if q['sql'].lstrip().upper().startswith('SELECT'))
        assert '"config"' not in read_sql and '"runtime"' not in read_sql and '"description"' not in read_sql


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


def test_missing_payload_no_kpi_and_observation_are_distinct(studio, experiment):
    _, summary, rows = experiment('missing-payload', payload={'components':{}})
    experiment('no-kpi', payload={'components':{'one':{'type':'ab_compare','header':{},'body':{'kpi_rows':[]}}}})
    _, unavailable, _ = experiment('unavailable', payload=payload())
    ReportSourceSummary.objects.filter(pk=unavailable.pk).update(observed_at=timezone.now()-timedelta(seconds=301))
    by_slug={r['slug']:r for r in overview(studio)['rows']}
    assert by_slug['missing-payload']['result_state']=='No A/B payload in the observed build'
    assert by_slug['no-kpi']['result_state']=='No KPI rows in the payload'
    assert by_slug['unavailable']['result_state']=='Observation unavailable'


def test_running_filter_includes_past_end_and_timeline_excludes_backward_bars(studio, experiment):
    today=timezone.now().date()
    experiment('past-end', entries={'start_date':(today-timedelta(days=20)).isoformat(),'end_date':(today-timedelta(days=2)).isoformat()}, schedule_cron='0 9 * * *')
    experiment('running', entries={'start_date':(today-timedelta(days=5)).isoformat()})
    experiment('backward', entries={'start_date':(today-timedelta(days=5)).isoformat(),'end_date':(today-timedelta(days=10)).isoformat()})
    experiment('scheduled', entries={'start_date':(today+timedelta(days=5)).isoformat()})
    data=overview(studio,params={'status':'running'})
    assert data['filtered_count']==2 and data['tiles']['running']==2
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
