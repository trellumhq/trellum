"""Negative evidence guards; synthetic inputs are never product journey proof."""
from copy import deepcopy
from dataclasses import asdict
import pytest
from .bindings import cohort_bindings,FIXTURE_ID
from .coverage import validate_cohort,aggregate_cohort,parameter_fingerprint,validate_na

pytestmark=pytest.mark.cohort_executor('http')


def valid_case(executor='chromium'):
    bindings=cohort_bindings(executor)
    variables={'group_search':'group-last','studio_search':'studio-last','report_search':'report-last',
               'member_search':'person-last','candidate':'12','saved_reports':['1','2'],'invalid_reports':['1','2','99']}
    identities={row.principal:{'actor_id':None if row.principal in ('anonymous','inactive') else 17,
                'impersonator_id':99 if row.principal=='impersonated' else None} for row in bindings}
    collected=sorted({row.node_id for row in bindings});evidence=[];events=[];prior={};saves={}
    for number,binding in enumerate(bindings):
        event_id=f'event-{number}'
        if binding.condition=='access-save-after-browse':event_id=saves[binding.node_id]
        actor={'principal':binding.principal,**identities[binding.principal],'session_digest':'digest' if identities[binding.principal]['actor_id'] else None}
        event={'event_id':event_id,'executor':executor,'route':binding.route,'method':binding.method,'action':binding.transport['action'],
               'node_id':binding.node_id,'principal':binding.principal,'correlation':binding.node_id,'complete':True,
               'source':'source','binding_hash':'bindings','status':binding.transport['status'] or 200,
               'started_ns':number*10+1,'finished_ns':number*10+2,
               **{key:actor[key] for key in ('actor_id','session_digest','impersonator_id')},'parameters':{}}
        for name in ('query','body'):
            event['parameters'][name]={key:parameter_fingerprint(variables[value[1:]] if isinstance(value,str) and value.startswith('$') else value) for key,value in binding.transport[name].items()}
        if binding.condition!='access-save-after-browse':events.append(event)
        if binding.condition=='access-save':saves[binding.node_id]=event_id
        if binding.condition in ('access-browse','access-search'):prior[binding.node_id]=event_id
        evidence.append({'scope':'cohort','key':list(binding.key),'executor':executor,'node_id':binding.node_id,'variant':binding.variant,
            'source':'source','binding_hash':'bindings','manifest_hash':'manifest','fixture':FIXTURE_ID,'correlation':binding.node_id,
            'actor':actor,'checkpoints':list(binding.obligations),'events':[event_id],
            'prior_events':[prior[binding.node_id]] if binding.transport['prior'] else []})
    routes={}
    for binding in bindings:
        row=routes.setdefault(binding.route,{'id':binding.route,'scenarios':[]})
        scenario={'method':binding.method,'action':binding.action}
        if scenario not in row['scenarios']:row['scenarios'].append(scenario)
    return {'executor':executor,'collected':collected,'outcomes':{node:'passed' for node in collected},'assertions':evidence,
        'events':events,'source':'source','digest':'bindings','manifest_hash':'manifest',
        'fixture_variables':{row.variant or 'http':variables for row in bindings},'identities':identities,'ledger':{'routes':list(routes.values())}}


@pytest.mark.parametrize('executor',['chromium','webkit','http'])
def test_valid_namespaced_cohort(executor):
    assert validate_cohort(**valid_case(executor))['status']=='pass'


@pytest.mark.parametrize('mutation',['wrong-engine','uncollected','skipped','stale-source','stale-binding','stale-manifest','wrong-fixture','wrong-variant','wrong-actor','missing-impersonator','wrong-action','wrong-method','wrong-query','wrong-body','missing-checkpoint','no-prior','late-prior','unrelated-reuse','duplicate-node'])
def test_mutations_block(mutation):
    case=valid_case();row=case['assertions'][0];event=case['events'][0]
    if mutation=='wrong-engine':event['executor']='webkit'
    elif mutation=='uncollected':case['collected']=[]
    elif mutation=='skipped':case['outcomes'][row['node_id']]='skipped'
    elif mutation=='stale-source':row['source']='old'
    elif mutation=='stale-binding':event['binding_hash']='old'
    elif mutation=='stale-manifest':row['manifest_hash']='old'
    elif mutation=='wrong-fixture':row['fixture']='other'
    elif mutation=='wrong-variant':row['variant']='other'
    elif mutation=='wrong-actor':event['actor_id']=999
    elif mutation=='missing-impersonator':
        case=valid_case('http');row=next(row for row in case['assertions'] if row['actor']['principal']=='impersonated');row['actor']['impersonator_id']=None
    elif mutation=='wrong-action':event['action']='action=grant'
    elif mutation=='wrong-method':event['method']='POST'
    elif mutation=='wrong-query':event['parameters']['query']['q']='not-the-actual-query'
    elif mutation=='wrong-body':
        event=next(row for row in case['events'] if row['method']=='POST');event['parameters']['body']['action']='wrong-body'
    elif mutation=='missing-checkpoint':row['checkpoints']=[]
    elif mutation in ('no-prior','late-prior'):
        row=next(row for row in case['assertions'] if row['key'][4]=='access-save-after-browse')
        if mutation=='no-prior':row['prior_events']=[]
        else:next(event for event in case['events'] if event['event_id']==row['prior_events'][0])['finished_ns']=10**9
    elif mutation=='unrelated-reuse':case['assertions'][1]['events']=row['events']
    elif mutation=='duplicate-node':case['collected']*=2
    assert validate_cohort(**case)['status']=='blocked'


def bundle(executor):
    return {'executor':executor,'source':'source','binding_hash':'bindings','manifest_hash':'manifest','fixture_hash':'fixture',
            'status':'pass','collected':['actual-node']}


def test_complete_executor_matrix_aggregates_without_node_collision():
    result=aggregate_cohort([bundle(name) for name in ('chromium','webkit','http')])
    assert result['status']=='pass' and result['execution_count']==3 and result['full_journey_gate']=='pending'


@pytest.mark.parametrize('mutation',['missing-webkit','duplicate-engine','mixed-source','mixed-bindings','mixed-manifest','mixed-fixture','nonpass','duplicate-node'])
def test_incomplete_or_mixed_matrix_blocks(mutation):
    rows=[bundle(name) for name in ('chromium','webkit','http')]
    if mutation=='missing-webkit':rows.pop(1)
    elif mutation=='duplicate-engine':rows[1]['executor']='chromium'
    elif mutation.startswith('mixed-'):
        name={'bindings':'binding_hash','manifest':'manifest_hash','fixture':'fixture_hash'}.get(mutation[6:],mutation[6:]);rows[1][name]='other'
    elif mutation=='nonpass':rows[1]['status']='blocked'
    else:rows[0]['collected']*=2
    assert aggregate_cohort(rows)['status']=='blocked'


def test_na_is_explicit_reviewed_policy_and_never_runtime_pass():
    policy={'key':{'reason':'native protocol not using this principal','policy_digest':'source','reviewer':'reviewed'}}
    row={'policy_key':'key','reason':policy['key']['reason'],'policy_digest':'source','proof':'actual-foundation-node'}
    result=validate_na([row],policy,{'actual-foundation-node'})
    assert result['status']=='pass' and result['runtime_pass_count']==0
    assert validate_na([row],{}, {'actual-foundation-node'})['status']=='blocked'
    assert validate_na([row],policy,set())['status']=='blocked'
    assert validate_na([{**row,'policy_digest':'old'}],policy,{'actual-foundation-node'})['status']=='blocked'
