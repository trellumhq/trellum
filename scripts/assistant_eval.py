"""Offline synthetic calculation contracts and supplied structured-capture scoring.

No provider invocation, intent router, portal authorization emulator or network IO.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trellum.data.assistant_query import QueryRejected, validate_query
from trellum.metric_execution import MetricUnavailable, execute_metric, make_binding
from trellum.metrics import Metric

DEFAULT_SUITE = ROOT / 'apps/assistant/tests/fixtures/reliability/suite-v1.json'
CLASSES = ('correct_answer', 'incorrect', 'clarification', 'abstention', 'unnecessary_abstention')


def load_suite(path=DEFAULT_SUITE):
    suite = json.loads(Path(path).read_text())
    if suite['schema_version'] != 1:
        raise ValueError('Unsupported suite version')
    ids = [case['id'] for case in suite['intents']]
    if len(ids) != len(set(ids)) or any(len(c['phrasings']) < 2 for c in suite['intents']):
        raise ValueError('Unique intents with at least two phrasings required')
    return suite


def samples(suite, split='all'):
    for case in suite['intents']:
        if split in ('all', case['split']):
            for index, question in enumerate(case['phrasings']):
                yield f"{case['id']}:{index}", case, question


def close(actual, expected, tolerance):
    """Compare shape and values, distinguishing null, zero, strings and booleans."""
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            close(a, e, tolerance) for a, e in zip(actual, expected))
    if type(expected) in (float, int):
        return type(actual) in (float, int) and math.isfinite(actual) and math.isclose(
            actual, expected, abs_tol=tolerance['abs'], rel_tol=tolerance['rel'])
    return type(actual) is type(expected) and actual == expected


def metric_inputs(suite, case):
    mid = case['expected']['metric_id']
    definition = suite['metrics'][mid]
    semantics = dict(timezone=suite['timezone'], unit=definition['unit'], currency=definition.get('currency'),
                     grain='day', dimensions=['platform'], additive_dimensions=['platform'] if definition.get('additive', True) else [],
                     filters=['platform'], time_aggregation=definition.get('time', 'sum'),
                     null_policy='propagate', zero_denominator='null', max_age_seconds=86400)
    metric = Metric(name=mid, dataset='daily', date_col='day', agg=definition.get('agg', 'sum'),
                    column=definition.get('column'), numerator=definition.get('numerator'),
                    denominator=definition.get('denominator'), execution=semantics,
                    time_agg={'last': 'last', 'average': 'avg'}.get(definition.get('time')))
    needed = ['day', 'platform', *([metric.numerator, metric.denominator] if metric.agg == 'ratio' else [metric.column])]
    entry = make_binding(metric, output_dataset='daily', columns={c: c for c in needed},
                         coverage_start='2026-10-01', coverage_end='2026-10-04',
                         source_revision=suite['source_revision'], build_id=suite['build_id'],
                         generated_at=suite['generated_at'])
    rows = deepcopy(suite['rows'])
    variant = case['variant']
    if variant == 'missing_day':
        rows = [r for r in rows if r[0] != '2026-10-02']
    elif variant == 'empty':
        rows = []
    elif variant == 'null_value':
        rows[0][suite['columns'].index(metric.column)] = None
    elif variant == 'zero_denominator':
        for row in rows:
            row[suite['columns'].index(metric.denominator)] = 0
    payload = {'components': {'kpi': {'dataset_id': 'daily', 'kpis': [{'metric': mid, **metric.agg_spec()}]}},
               '_ds_daily': {'_cols': suite['columns'], '_data': rows}}
    return payload, entry


def source_rows(suite, sql, relations):
    """Validate actual PostgreSQL scope, calculate compatible fixture SQL in SQLite.

    SQLite is an independent synthetic arithmetic oracle, not a connector test.
    Never use this path for customer queries or dialect/cancellation certification.
    """
    validate_query(sql, relations)
    with sqlite3.connect(':memory:') as connection:
        connection.execute("ATTACH DATABASE ':memory:' AS pilot")
        # Names come exclusively from the versioned fixture, never a prediction.
        connection.execute('CREATE TABLE pilot.daily (' + ','.join(suite['columns']) + ')')
        connection.executemany('INSERT INTO pilot.daily VALUES (' + ','.join('?' for _ in suite['columns']) + ')', suite['rows'])
        connection.execute('CREATE TABLE pilot.events (day, platform, user_id)')
        connection.executemany('INSERT INTO pilot.events VALUES (?, ?, ?)', suite['source']['events_rows'])
        return [list(row) for row in connection.execute(sql).fetchall()]


def contract(suite, case):
    expected = case['expected']
    if case['reads']:
        try:
            calculated = []
            for read in case['reads']:
                result = source_rows(suite, read['sql'], case['permitted_relations'])
                calculated.append(result)
                if not close(result, read['rows'], expected['tolerance']):
                    return False, 'source arithmetic mismatch'
        except QueryRejected:
            return expected['status'] == 'access_denied', 'source scope rejection'
        if case['source_calculation'] not in ('last_read', 'second_minus_first'):
            raise ValueError('Unsupported source reference calculation')
        final = ([[calculated[1][0][0] - calculated[0][0][0]]]
                 if case['source_calculation'] == 'second_minus_first' else calculated[-1])
        return (expected['status'] == 'answered' and close(final, expected['rows'], expected['tolerance'])), 'synthetic source arithmetic and parser scope only'
    if expected['metric_id'] and expected['status'] != 'access_denied':
        payload, entry = metric_inputs(suite, case)
        try:
            result = execute_metric(payload, entry, **expected['params'], now=datetime.fromisoformat(suite['clock']))
            return (result['status'] == expected['status'] and close(result['rows'], expected['rows'], expected['tolerance'])), 'metric executor'
        except MetricUnavailable as exc:
            return exc.status == expected['status'], 'metric executor rejection'
    return None, 'semantic ambiguity / actor authorization requires captured behavior and portal tests'


def evidence_identity(suite, case):
    return {key: suite[key] for key in ('build_id', 'artifact_revision', 'source_revision')} | {
        'actor': case['actor'], 'scope': case['scope'], 'metric_id': case['expected']['metric_id'],
        'params': deepcopy(case['expected']['params']), 'clock': suite['clock'], 'timezone': suite['timezone']}


def score(suite, case, prediction):
    """Structured blocks only: free prose is deliberately outside this contract."""
    expected = case['expected']
    if prediction is None:
        return 'incorrect', 'missing prediction'
    outcome = prediction.get('outcome')
    # Validate every read, including extra reads and reads preceding abstention.
    trace = prediction.get('trace', [])
    if not isinstance(trace, list):
        return 'incorrect', 'malformed trace'
    for read in trace:
        if not isinstance(read, dict) or read.get('source_id') != suite['source']['id']:
            return 'incorrect', 'unknown source'
        try:
            rows = source_rows(suite, read.get('sql'), case['permitted_relations'])
        except (QueryRejected, sqlite3.Error, TypeError):
            return 'incorrect', 'unauthorized or unsupported source read'
        if not close(read.get('rows'), rows, expected['tolerance']):
            return 'incorrect', 'trace result disagrees with synthetic source'
    if outcome == 'answered':
        if expected['status'] != 'answered':
            return 'incorrect', 'answer supplied when clarification or abstention required'
        evidence = prediction.get('evidence', {})
        if not isinstance(evidence, dict) or any(evidence.get(k) != v for k, v in evidence_identity(suite, case).items()):
            return 'incorrect', 'mismatched evidence identity or parameters'
        if not close(prediction.get('rows'), expected['rows'], expected['tolerance']):
            return 'incorrect', 'wrong answer'
        if not close(evidence.get('rows'), prediction.get('rows'), expected['tolerance']):
            return 'incorrect', 'answer disagrees with evidence'
        if case['reads']:
            if len(trace) != len(case['reads']):
                return 'incorrect', 'missing or extra investigation reads'
            for actual, wanted in zip(trace, case['reads']):
                if (validate_query(actual['sql'], case['permitted_relations']) != validate_query(wanted['sql'], case['permitted_relations'])
                        or not close(actual['rows'], wanted['rows'], expected['tolerance'])):
                    return 'incorrect', 'investigation does not match reviewed read sequence'
        elif trace:
            return 'incorrect', 'unexpected source path for canonical metric'
        return 'correct_answer', ''
    if outcome == 'clarification_required':
        if expected['status'] == outcome:
            return 'clarification', ''
        return ('unnecessary_abstention' if expected['status'] == 'answered' else 'incorrect'), 'unneeded clarification'
    if outcome in ('unsupported', 'incomplete', 'stale', 'access_denied', 'abstained'):
        if expected['status'] == 'answered':
            return 'unnecessary_abstention', 'answerable question'
        if outcome == expected['status']:
            return 'abstention', ''
    return 'incorrect', 'wrong or unspecified limitation'


def evaluate(suite, predictions=None, split='all'):
    selected = list(samples(suite, split))
    output = dict(schema_version=1, suite_id=suite['suite_id'], split=split,
                  suite_sha256=hashlib.sha256(json.dumps(suite, sort_keys=True).encode()).hexdigest(),
                  mode='deterministic_contract' if predictions is None else 'captured_predictions',
                  sample_count=len(selected), intent_count=len({c['id'] for _, c, _ in selected}))
    results = []
    for case in suite['intents']:
        if split in ('all', case['split']):
            passed, detail = contract(suite, case)
            results.append(dict(intent_id=case['id'], passed=passed, detail=detail))
    output['failed'] = sum(r['passed'] is False for r in results)
    if predictions is None:
        output.update(model_evaluation=False, model_accuracy=None, results=results,
                      checked=sum(r['passed'] is not None for r in results),
                      failed=sum(r['passed'] is False for r in results),
                      unmeasured=sum(r['passed'] is None for r in results))
        return output
    output['contract_results'] = results
    if not isinstance(predictions, dict):
        raise ValueError('Predictions capture must be an object')
    if not isinstance(predictions.get('metadata', {}), dict):
        raise ValueError('Capture metadata must be an object')
    if predictions.get('schema_version') != 1 or not isinstance(predictions.get('predictions'), dict):
        raise ValueError('Expected schema_version 1 and predictions keyed by sample ID')
    supplied = predictions['predictions']
    known = {key for key, _, _ in samples(suite)}
    if set(supplied) - known:
        raise ValueError('Unknown prediction sample IDs')
    if any(not isinstance(value, dict) for value in supplied.values()):
        raise ValueError('Predictions must be structured objects')
    counts = dict.fromkeys(CLASSES, 0)
    results = []
    answerable = direct = 0
    for key, case, _ in selected:
        classification, reason = score(suite, case, supplied.get(key))
        counts[classification] += 1
        answerable += case['expected']['status'] == 'answered'
        direct += supplied.get(key, {}).get('outcome') == 'answered'
        results.append(dict(sample_id=key, classification=classification, reason=reason))
    n = len(selected)
    output.update(model_evaluation=predictions.get('metadata', {}).get('kind') == 'model_capture',
                  capture_metadata=predictions.get('metadata', {}),
                  capture_authenticity='supplied, not independently verified', counts=counts,
                  supplied_count=sum(key in supplied for key, _, _ in selected),
                  answerable_count=answerable, direct_answer_count=direct,
                  direct_answer_coverage=direct/n if n else None,
                  correct_direct_answer_coverage=counts['correct_answer']/answerable if answerable else None,
                  correct_outcome_rate=sum(counts[k] for k in ('correct_answer', 'clarification', 'abstention'))/n if n else None,
                  results=results)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--predictions', type=Path, help='Supplied captured structured predictions/trace JSON; never invokes a model')
    parser.add_argument('--split', choices=('all', 'baseline', 'heldout'), default='all')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        result = evaluate(load_suite(args.suite), json.loads(args.predictions.read_text()) if args.predictions else None, args.split)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end='')
    return int(result.get('failed', 0) > 0 or result.get('counts', {}).get('incorrect', 0) > 0
               or result.get('counts', {}).get('unnecessary_abstention', 0) > 0)


if __name__ == '__main__':
    raise SystemExit(main())
