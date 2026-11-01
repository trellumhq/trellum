"""Synthetic local persistence only: no login, provider, or real credential paths."""
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import stat
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from trellum.cli.commands.datasource import _update_env_file
from trellum.cli.secrets import resolve_secret
from trellum.private_files import CredentialPersistenceError


def test_env_preserves_comments_multiline_unrelated_settings_and_quotes(tmp_path):
    path = tmp_path / '.env'
    original = '# header\r\nexport KEEP="two\r\nlines" # retained\r\nPASS=old # password\r\nPASS=duplicate # duplicate comment\r\nTAIL=yes'
    path.write_bytes(original.encode())
    _update_env_file(str(path), {'PASS': "quotes' slash\\ # literal", 'NEW': 'new'})
    text = path.read_bytes().decode()
    assert '# header\r\nexport KEEP="two\r\nlines" # retained\r\n' in text
    assert ' # password\r\n' in text and 'TAIL=yes\r\n' in text
    parsed = dotenv_values(path, interpolate=False)
    assert parsed == {'KEEP': 'two\nlines', 'PASS': "quotes' slash\\ # literal", 'TAIL': 'yes', 'NEW': 'new'}
    assert text.count('PASS=') == 1
    assert '# duplicate comment\r\n' in text


def test_env_does_not_treat_hash_inside_quotes_as_comment(tmp_path):
    path = tmp_path / '.env'
    path.write_text("PASS='old # inside'\n")
    _update_env_file(str(path), {'PASS': 'new'})
    assert path.read_text() == 'PASS=new\n'


@pytest.mark.parametrize('failure', ['replace', 'fsync', 'protect'])
def test_failed_write_keeps_prior_valid_data_and_removes_staging(tmp_path, monkeypatch, failure):
    import trellum.private_files as files
    path = tmp_path / '.env'
    path.write_text('# retained\nPASS=old\n')
    before = path.read_bytes()
    if failure == 'protect':
        real = files.protect
        def protect(p, **kwargs):
            if p.name.startswith('..env.'):
                raise OSError('synthetic secret must not escape')
            real(p, **kwargs)
        monkeypatch.setattr(files, 'protect', protect)
    else:
        monkeypatch.setattr(files.os, failure, Mock(side_effect=OSError('synthetic secret must not escape')))
    with pytest.raises(CredentialPersistenceError) as error:
        _update_env_file(str(path), {'PASS': 'new'})
    assert 'synthetic secret' not in str(error.value)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.env', '.env.lock']


def test_concurrent_env_updates_merge_latest_values(tmp_path):
    path = tmp_path / '.env'
    path.write_text('# keep\n')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: _update_env_file(str(path), {f'KEY_{i}': str(i)}), range(24)))
    assert dotenv_values(path) == {f'KEY_{i}': str(i) for i in range(24)}
    assert path.read_text().startswith('# keep\n')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX modes')
def test_existing_permissions_are_tightened_without_changing_umask(tmp_path):
    path = tmp_path / '.env'
    path.write_text('KEEP=yes\n')
    path.chmod(0o644)
    before = os.umask(0)
    os.umask(before)
    _update_env_file(str(path), {'PASS': 'synthetic'})
    after = os.umask(before)
    assert after == before
    for p in [path, tmp_path / '.env.lock']:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory', 'lock_symlink', 'parent_symlink'])
def test_unsafe_paths_are_rejected_without_touching_target(tmp_path, kind):
    path = tmp_path / '.env'
    target = tmp_path / 'target'
    target.write_text('KEEP=prior\n')
    if kind == 'symlink':
        path.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, path)
    elif kind == 'directory':
        path.mkdir()
    elif kind == 'lock_symlink':
        (tmp_path / '.env.lock').symlink_to(target)
    else:
        parent = tmp_path / 'linked'
        parent.symlink_to(tmp_path, target_is_directory=True)
        path = parent / '.env'
    with pytest.raises(CredentialPersistenceError):
        _update_env_file(str(path), {'PASS': 'new'})
    assert target.read_text() == 'KEEP=prior\n'


def _cache(tmp_path):
    pytest.importorskip('msal_extensions')
    from trellum.data.token_cache import load_cache
    return load_cache(str(tmp_path / 'cache' / 'msal_cache.bin'))


def _add(cache, name):
    cache.modify('Account', {'home_account_id': name, 'environment': 'synthetic', 'realm': 'test'},
                 {'username': name})


def test_existing_serializable_cache_is_reused_and_hardened(tmp_path):
    msal = pytest.importorskip("msal")
    cache_path = tmp_path / 'cache' / 'msal_cache.bin'
    cache_path.parent.mkdir()
    original = msal.SerializableTokenCache()
    _add(original, 'existing')
    cache_path.write_text(original.serialize())
    before = cache_path.read_bytes()
    cache = _cache(tmp_path)
    assert list(cache.search('Account'))[0]['username'] == 'existing'
    assert cache_path.read_bytes() == before
    if os.name == 'posix':
        assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(cache_path.parent.stat().st_mode) == 0o700


