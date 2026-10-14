"""A passing status or invented node must never certify a journey."""
from copy import deepcopy
import pytest
from .bindings import Binding, binding_digest
from .coverage import coverage_summary, validate_execution


def valid_case():
    binding = Binding('route-account', 'POST', 'action=profile', 'org-admin',
                      'profile-save', 'test_profile[mobile]', ('actor', 'db', 'reload'))
    actor = {'principal': 'org-admin', 'actor_id': 17, 'session_digest': 'hashed-session', 'impersonator_id': None}
    event = {'event_id': 'request-1', 'complete': True, 'status': 302, 'node_id': binding.node_id,
             'correlation': 'window-1', 'route': binding.route, 'method': binding.method,
             'action': binding.action, 'principal': binding.principal, 'condition': binding.condition,
             **{key: actor[key] for key in ('actor_id', 'session_digest', 'impersonator_id')}}
    evidence = {'key': list(binding.key), 'node_id': binding.node_id, 'source': 'source-1',
                'binding_hash': 'binding-1', 'checkpoints': list(binding.obligations),
                'correlation': 'window-1', 'events': [event['event_id']], 'actor': actor}
    return {'bindings': [binding], 'collected': [binding.node_id], 'outcomes': {binding.node_id: 'passed'},
            'evidence': [evidence], 'events': [event], 'source': 'source-1', 'binding_hash': 'binding-1',
            'conditions': {'profile-save': 'Reviewed own profile mutation and reload'},
            'identity_registry': {'org-admin': {'actor_id': 17, 'impersonator_id': None}}}


def test_valid_executed_binding():
    assert validate_execution(**valid_case())['status'] == 'pass'


@pytest.mark.parametrize('mutation', [
    'uncollected', 'skipped', 'xfail', 'error', 'missing-evidence', 'duplicate-binding',
    'duplicate-evidence', 'unbound-evidence', 'stale-source', 'stale-binding', 'wrong-node',
    'missing-db', 'no-events', 'unknown-event', 'incomplete', 'wrong-window', 'foreign-node',
    'wrong-route', 'wrong-method', 'wrong-action', 'wrong-principal', 'wrong-condition',
    'wrong-actor', 'wrong-session', 'wrong-impersonator', 'no-actor', 'unreviewed-condition',
    'duplicate-event', 'spoofed-role-header', 'no-session-proof', 'no-identity-registry',
    'duplicate-collected', 'no-status',
])
def test_mutated_evidence_blocks(mutation):
    case = valid_case()
    row, event = case['evidence'][0], case['events'][0]
    if mutation == 'uncollected': case['collected'] = []
    elif mutation in ('skipped', 'xfail', 'error'): case['outcomes'][case['bindings'][0].node_id] = mutation
    elif mutation == 'missing-evidence': case['evidence'] = []
    elif mutation == 'duplicate-binding': case['bindings'] *= 2
    elif mutation == 'duplicate-evidence': case['evidence'] *= 2
    elif mutation == 'unbound-evidence': case['evidence'].append({**deepcopy(row), 'key': ['fake']})
    elif mutation == 'stale-source': row['source'] = 'previous-source'
    elif mutation == 'stale-binding': row['binding_hash'] = 'previous-binding'
    elif mutation == 'wrong-node': row['node_id'] = 'other-test'
    elif mutation == 'missing-db': row['checkpoints'].remove('db')
    elif mutation == 'no-events': row['events'] = []
    elif mutation == 'unknown-event': row['events'] = ['imaginary']
    elif mutation == 'incomplete': event['complete'] = False
    elif mutation == 'wrong-window': event['correlation'] = 'different'
    elif mutation == 'foreign-node': event['node_id'] = 'other-test'
    elif mutation.startswith('wrong-'):
        name = {'wrong-actor': 'actor_id', 'wrong-session': 'session_digest',
                'wrong-impersonator': 'impersonator_id'}.get(mutation, mutation.removeprefix('wrong-'))
        event[name] = 'wrong'
    elif mutation == 'no-actor': row['actor'] = None
    elif mutation == 'unreviewed-condition': case['conditions'] = {}
    elif mutation == 'duplicate-event': case['events'] *= 2
    elif mutation == 'spoofed-role-header':
        # Matching arbitrary role labels cannot disguise a different real actor.
        row['actor']['actor_id'] = event['actor_id'] = 999
    elif mutation == 'no-session-proof': row['actor']['session_digest'] = event['session_digest'] = None
    elif mutation == 'no-identity-registry': case['identity_registry'] = {}
    elif mutation == 'duplicate-collected': case['collected'] *= 2
    elif mutation == 'no-status': event.pop('status')
    assert validate_execution(**case)['status'] == 'blocked'


def ledger():
    return {'routes': [{'id': 'route-account', 'allowed_roles': ['org-admin'], 'denied_roles': ['viewer'],
        'conditional_roles': {'revoked': {'condition': 'Own account only'}},
        'scenarios': [{'method': 'POST', 'action': 'action=profile'}]}]}


def test_no_bindings_or_empty_shard_can_pass():
    case = valid_case()
    case['bindings'], case['evidence'] = [], []
    assert validate_execution(**case)['status'] == 'blocked'
    result = coverage_summary(ledger(), [], {'status': 'pass'})
    assert result['status'] == 'blocked' and len(result['pending_policy_tuples']) == 3


def test_happy_path_does_not_certify_unreviewed_conditions():
    case = valid_case()
    result = coverage_summary(ledger(), case['bindings'], validate_execution(**case))
    assert len(result['pending_policy_tuples']) == 3
    required = {('route-account', 'POST', 'action=profile', 'org-admin', 'allowed_roles', None): [case['bindings'][0].key]}
    result = coverage_summary(ledger(), case['bindings'], validate_execution(**case), required)
    assert len(result['pending_policy_tuples']) == 2 and result['status'] == 'blocked'


@pytest.mark.parametrize('kind', ['route', 'action', 'principal'])
def test_policy_drift_blocks(kind):
    case = valid_case()
    case['ledger'] = ledger()
    row = case['ledger']['routes'][0]
    if kind == 'route': row['id'] = 'different'
    elif kind == 'action': row['scenarios'][0]['action'] = 'action=password'
    else: row['allowed_roles'] = []
    assert validate_execution(**case)['status'] == 'blocked'


def test_binding_hash_tracks_conditions_and_obligations():
    case = valid_case()
    digest = binding_digest(case['bindings'], case['conditions'])
    assert digest != binding_digest(case['bindings'], {'profile-save': 'different'})
    assert digest != binding_digest([], case['conditions'])


@pytest.mark.parametrize('payload', [None, {}, {'key': None}, {'key': [['unhashable']]}, 'not-an-object'])
def test_malformed_evidence_shape_blocks(payload):
    case = valid_case()
    case['evidence'] = [payload]
    assert validate_execution(**case)['status'] == 'blocked'
