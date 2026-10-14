"""Declaration authority and bounded source-result normalization."""
import json
import math
import pytest
from django.db import transaction
from django.utils import timezone
from apps.reports.models import Report, ReportSourceSummary
from apps.reports.report_experiment_projection import project_results, sync_declarations
from apps.reports.source_summary import reconcile

pytestmark = pytest.mark.django_db(transaction=True)


def component(name, delta=4, ci=(1,5)):
    return {'type':'ab_compare','header':{'test_name':name},'body':{'kpi_rows':[
        {'key':'rev','metric':'Revenue','delta_pct':delta,'ci_pct':ci,'higher_is_better':True}]}}


def fixture(studio_tree, config=None, data=None):
    report = Report.objects.create(studio=studio_tree,slug='ab',config=config or {'ab_test':[{'test_name':'Alpha'},{'test_name':'Beta'}]})
    output = studio_tree.output_dir/report.slug
    output.mkdir(parents=True)
    (output/'_meta.json').write_text(json.dumps({'last_status':'success','last_run':timezone.now().isoformat(),'metrics_used':['known']}))
    (output/'index.html').write_text('served')
    if data is not None:
        (output/'data.json').write_text(json.dumps(data))
    return report,output


def test_first_scope_declaration_authority_and_gap(studio_tree):
    report,path=fixture(studio_tree,{'ab_test':[{'test_name':' BETA '},{'test_name':'Missing'},{}]},
        {'components':{},'_scopeFiles':{'first':'data_a.json','second':'data_b.json'}})
    (path/'data_a.json').write_text(json.dumps({'components':{'a':component('Alpha'),'b':component('Beta')}}))
    (path/'data_b.json').write_text(json.dumps({'components':{'c':component('Missing')}}))
    assert reconcile(report.pk)['status']=='ready'
    rows=list(report.source_summary.experiments.order_by('ordinal'))
    assert rows[0].test_name==' BETA ' and rows[0].delta_pct==4
    assert rows[1].payload_state=='missing' and rows[1].delta_pct is None
    assert rows[2].payload_state=='missing'  # unnamed ordinal2, only2 built


@pytest.mark.parametrize('value',[None,'4',True,float('nan'),float('inf'),float('-inf')])
def test_invalid_delta_excluded(value):
    rows,_=project_results({'ab_test':{}},{},[component('Alpha',value)])
    assert rows[0].get('delta_pct') is None
    assert rows[0]['payload_state']==('ready' if value is None else 'invalid')


def test_modes_primary_and_nonsignificant_denominator():
    comp=component('Alpha')
    comp['modes']={'raw':{'label':'Raw','kpi_rows':[]},'adjusted':{'label':'Adjusted','kpi_rows':[
        {'metric':'Revenue','delta_pct':-2,'higher_is_better':False,'ci_pct':[-3,-1]},
        {'key':'other','metric':'Other','delta_pct':2,'ci_pct':[-1,3]}]}}
    comp['default_mode']='adjusted'
    rows,_=project_results({'ab_test':{'primary_metric':'OTHER'}},{},[comp])
    assert rows[0]['mode_label']=='Adjusted' and rows[0]['delta_pct']==2 and not rows[0]['significant']


def test_scope_rewrite_without_main_or_metadata_change(studio_tree):
    report,path=fixture(studio_tree,{'ab_test':{}},{'components':{},'_scopeFiles':{'first':'data_a.json'}})
    scope=path/'data_a.json'
    scope.write_text(json.dumps({'components':{'a':component('Alpha',1)}}))
    reconcile(report.pk)
    first=report.source_summary.experiments.get().delta_pct
    scope.write_text(json.dumps({'components':{'a':component('Alpha',9)}}))
    ReportSourceSummary.objects.filter(report=report).update(next_check_at=timezone.now())
    reconcile(report.pk)
    assert first==1 and report.source_summary.experiments.get().delta_pct==9


def test_declaration_change_cancels_lease_and_updates_without_build(studio_tree):
    report,path=fixture(studio_tree,{'ab_test':{'test_name':'Alpha'}},{'components':{'a':component('Alpha')}})
    reconcile(report.pk)
    summary=report.source_summary
    ReportSourceSummary.objects.filter(pk=summary.pk).update(lease_token='old')
    report.config={'ab_test':{'test_name':'Renamed'}}
    with transaction.atomic():
        report.save(update_fields=['config'])
        sync_declarations(report)
    summary.refresh_from_db()
    assert not summary.lease_token and summary.experiment_state=='pending'
    assert summary.experiments.get().test_name=='Renamed'


def test_no_undeclared_experiments(studio_tree):
    report,path=fixture(studio_tree,{'description':'plain'},{'components':{'a':component('Alpha')}})
    reconcile(report.pk)
    assert report.source_summary.experiment_state=='not-applicable'
    assert not report.source_summary.experiments.exists()


def test_huge_number_bad_date_and_zero_display_do_not_poison_peer():
    bad=component('Bad',10**400)
    good=component('Good',2)
    good['body']['kpi_rows'][0].update(control_val=0,test_val=False)
    rows,_=project_results({'ab_test':[{'test_name':'Bad'},{'test_name':'Good'}]},
        {'_freshness':{'generated_at':'2026-99-99T00:00:00'}},[bad,good])
    assert rows[0]['payload_state']=='invalid' and rows[0].get('delta_pct') is None
    assert rows[1]['payload_state']=='ready' and rows[1]['delta_pct']==2
    assert rows[1]['generated_at'] is None and rows[1]['control_val']=='0' and rows[1]['test_val']=='False'


