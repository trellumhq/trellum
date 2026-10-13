"""Live browser tests collected only by the disposable runner."""
import json
import os
from pathlib import Path
import pytest
import django
if os.environ.get("PORTAL_E2E_ORIGIN"):
    django.setup()

pytestmark = [pytest.mark.portal_e2e, pytest.mark.skipif(not os.environ.get('PORTAL_E2E_ORIGIN'), reason='run scripts/portal_e2e.py')]

@pytest.mark.parametrize('viewport', [{'width': 1440, 'height': 900}, {'width': 390, 'height': 844}], ids=['desktop', 'mobile'])
def test_login_save_reload(viewport):
    from playwright.sync_api import sync_playwright, expect
    from apps.core.tests.portal_e2e.seed import PASSWORD
    from django.contrib.auth import get_user_model
    from django.db import connection
    origin = os.environ['PORTAL_E2E_ORIGIN']
    artifacts = Path(os.environ['PORTAL_E2E_ARTIFACTS'])
    browser_name = os.environ.get('PORTAL_E2E_BROWSER', 'chromium')
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch()
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        context.tracing.start(screenshots=True, snapshots=True)
        try:
            page.goto(origin + '/login')
            page.locator('input[type=email]').fill('alpha-admin@example.invalid')
            page.locator('input[type=password]').fill(PASSWORD)
            page.locator('button[type=submit]').click()
            page.wait_for_url(lambda url: '/login' not in url)
            page.goto(origin + '/account')
            field = page.locator('input[name=name]')
            saved_name = f'Persisted {browser_name} {viewport["width"]}'
            field.fill(saved_name)
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/account')) as saved:
                field.locator('xpath=ancestor::form').locator('button[type=submit]').click()
            assert saved.value.status in (200, 302)
            expect(field).to_have_value(saved_name)
            page.reload()
            expect(page.locator('input[name=name]')).to_have_value(saved_name)
            assert connection.vendor == 'postgresql'
            for endpoint in ('registry', 'system/status', 'system/run-stats'):
                response = context.request.get(origin + '/s/alpha/studio-0/api/' + endpoint)
                assert response.status == 200, (endpoint, response.status, response.text()[:200])
                payload = response.json()
                (artifacts / (endpoint.replace('/', '-') + '.json')).write_text(json.dumps(payload, indent=2))
            page.screenshot(path=str(artifacts / f'{browser_name}-{viewport["width"]}.png'), full_page=True)
        finally:
            context.tracing.stop(path=str(artifacts / f'{browser_name}-{viewport["width"]}-trace.zip'))
            browser.close()
    assert get_user_model().objects.get(email='alpha-admin@example.invalid').name == saved_name

@pytest.mark.parametrize('role', ['org-admin', 'studio-admin', 'developer', 'viewer', 'viewer-full', 'private-viewer', 'group-member', 'group-admin', 'group-selected', 'revoked'])
def test_role_request_baseline(role):
    """Actual login plus permission-separated full HTTP request samples."""
    import requests
    from apps.core.tests.portal_e2e.seed import PASSWORD
    from apps.core.tests.portal_e2e.identities import ROLES
    session = requests.Session()
    session.headers['X-Portal-E2E-Role'] = role
    tenant = 'beta' if role=='viewer-full' else 'alpha'
    other = 'alpha' if tenant=='beta' else 'beta'
    session.headers['X-Portal-E2E-Cache-Target'] = f'/s/{tenant}/studio-0/api/registry'
    origin = os.environ['PORTAL_E2E_ORIGIN']
    session.get(origin + '/login', timeout=10)
    response = session.post(origin + '/login', data={'email': ROLES[role], 'password': PASSWORD, 'csrfmiddlewaretoken': session.cookies['csrftoken']}, headers={'Referer': origin+'/login'}, timeout=10)
    assert response.status_code == 200
    for phase in ('registry-cache-cold', 'registry-cache-warm'):
        session.headers['X-Portal-E2E-Phase'] = phase
        for endpoint in ('registry', 'system/status', 'system/run-stats'):
            url = f'/s/{tenant}/studio-0/api/' + endpoint
            response = session.get(origin + url, timeout=30, allow_redirects=False)
            if role == 'revoked':
                assert response.status_code in (302, 403, 404)
            elif endpoint == 'registry':
                assert response.status_code == 200
            else:
                assert response.status_code == (200 if role in ('org-admin', 'studio-admin', 'developer', 'group-admin') else 403), (role, endpoint, response.status_code)
            if endpoint == 'registry' and role not in ('revoked',):
                payload = response.json()['reports']
                if role == 'group-selected':
                    assert [row['slug'] for row in payload] == ['report-0000']
                if role == 'private-viewer':
                    assert 'report-0004' in {row['slug'] for row in payload}
                if role in ('viewer','group-member'):
                    assert 'report-0004' not in {row['slug'] for row in payload}
            # Retain numeric full-request samples separately from access assertions.
            row = {'role': role, 'cache_phase': phase, 'endpoint': endpoint, 'status': response.status_code, 'http_ms': response.elapsed.total_seconds()*1000, 'response_bytes': len(response.content)}
            with (Path(os.environ['PORTAL_E2E_ARTIFACTS']) / 'roles.jsonl').open('a') as out:
                out.write(json.dumps(row)+'\n')
            denied = session.get(origin + f'/s/{other}/studio-0/api/' + endpoint, timeout=30, allow_redirects=False)
            assert denied.status_code in (302, 403, 404), (role, endpoint, denied.status_code)


