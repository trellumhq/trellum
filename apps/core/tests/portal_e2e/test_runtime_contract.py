"""Resource and evidence contracts that do not require a Docker daemon."""
from pathlib import Path
import pytest
from .runtime import Runtime, relocate_files, safe_env


def test_environment_does_not_inherit_product_secrets(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'production-must-not-leak')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'secret')
    assert not {'DATABASE_URL', 'SMTP_PASSWORD', 'AWS_SECRET_ACCESS_KEY'} & safe_env().keys()


def test_file_relocation_preserves_template(tmp_path):
    import shutil
    template = tmp_path / 'template'
    template.mkdir()
    content = f'remote = file://{template}/remote.git\n'
    (template / 'config').write_text(content)
    clone = tmp_path / 'clone'
    shutil.copytree(template, clone)
    relocate_files(clone, template, clone)
    assert (template / 'config').read_text() == content
    assert (clone / 'config').read_text() == content.replace(str(template), str(clone))


def test_symlink_cannot_escape_unit(tmp_path):
    directory = tmp_path / 'clone'
    directory.mkdir()
    (directory / 'link').symlink_to(tmp_path)
    with pytest.raises(RuntimeError, match='symlink'):
        relocate_files(directory, '/template', directory)


def test_binary_path_relocation_fails_instead_of_corrupting_git(tmp_path):
    (tmp_path / 'binary').write_bytes(b'\xff/template\0')
    with pytest.raises(RuntimeError, match='binary'):
        relocate_files(tmp_path, '/template', tmp_path)


def test_runtime_environment_uses_disposable_paths(tmp_path):
    runtime = Runtime(tmp_path)
    runtime.dbport, runtime.static_root = 1234, tmp_path / 'static'
    env = runtime.environment('unit_example', tmp_path / 'unit')
    assert env['PORTAL_E2E_REAL_HASHER'] == '1'
    assert env['DATABASE_URL'].endswith('/unit_example')
    assert env['PORTAL_E2E_DATA_DIR'] == str(tmp_path / 'unit')
    assert env['PORTAL_E2E_STATIC_ROOT'] == str(tmp_path / 'static')


def test_owner_cannot_close_active_contexts(tmp_path):
    runtime = Runtime(tmp_path)
    runtime.units['owned-unit'] = object()
    with pytest.raises(RuntimeError, match='live unit'):
        runtime.close()


def test_failed_create_does_not_remove_an_unowned_container(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path)
    runtime.container = 'name-not-successfully-created'
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: pytest.fail('unowned container removal'))
    runtime.close()


def test_failed_owned_cleanup_cannot_claim_success(tmp_path, monkeypatch):
    import subprocess
    runtime = Runtime(tmp_path)
    runtime.container, runtime.container_created = 'owned', True
    def failure(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr('subprocess.run', failure)
    with pytest.raises(subprocess.CalledProcessError):
        runtime.close()
    assert runtime.container_created


def test_final_evidence_error_cannot_return_a_pass(tmp_path, monkeypatch):
    """Inject a runner I/O error after its successful test-result decision."""
    import importlib.util
    import json
    import sys
    from .runtime import ROOT
    spec = importlib.util.spec_from_file_location('portal_gate_contract', ROOT / 'scripts/portal_e2e.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    artifacts = tmp_path / 'artifacts'
    monkeypatch.setattr(sys, 'argv', ['portal_e2e.py', '--infrastructure', '--artifacts', str(artifacts)])
    class SuccessfulRuntime:
        template_db, template_data, descriptor = 'private', tmp_path, tmp_path / 'descriptor'
        units = {}
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def environment(self, *args): return {}
    monkeypatch.setattr(runner, 'Runtime', SuccessfulRuntime)
    def output(command, **kwargs):
        if command[0] == 'git': return 'source-sha' if command[1] == 'rev-parse' else ''
        if command[0] == 'node': return 'v22.0.0'
        return '{}'
    monkeypatch.setattr(runner.subprocess, 'check_output', output)
    def manifest(*args, **kwargs):
        (artifacts / 'routes.json').write_text(json.dumps({'mapping_status': 'complete'}))
    monkeypatch.setattr(runner.subprocess, 'run', manifest)
    monkeypatch.setattr(runner, 'run_tests', lambda *args: {'status': 'pass', 'collected': ['synthetic-unit-proof'], 'outcomes': {'synthetic-unit-proof': 'passed'}})
    monkeypatch.setattr(runner, 'write_budgets', lambda *args: None)
    monkeypatch.setattr(runner, 'validate_execution', lambda *args, **kwargs: {'status': 'pass'})
    calls = 0
    def source(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('final source read failed')
        return 'initial-source'
    monkeypatch.setattr(runner, 'source_digest', source)
    assert runner.main() == 1
    result = json.loads((artifacts / 'result.json').read_text())
    assert result['status'] == 'blocked' and result['reason'] == 'final source read failed'
    assert result['infrastructure_status'] == 'pass'
