"""Offline evaluator tests; constructed predictions test scoring, never accuracy."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from scripts.assistant_eval import (
    DEFAULT_SUITE, ROOT, contract, evaluate, evidence_identity, load_suite, samples,
)


def prediction(suite, case):
    expected = case['expected']
    result = {'outcome': expected['status']}
    if expected['status'] == 'answered':
        result.update(rows=deepcopy(expected['rows']),
                      evidence={**evidence_identity(suite, case), 'rows': deepcopy(expected['rows'])},
                      trace=[{'source_id': 'commerce', **deepcopy(read)} for read in case['reads']])
    return result


def captures(suite):
    return {'schema_version': 1, 'metadata': {'kind': 'synthetic_test'},
            'predictions': {key: prediction(suite, case) for key, case, _ in samples(suite)}}


def test_fixture_inventory_and_independent_arithmetic():
    suite = load_suite()
    assert len(suite['intents']) == 40
    assert len(suite['metrics']) == 10
    assert len(list(samples(suite))) == 80
    assert {c['split'] for c in suite['intents']} == {'baseline', 'heldout'}
    results = evaluate(suite)
    assert results['failed'] == 0
    assert results['checked'] == 37
    assert results['unmeasured'] == 3
    assert results['model_accuracy'] is None and results['model_evaluation'] is False
    # These hand calculations do not call the executor or read expected values.
    assert 100+300+200+0+50+150 == 800
    assert (1+9+4+0+2+3)/(10+90+20+10+10+60) == .095
    assert (600+800) == 1400  # Last balance, never the sum of daily balances.
    changed = deepcopy(suite)
    changed['rows'][0][2] += 100
    assert contract(changed, changed['intents'][0])[0] is False
    changed = deepcopy(suite)
    changed['intents'][0]['expected']['rows'] = [[900]]
    assert contract(changed, changed['intents'][0])[0] is False


def test_scoring_constructed_captures_is_not_a_model_run():
    suite = load_suite()
    report = evaluate(suite, captures(suite))
    assert report['counts'] == dict(correct_answer=56, incorrect=0, clarification=8,
                                    abstention=16, unnecessary_abstention=0)
    assert report['correct_outcome_rate'] == 1
    assert report['direct_answer_coverage'] == 56/80
    assert report['correct_direct_answer_coverage'] == 1
    assert report['model_evaluation'] is False


def test_abstain_all_cannot_pass():
    suite = load_suite()
    captured = captures(suite)
    captured['predictions'] = {key: {'outcome': 'abstained'} for key, _, _ in samples(suite)}
    report = evaluate(suite, captured)
    assert report['counts']['unnecessary_abstention'] == 56
    assert report['direct_answer_coverage'] == 0
    assert report['correct_outcome_rate'] == 0


@pytest.mark.parametrize('mutation', ['wrong_value', 'wrong_evidence_value', 'revision', 'actor', 'scope',
                                     'metric', 'period', 'unauthorized', 'extra_read', 'nonfinite'])
def test_wrong_answers_and_evidence_fail(mutation):
    suite = load_suite()
    captured = captures(suite)
    item = captured['predictions']['revenue_total:0']
    if mutation == 'wrong_value':
        item['rows'] = [[801]]
    elif mutation == 'wrong_evidence_value':
        item['evidence']['rows'] = [[799]]
    elif mutation == 'revision':
        item['evidence']['source_revision'] = 'other'
    elif mutation == 'actor':
        item['evidence']['actor'] = 'outsider'
    elif mutation == 'scope':
        item['evidence']['scope'] = 'studio:other'
    elif mutation == 'metric':
        item['evidence']['metric_id'] = 'orders'
    elif mutation == 'period':
        item['evidence']['params']['end'] = '2026-10-03'
    elif mutation == 'unauthorized':
        item['trace'] = [{'source_id': 'commerce', 'sql': 'SELECT * FROM private.payroll', 'rows': []}]
    elif mutation == 'extra_read':
        item['trace'] = [{'source_id': 'commerce', 'sql': 'SELECT SUM(revenue) FROM pilot.daily', 'rows': [[800]]}]
    else:
        item['rows'] = [[float('nan')]]
    assert evaluate(suite, captured)['counts']['incorrect'] == 1


def test_trace_successive_reads_and_denied_read_before_abstention():
    suite = load_suite()
    captured = captures(suite)
    captured['predictions']['successive_reads:0']['trace'].pop(0)
    captured['predictions']['report_scope:0']['trace'] = [
        {'source_id': 'commerce', 'sql': 'SELECT SUM(revenue) FROM pilot.daily', 'rows': [[800]]}]
    captured['predictions']['unregistered_net:0']['trace'][0]['rows'] = [[800]]
    assert evaluate(suite, captured)['counts']['incorrect'] == 3


def test_missing_predictions_and_split_denominators():
    suite = load_suite()
    report = evaluate(suite, {'schema_version': 1, 'predictions': {}}, 'heldout')
    assert report['sample_count'] == 20
    assert report['intent_count'] == 10
    assert report['supplied_count'] == 0
    assert report['counts']['incorrect'] == 20
    assert report['correct_outcome_rate'] == 0
    with pytest.raises(ValueError, match='Unknown prediction'):
        evaluate(suite, {'schema_version': 1, 'predictions': {'invented': {}}})


def test_cli_reproducible_and_failure_exit(tmp_path):
    command = [sys.executable, str(ROOT / 'scripts/assistant_eval.py')]
    first = subprocess.run(command, cwd=tmp_path, capture_output=True, check=True, text=True)
    second = subprocess.run(command, cwd=tmp_path, capture_output=True, check=True, text=True)
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)['mode'] == 'deterministic_contract'
    supplied = tmp_path / 'capture.json'
    supplied.write_text(json.dumps({'schema_version': 1, 'predictions': {}}))
    bad = subprocess.run([*command, '--predictions', str(supplied)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert json.loads(bad.stdout)['counts']['incorrect'] == 80
    changed = load_suite(DEFAULT_SUITE)
    changed['intents'][0]['expected']['rows'] = [[999]]
    suite_file = tmp_path / 'suite.json'
    suite_file.write_text(json.dumps(changed))
    assert subprocess.run([*command, '--suite', str(suite_file)], capture_output=True).returncode == 1


def test_source_oracle_detects_changed_truth_and_empty_is_a_valid_answer():
    suite = load_suite()
    case = next(c for c in suite['intents'] if c['id'] == 'source_empty')
    assert contract(suite, case)[0] is True
    captured = captures(suite)
    captured['predictions']['source_empty:0']['rows'] = [[0]]
    assert evaluate(suite, captured)['counts']['incorrect'] == 1
    source = next(c for c in suite['intents'] if c['id'] == 'unregistered_net')
    source['expected']['rows'] = [[999]]
    report = evaluate(suite, captures(suite))
    assert report['failed'] == 1  # Agreement with a corrupted fixture cannot pass.