def test_mapping_and_seed():
    from apps.core.tests.portal_e2e.manifest import validate
    from apps.core.tests.portal_e2e.seed import seed_manifest
    from apps.reports.models import Report, MetricDefinition, EmailSchedule
    from apps.alerts.models import AlertRule
    from apps.studios.models import Studio
    from apps.reports.overviews.experiments import studio_experiments
    from apps.reports.overviews.annotations import calendar_payload
    from apps.core.tests.portal_e2e.identities import ROLES
    from apps.core.tests.portal_e2e.parameters import resolve
    from django.contrib.auth import get_user_model
    for role,email in ROLES.items():
        if email:
            assert get_user_model().objects.filter(email=email).exists(), role
    mapping = validate()
    assert mapping['mapping_status'] == 'complete', mapping['errors']
    assert mapping['first_party_routes'] == 196
    assert mapping['verification_status'] == 'pending'
    for route in mapping['routes']:
        for value in [*route['seeded_parameters'].values(), *route.get('seeded_query_parameters', {}).values()]:
            if value.get('provider'):
                assert resolve(value['provider'],route) is not None, (route['id'],value['provider'])
    from urllib.parse import parse_qs, urlsplit
    from apps.core.tests.portal_e2e.parameters import url_for
    summary_route = next(row for row in mapping['routes'] if row['name'] == 'studio-metrics-summary')
    for role, org_slug in [('org-admin', 'alpha'), ('viewer-full', 'beta')]:
        url = urlsplit(url_for(summary_route, role))
        assert url.path.startswith('/s/'+org_slug+'/studio-0/')
        metric = MetricDefinition.objects.get(pk=parse_qs(url.query)['ids'][0])
        assert metric.studio.org.slug == org_slug
    missing_query = json.loads(Path(__file__).with_name('routes.json').read_text())
    next(row for row in missing_query['routes'] if row['name'] == 'studio-metrics-summary')['seeded_query_parameters'].pop('ids')
    assert any('missing required query fixture' in error for error in validate(missing_query)['errors'])
    bad_query = json.loads(Path(__file__).with_name('routes.json').read_text())
    next(row for row in bad_query['routes'] if row['name'] == 'studio-metrics-summary')['seeded_query_parameters']['ids'] = {'provider': 'seed.fabricated'}
    assert any('unknown parameter provider' in error for error in validate(bad_query)['errors'])
    bad = json.loads(Path(__file__).with_name('routes.json').read_text())
    bad['routes'][0]['case'] = None
    assert validate(bad)['mapping_status'] == 'fail'
    missing_account_role = json.loads(Path(__file__).with_name('routes.json').read_text())
    account_role = next(row for row in missing_account_role['routes'] if row['pattern']=='account')
    account_role['allowed_roles'].remove('org-admin')
    assert any('unclassified principal roles' in error and 'org-admin' in error for error in validate(missing_account_role)['errors'])
    missing_method = json.loads(Path(__file__).with_name('routes.json').read_text())
    deliveries_route = next(row for row in missing_method['routes'] if row['pattern']=='deliveries')
    deliveries_route['scenarios'] = [scenario for scenario in deliveries_route['scenarios'] if scenario['method']!='POST']
    assert any('unmapped source methods' in error and 'POST' in error for error in validate(missing_method)['errors'])
    missing_adapter_method = json.loads(Path(__file__).with_name('routes.json').read_text())
    source_form = next(row for row in missing_adapter_method['routes'] if row['pattern']=='s/<slug:org_slug>/<slug:studio_slug>/settings/datasources/new')
    source_form['scenarios'] = [scenario for scenario in source_form['scenarios'] if scenario['method']!='POST']
    assert any('unmapped source methods' in error and 'POST' in error for error in validate(missing_adapter_method)['errors'])
    overlapping_role = json.loads(Path(__file__).with_name('routes.json').read_text())
    overlapping_role['routes'][0]['denied_roles'].append('org-admin')
    assert any('overlapping principal classifications' in error for error in validate(overlapping_role)['errors'])
    missing_condition = json.loads(Path(__file__).with_name('routes.json').read_text())
    conditional_route = next(row for row in missing_condition['routes'] if row.get('conditional_roles'))
    next(iter(conditional_route['conditional_roles'].values()))['condition'] = ''
    assert any('missing concrete principal condition/scenario' in error for error in validate(missing_condition)['errors'])
    bad_role = json.loads(Path(__file__).with_name('routes.json').read_text())
    bad_role['routes'][0]['allowed_roles'] = ['fabricated-role']
    assert any('unknown principal' in error for error in validate(bad_role)['errors'])
    bad_provider = json.loads(Path(__file__).with_name('routes.json').read_text())
    parametrized = next(row for row in bad_provider['routes'] if row['seeded_parameters'])
    key = next(iter(parametrized['seeded_parameters']))
    parametrized['seeded_parameters'][key] = {'provider':'seed.fabricated'}
    assert any('unknown parameter provider' in error for error in validate(bad_provider)['errors'])
    bad_action = json.loads(Path(__file__).with_name('routes.json').read_text())
    account = next(row for row in bad_action['routes'] if row['pattern']=='account')
    account['scenarios'] = [scenario for scenario in account['scenarios'] if scenario['action'] != 'action=profile']
    assert any('unmapped source actions' in error for error in validate(bad_action)['errors'])
    bad['routes'].pop()
    assert any('resolver drift' in error for error in validate(bad)['errors'])
    studio = Studio.objects.get(org__slug='alpha', slug='studio-0')
    assert MetricDefinition.objects.filter(studio=studio).count() >= 20
    assert EmailSchedule.objects.filter(report__studio=studio).exists()
    assert AlertRule.objects.filter(studio=studio).exists()
    assert studio.reports.filter(kind='analysis').exists()
    assert studio.reports.filter(audience='private').exists()
    assert Report.objects.get(studio=studio, slug='report-0002').data_expired_at is not None
    experiments = studio_experiments(studio)
    annotations = calendar_payload(studio)
    assert len(experiments['rows']) >= 2
    assert annotations['total_count'] >= 3
    # Require meaningful storage work on cold registry requests and no hidden SQL substitution.
    samples = [json.loads(line) for line in (Path(os.environ['PORTAL_E2E_ARTIFACTS'])/'work.jsonl').read_text().splitlines()]
    cold = [row for row in samples if row['role']=='org-admin' and row['path']=='/s/alpha/studio-0/api/registry' and row['cache_phase']=='registry-cache-cold']
    warm = [row for row in samples if row['role']=='org-admin' and row['path']=='/s/alpha/studio-0/api/registry' and row['cache_phase']=='registry-cache-warm']
    assert cold and cold[0]['storage_count'] > 0
    assert warm and warm[0]['storage_count'] == 0
    assert all(row['sql_count']>0 and row['response_bytes']>0 for row in cold+warm)
    delay = float(os.environ.get('PORTAL_E2E_SQL_DELAY_MS','0'))
    if delay:
        assert all(row['sql_ms'] >= row['sql_count']*delay for row in cold+warm), 'delay did not reach live HTTP SQL threads'
    recorded = json.loads((Path(os.environ['PORTAL_E2E_ARTIFACTS'])/'seed.json').read_text())
    assert recorded['counts'] == seed_manifest()['counts']



