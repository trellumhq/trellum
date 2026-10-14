"""Static contract for the key-journey bindings (no browser, no database)."""
import json
from pathlib import Path

from .bindings import (BINDINGS, CONDITIONS, JOURNEY_MODULES, KEY_BINDINGS, KEY_CONDITIONS,
                       OUT_OF_SCOPE_REMAINDER)
from .coverage import coverage_summary

LEDGER = json.loads((Path(__file__).with_name('routes.json')).read_text())
ROUTES = {row['id']: row for row in LEDGER['routes']}
HEAVY_MOBILE = {'key-login-dashboard', 'key-catalog-back', 'key-operations-filter', 'key-operations-run'}


def policy_principals(binding):
    scenarios = [row for row in ROUTES[binding.route]['scenarios']
                 if (row['method'], row.get('action')) == (binding.method, binding.action)]
    assert len(scenarios) == 1, binding.key
    policy = scenarios[0] if 'allowed_roles' in scenarios[0] else ROUTES[binding.route]
    return {*policy.get('allowed_roles', ()), *policy.get('denied_roles', ()),
            *policy.get('conditional_roles', {}), *policy.get('not_applicable_roles', {})}


def test_every_key_binding_matches_reviewed_route_policy_and_its_shard_module():
    assert BINDINGS == KEY_BINDINGS and len({row.key for row in BINDINGS}) == len(BINDINGS)
    for binding in BINDINGS:
        assert binding.principal in policy_principals(binding), binding.key
        assert binding.condition in KEY_CONDITIONS and binding.condition in CONDITIONS
        shard = ROUTES[binding.route]['shard']
        module = binding.node_id.split('::')[0]
        assert module in JOURNEY_MODULES[shard], (binding.key, shard)
        assert Path(__file__).parent.joinpath(Path(module).name).exists()
        assert 'actor' in binding.obligations and 'observed-actor' in binding.obligations


def test_webkit_mobile_variants_exist_only_for_the_reviewed_journeys():
    mobile = {row.condition for row in BINDINGS if row.browser == 'webkit'}
    assert mobile == HEAVY_MOBILE
    assert all(row.viewport == '390x844' for row in BINDINGS if row.browser == 'webkit')
    assert all(row.browser == 'chromium' and row.viewport == '1440x900' for row in BINDINGS if row.condition not in HEAVY_MOBILE)


def test_every_journey_condition_has_at_least_one_binding_and_a_desktop_variant():
    assert {row.condition for row in BINDINGS} == set(KEY_CONDITIONS)
    for condition in KEY_CONDITIONS:
        assert any(row.browser == 'chromium' for row in BINDINGS if row.condition == condition)


def passing_execution():
    return {'status': 'pass', 'errors': [], 'executed_bindings': len(BINDINGS)}


def test_remainder_is_out_of_scope_not_pending_when_the_bound_journeys_pass():
    summary = coverage_summary(LEDGER, BINDINGS, passing_execution(), None, OUT_OF_SCOPE_REMAINDER)
    assert summary['status'] == 'pass' and summary['pending_policy_tuples'] == []
    assert summary['out_of_scope_policy_tuples'] > 7000
    assert summary['out_of_scope_reason'] == OUT_OF_SCOPE_REMAINDER


def test_out_of_scope_never_hides_a_failed_or_missing_bound_journey():
    blocked = {'status': 'blocked', 'errors': ['required node did not pass'], 'executed_bindings': len(BINDINGS)}
    assert coverage_summary(LEDGER, BINDINGS, blocked, None, OUT_OF_SCOPE_REMAINDER)['status'] == 'blocked'
    empty = {'status': 'blocked', 'errors': [], 'executed_bindings': 0, 'empty': True}
    assert coverage_summary(LEDGER, [], empty, None, OUT_OF_SCOPE_REMAINDER)['status'] == 'blocked'


def test_without_an_explicit_scope_decision_the_unbound_remainder_still_blocks():
    summary = coverage_summary(LEDGER, BINDINGS, passing_execution())
    assert summary['status'] == 'blocked' and summary['pending_policy_tuples']
