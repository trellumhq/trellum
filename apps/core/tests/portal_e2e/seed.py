"""Deterministic isolated baseline data; no provider credentials."""
from django.contrib.auth import get_user_model
from apps.orgs.models import Organization, OrgMembership, PermissionGroup, PermissionGroupMembership, PermissionGroupGrant
from apps.studios.models import Studio, StudioMembership
from apps.reports.models import Report, ReportPermissionGrant

SEED_VERSION = 'portal-baseline-v2'
PASSWORD = 'Disposable-browser-password-42!'

def seed(report_count=20, member_count=3, studio_count=1):
    User = get_user_model()
    for slug in ('alpha', 'beta'):
        org = Organization.objects.create(slug=slug, name=slug.title())
        for index in range(studio_count):
            studio = Studio.objects.create(org=org, slug=f'studio-{index}', name=f'Studio {index}')
            studio.ensure_dirs()
            Report.objects.bulk_create([Report(studio=studio, slug=f'report-{n:04}', name=f'Report {n}') for n in range(report_count)])
            for role in ('admin', 'developer', 'viewer'):
                user, _ = User.objects.get_or_create(email=f'{slug}-{role}@example.invalid')
                user.set_password(PASSWORD)
                user.save()
                OrgMembership.objects.get_or_create(org=org, user=user, defaults={'role': 'admin' if role == 'admin' else 'member'})
                StudioMembership.objects.create(studio=studio, user=user, role=role)
        studio_admin = User.objects.create_user(f'{slug}-studio-admin@example.invalid', PASSWORD)
        OrgMembership.objects.create(org=org,user=studio_admin,role='member')
        StudioMembership.objects.create(studio=org.studios.get(slug='studio-0'),user=studio_admin,role='admin')
        for role in ('member', 'admin', 'selected'):
            user = User.objects.create_user(f'{slug}-group-{role}@example.invalid', PASSWORD)
            OrgMembership.objects.create(org=org, user=user, role='member')
            group = PermissionGroup.objects.create(org=org, name=role, org_role='admin' if role == 'admin' else '')
            PermissionGroupMembership.objects.create(group=group, user=user)
            first = org.studios.get(slug='studio-0')
            grant = PermissionGroupGrant.objects.create(group=group, studio=first, role='viewer', viewer_scope='selected' if role == 'selected' else 'all')
            if role == 'selected':
                ReportPermissionGrant.objects.create(grant=grant, report=first.reports.first())
        User.objects.create_user(f'{slug}-revoked@example.invalid', PASSWORD)
        impersonated = User.objects.create_user(f'{slug}-impersonated@example.invalid', PASSWORD)
        OrgMembership.objects.create(org=org, user=impersonated, role='member')
        for n in range(member_count):
            user = User.objects.create_user(f'{slug}-member-{n}@example.invalid', PASSWORD)
            OrgMembership.objects.create(org=org, user=user, role='member')
            StudioMembership.objects.bulk_create([StudioMembership(studio=studio, user=user, role='viewer') for studio in org.studios.all()])
    User.objects.create_superuser('operator@example.invalid', PASSWORD)
    User.objects.create_user('inactive@example.invalid', PASSWORD, is_active=False)