@pytest.mark.parametrize('role', ['org-admin','studio-admin','developer','viewer','viewer-full','private-viewer','group-member','group-admin','group-selected','cross-org','revoked','anonymous'])
def test_seeded_get_mapping(role):
    """Safe live GETs validate downstream gates for the next list/catalog cases.

This is HTTP role coverage, not execution of the planned browser journeys.
"""
    import requests
    from apps.core.tests.portal_e2e.identities import ROLES
    from apps.core.tests.portal_e2e.parameters import url_for
    from apps.core.tests.portal_e2e.seed import PASSWORD
    session = requests.Session()
    session.headers.update({'X-Portal-E2E-Role':role,'X-Portal-E2E-Phase':'mapped-GET-baseline'})
    origin = os.environ['PORTAL_E2E_ORIGIN']
    if ROLES[role]:
        session.get(origin+'/login',timeout=10)
        session.post(origin+'/login',data={'email':ROLES[role],'password':PASSWORD,'csrfmiddlewaretoken':session.cookies['csrftoken']},headers={'Referer':origin+'/login'},timeout=10)
    ledger = json.loads(Path(__file__).with_name('routes.json').read_text())
    for route in ledger['routes']:
        if not route['first_party'] or route['case'] not in ('C04','C06','C07','C08') or not any(s['method']=='GET' and not s['action'] for s in route['scenarios']):
            continue
        if role not in route['allowed_roles'] and role not in route['denied_roles']:
            continue
        response = session.get(origin+url_for(route,role),timeout=30,allow_redirects=False)
        redirected_to_login = response.status_code in (301,302) and '/login' in response.headers.get('Location','')
        outcome = 'allow' if response.status_code in (200,301,302) and not redirected_to_login else 'deny'
        expected = 'allow' if role in route['allowed_roles'] else 'deny'
        terminal = None
        if route.get('redirect_boundary'):
            terminal = session.get(origin+url_for(route,role),timeout=30)
            terminal_auth_denied = '/login' in terminal.url or terminal.status_code in (401,403,404)
            if role in route['redirect_boundary']['denied_destination_roles']:
                assert terminal_auth_denied, (route['id'],role,'destination should deny authentication')
            elif role in route['redirect_boundary']['tenant_hidden_roles']:
                assert '/s/alpha/' not in terminal.text, (route['id'],role,'cross-org studio links leaked')
        row = {'route_id':route['id'],'role':role,'method':'GET','status':response.status_code,'expected':expected,'actual':outcome,'scope':'redirect entry only; destination checked separately' if route.get('redirect_boundary') else 'direct route response','terminal_status':terminal.status_code if terminal is not None else None,'http_ms':response.elapsed.total_seconds()*1000,'response_bytes':len(response.content)}
        with (Path(os.environ['PORTAL_E2E_ARTIFACTS'])/'mapped-get.jsonl').open('a') as out:
            out.write(json.dumps(row)+'\n')
        assert response.status_code < 500, row
        assert outcome == expected, row

