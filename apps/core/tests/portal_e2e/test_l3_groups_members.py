"""Bounded actual-HTTP member/access journeys; other cohorts remain pending."""
import json
from pathlib import Path
import pytest
from .bindings import COHORT_VARIANTS,COHORT_PRINCIPALS,ORG_STATUS,STUDIO_JSON_STATUS
from .fixtures import login,visible_geometry,browser_metrics
from .journey_fixtures import Capture,business_state,assignments,audit_rows,assert_audit_delta,http_login,InputParser,html_checked_reports

pytestmark=pytest.mark.portal_e2e
VARIANTS=[pytest.param(row,id=row[0],marks=pytest.mark.cohort_executor(row[2])) for row in COHORT_VARIANTS]


def checked_values(form):
    return form.locator('input[name=reports]:checked').evaluate_all('nodes => nodes.map(n => n.value).sort()')


def selected_reports(form,mode):
    """Effective report draft: every report is an inline checkbox in the picker."""
    return checked_values(form)


def form_values(form):
    return form.evaluate('f => Array.from(new FormData(f).entries())')


def no_beta(text):
    assert 'Protected Beta report sentinel' not in text
    assert 'Protected Beta person sentinel' not in text


def overview(page,journey,data,capture):
    from playwright.sync_api import expect
    before=journey.database(business_state)
    page.goto(journey.unit.origin+'/orgs/alpha/settings/groups')
    assert page.locator('tbody tr').count()<=50
    row=page.locator('tbody tr').filter(has_text=data['summary_group_name'])
    expect(row.locator('[data-label=Overrides]')).to_have_text('3 studios')
    expect(row.locator('[data-label=Members]')).to_have_text('3')
    page.goto(journey.unit.origin+'/orgs/alpha/settings/groups?q='+data['variables']['group_search'].replace(' ','%20'))
    expect(page.locator('tbody tr')).to_have_count(1)
    expect(page.locator('tbody')).to_contain_text(data['variables']['group_search'])
    no_beta(page.content());assert journey.database(business_state)==before
    capture.record('group-counts',capture.event('route-295','GET'),('counts','search','scope','no-write'))
    path=f'/orgs/alpha/settings/groups/{data["summary_group"]}?tab=overview&grant_q='+data['variables']['studio_search']
    page.goto(journey.unit.origin+path)
    expect(page.locator('main')).to_contain_text('3 explicit studio grants.')
    expect(page.locator('main')).to_contain_text(data['variables']['studio_search'])
    no_beta(page.content());assert journey.database(business_state)==before
    capture.record('overview-discovery',capture.event('route-297','GET'),('counts','search','scope','no-write'))


