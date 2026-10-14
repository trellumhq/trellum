"""Serving facts and extension failures share a finite worker read budget."""
import json
import pytest
from django.utils import timezone
from apps.core import storage
from apps.reports.models import Report, ReportSourceSummary
from apps.reports.source_summary import INSPECTION_BYTES, reconcile
from apps.reports.report_experiment_projection import AB_BYTES

pytestmark=pytest.mark.django_db(transaction=True)
MIB=1024*1024


def setup(studio_tree,config=None):
    report=Report.objects.create(studio=studio_tree,slug='metrics',tags=['metrics'],config=config or {'ab_test':{}})
    path=studio_tree.output_dir/report.slug
    path.mkdir(parents=True)
    meta={'last_status':'success','last_run':timezone.now().isoformat(),'metrics_used':['known'],
        'metrics_workspace':{'version':1},'framework_version':'1.2','validation':{'summary':{'pass':3},'checks':[{'message':'private-driver'}]},
        'details':{'totals':{'total_rows':42,'secret':13},'filter_matrix':['private']}}
    (path/'_meta.json').write_text(json.dumps(meta))
    (path/'index.html').write_text('main')
    return report,path


def padded(obj,size):
    obj={**obj,'padding':''}
    raw=json.dumps(obj).encode()
    obj['padding']='x'*(size-len(raw))
    raw=json.dumps(obj).encode()
    assert len(raw)==size
    return raw


def test_runtime_compact_no_private_payloads(studio_tree):
    report,path=setup(studio_tree,{'description':'plain'})
    (path/'_live_queries.json').write_text('PRIVATE SQL SECRET')
    reconcile(report.pk)
    summary=report.source_summary
    assert summary.runtime_state=='ready' and summary.has_output and summary.has_live_queries
    assert summary.html_entry=='index.html'
    assert summary.runtime=={'validation':{'summary':{'pass':3}},'details':{'totals':{'total_rows':42}},'framework_version':'1.2'}
    assert 'private' not in json.dumps(summary.runtime).lower()


def test_shared_main16_scope10_and_local_content_readback(studio_tree,monkeypatch):
    report,path=setup(studio_tree)
    (path/'data.json').write_bytes(padded({'components':{},'_scopeFiles':{'first':'data_a.json'}},16*MIB))
    (path/'data_a.json').write_bytes(padded({'components':{'a':{'type':'ab_compare','body':{'kpi_rows':[]}}}},10*MIB))
    reads=[]
    original=storage.read_summary_artifact
    def observe(*args,**kwargs):
        reads.append(args[2])
        return original(*args,**kwargs)
    monkeypatch.setattr(storage,'read_summary_artifact',observe)
    result=reconcile(report.pk)
    summary=report.source_summary
    assert result['status']=='ready' and summary.preview_state=='ready' and summary.experiment_state=='ready'
    assert reads.count('data.json')==2 and reads.count('data_a.json')==2
    assert 52*MIB<=result['bytes']<INSPECTION_BYTES<64*MIB


def test_preview_main_ab_oversized_keeps_claims_workspace(studio_tree,monkeypatch):
    report,path=setup(studio_tree)
    (path/'data.json').write_bytes(padded({'components':{}},27*MIB))
    reads=[]
    original=storage.read_summary_artifact
    def observe(*args,**kwargs):
        reads.append(args[2]);return original(*args,**kwargs)
    monkeypatch.setattr(storage,'read_summary_artifact',observe)
    result=reconcile(report.pk)
    summary=report.source_summary
    assert summary.experiment_state=='oversized' and summary.preview_state=='ready'
    assert summary.workspace_version==1 and summary.claims.get().metric_name=='known'
    assert reads.count('data.json')==1 and result['bytes']<INSPECTION_BYTES


def test_extension_outage_does_not_poison_core(studio_tree,monkeypatch):
    report,path=setup(studio_tree,{'description':'plain'})
    monkeypatch.setattr(storage,'summary_output_facts',lambda *a,**k:(_ for _ in ()).throw(OSError('provider outage')))
    result=reconcile(report.pk)
    summary=report.source_summary
    assert result['status']=='ready' and summary.runtime_state=='unavailable'
    assert summary.workspace_version==1 and summary.claims.count()==1
    assert result['bytes']>=96*1024


def test_local_scope_race_does_not_publish_results(studio_tree,monkeypatch):
    report,path=setup(studio_tree)
    payload={'components':{'a':{'type':'ab_compare','body':{'kpi_rows':[{'metric':'Revenue','delta_pct':1}]}}}}
    (path/'data.json').write_text(json.dumps(payload))
    original=storage.read_summary_artifact
    count=0
    def race(*args,**kwargs):
        nonlocal count
        if args[2]=='data.json':
            count+=1
            if count==2:
                (path/'data.json').write_text(json.dumps({'components':{}}))
        return original(*args,**kwargs)
    monkeypatch.setattr(storage,'read_summary_artifact',race)
    assert reconcile(report.pk)['status']=='ready'
    summary=report.source_summary
    assert summary.experiment_state=='unavailable' and summary.experiments.get().delta_pct is None
    assert summary.claims.count()==1


def test_reservation_first_report_always_admissible(studio_tree,monkeypatch):
    from apps.reports import source_reconciliation as worker
    for slug in ('a','b'):
        Report.objects.create(studio=studio_tree,slug=slug)
    assert worker.MAX_INSPECTION_BYTES==62*MIB+128*1024<worker.BATCH_BYTES
    monkeypatch.setattr(worker,'reconcile',lambda pk:{'status':'ready','bytes':worker.MAX_INSPECTION_BYTES})
    result=worker.run_batch()
    assert result['inspections']==1 and result['bytes']<worker.BATCH_BYTES


@pytest.mark.parametrize('qualifier',['mock','real','unknown'])
def test_details_data_source_qualifier(studio_tree,qualifier):
    report,path=setup(studio_tree,{'description':'plain'})
    meta=json.loads((path/'_meta.json').read_text())
    meta['details']['data_source']=qualifier
    (path/'_meta.json').write_text(json.dumps(meta))
    reconcile(report.pk)
    details=report.source_summary.runtime['details']
    assert details.get('data_source')==(qualifier if qualifier in ('mock','real') else None)
