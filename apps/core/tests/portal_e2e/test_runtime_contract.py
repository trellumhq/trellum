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