def access(page,context,journey,data,capture,mode):
    from playwright.sync_api import expect
    origin=journey.unit.origin
    path=f'/orgs/alpha/settings/groups/{data["group"]}?tab=access&studio={data["studio"]}'
    page.goto(origin+path)
    form=page.locator('[data-studio-access]')
    expect(form).to_be_visible()
    before=journey.database(business_state);audit_before=journey.database(audit_rows)
    expect(form.locator(f'[name=reports][value="{data["reports"]["29"]}"]')).to_be_checked()
    expect(form.locator(f'[name=reports][value="{data["reports"]["59"]}"]')).to_be_checked()
    # No pager, Previous/Next or Search buttons anywhere in the picker.
    assert form.get_by_role('button',name='Next reports').count()==0 and form.get_by_role('button',name='Search reports').count()==0
    picker=form.locator('[data-recipient-picker]')
    picker.locator('.ui-picker-trigger').click()
    picker.locator(f'[name=reports][value="{data["reports"]["1"]}"]').uncheck()
    picker.locator('.ui-picker-search').fill(data['variables']['report_search'])
    option=picker.locator(f'[name=reports][value="{data["reports"]["50"]}"]')
    expect(option).to_be_visible();option.check()
    page.keyboard.press('Escape')
    assert selected_reports(form,mode)==data['variables']['saved_reports']
    assert 'missing from repository' in form.text_content() and 'inactive' in form.text_content()
    no_beta(page.content());assert journey.database(business_state)==before
    assert selected_reports(form,mode)==data['variables']['saved_reports']
    # Add a real foreign ID only as an invalid request draft. Product APIs and
    # HTML still reach the live origin and perform authoritative validation.
    form.evaluate("(f,value) => {let c=document.createElement('input');c.type='checkbox';c.name='reports';c.value=value;c.checked=true;c.id='foreign-draft';f.append(c);if(window.TrellumSettingsForms)TrellumSettingsForms.trackControls(f,[c]);c.dispatchEvent(new Event('input',{bubbles:true}));}",str(data['beta_report']))
    save=form.get_by_role('button',name='Save changes',exact=True)
    with page.expect_response(lambda r:r.request.method=='POST' and '/settings/groups/' in r.url) as invalid:
        save.click()
    assert invalid.value.status==400
    form=page.locator('[data-studio-access]')
    error=form.locator('[data-settings-error],.errorlist').first
    visible_geometry(error)
    assert selected_reports(form,mode)==data['variables']['invalid_reports']
    expect(form.locator('[name=role]')).to_have_value('viewer')
    no_beta(page.content());assert journey.database(business_state)==before
    capture.record('access-invalid',capture.event('route-297','POST'),('draft','visible-error','scope','no-write'))
    form.locator(f'[name=reports][value="{data["beta_report"]}"]').uncheck()
    save=form.get_by_role('button',name='Save changes',exact=True)
    save.scroll_into_view_if_needed();y=page.evaluate('scrollY')
    with page.expect_response(lambda r:r.request.method=='POST' and '/settings/groups/' in r.url) as saved:
        save.click()
    assert saved.value.status==200
    expect(form.locator('[data-settings-dirty-status]')).to_have_text('Studio access updated.')
    visible_geometry(form.locator('[data-settings-dirty-status]'))
    expect(save).to_be_focused();assert abs(page.evaluate('scrollY')-y)<=2
    assert saved.value.request.headers['x-trellum-form']=='1' and saved.value.json()['ok'] is True
    assert journey.database(assignments,data)==set(data['saved'])
    journey.database(assert_audit_delta,audit_before,data)
    saved_event=capture.event('route-297','POST')
    assert sum(row['method']=='POST' and row['action']=='action=studio_access' and row['status'] in (200,302) for row in journey.events())==1
    page.reload()
    assert selected_reports(page.locator('[data-studio-access]'),mode)==data['variables']['saved_reports']
    checkpoints=('db-persistence','reload-persistence','visible-feedback','audit-delta','single-save','scope')
    capture.record('access-save',saved_event,checkpoints)
    after=journey.database(business_state)
    csrf=context.request.post(origin+path,form={'action':'studio_access','studio':str(data['studio']),'role':'','viewer_scope':'all'},headers={'Referer':origin+path})
    assert csrf.status==403 and journey.database(business_state)==after
    capture.record('access-csrf',capture.event('route-297','POST',status=403),('csrf-no-write','no-write'))


@pytest.mark.parametrize('variant',VARIANTS)
def test_browser_workflow(cohort_case,variant):
    from playwright.sync_api import sync_playwright
    journey,data,executor=cohort_case
    identifier,family,engine,width,height,principal,mode=variant
    capture=Capture(journey,data,executor,identifier)
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch()
        context=browser.new_context(viewport={'width':width,'height':height},extra_http_headers=journey.headers(principal,'cohort'))
        context.tracing.start(screenshots=True,snapshots=True)
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            login(page,journey.unit.origin,principal);journey.assert_actor(context,principal)
            if family=='overview':overview(page,journey,data,capture)
            else:access(page,context,journey,data,capture,mode)
            assert not errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
            with (Path(journey.unit.env['PORTAL_E2E_ARTIFACTS'])/'browser-metrics.jsonl').open('a') as output:
                output.write(json.dumps({'executor':executor,'node_id':journey.node_id,**browser_metrics(page)})+'\n')
        finally:
            context.tracing.stop(path=str(Path(journey.unit.env['PORTAL_E2E_ARTIFACTS'])/f'{journey.correlation}-private-trace.zip'))
            browser.close()