def test_live_revoke_inactive_and_impersonation():
    import requests
    from apps.core.tests.portal_e2e.seed import PASSWORD
    from django.contrib.auth import get_user_model
    from apps.orgs.models import OrgMembership
    from apps.studios.models import StudioMembership
    from concurrent.futures import ThreadPoolExecutor
    origin = os.environ['PORTAL_E2E_ORIGIN']
    def login(email):
        session = requests.Session()
        session.get(origin+'/login', timeout=10)
        session.post(origin+'/login', data={'email':email,'password':PASSWORD,'csrfmiddlewaretoken':session.cookies['csrftoken']},headers={'Referer':origin+'/login'},timeout=10)
        return session
    inactive = login('inactive@example.invalid')
    assert not inactive.get(origin+'/api/me',timeout=10).json().get('authenticated')
    viewer = login('alpha-viewer@example.invalid')
    assert viewer.get(origin+'/s/alpha/studio-0/api/registry',timeout=10).status_code == 200
    def revoke():
        user = get_user_model().objects.get(email='alpha-viewer@example.invalid')
        StudioMembership.objects.filter(user=user).delete()
        OrgMembership.objects.filter(user=user).delete()
    # DB checks/mutations run outside Playwright's event loop and touch only this disposable seed.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(revoke).result()
    assert viewer.get(origin+'/s/alpha/studio-0/api/registry',timeout=10,allow_redirects=False).status_code in (403,404)
    operator = login('operator@example.invalid')
    target = get_user_model().objects.get(email='alpha-impersonated@example.invalid')
    response = operator.post(origin+f'/operator/orgs/alpha/impersonate/{target.pk}', data={'csrfmiddlewaretoken':operator.cookies['csrftoken']},headers={'Referer':origin+'/operator/'},timeout=10)
    assert response.status_code == 200
    assert operator.get(origin+'/api/me',timeout=10).json().get('email') == target.email
    denied = operator.get(origin+'/api/system/health',timeout=10)
    assert denied.status_code in (403,404)
    operator.post(origin+'/operator/impersonate/stop',data={'csrfmiddlewaretoken':operator.cookies['csrftoken']},headers={'Referer':origin+'/'},timeout=10)
    assert operator.get(origin+'/api/me',timeout=10).json().get('email') == 'operator@example.invalid'
