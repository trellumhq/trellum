#!/usr/bin/env python3
"""Run isolated browser → Django → PostgreSQL baseline, retaining evidence."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', choices=['L0', 'L1', 'L2', 'L3', 'L4', 'L5'], default='L0')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--browser', choices=['chromium', 'webkit'], default='chromium')
    parser.add_argument('--reports', type=int, choices=[20, 200, 1000], default=20)
    parser.add_argument('--members', type=int, choices=[3, 25, 100], default=3)
    parser.add_argument('--studios', type=int, choices=[1, 10, 50], default=1)
    parser.add_argument('--real-hasher', action='store_true')
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    if artifacts.exists() and any(artifacts.iterdir()):
        parser.error('artifact directory must be empty to prevent mixed evidence')
    artifacts.mkdir(parents=True, exist_ok=True)
    result = {'shard': args.shard, 'sha': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(), 'seed': 'portal-baseline-v1', 'status': 'blocked', 'command': sys.argv, 'pending_shards': ['L1','L2','L3','L4','L5']}
    result['dirty_tree'] = bool(subprocess.check_output(['git','status','--porcelain'], cwd=ROOT, text=True).strip())
    result['scope'] = 'P00a baseline only'
    result['manifest_mapping_status'] = 'pending P00b'
    pg = None
    server = None
    try:
        if (ROOT/'.env').exists():
            raise RuntimeError('Refusing checkout with .env; use a clean task worktree')
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError('Python 3.12 required')
        if importlib.metadata.version('playwright') != '1.62.0':
            raise RuntimeError('Playwright 1.62.0 required')
        result['versions'] = {'python': sys.version, 'playwright': importlib.metadata.version('playwright'), 'node': subprocess.check_output(['node','--version'], text=True).strip()}
        if not result['versions']['node'].startswith('v22.'):
            raise RuntimeError('Node 22 required (use a task-local PATH)')
        if args.shard != 'L0':
            raise RuntimeError(f'{args.shard} is pending implementation; no pass claimed')
        with tempfile.TemporaryDirectory(prefix='portal-p00-') as temporary:
            dbport, httpport = port(), port()
            pg = 'portal-p00-' + uuid.uuid4().hex[:12]
            run(['docker','run','--rm','-d','--name',pg,'-e','POSTGRES_PASSWORD=disposable','-e','POSTGRES_DB=portal_e2e','-p',f'127.0.0.1:{dbport}:5432','postgres:16-alpine'], stdout=subprocess.DEVNULL)
            for _ in range(120):
                if subprocess.run(['docker','exec',pg,'pg_isready','-U','postgres'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError('disposable PostgreSQL did not become ready')
            inherited = {key: value for key, value in os.environ.items() if key in {'PATH','LD_LIBRARY_PATH','PLAYWRIGHT_BROWSERS_PATH','LANG','LC_ALL','HOME','PORTAL_E2E_SQL_DELAY_MS'}}
            env = dict(inherited, DATABASE_URL=f'postgres://postgres:disposable@127.0.0.1:{dbport}/portal_e2e', DJANGO_SETTINGS_MODULE='apps.core.tests.portal_e2e.settings', PORTAL_E2E_DATA_DIR=temporary, PORTAL_E2E_WORK_LOG=str(artifacts/'work.jsonl'), PORTAL_E2E_ARTIFACTS=str(artifacts), PORTAL_E2E_ORIGIN=f'http://127.0.0.1:{httpport}', PORTAL_E2E_BROWSER=args.browser, PORTAL_E2E_REAL_HASHER='1' if args.real_hasher else '0')
            with (artifacts/'migrations.log').open('w') as out:
                run([sys.executable,'manage.py','migrate','--noinput'], env=env, stdout=out, stderr=subprocess.STDOUT)
                run([sys.executable,'manage.py','makemigrations','--check','--dry-run'], env=env, stdout=out, stderr=subprocess.STDOUT)
            code = f'import django; django.setup(); from apps.core.tests.portal_e2e.seed import seed; seed({args.reports}, {args.members}, {args.studios})'
            run([sys.executable,'-c',code], env=env)
            run([sys.executable,'-m','apps.core.tests.portal_e2e.manifest',str(artifacts/'routes.json')], env=env)
            with (artifacts/'server.log').open('w') as log:
                server = subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{httpport}','--noreload','--insecure'], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                for _ in range(120):
                    if server.poll() is not None:
                        raise RuntimeError('Django exited; see server.log')
                    try:
                        urlopen(env['PORTAL_E2E_ORIGIN']+'/healthz', timeout=1).close()
                        break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise RuntimeError('Django did not become ready')
                # Database is already migrated/seeded: never let pytest create a substitute test DB.
                testenv = dict(env, PYTEST_ADDOPTS='')
                completed = subprocess.run([sys.executable,'-c','import django; django.setup(); import pytest; raise SystemExit(pytest.main(__import__("sys").argv[1:]))','apps/core/tests/portal_e2e/test_baseline.py','--confcutdir=apps/core/tests/portal_e2e','-v','--junitxml='+str(artifacts/'junit.xml'),'-p','no:django','-o','DJANGO_SETTINGS_MODULE='], cwd=ROOT, env=testenv)
                result['status'] = 'pass' if completed.returncode == 0 else 'fail'
                result['node_ids'] = [case.get('classname')+'::'+case.get('name') for case in ET.parse(artifacts/'junit.xml').iter('testcase')]
                if any(True for _ in ET.parse(artifacts/'junit.xml').iter('skipped')):
                    result['status'] = 'blocked'
                    result['reason'] = 'required baseline test skipped'
                if (artifacts/'work.jsonl').exists():
                    samples = [json.loads(line) for line in (artifacts/'work.jsonl').read_text().splitlines()]
                    ledger = []
                    for path in sorted(set(row['path'] for row in samples)):
                        rows = [row for row in samples if row['path'] == path]
                        ledger.append({'path': path, 'window': 'complete live HTTP request', 'cache': 'mixed baseline; cold/warm split pending', 'fixture': {'reports': args.reports, 'members': args.members, 'studios': args.studios}, 'sql_count_baseline_max': max(row['sql_count'] for row in rows), 'sql_ms_baseline_max': max(row['sql_ms'] for row in rows), 'http_ms_baseline_max': max(row['elapsed_ms'] for row in rows), 'threshold': {'sql_count': max(row['sql_count'] for row in rows), 'http_ms': max(row['elapsed_ms'] for row in rows)}, 'reviewer': 'pending coordinator review', 'threshold_scope': 'observed baseline ceiling; not approved feature cap', 'status': 'baseline-only'})
                    (artifacts/'budgets.json').write_text(json.dumps(ledger, indent=2))
                result['baseline_status'] = result['status']
                if args.all:
                    result['status'] = 'blocked'
                    result['reason'] = 'L1–L5 and route/action verification pending'
    except Exception as exc:
        result['reason'] = str(exc)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        if pg:
            subprocess.run(['docker','rm','-f',pg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (artifacts/'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'pass' else 1

if __name__ == '__main__':
    raise SystemExit(main())