@pytest.mark.cohort_executor('http')
@pytest.mark.parametrize('principal',COHORT_PRINCIPALS)
def test_authorization_boundaries(cohort_case,principal):
    journey,data,executor=cohort_case
    session,context=http_login(journey.unit.origin,principal,journey.headers(principal,'cohort'))
    journey.assert_actor(context,principal)
    capture=Capture(journey,data,executor)
    before=journey.database(business_state)
    path=f'/orgs/alpha/settings/groups/{data["group"]}?tab=access'
    response=session.get(journey.unit.origin+path,allow_redirects=False,timeout=20)
    assert response.status_code==ORG_STATUS[principal];no_beta(response.text)
    if response.status_code==200:assert data['group_name'] in response.text
    elif response.status_code==302:assert '/login' in response.headers['Location']
    else:assert data['group_name'] not in response.text
    assert journey.database(business_state)==before
    capture.record('auth-org',capture.event('route-297','GET'),('authorization','scope','no-write'))
    response=session.get(journey.unit.origin+'/s/alpha/studio-0/api/member-choices',params={'q':data['variables']['member_search']},timeout=20)
    assert response.status_code==STUDIO_JSON_STATUS[principal];no_beta(response.text)
    if response.status_code==200:
        assert [item['value'] for item in response.json()['results']]==[data['variables']['candidate']]
    assert journey.database(business_state)==before
    capture.record('auth-studio',capture.event('route-321','GET'),('authorization','scope','no-write'))
    if ORG_STATUS[principal]!=200:
        response=session.post(journey.unit.origin+path,data={'csrfmiddlewaretoken':session.cookies['csrftoken'],'action':'studio_access','studio':data['studio'],'role':'viewer','viewer_scope':'selected','reports':data['variables']['saved_reports']},headers={'Referer':journey.unit.origin+path},allow_redirects=False,timeout=20)
        assert response.status_code==ORG_STATUS[principal];no_beta(response.text)
        assert journey.database(business_state)==before
        capture.record('auth-write-denied',capture.event('route-297','POST'),('authorization','scope','no-write'))
    if principal=='impersonated':
        session.post(journey.unit.origin+'/operator/impersonate/stop',data={'csrfmiddlewaretoken':session.cookies['csrftoken']},headers={'Referer':journey.unit.origin+'/'},timeout=20)
    session.close()


@pytest.mark.cohort_executor('http')
def test_operator_bounded_access_save(cohort_case):
    journey,data,executor=cohort_case
    session,context=http_login(journey.unit.origin,'operator',journey.headers('operator','cohort'))
    journey.assert_actor(context,'operator');capture=Capture(journey,data,executor)
    before=journey.database(audit_rows)
    path=f'/orgs/alpha/settings/groups/{data["group"]}?tab=access&studio={data["studio"]}'
    response=session.post(journey.unit.origin+path,data={'csrfmiddlewaretoken':session.cookies['csrftoken'],'action':'studio_access','studio':data['studio'],'role':'viewer','viewer_scope':'selected','reports':data['variables']['saved_reports']},headers={'Referer':journey.unit.origin+path,'X-Trellum-Form':'1'},timeout=20)
    assert response.status_code==200 and response.json()['ok'] is True
    assert journey.database(assignments,data)==set(data['saved'])
    journey.database(assert_audit_delta,before,data)
    response=session.get(journey.unit.origin+path,timeout=20)
    packet=json.loads(next(row['value'] for row in InputParser(response.text).inputs if row.get('name')=='reports__selection'))
    assert sorted(packet['selected'])==data['variables']['saved_reports']
    assert html_checked_reports(response.text)==sorted(set(packet['selected'])&set(packet['editable']))
    assert set(packet['selected'])-set(packet['editable'])<set(packet['selected'])
    no_beta(response.text)
    assert sum(row['method']=='POST' and row['route']=='route-297' for row in journey.events())==1
    capture.record('operator-save',capture.event('route-297','POST'),('db-persistence','reload-persistence','audit-delta','single-save','scope'))
    session.close()