def test_bad_mode_identity_does_not_poison_other_component():
    bad=component('Bad');bad.update(modes={'raw':{}},default_mode=['not-hashable'])
    rows,_=project_results({'ab_test':[{'test_name':'Bad'},{'test_name':'Good'}]},{},[bad,component('Good')])
    assert rows[0]['payload_state']=='invalid' and rows[1]['payload_state']=='ready'


@pytest.mark.parametrize('attempt',['','error'])
def test_historical_unbuilt_declarations_without_scan_or_metadata(studio_tree,attempt):
    report=Report.objects.create(studio=studio_tree,slug='unbuilt',config={'ab_test':[{'test_name':'Alpha'},{'test_name':'Beta'}]})
    if attempt:
        ReportSourceSummary.objects.create(report=report,attempt_status=attempt)
    result=reconcile(report.pk)
    summary=report.source_summary
    assert result['status']==('uncertain' if attempt else 'missing')
    rows=list(summary.experiments.order_by('ordinal'))
    assert [row.test_name for row in rows]==['Alpha','Beta']
    assert all(row.delta_pct is None for row in rows)
    assert all(row.payload_state in ('missing','unavailable') for row in rows)
    assert not summary.has_output


def test_declaration_limit_never_publishes_partial_result_rows(studio_tree):
    report,path=fixture(studio_tree,{'ab_test':[{'test_name':str(i)} for i in range(257)]},{'components':{}})
    assert reconcile(report.pk)['status']=='ready'
    summary=report.source_summary
    assert summary.experiment_state=='oversized' and not summary.experiments.exists()
    assert summary.claims.count()==1


def test_oversized_input_short_circuits_normalization_and_hash(monkeypatch):
    from apps.reports import report_experiment_projection as projection
    monkeypatch.setattr(projection,'_ab_entries',lambda config:pytest.fail('oversized list must not be walked'))
    monkeypatch.setattr(projection.json,'dumps',lambda *a,**k:pytest.fail('oversized input must not be serialized'))
    config={'ab_test':[{}]*100000}
    with pytest.raises(projection.ProjectionOversized):
        projection.declarations(config)
    assert projection.declaration_identity(config)==projection.OVERSIZED_DECLARATION_DIGEST


@pytest.mark.parametrize('value',['x'*100000,{'nested':['x'*100000]}])
def test_oversized_scalar_short_circuits_json(value,monkeypatch):
    from apps.reports import report_experiment_projection as projection
    monkeypatch.setattr(projection.json,'dumps',lambda *a,**k:pytest.fail('oversized scalar must not be serialized'))
    config={'ab_test':{'name':value}}
    assert projection.declaration_identity(config)==projection.OVERSIZED_DECLARATION_DIGEST


def test_normal_256_names_digest_fully_sensitive():
    from apps.reports.report_experiment_projection import declarations
    config={'ab_test':[{'name':str(i),'test_name':str(i)} for i in range(256)]}
    rows,old=declarations(config)
    assert len(rows)==256 and rows[-1]['name']=='255'
    config['ab_test'][-1]['name']='Renamed'
    assert declarations(config)[1]!=old


def test_oversized_scan_repair_transition_keeps_core_and_runtime(studio_tree):
    from apps.reports.report_experiment_projection import OVERSIZED_DECLARATION_DIGEST
    report,path=fixture(studio_tree,{'ab_test':[{}]*257},{'components':{'a':component('Alpha')}})
    with transaction.atomic():
        sync_declarations(report)
    first_generation=report.source_summary.generation
    assert reconcile(report.pk)['status']=='ready'
    summary=report.source_summary
    assert summary.experiment_state=='oversized' and summary.declaration_digest==OVERSIZED_DECLARATION_DIGEST
    assert not summary.experiments.exists() and summary.runtime_state=='ready' and summary.has_output
    assert summary.claims.get().metric_name=='known'
    report.config={'ab_test':{'name':'Supported'}}
    with transaction.atomic():
        report.save(update_fields=['config']);sync_declarations(report)
    summary.refresh_from_db()
    assert summary.generation>first_generation and summary.declaration_digest!=OVERSIZED_DECLARATION_DIGEST
    assert reconcile(report.pk)['status']=='ready'
    assert summary.experiments.get().delta_pct==4


def test_multi_entry_display_name_rename_without_rebuild(studio_tree):
    report,path=fixture(studio_tree,{'ab_test':[{'name':'Old','test_name':'Alpha'},{'name':'Second','test_name':'Beta'}]},
        {'components':{'a':component('Alpha'),'b':component('Beta')}})
    reconcile(report.pk)
    identity=report.source_summary.source_identity
    old_digest=report.source_summary.declaration_digest
    assert report.source_summary.experiments.get(ordinal=0).declaration['name']=='Old'
    report.config['ab_test'][0]['name']='New'
    with transaction.atomic():
        report.save(update_fields=['config']);sync_declarations(report)
    reconcile(report.pk)
    summary=report.source_summary
    assert summary.source_identity==identity and summary.declaration_digest!=old_digest
    row=summary.experiments.get(ordinal=0)
    assert row.declaration['name']=='New' and row.delta_pct==4