def enrich(metric_count=20, schedule_count=1, rule_count=1):
    """Valid portal artifacts with controlled lifecycle states, no network calls."""
    import json
    import subprocess
    from datetime import timedelta
    from django.utils import timezone
    from django.conf import settings
    from apps.studios.models import StudioRepo
    from apps.reports.models import MetricDefinition, EmailSchedule, ShareLink
    from apps.alerts.models import AlertRule, AlertRun
    from apps.runner.models import Run
    from apps.datasources.models import DataSource, RepoDataSource
    from apps.assistant.models import AssistantSession, ProposedAction
    from apps.accounts.models import Invitation
    from apps.core.models import EmailApiConnection
    from pathlib import Path
    now = timezone.now()
    root = Path(settings.DATA_DIR)
    remote = root/'local-git.git'
    subprocess.run(['git','init','--bare','--quiet','--initial-branch=main',str(remote)], check=True)
    source = root/'repository-source'
    subprocess.run(['git','init','--quiet','--initial-branch=main',str(source)], check=True)
    (source/'README.md').write_text('Disposable portal browser fixture repository.\n')
    subprocess.run(['git','-C',str(source),'add','README.md'], check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Portal fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Seed disposable report repository'], check=True)
    subprocess.run(['git','-C',str(source),'push','--quiet',str(remote),'main'], check=True)
    for studio in Studio.objects.select_related('org'):
        admin = get_user_model().objects.get(email=f'{studio.org.slug}-admin@example.invalid')
        subprocess.run(['git','clone','--quiet',str(remote),str(studio.repo_dir)], check=True)
        StudioRepo.objects.create(studio=studio, repo_url=remote.as_uri(), auth_method='none', sync_interval_minutes=0)
        DataSource.objects.create(studio=studio, name='sample', type='file', config={'path':'sample.csv'})
        RepoDataSource.objects.create(studio=studio,name='sample',type='file',config={'path':'files/sample.csv'},source_file='data-sources/config.yaml',shipped_path='data-sources/files/sample.csv')
        (studio.datasources_dir/'files'/'sample.csv').write_text('day,revenue\n2026-10-01,100\n2026-10-02,110\n')
        MetricDefinition.objects.bulk_create([MetricDefinition(studio=studio, name=f'metric-{n:04}', label=f'Metric {n}', agg='sum', column='revenue', dimensions=['day'], definition_hash=f'{n:064x}') for n in range(metric_count)])
        reports = list(studio.reports.order_by('slug'))
        built, unbuilt, expired, failed, private, analysis = reports[:6]
        for report in (built, private, analysis):
            report.last_built_at = now
            if report == private and studio.org.slug == 'alpha':
                report.audience = 'private'
            if report == analysis:
                report.kind = 'analysis'
            report.config = {'ab_test': [{'name':'checkout', 'start_date':'2026-10-01'}, {'name':'upsell','start_date':'2026-10-02'}], 'annotations': {'events': [{'date':'2026-10-01','label':'Baseline release','type':'release'}]}}
            report.save()
            output = studio.output_dir/report.slug
            output.mkdir(parents=True, exist_ok=True)
            (output/'index.html').write_text('<!doctype html><html><head><title>Baseline report</title></head><body><h1>Baseline report</h1><p>Controlled built fixture</p></body></html>')
            (output/'_meta.json').write_text(json.dumps({'schema_version':2,'last_run':now.isoformat(),'last_status':'success','last_duration':0.1,'metrics_used':[{'id':'metric-0000','definition_hash':f'{0:064x}','version':1}]}))
            component = {'type':'ab_compare','header':{'test_name':'checkout'},'body':{'kpi_rows':[{'metric':'revenue','key':'revenue','delta_pct':10,'ci_pct':[1,19],'higher_is_better':True,'control_val':'100','test_val':'110'}],'volume_rows':[],'timeseries':{}},'modes':None,'default_mode':''}
            (output/'data.json').write_text(json.dumps({'components':{'fw_c1':component,'fw_c2':dict(component,header={'test_name':'upsell'})},'_freshness':{'generated_at':now.isoformat()}}))
            Run.objects.create(studio=studio, report=report, slug=report.slug, status=Run.SUCCESS, finished_at=now, started_at=now-timedelta(seconds=1), exit_code=0)
        expired.last_built_at = now-timedelta(days=120)
        expired.data_expired_at = now-timedelta(days=30)
        expired.save()
        Run.objects.create(studio=studio,report=failed,slug=failed.slug,status=Run.ERROR,finished_at=now,exit_code=1,stderr_tail='Controlled fixture failure')
        failure_output = studio.output_dir/failed.slug
        failure_output.mkdir(parents=True,exist_ok=True)
        (failure_output/'_meta.json').write_text(json.dumps({'schema_version':2,'last_status':'error','last_run':now.isoformat(),'last_error':'Controlled fixture failure'}))
        for offset,state in ((6,'unbuilt'),(7,'failed'),(8,'expired')):
            analysis_state = reports[offset]
            analysis_state.kind = 'analysis'
            if state == 'expired':
                analysis_state.last_built_at = now-timedelta(days=120)
                analysis_state.data_expired_at = now-timedelta(days=30)
            if state == 'failed':
                Run.objects.create(studio=studio,report=analysis_state,slug=analysis_state.slug,status=Run.ERROR,finished_at=now,exit_code=1,stderr_tail='Controlled analysis failure')
            analysis_state.save()
        (studio.project_root/'events.yaml').write_text('events:\n  - date: 2026-10-01\n    label: Baseline campaign\n    type: campaign\n  - date: 2026-10-02\n    end_date: 2026-10-05\n    label: Baseline experiment\n    type: ab_test\n')
        import yaml
        (studio.project_root/'metrics.yaml').write_text(yaml.safe_dump({'metrics':[{'name':f'metric-{n:04}','label':f'Metric {n}','agg':'sum','column':'revenue'} for n in range(metric_count)]}))
        for report in studio.reports.all():
            folder = studio.reports_dir/report.slug
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'report.yaml').write_text(yaml.safe_dump({'name':report.name, 'kind':report.kind, **report.config}))
            if report.kind == Report.KIND_ANALYSIS:
                (folder/'content.md').write_text('# Controlled analysis\n\nDisposable browser fixture.\n')
            else:
                (folder/'generator.py').write_text(
                    'from trellum import BaseReport\n'
                    'from trellum.components import ReportMetadata\n\n'
                    'class FixtureReport(BaseReport):\n'
                    '    def generate(self, ctx):\n'
                    '        ctx.add_section("Fixture", [ReportMetadata(values={"Source": "Disposable fixture"})])\n'
                )
            from apps.reports.annotation_projection import sync_report_declarations
            sync_report_declarations(report)
        # Exercise the real producer after both declarations and artifacts exist.
        # Request handlers must not repair missing projections during a journey.
        from apps.reports.source_summary import reconcile
        for report in (built, private, analysis):
            observation = reconcile(report.pk)
            if observation['status'] != 'ready' or observation.get('projection_unavailable'):
                raise RuntimeError('Controlled report projection did not become ready')
        for n in range(schedule_count):
            schedule = EmailSchedule.objects.create(report=built, created_by=admin, recipient_roles=['everyone'], enabled=False, send_hour=n%24)
            schedule.recipients.add(admin)
        for n in range(rule_count):
            rule = AlertRule.objects.create(org=studio.org, studio=studio, report=built, name=f'Rule {n}', instructions='Controlled baseline', created_by=admin, enabled=False, recipient_roles=['developers'])
            rule.recipients.add(admin)
            AlertRun.objects.create(rule=rule, finished_at=now, execution_mode='test', condition='not_met', decision='quiet', title='Controlled baseline history')
        if studio.slug == 'studio-0':
            full_viewer = get_user_model().objects.create_user(f'{studio.org.slug}-full-viewer@example.invalid', PASSWORD)
            OrgMembership.objects.create(org=studio.org,user=full_viewer,role='member')
            group = PermissionGroup.objects.create(org=studio.org,name='Full viewer')
            PermissionGroupMembership.objects.create(group=group,user=full_viewer)
            grant = PermissionGroupGrant.objects.create(group=group,studio=studio,role='viewer',viewer_scope='all')
            if studio.org.slug == 'alpha':
                ReportPermissionGrant.objects.create(grant=grant,report=private)
        ShareLink.objects.create(report=built, created_by=admin)
        session = AssistantSession.objects.create(user=admin,org=studio.org,studio=studio,report=built,title='Controlled baseline conversation')
        ProposedAction.objects.create(session=session,org=studio.org,studio=studio,user=admin,tool='run_report',summary='Controlled baseline proposal',arguments={'slug':built.slug},required_role='developer',expires_at=now+timedelta(days=1))
    import shutil
    first_studio = Studio.objects.filter(org__slug='alpha',slug='studio-0').first()
    shutil.copytree(first_studio.reports_dir,source/'reports')
    shutil.copytree(first_studio.datasources_dir,source/'data-sources')
    (source/'data-sources'/'config.yaml').write_text('sample:\n  type: file\n  path: files/sample.csv\n')
    shutil.copy2(first_studio.project_root/'metrics.yaml',source/'metrics.yaml')
    shutil.copy2(first_studio.project_root/'events.yaml',source/'events.yaml')
    subprocess.run(['git','-C',str(source),'add','.'],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Portal fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Add controlled report source fixtures'],check=True)
    subprocess.run(['git','-C',str(source),'push','--quiet',str(remote),'main'],check=True)
    for studio in Studio.objects.all():
        from apps.runner.gitsync import studio_sync_lock
        from apps.reports.annotation_reconciliation import authoritative_handoff, observe
        from apps.reports.models import AnnotationSourceState
        with studio_sync_lock(studio.pk) as locked:
            if not locked:
                raise RuntimeError('Controlled fixture checkout is locked')
            subprocess.run(['git','-C',str(studio.repo_dir),'pull','--quiet','--ff-only'],check=True)
            sha = subprocess.check_output(['git','-C',str(studio.repo_dir),'rev-parse','HEAD'],text=True).strip()
            authoritative_handoff(studio, sha)
            global_source = AnnotationSourceState.objects.get(studio=studio, kind='global')
            observe(global_source.pk)
            global_source.refresh_from_db()
            if global_source.status != 'ready':
                raise RuntimeError('Controlled global annotation projection did not become ready')
    for org in Organization.objects.all():
        DataSource.objects.create(org=org,name='sample',type='file',config={'path':'sample.csv'})
        org.datasources_dir.mkdir(parents=True,exist_ok=True)
        (org.datasources_dir/'sample.csv').write_text('day,revenue\n2026-10-01,100\n')
        Invitation.objects.create(org=org,email=f'invited-{org.slug}@example.invalid')
    from django.contrib.auth.models import Group
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialApp, SocialAccount, SocialToken
    from apps.orgs.models import OrgSSOConfig, OrgAssistantConfig
    from apps.core.models import InstanceConfig, AuditLog, ServerLogEvent
    operator = get_user_model().objects.get(email='operator@example.invalid')
    Group.objects.create(name='Native boundary fixture')
    EmailAddress.objects.create(user=operator,email=operator.email,verified=True,primary=True)
    social_app = SocialApp.objects.create(provider='openid_connect',provider_id='portal-local-oidc',name='Local OIDC boundary',client_id='disposable-client',settings={'server_url':'http://127.0.0.1:9/disabled'})
    account = SocialAccount.objects.create(user=operator,provider='portal-local-oidc',uid='disposable-operator')
    SocialToken.objects.create(app=social_app,account=account,token='disposable-not-a-provider-token')
    InstanceConfig.load()
    for org in Organization.objects.all():
        OrgSSOConfig.objects.create(org=org)
        OrgAssistantConfig.objects.create(org=org,enabled=False)
    for org in Organization.objects.all():
        AuditLog.objects.bulk_create([AuditLog(org=org,actor=operator,action='fixture.seed',category='system',metadata={'fixture':True,'sequence':n}) for n in range(120)])
    ServerLogEvent.objects.bulk_create([ServerLogEvent(timestamp=now,level='INFO',service='portal-p00',host='disposable',process=1,logger='portal.fixture',message=f'Controlled fixture event {n}',context={'fixture':True}) for n in range(120)])
    EmailApiConnection.objects.create(name='Local boundary fixture',provider='sendgrid',from_email='sender@example.invalid',credentials={'api_key':'disposable-not-a-provider-key'})


def seed_manifest():
    """Counts and credential-free identity providers for portable evidence."""
    from apps.reports.models import MetricDefinition, EmailSchedule, ShareLink
    from apps.alerts.models import AlertRule, AlertRun
    from apps.runner.models import Run
    from apps.datasources.models import DataSource, RepoDataSource
    from apps.assistant.models import AssistantSession, ProposedAction
    from apps.accounts.models import Invitation
    from apps.core.models import EmailApiConnection
    models = [AssistantSession, ProposedAction, RepoDataSource, get_user_model(), Organization, OrgMembership, Studio, StudioMembership, Report, MetricDefinition, EmailSchedule, AlertRule, AlertRun, PermissionGroup, PermissionGroupMembership, PermissionGroupGrant, ReportPermissionGrant, Run, DataSource, ShareLink, Invitation, EmailApiConnection]
    from apps.core.models import AuditLog, ServerLogEvent
    return {'seed': 'portal-baseline-v2', 'mutable_counts_at_snapshot': {'core.AuditLog':AuditLog.objects.count(),'core.ServerLogEvent':ServerLogEvent.objects.count()}, 'counts': {model._meta.label: model.objects.count() for model in models},
            'per_studio': list(Studio.objects.values('org__slug','slug').order_by('org__slug','slug')),
            'providers': {'org_slug':'alpha','studio_slug':'studio-0','slug':'report-0000',
                          'report_id':Report.objects.filter(studio__org__slug='alpha',studio__slug='studio-0').first().pk,
                          'schedule_id':EmailSchedule.objects.first().pk,'rule_id':AlertRule.objects.first().pk,'session_id':AssistantSession.objects.first().pk,'proposal_id':ProposedAction.objects.first().pk,
                          'group_id':PermissionGroup.objects.first().pk,'user_id':get_user_model().objects.get(email='alpha-impersonated@example.invalid').pk,
                          'invite_id':Invitation.objects.first().pk,'connection_id':EmailApiConnection.objects.first().pk,'link_id':ShareLink.objects.first().pk,'run_id':str(Run.objects.first().pk)},
            'credential_providers': ['seed.token: lookup owning ShareLink/Invitation at runtime', 'native-boundary: mint native protocol credentials in scenario setup'],
            'lifecycle': {'built':'report-0000','unbuilt':'report-0001','expired':'report-0002','failed':'report-0003','private':'report-0004','analysis':'report-0005','analysis_unbuilt':'report-0006','analysis_failed':'report-0007','analysis_expired':'report-0008'},
            'unverified': ['worker generation of seeded artifacts','MFA/provider callbacks','third-party failures','mail delivery','signed unsubscribe','object-store remote backend']}
