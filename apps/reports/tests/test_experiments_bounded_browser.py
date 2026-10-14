"""Real Gunicorn/PostgreSQL native experiment browser checks with shared pending feedback.

Uses an owned pytest database and subprocess, with PBKDF2 authentication.
Artifacts are opt-in through EXPERIMENTS_BROWSER_ARTIFACTS; no external service.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote, parse_qs, urlsplit
from urllib.request import urlopen

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", params=(0, 20, 50))
def experiments_server(request, django_db_setup, django_db_blocker, tmp_path_factory):
    from django.contrib.auth.hashers import PBKDF2PasswordHasher
    from django.utils import timezone
    from apps.accounts.models import User
    from apps.core import roles
    from apps.orgs.models import Organization, OrgMembership
    from apps.studios.models import Studio, StudioMembership
    from apps.reports.models import Report, ReportSourceSummary, ReportExperimentSummary
    tmp = tmp_path_factory.mktemp(f"experiments-{request.param}")
    artifacts = Path(os.environ.get("EXPERIMENTS_BROWSER_ARTIFACTS", tmp)) / str(request.param)
    artifacts.mkdir(parents=True, exist_ok=True)
    with django_db_blocker.unblock():
        org = Organization.objects.create(slug=f"experiments-{request.param}", name="Experiments browser")
        studio = Studio.objects.create(org=org, slug="reports", name="Reports")
        empty = Studio.objects.create(org=org, slug="empty", name="Empty")
        users = {}
        for role in (roles.ADMIN, roles.DEVELOPER, roles.VIEWER):
            user = User.objects.create(email=f"{role}-{request.param}@experiments.example", password=PBKDF2PasswordHasher().encode("Experiments-test-42", PBKDF2PasswordHasher().salt()))
            OrgMembership.objects.create(org=org, user=user)
            for target in (studio, empty):
                StudioMembership.objects.create(studio=target, user=user, role=role)
            users[role] = user.email
        rows = Report.objects.bulk_create([Report(studio=studio, slug=f"report-{i:04}", name=f"Report {i:04}" if i != 1 else "A long report name "*15) for i in range(1000)])
        from datetime import timedelta
        now = timezone.now()
        summaries = ReportSourceSummary.objects.bulk_create([ReportSourceSummary(report=r, schema_version=2, declaration_digest='digest', state='ready', quality='verified', experiment_state='ready', observed_at=now, has_output=True) for r in rows])
        ReportExperimentSummary.objects.bulk_create([ReportExperimentSummary(summary=s, ordinal=j, declaration_digest='digest', declaration={'name':f'Experiment {i:04}-{j}', 'test_name':f'Test {i:04}-{j}'}, test_name=f'Test {i:04}-{j}', start_date=now.date()-timedelta(days=30), end_date=now.date()-timedelta(days=2), payload_state='ready', metric='Revenue', delta_pct=2, ci_low=1, ci_high=3, higher_is_better=True, significant=True) for i,s in enumerate(summaries) for j in range(2)])
        unknown = Studio.objects.create(org=org, slug='unknown', name='Unknown coverage')
        Report.objects.create(studio=unknown, slug='historical')
        for user in User.objects.filter(email__in=users.values()):
            StudioMembership.objects.create(studio=unknown, user=user, role=roles.ADMIN)
        denied = User.objects.create(email=f'denied-{request.param}@experiments.example', password=PBKDF2PasswordHasher().encode("Experiments-test-42", PBKDF2PasswordHasher().salt()))
        OrgMembership.objects.create(org=org, user=denied)
        users['denied'] = denied.email
    db = settings.DATABASES["default"]
    database_url = f"postgres://{quote(db['USER'])}:{quote(db['PASSWORD'])}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    (tmp / "experiments_browser_settings.py").write_text(
        "from trellum_portal.settings.test import *\n"
        "WHITENOISE_USE_FINDERS=True\n"
        "PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher']\n"
        "MIDDLEWARE=['apps.core.tests.portal_e2e.instrumentation.MeasureRequests']+MIDDLEWARE\n"
        f"DATA_DIR={str(tmp / 'data')!r}\n"
    )
    (tmp / "experiments_browser_wsgi.py").write_text("from django.core.wsgi import get_wsgi_application\napplication=get_wsgi_application()\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "experiments_browser_settings", "DATABASE_URL": database_url,
           "PYTHONPATH": str(tmp)+os.pathsep+str(settings.BASE_DIR), "PORTAL_E2E_WORK_LOG": str(artifacts / "work.jsonl"),
           "PORTAL_E2E_SQL_DELAY_MS": str(request.param), "PORTAL_E2E_SOURCE_DIGEST": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    log = open(artifacts / "gunicorn.log", "w")
    process = subprocess.Popen([sys.executable, "-m", "gunicorn", "experiments_browser_wsgi:application", "--bind", f"127.0.0.1:{port}", "--workers", "1", "--threads", "2"], env=env, stdout=log, stderr=log)
    origin = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if urlopen(origin+"/login", timeout=1).status == 200:
                    break
            except Exception:
                if process.poll() is not None:
                    raise RuntimeError((artifacts / "gunicorn.log").read_text())
                time.sleep(.1)
        else:
            raise RuntimeError("Gunicorn did not start")
        yield dict(origin=origin, path=f"/s/{org.slug}/{studio.slug}/experiments", empty=f"/s/{org.slug}/{empty.slug}/experiments", unknown=f"/s/{org.slug}/{unknown.slug}/experiments", users=users, artifacts=artifacts, delay=request.param)
    finally:
        process.terminate(); process.wait(timeout=15); log.close()
        with django_db_blocker.unblock():
            org.delete()
            User.objects.filter(email__in=users.values()).delete()


def _login(page, server, role):
    from playwright.sync_api import expect
    page.goto(server['origin']+'/login?next='+server['path'])
    page.get_by_label('Email', exact=True).fill(server['users'][role])
    page.get_by_label('Password', exact=True).fill('Experiments-test-42')
    started = time.perf_counter()
    page.get_by_role('button', name='Continue', exact=True).click()
    page.wait_for_url('**'+server['path'])
    if role != 'denied':
        expect(page.locator('tbody tr')).to_have_count(25)
        expect(page.locator('tbody tr').first).to_be_visible()
    return time.perf_counter()-started


@pytest.mark.parametrize('engine', ('chromium', 'webkit'))
def test_experiments_gunicorn_native_controls_devices_roles(experiments_server, engine):
    from playwright.sync_api import sync_playwright, expect
    server = experiments_server
    timings=[]
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        try:
            for javascript in tuple(bool(int(v)) for v in os.environ.get('EXPERIMENTS_BROWSER_JAVASCRIPT','0,1').split(',')):
                for width in tuple(int(v) for v in os.environ.get('EXPERIMENTS_BROWSER_WIDTHS','1440,390,720,195').split(',')):
                    context=browser.new_context(extra_http_headers={'X-Portal-E2E-Role':'studio-admin'},java_script_enabled=javascript, viewport={'width':width,'height':900}, reduced_motion='reduce', color_scheme='dark' if width==720 else 'light')
                    page=context.new_page(); page.set_default_timeout(15000)
                    errors=[]; page.on('pageerror',lambda error: errors.append(str(error)))
                    if javascript:
                        page.add_init_script("""window.addEventListener('click',()=>{const start=performance.now();queueMicrotask(()=>{const s=document.querySelector('[data-console-navigation-native-status]');if(s&&s.textContent)sessionStorage.setItem('experimentFeedback',JSON.stringify({text:s.textContent,milliseconds:performance.now()-start}));});});""")
                    cold=_login(page,server,'admin')
                    assert cold <= {0:3,20:4.5,50:6.5}[server['delay']]
                    started=time.perf_counter()
                    response=page.goto(server['origin']+server['path'],wait_until='domcontentloaded')
                    expect(page.locator('tbody tr')).to_have_count(25)
                    elapsed=time.perf_counter()-started
                    assert elapsed <= {0:3,20:4.5,50:6.5}[server['delay']]
                    assert len(response.body()) <= 160*1024 and page.locator('*').count() <= 1800
                    assert page.locator('.tl-lane').count() <= 25
                    assert page.locator('body').evaluate('e=>e.scrollWidth<=innerWidth+1')
                    page.screenshot(path=str(server['artifacts']/f'{engine}-{width}-js{int(javascript)}.png'),full_page=False)
                    timings.append(dict(engine=engine,width=width,javascript=javascript,cold_login_seconds=cold,first_content_seconds=elapsed,html_bytes=len(response.body()),dom_nodes=page.locator('*').count()))
                    # Independent native paging retains the other lane/table page.
                    page.get_by_role('link',name='Next timeline',exact=True).focus();page.keyboard.press('Enter')
                    page.wait_for_url(lambda u:parse_qs(urlsplit(u).query).get('timeline_page')==['2'])
                    expect(page.get_by_role('navigation',name='Timeline pages')).to_contain_text('Page 2 of 80')
                    page.get_by_role('link',name='Next experiments',exact=True).focus();page.keyboard.press('Enter')
                    page.wait_for_url(lambda u:parse_qs(urlsplit(u).query).get('page')==['2'])
                    expect(page.get_by_role('navigation',name='Experiment pages')).to_contain_text('Page 2 of 80')
                    assert 'timeline_page=2' in page.url and 'page=2' in page.url
                    if javascript:
                        recorded=page.evaluate("sessionStorage.getItem('experimentFeedback')")
                        feedback=json.loads(recorded) if recorded else None
                        if feedback or os.environ.get('EXPERIMENTS_REQUIRE_NAV_FEEDBACK')=='1':
                            assert feedback and feedback['text']=='Opening page…' and feedback['milliseconds']<100
                        timings[-1]['local_native_pending_feedback']=feedback
                        timings[-1]['local_native_pending_adoption_required']=feedback is None
                    page.get_by_label('Search all experiments').fill('Experiment 0999-1');page.get_by_role('button',name='Search',exact=True).click()
                    expect(page.locator('tbody tr')).to_have_count(1)
                    expect(page.locator('tbody')).to_contain_text('Experiment 0999-1')
                    assert 'timeline_page=' not in page.url and 'page=' not in page.url
                    expect(page.locator('.ui-stat-row')).to_contain_text('2000')
                    page.get_by_role('link',name='Concluded 2000',exact=True).click()
                    assert 'q=Experiment' in page.url and 'status=concluded' in page.url
                    page.get_by_role('link',name='Unknown 0',exact=True).click()
                    expect(page.locator('tbody')).to_contain_text('No experiments match this filter')
                    if javascript and width == 1440 and server["delay"]:
                        # This existing sidebar foundation is separate from local
                        # search/range/paging adapter adoption.
                        page.evaluate("""() => {
                          window.addEventListener('click', () => {
                            const started = performance.now();
                            const panel = document.querySelector('[data-console-native-pending]');
                            const style = panel && getComputedStyle(panel);
                            const snapshot = {
                              milliseconds:performance.now()-started,
                              visible:!!(panel && panel.getClientRects().length && style.display !== 'none' && style.visibility !== 'hidden'),
                              text:panel ? panel.textContent : '',
                              animation_frame_observed:false
                            };
                            sessionStorage.setItem('experimentSidebarFeedback', JSON.stringify(snapshot));
                            requestAnimationFrame(() => {
                              snapshot.animation_frame_observed = true;
                              snapshot.animation_frame_milliseconds = performance.now()-started;
                              sessionStorage.setItem('experimentSidebarFeedback', JSON.stringify(snapshot));
                            });
                          }, {once:true});
                        }""")
                        started = time.perf_counter()
                        page.locator(f'a.tl-console-nav-link[href="{server["path"]}"]').click()
                        page.wait_for_url("**"+server["path"])
                        expect(page.locator("tbody tr")).to_have_count(25)
                        sidebar_feedback = json.loads(page.evaluate("sessionStorage.getItem('experimentSidebarFeedback')"))
                        assert sidebar_feedback["visible"] and "Opening Experiments…" in sidebar_feedback["text"]
                        assert sidebar_feedback["milliseconds"] < 100
                        timings[-1]["sidebar_dom_pending_feedback"] = sidebar_feedback
                        timings[-1]["sidebar_to_experiment_rows_seconds"] = time.perf_counter()-started
                    page.goto(server['origin']+server['empty'])
                    expect(page.locator('.ui-empty')).to_contain_text('No A/B test reports in this studio')
                    page.goto(server['origin']+server['unknown'])
                    expect(page.locator('.ui-empty')).to_contain_text('not fully indexed yet')
                    assert not errors
                    context.close()
            for role,status in (('viewer',200),('denied',404)):
                context=browser.new_context(extra_http_headers={'X-Portal-E2E-Role':'viewer-full' if role=='viewer' else 'unlabeled'});page=context.new_page();_login(page,server,role)
                response=page.goto(server['origin']+server['path'])
                assert response.status==status
                context.close()
        finally:
            browser.close()
    work=[json.loads(line) for line in (server['artifacts']/'work.jsonl').read_text().splitlines()]
    requests=[row for row in work if row['path'].endswith('/experiments')]
    assert requests
    assert all(row['storage_count']==0 and row['sql_count']<=30 and row['response_bytes']<=160*1024 for row in requests if row['status']==200)
    assert all(row['sql_count']<=24 for row in requests if row['status']==200 and row['role']=='studio-admin')
    assert all(row['sql_count']<=20 for row in requests if row['status']==404)
    (server['artifacts']/f'{engine}-timings.json').write_text(json.dumps({'source':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),'engine':engine,'delay_ms':server['delay'],'timings':timings,'max_sql':max(row['sql_count'] for row in requests),'storage':sum(row['storage_count'] for row in requests)},indent=2))