def test_cache_writers_reload_even_with_identical_mtime(tmp_path, monkeypatch):
    caches = [_cache(tmp_path) for _ in range(3)]
    from trellum.data.token_cache import AtomicPersistence
    monkeypatch.setattr(AtomicPersistence, 'time_last_modified', lambda self: 1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda pair: _add(*pair), zip(caches, ['one', 'two', 'three'])))
    assert {x['username'] for x in _cache(tmp_path).search('Account')} == {'one', 'two', 'three'}


def test_cache_failed_replace_keeps_prior_cache_and_next_update_reloads(tmp_path, monkeypatch):
    cache = _cache(tmp_path)
    _add(cache, 'prior')
    path = tmp_path / 'cache' / 'msal_cache.bin'
    before = path.read_bytes()
    import trellum.private_files as files
    with monkeypatch.context() as patch:
        patch.setattr(files.os, 'replace', Mock(side_effect=OSError('synthetic')))
        with pytest.raises(CredentialPersistenceError):
            _add(cache, 'failed')
    assert path.read_bytes() == before
    _add(cache, 'next')
    assert {x['username'] for x in cache.search('Account')} == {'prior', 'next'}


def test_invalid_cache_diagnostic_does_not_disclose_content(tmp_path):
    path = tmp_path / 'cache' / 'msal_cache.bin'
    path.parent.mkdir()
    path.write_text('synthetic-invalid-secret')
    with pytest.raises(CredentialPersistenceError) as error:
        _cache(tmp_path)
    assert 'synthetic-invalid-secret' not in str(error.value)
    assert path.read_text() == 'synthetic-invalid-secret'


@pytest.mark.parametrize('name', ['password', 'key'])
@pytest.mark.parametrize('source', ['argv', 'stdin', 'env', 'prompt'])
def test_secret_input_compatibility_and_alternatives(name, source, monkeypatch, capsys):
    from trellum.cli import secrets
    args = Namespace(**{name: 'synthetic' if source == 'argv' else None,
                        name + '_stdin': source == 'stdin',
                        name + '_env': 'SYNTHETIC_INPUT' if source == 'env' else None})
    monkeypatch.setenv('SYNTHETIC_INPUT', 'synthetic')
    monkeypatch.setattr(secrets.sys, 'stdin', io.StringIO('synthetic\n'))
    monkeypatch.setattr(secrets.sys.stdin, 'isatty', lambda: source == 'prompt')
    monkeypatch.setattr(secrets.getpass, 'getpass', lambda prompt: 'synthetic')
    assert resolve_secret(args, name, 'Secret: ') == 'synthetic'
    output = capsys.readouterr()
    assert 'synthetic' not in output.out + output.err


