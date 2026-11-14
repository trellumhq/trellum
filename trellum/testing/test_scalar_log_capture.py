"""Secret filtering at the OS stream boundary used by portal build processes."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trellum.log_capture import SecretFilter


@pytest.mark.parametrize('width', [1, 2, 7, 1000])
def test_filter_retains_split_utf8_and_multiline_secrets(width):
    secret = 'review-sécret\nsecond-line'
    data = ('before ' + secret + ' after ' + json.dumps(secret)[1:-1] + '\n').encode()
    scrubber = SecretFilter([secret])
    output = b''.join(scrubber.feed(data[i:i+width]) for i in range(0, len(data), width))
    output += scrubber.feed(b'', final=True)
    assert output == b'before [REDACTED] after [REDACTED]\n'


def test_filter_streams_safe_progress_and_bounds_pending_bytes():
    secret = 'fixture-secret-' + 'x' * 16000
    scrubber = SecretFilter([secret])
    assert scrubber.feed(b'building report\n') == b'building report\n'
    assert scrubber.feed(secret[:-1].encode()) == b''
    assert len(scrubber.pending) < len(secret.encode())
    assert scrubber.feed(b'x\n') == b'[REDACTED]\n'
    assert not scrubber.pending


def test_filter_handles_overlapping_values_without_refiltering_markers():
    scrubber = SecretFilter(['abcdef', 'abc', 'cdefg'])
    output = scrubber.feed(b'abc') + scrubber.feed(b'defg ') + scrubber.feed(b'abc', final=True)
    assert output == b'[REDACTED]g [REDACTED]'


@pytest.mark.heavy
@pytest.mark.skipif(os.name == 'nt', reason='POSIX container stop signal')
def test_capture_forwards_termination_and_preserves_signal_exit(tmp_path):
    import signal
    import time

    ready = tmp_path / 'ready'
    child = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(30)"
    program = 'from trellum.log_capture import run_filtered; import sys; sys.exit(run_filtered([sys.executable,"-c",sys.argv[1],sys.argv[2]]))'
    process = subprocess.Popen([sys.executable, '-c', program, child, str(ready)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        assert process.returncode == 128 + signal.SIGTERM
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


@pytest.mark.heavy
@pytest.mark.parametrize('mode', ['direct', 'container_redirect', 'local', 'local_all'])
def test_real_entrypoint_redacts_native_bootstrap_and_child_output(tmp_path, mode):
    """Both portal launch modes use this entrypoint before persisting OS logs."""
    repo = Path(__file__).resolve().parents[2]
    secret = 'synthetic-capture-sécret\nsecond-line'
    prefix = 'TRELLUM_DS_1'
    sources = tmp_path / 'data-sources'
    sources.mkdir()
    (sources / 'config.yaml').write_text(json.dumps({'sources': {'key': {
        'type': 'secret', 'credentials': {'local': prefix}}}}))
    report = tmp_path / 'reports' / 'capture'
    report.mkdir(parents=True)
    (report / 'report.yaml').write_text(json.dumps({'name': 'Capture', 'slug': 'capture', 'data_sources': ['key']}))
    (tmp_path / 'bootstrap.py').write_text(
        "import os\nos.write(2, os.environ['TRELLUM_DS_1_VALUE'].encode())\n")
    (report / 'generator.py').write_text('''from trellum import BaseReport
import json, os, subprocess, sys
class Report(BaseReport):
    def generate(self, ctx):
        value = ctx.read_text('key')
        print(value)
        for piece in value.encode():
            os.write(1, bytes([piece]))
        sys.stderr.buffer.write(json.dumps(value)[1:-1].encode())
        sys.stderr.buffer.flush()
        subprocess.run([sys.executable, '-c', "import os; os.write(1, os.environ['TRELLUM_DS_1_VALUE'].encode())"], check=True)
        print('capture-complete', flush=True)
        ctx.set_custom_output('<html>ok</html>', {'ok': True})
''')
    env = {**os.environ, 'FW_PROJECT_ROOT': str(tmp_path), 'FW_UPDATE_CHECK': '0',
           'PYTHONPATH': str(repo), prefix + '_VALUE': secret,
           'TRELLUM_SCALAR_SECRET_NAMES': json.dumps([prefix + '_VALUE'])}
    if mode.startswith('local'):
        env.pop('TRELLUM_SCALAR_SECRET_NAMES', None)
        env.pop('TRELLUM_SCALAR_SECRETS', None)
        env.pop(prefix + '_VALUE')
        (tmp_path / '.env').write_text(prefix + "_VALUE='" + secret + "'\n")
    command = [sys.executable, '-m', 'trellum.run', str(report), '--no-serve']
    if mode == 'local_all':
        command = [sys.executable, '-m', 'trellum.run', '--all', '--no-serve']
    stdout, stderr = tmp_path / 'stdout.log', tmp_path / 'stderr.log'
    if mode == 'container_redirect':
        if os.name == 'nt':
            pytest.skip('Container shell redirection uses POSIX')
        import shlex
        shell = 'exec ' + shlex.join(command) + ' >> ' + shlex.quote(str(stdout)) + ' 2>> ' + shlex.quote(str(stderr))
        result = subprocess.run(['/bin/sh', '-c', shell], env=env, cwd=repo, capture_output=True, timeout=30)
    else:
        with stdout.open('wb') as out, stderr.open('wb') as err:
            result = subprocess.run(command, env=env, cwd=repo, stdout=out, stderr=err, timeout=30)
    assert result.returncode == 0
    for path in (stdout, stderr):
        data = path.read_bytes()
        assert secret.encode() not in data
        assert json.dumps(secret)[1:-1].encode() not in data
        assert b'[REDACTED]' in data
    assert b'capture-complete' in stdout.read_bytes()


@pytest.mark.heavy
def test_capture_preserves_failure_code_and_filters_json_leaves(tmp_path):
    secret = 'synthetic-child-json-key'
    code = "import os,sys; os.write(2, os.environ['LEAF'].encode()); sys.exit(7)"
    program = 'from trellum.log_capture import run_filtered; import sys; sys.exit(run_filtered([sys.executable,"-c",sys.argv[1]]))'
    env = {**os.environ, 'LEAF': secret, 'TRELLUM_SCALAR_SECRETS': json.dumps([json.dumps({'credential': secret})])}
    result = subprocess.run([sys.executable, '-c', program, code], env=env, capture_output=True, timeout=10)
    assert result.returncode == 7
    assert result.stderr == b'[REDACTED]'
