"""Small post-clone business fixtures and explicit cohort evidence capture."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .bindings import FIXTURE_ID, cohort_bindings

PLAN = {'id':FIXTURE_ID,'studios':3,'groups':60,'reports':60,'eligible_members':60,
        'initial_public':[1,29,59],'private':4,'save_remove':1,'save_add':50}
FIXTURE_HASH = hashlib.sha256(json.dumps(PLAN,sort_keys=True).encode()).hexdigest()


def build():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.hashers import make_password
    from apps.orgs.models import Organization,OrgMembership,PermissionGroup,PermissionGroupGrant,PermissionGroupMembership
    from apps.reports.models import Report,ReportPermissionGrant
    from apps.studios.models import Studio
    from .seed import PASSWORD
    org=Organization.objects.get(slug='alpha')
    studio=Studio.objects.get(org=org,slug='studio-0')
    extra=[]
    for number in (1,2):
        item=Studio.objects.create(org=org,slug=f'cohort-studio-{number}',name=f'Cohort studio {number}')
        item.ensure_dirs(); extra.append(item)
    existing={report.slug:report for report in Report.objects.filter(studio=studio)}
    Report.objects.bulk_create([Report(studio=studio,slug=f'report-{number:04}',name=f'Report {number}')
        for number in range(60) if f'report-{number:04}' not in existing])
    reports={int(report.slug.removeprefix('report-')):report for report in Report.objects.filter(studio=studio,slug__startswith='report-')}
    Report.objects.filter(pk=reports[29].pk).update(present_in_scan=False)
    Report.objects.filter(pk=reports[59].pk).update(disabled=True)
    editable=PermissionGroup.objects.create(org=org,name='Cohort editable access')
    grant=PermissionGroupGrant.objects.create(group=editable,studio=studio,role='viewer',viewer_scope='selected')
    initial={reports[number].pk for number in (1,29,59,4)}
    ReportPermissionGrant.objects.bulk_create([ReportPermissionGrant(grant=grant,report_id=pk) for pk in initial])
    groups=PermissionGroup.objects.bulk_create([PermissionGroup(org=org,name=f'Cohort Group {number:03}') for number in range(60)])
    User=get_user_model()
    password=make_password(PASSWORD)
    users=User.objects.bulk_create([User(email=f'cohort-alpha-{number:03}@example.invalid',name=f'Cohort person {number:03}',password=password) for number in range(60)])
    OrgMembership.objects.bulk_create([OrgMembership(org=org,user=user,role='member') for user in users])
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(group=groups[0],user=user) for user in users[:3]])
    PermissionGroupGrant.objects.bulk_create([PermissionGroupGrant(group=groups[0],studio=item,role='viewer',viewer_scope='all') for item in [studio,*extra]])
    beta_report=Report.objects.filter(studio__org__slug='beta').first()
    beta_report.name='Protected Beta report sentinel'; beta_report.save(update_fields=['name'])
    beta_user=User.objects.get(email='beta-member-0@example.invalid')
    beta_user.name='Protected Beta person sentinel';beta_user.save(update_fields=['name'])
    saved=(initial-{reports[1].pk})|{reports[50].pk}
    variables={'group_search':groups[-1].name,'studio_search':extra[-1].slug,
        'report_search':reports[50].name,'member_search':users[-1].name,'candidate':str(users[-1].pk),
        'saved_reports':sorted(str(pk) for pk in saved),'invalid_reports':sorted(str(pk) for pk in saved|{beta_report.pk})}
    return {'group':editable.pk,'group_name':editable.name,'summary_group':groups[0].pk,'summary_group_name':groups[0].name,
        'studio':studio.pk,'studio_name':studio.name,'grant':grant.pk,'reports':{str(number):report.pk for number,report in reports.items()},
        'initial':sorted(initial),'saved':sorted(saved),'beta_report':beta_report.pk,'variables':variables,
        'group_count':PermissionGroup.objects.filter(org=org).count(),'candidate_count':OrgMembership.objects.filter(org=org).exclude(user__studio_memberships__studio=studio).count()}


def business_state():
    from apps.orgs.models import PermissionGroup,PermissionGroupMembership,PermissionGroupGrant,OrgMembership
    from apps.studios.models import StudioMembership
    from apps.reports.models import ReportPermissionGrant
    from apps.core.models import AuditLog
    return {model._meta.label:list(model.objects.order_by('pk').values_list(*fields)) for model,fields in (
        (PermissionGroup,('pk','org_id','name','org_role','default_studio_role')),
        (PermissionGroupMembership,('pk','group_id','user_id')),
        (PermissionGroupGrant,('pk','group_id','studio_id','role','viewer_scope')),
        (OrgMembership,('pk','org_id','user_id','role')),(StudioMembership,('pk','studio_id','user_id','role')),
        (ReportPermissionGrant,('pk','grant_id','report_id')),
        (AuditLog,('pk','action','target_type','target_id'))) if model is not AuditLog} | {
        'business_audit':list(AuditLog.objects.filter(action__startswith='group.').order_by('pk').values_list('pk','action','target_type','target_id'))}


def assignments(data):
    from apps.reports.models import ReportPermissionGrant
    return set(ReportPermissionGrant.objects.filter(grant_id=data['grant']).values_list('report_id',flat=True))


def audit_rows():
    from apps.core.models import AuditLog
    return list(AuditLog.objects.filter(action__startswith='group.').order_by('pk').values_list('action','target_id'))


def assert_audit_delta(before,data):
    after=audit_rows()
    assert after[:len(before)]==before
    expected=Counter([('group.grant',str(data['group']))])
    expected.update(('group.report_grant',str(pk)) for pk in set(data['saved'])-set(data['initial']))
    expected.update(('group.report_ungrant',str(pk)) for pk in set(data['initial'])-set(data['saved']))
    assert Counter(after[len(before):])==expected
    return dict(expected)


class HTTPContext:
    def __init__(self,session): self.session=session
    def cookies(self,origin): return [{'name':cookie.name,'value':cookie.value} for cookie in self.session.cookies]


def http_login(origin,principal,headers):
    import requests
    from .identities import ROLES
    from .seed import PASSWORD
    session=requests.Session();session.headers.update(headers)
    session.get(origin+'/login',timeout=20)
    if principal!='anonymous':
        email=ROLES['operator'] if principal=='impersonated' else ROLES[principal]
        session.post(origin+'/login',data={'email':email,'password':PASSWORD,'csrfmiddlewaretoken':session.cookies['csrftoken']},headers={'Referer':origin+'/login'},timeout=20)
    if principal=='impersonated':
        from django.contrib.auth import get_user_model
        target=get_user_model().objects.get(email=ROLES['impersonated'])
        response=session.post(origin+f'/operator/orgs/alpha/impersonate/{target.pk}',data={'csrfmiddlewaretoken':session.cookies['csrftoken']},headers={'Referer':origin+'/operator/'},timeout=20)
        assert response.status_code==200
    return session,HTTPContext(session)


class Capture:
    def __init__(self,journey,data,executor,variant=''):
        self.journey,self.data,self.executor,self.variant=journey,data,executor,variant
        self.rows=[row for row in cohort_bindings(executor) if row.node_id==journey.node_id and row.variant==variant]
        self.recorded={}

    def event(self,route,method,*,status=None,last=True):
        events=[row for row in self.journey.events() if row['route']==route and row['method']==method and (status is None or row['status']==status)]
        assert events
        return events[-1] if last else events[0]

    def record(self,condition,event,checkpoints):
        matches=[row for row in self.rows if row.condition==condition]
        assert len(matches)==1
        binding=matches[0]
        self.journey.assert_observed_actor(event)
        assert set(binding.obligations)<=set(('actor','observed-actor',*checkpoints))
        prior=[ref for name in binding.transport['prior'] for ref in self.recorded.get(name,())]
        from .fixtures import cohort_record
        cohort_record(self.journey,binding,[event['event_id']],checkpoints,prior,self.data['variables'])
        self.recorded[condition]=[event['event_id']]