def test_lock_timeout_is_bounded_and_prior_file_survives(tmp_path, monkeypatch):
    import portalocker
    import time
    path = tmp_path / '.env'
    path.write_text('KEEP=yes\n')
    times = iter([0, 11])
    monkeypatch.setattr(time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(portalocker, 'lock', Mock(side_effect=portalocker.exceptions.AlreadyLocked()))
    with pytest.raises(CredentialPersistenceError, match='busy'):
        _update_env_file(str(path), {'PASS': 'secret'})
    assert path.read_text() == 'KEEP=yes\n'


def test_existing_cache_login_uses_silent_auth_without_device_flow(tmp_path, monkeypatch):
    msal = pytest.importorskip("msal")
    from trellum.data import query
    cache = _cache(tmp_path)
    _add(cache, 'existing')
    monkeypatch.setattr(query, '_msal_cache', None)
    monkeypatch.setattr(query, '_MSAL_CACHE_PATH', str(tmp_path / 'cache' / 'msal_cache.bin'))
    monkeypatch.setattr('subprocess.run', Mock(side_effect=FileNotFoundError))
    app = Mock()
    def build(*args, token_cache, **kwargs):
        app.get_accounts.return_value = list(token_cache.search('Account'))
        app.acquire_token_silent.return_value = {'access_token': 'synthetic-silent-token'}
        return app
    monkeypatch.setattr(msal, 'PublicClientApplication', build)
    assert query._get_onedrive_token('', '', '') == ('synthetic-silent-token', False)
    app.acquire_token_silent.assert_called_once()
    app.initiate_device_flow.assert_not_called()


@pytest.mark.parametrize('flag', ['--password', '--password-stdin', '--password-env'])
def test_password_parser_accepts_existing_and_new_options(flag):
    import argparse
    from trellum.cli.commands.datasource import add_datasource_commands
    parser = argparse.ArgumentParser()
    add_datasource_commands(parser.add_subparsers())
    args = ['datasource', 'add', 'synthetic', '--type', 'postgres', '--host', 'fake', flag]
    if flag != '--password-stdin':
        args.append('synthetic')
    assert parser.parse_args(args).name == 'synthetic'
    with pytest.raises(SystemExit):
        parser.parse_args(args + ['--password-stdin' if flag != '--password-stdin' else '--password', 'other'])


@pytest.mark.parametrize('name', ['password', 'key'])
def test_missing_or_echoed_secret_inputs_fail_without_disclosure(name, monkeypatch):
    from trellum.cli import secrets
    args = Namespace(**{name: None, name + '_env': 'SYNTHETIC_ABSENT'})
    monkeypatch.delenv('SYNTHETIC_ABSENT', raising=False)
    with pytest.raises(ValueError, match='not set'):
        resolve_secret(args, name, 'Secret: ')
    args = Namespace(**{name: None, name + '_stdin': True})
    monkeypatch.setattr(secrets.sys, 'stdin', io.StringIO(''))
    with pytest.raises(ValueError, match='ended'):
        resolve_secret(args, name, 'Secret: ')
    args = Namespace(**{name: None})
    monkeypatch.setattr(secrets.sys.stdin, 'isatty', lambda: True)
    def echoed(prompt):
        import warnings
        warnings.warn('echo fallback', secrets.getpass.GetPassWarning)
    monkeypatch.setattr(secrets.getpass, 'getpass', echoed)
    with pytest.raises(ValueError, match='hidden terminal'):
        resolve_secret(args, name, 'Secret: ')


@pytest.mark.heavy
def test_independent_processes_preserve_env_and_cache_updates(tmp_path):
    import subprocess
    import sys
    code = '''
from pathlib import Path
import sys
from trellum.cli.commands.datasource import _update_env_file
from trellum.data.token_cache import load_cache
root, number = Path(sys.argv[1]), sys.argv[2]
cache = load_cache(str(root / 'cache' / 'msal_cache.bin'))
for index in range(5):
    name = 'worker_' + number + '_' + str(index)
    _update_env_file(str(root / '.env'), {name: 'synthetic'})
    cache.modify('Account', {'home_account_id': name, 'environment': 'synthetic', 'realm': 'test'}, {'username': name})
'''
    processes = [subprocess.Popen([sys.executable, '-c', code, str(tmp_path), str(i)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(4)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr.decode()
            assert not stdout
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert len(dotenv_values(tmp_path / '.env')) == 20
    assert len(list(_cache(tmp_path).search('Account'))) == 20


@pytest.mark.parametrize('source', ['stdin', 'env'])
def test_portal_setup_new_key_inputs_write_only_env(tmp_path, monkeypatch, capsys, source):
    from trellum.cli import main, secrets
    value = 'synthetic_portal_key'
    args = ['setup', 'portal', '--url', 'https://synthetic.invalid/s/test/studio', '--dest', str(tmp_path)]
    if source == 'stdin':
        args.append('--key-stdin')
        monkeypatch.setattr(secrets.sys, 'stdin', io.StringIO(value + '\n'))
    else:
        args += ['--key-env', 'SYNTHETIC_INPUT']
        monkeypatch.setenv('SYNTHETIC_INPUT', value)
    assert main(args) == 0
    assert dotenv_values(tmp_path / '.env')['TRELLUM_API_KEY'] == value
    output = capsys.readouterr()
    assert value not in output.out + output.err
    assert value not in (tmp_path / '.mcp.json').read_text()


def test_cli_persistence_error_redacts_exception_and_key(tmp_path, monkeypatch, capsys):
    from trellum.cli import main
    from trellum.cli.commands import datasource
    monkeypatch.setattr(datasource, '_update_env_file', Mock(side_effect=OSError('synthetic_key_in_error')))
    assert main(['setup', 'portal', '--url', 'https://synthetic.invalid/s/a/b',
                 '--key', 'synthetic_key_in_error', '--dest', str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert 'synthetic_key_in_error' not in output.out + output.err
    assert 'Could not save private portal credentials' in output.err


def test_portal_non_interactive_does_not_prompt(tmp_path, monkeypatch):
    from trellum.cli import main, secrets
    monkeypatch.setattr(secrets.sys, 'stdin', io.StringIO(''))
    monkeypatch.setattr(secrets.sys.stdin, 'isatty', lambda: True)
    prompt = Mock(side_effect=AssertionError('must not prompt'))
    monkeypatch.setattr(secrets.getpass, 'getpass', prompt)
    assert main(['setup', 'portal', '--url', 'https://synthetic.invalid/s/a/b',
                 '--non-interactive', '--dest', str(tmp_path)]) == 2
    prompt.assert_not_called()
