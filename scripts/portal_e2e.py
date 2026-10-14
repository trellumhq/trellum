#!/usr/bin/env python3
"""Run owned Gunicorn/PostgreSQL gates; incomplete journey coverage blocks."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.core.tests.portal_e2e.bindings import (BINDINGS, CONDITIONS, INFRASTRUCTURE_CONDITIONS,
    JOURNEY_MODULES, REVIEWED_REQUIREMENTS, binding_digest, infrastructure_bindings)
from apps.core.tests.portal_e2e.coverage import coverage_summary, source_digest, validate_execution
from apps.core.tests.portal_e2e.runtime import Runtime

BASELINE = ['apps/core/tests/portal_e2e/test_baseline.py']
INFRASTRUCTURE = ['apps/core/tests/portal_e2e/' + name for name in (
    'test_coverage_contract.py', 'test_runtime_contract.py', 'test_fixture_isolation.py', 'test_runtime_smoke.py')]
SHARDS = {'L0': BASELINE, **JOURNEY_MODULES}


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def run_tests(modules, env, directory):
    directory.mkdir()
    env = dict(env, PYTEST_ADDOPTS='', PORTAL_E2E_ARTIFACTS=str(directory))
    completed = subprocess.run([sys.executable, '-c',
        'import django; django.setup(); import pytest; raise SystemExit(pytest.main(__import__("sys").argv[1:]))',
        *modules, '--confcutdir=apps/core/tests/portal_e2e', '-v', '--junitxml=' + str(directory / 'junit.xml'),
        '-p', 'no:django', '-o', 'DJANGO_SETTINGS_MODULE='], cwd=ROOT, env=env)
    collected = json.loads((directory / 'collection.json').read_text()) if (directory / 'collection.json').exists() else []
    reports = lines(directory / 'outcomes.jsonl')
    outcomes = {}
    for node in collected:
        rows = [row for row in reports if row['node_id'] == node]
        if len(rows) != 3 or {row['phase'] for row in rows} != {'setup', 'call', 'teardown'}:
            outcomes[node] = 'incomplete'
        elif any(row['xfail'] or row['outcome'] == 'skipped' for row in rows):
            outcomes[node] = 'skipped-or-xfail'
        else:
            outcomes[node] = 'passed' if all(row['outcome'] == 'passed' for row in rows) else 'failed'
    status = 'pass' if completed.returncode == 0 and collected and all(value == 'passed' for value in outcomes.values()) else 'blocked'
    return {'status': status, 'exit_code': completed.returncode, 'collected': collected, 'outcomes': outcomes}


def write_budgets(artifacts):
    samples = lines(artifacts / 'work.jsonl')
    counts = json.loads((artifacts / 'seed.json').read_text())['counts']
    ledger = []
    keys = sorted(set((row['method'], row['role'], row['path'], row['cache_phase']) for row in samples))
    for method, role, path, phase in keys:
        rows = [row for row in samples if (row['method'], row['role'], row['path'], row['cache_phase']) == (method, role, path, phase)]
        ledger.append({'method': method, 'role': role, 'path': path, 'cache_phase': phase,
            'window': 'complete HTTP response through stream completion', 'sample_count': len(rows), 'fixture_counts': counts,
            'baseline_max': {metric: max(row[metric] for row in rows) for metric in
                ('sql_count', 'sql_ms', 'storage_count', 'storage_ms', 'response_bytes', 'elapsed_ms')},
            'approved_thresholds': None, 'reviewer': 'pending coordinator review',
            'status': 'measured-baseline; not an acceptance budget'})
    (artifacts / 'budgets.json').write_text(json.dumps(ledger, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', choices=list(SHARDS), default='L0')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--infrastructure', action='store_true', help='Phase 1 proof only, not journey coverage')
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--browser', choices=['chromium', 'webkit'], default='chromium')
    for name, choices, default in (('reports', [20, 200, 1000], 20), ('members', [3, 25, 100], 3),
        ('studios', [1, 10, 50], 1), ('metrics', [20, 500, 2000], 20), ('schedules', [1, 10, 50], 1), ('rules', [1, 10, 50], 1)):
        parser.add_argument('--' + name, type=int, choices=choices, default=default)
    parser.add_argument('--real-hasher', action='store_true', help='Compatibility flag; PBKDF2 is always used')
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    if artifacts.exists() and any(artifacts.iterdir()):
        parser.error('artifact directory must be empty to prevent mixed evidence')
    artifacts.mkdir(parents=True, exist_ok=True)
    result = {'shard': args.shard, 'sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'seed': 'portal-baseline-v2', 'status': 'blocked', 'command': sys.argv, 'cwd': str(ROOT), 'python': sys.executable,
              'runtime_module': str(Path(sys.modules[Runtime.__module__].__file__).resolve()),
              'pending_shards': [shard for shard, modules in SHARDS.items() if not modules]}
    result['dirty_tree'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    result['source_digest'] = source_digest(ROOT)
    result['binding_digest'] = binding_digest()
    result['scope'] = 'journey gate' if args.all else 'infrastructure only' if args.infrastructure else 'L0 baseline only' if args.shard == 'L0' else 'journey gate'
    result['manifest_mapping_status'] = 'not yet validated'
    try:
        if sys.version_info[:2] != (3, 12): raise RuntimeError('Python 3.12 required')
        if importlib.metadata.version('playwright') != '1.62.0': raise RuntimeError('Playwright 1.62.0 required')
        node = subprocess.check_output(['node', '--version'], text=True).strip()
        if not node.startswith('v22.'): raise RuntimeError('Node 22 required (use task-local PATH)')
        result['versions'] = {'python': sys.version, 'playwright': importlib.metadata.version('playwright'),
                              'node': node, 'gunicorn': importlib.metadata.version('gunicorn'), 'postgres_image': 'postgres:16-alpine'}
        ledger = json.loads((ROOT / 'apps/core/tests/portal_e2e/routes.json').read_text())
        runs = []
        if not args.infrastructure and not args.all and not SHARDS[args.shard]:
            result['reason'] = f'{args.shard} has no implemented journey bindings; empty shards never pass'
            result['coverage'] = coverage_summary(ledger, BINDINGS, {'status': 'blocked', 'errors': ['no executed journey bindings']})
        else:
            with Runtime(artifacts, browser=args.browser, reports=args.reports, members=args.members,
                         studios=args.studios, metrics=args.metrics, schedules=args.schedules, rules=args.rules) as runtime:
                env = runtime.environment(runtime.template_db, runtime.template_data)
                env['PORTAL_E2E_RUNTIME'] = str(runtime.descriptor)
                subprocess.run([sys.executable, '-m', 'apps.core.tests.portal_e2e.manifest', str(artifacts / 'routes.json')], cwd=ROOT, env=env, check=True)
                mapping = json.loads((artifacts / 'routes.json').read_text())
                result['manifest_mapping_status'] = mapping['mapping_status']
                if mapping['mapping_status'] != 'complete': raise RuntimeError('manifest mapping guard failed')
                code = ('import django; django.setup(); import json; from django.contrib.auth import get_user_model; '
                        'from apps.core.tests.portal_e2e.identities import ROLES; '
                        'users={role: (get_user_model().objects.get(email=email).pk if email else None) for role,email in ROLES.items()}; '
                        "print(json.dumps({role:{'actor_id':None if role=='inactive' else pk,'impersonator_id':users['operator'] if role=='impersonated' else None} for role,pk in users.items()}))")
                registry = json.loads(subprocess.check_output([sys.executable, '-c', code], cwd=ROOT, env=env, text=True))
                if args.all or (args.shard == 'L0' and not args.infrastructure):
                    with runtime.unit('L0-baseline') as unit:
                        run = run_tests(BASELINE, unit.env, artifacts / 'baseline')
                        runs.append(run)
                        result['baseline_status'] = run['status']
                if args.infrastructure or args.all:
                    run = run_tests(INFRASTRUCTURE, env, artifacts / 'infrastructure')
                    runs.append(run)
                    result['infrastructure_status'] = run['status']
                for shard in (list(JOURNEY_MODULES) if args.all else [args.shard]):
                    if not args.infrastructure and shard in JOURNEY_MODULES and JOURNEY_MODULES[shard]:
                        runs.append(run_tests(JOURNEY_MODULES[shard], env, artifacts / shard.lower()))
                write_budgets(artifacts)
                result['runtime_cleanup'] = {'live_units': len(runtime.units), 'owned_container_removed_on_exit': True}
            collected = [node_id for run in runs for node_id in run['collected']]
            outcomes = {node_id: value for run in runs for node_id, value in run['outcomes'].items()}
            result['node_ids'] = collected
            assertions = lines(artifacts / 'assertions.jsonl')
            evidence = [row for row in assertions if row.get('scope') == 'journey']
            selected_ledger = ledger if args.all or args.infrastructure or args.shard == 'L0' else {'routes': [row for row in ledger['routes'] if row['shard'] == args.shard]}
            selected_routes = {row['id'] for row in selected_ledger['routes']}
            selected_bindings = [row for row in BINDINGS if row.route in selected_routes]
            execution = validate_execution(selected_bindings, collected, outcomes, evidence, lines(artifacts / 'work.jsonl'),
                source=result['source_digest'], binding_hash=result['binding_digest'], conditions=CONDITIONS,
                ledger=selected_ledger, identity_registry=registry)
            result['coverage'] = coverage_summary(selected_ledger, selected_bindings, execution, REVIEWED_REQUIREMENTS)
            result['status'] = 'pass' if runs and all(run['status'] == 'pass' for run in runs) else 'blocked'
            if args.infrastructure or args.all:
                infrastructure_execution = validate_execution(infrastructure_bindings(args.browser), collected, outcomes,
                    [row for row in assertions if row.get('scope') == 'infrastructure'], lines(artifacts / 'work.jsonl'),
                    source=result['source_digest'], binding_hash=result['binding_digest'],
                    conditions=INFRASTRUCTURE_CONDITIONS, ledger=ledger, identity_registry=registry)
                result['infrastructure_execution'] = infrastructure_execution
                if infrastructure_execution['status'] != 'pass':
                    result['status'] = result['infrastructure_status'] = 'blocked'
            if args.all or (not args.infrastructure and args.shard != 'L0'):
                result['status'] = result['coverage']['status'] if result['status'] == 'pass' else 'blocked'
                result['reason'] = 'all required journey/principal/condition bindings must execute and pass'
            if source_digest(ROOT) != result['source_digest']:
                result['status'], result['reason'] = 'blocked', 'source changed during execution'
    except Exception as exc:
        result['reason'] = str(exc)
    finally:
        (artifacts / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps({key: value for key, value in result.items() if key not in ('coverage', 'node_ids')}, indent=2))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
