"""Python debug representations stay protected at text and byte log boundaries."""
import json
import logging
import sys
from subprocess import CalledProcessError, TimeoutExpired
from urllib.error import HTTPError

import pytest

from trellum.log_capture import SecretFilter
from trellum.redaction import build_redaction


@pytest.fixture(params=["synthetic-a'b\"c", "synthetic-é\t'\"\\\n"])
def secret(request):
    return request.param


def debug_message(secret):
    return f"dict={ {'key': secret} } bytes={secret.encode()!r} ascii={ascii(secret)}\n"


EXPECTED = "dict={'key': '[REDACTED]'} bytes=b'[REDACTED]' ascii='[REDACTED]'\n"


@pytest.mark.parametrize('width', [1, 7, 1000])
def test_native_log_redacts_python_representations(secret, width):
    data = debug_message(secret).encode()
    scrubber = SecretFilter([secret])
    output = b''.join(scrubber.feed(data[i:i + width]) for i in range(0, len(data), width))
    output += scrubber.feed(b'', final=True)
    assert output.decode() == EXPECTED


def test_python_stream_and_file_logging_redact_preformatted_values(secret, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', json.dumps([secret]))
    path = tmp_path / 'debug.log'
    handler = logging.FileHandler(path)
    logger = logging.getLogger('python-secret-representations')
    logger.addHandler(handler)
    try:
        with build_redaction({}):
            print(debug_message(secret), end='')
            logger.warning(debug_message(secret).rstrip('\n'))
        assert capsys.readouterr().out == EXPECTED
        assert path.read_text() == EXPECTED
    finally:
        logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_split_secret_stays_buffered_across_nested_build_exit(stream, monkeypatch, capsys):
    value = 'synthetic-nested-secret'
    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', json.dumps([value]))
    with build_redaction({}):
        with build_redaction({}):
            getattr(sys, stream).write(value[:10])
        getattr(sys, stream).write(value[10:])
    captured = capsys.readouterr()
    assert (captured.out if stream == 'stdout' else captured.err) == '[REDACTED]'


@pytest.mark.parametrize('value', [123456789, 1234.56789, True])
@pytest.mark.parametrize('slotted', [False, True])
def test_custom_numeric_exception_data_is_filtered_after_cleanup(value, slotted, monkeypatch):
    class ReportError(Exception):
        __slots__ = ('value',) if slotted else ()

        def __str__(self):
            return f'private value: {self.value}'

    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', json.dumps([json.dumps(value)]))
    with pytest.raises(ReportError) as caught:
        with build_redaction({}):
            error = ReportError('report failed')
            error.value = value
            raise error
    assert str(caught.value) == 'private value: [REDACTED]'


@pytest.mark.parametrize(('error', 'field'), [
    (SystemExit(7), 'code'),
    (OSError(7, 'unavailable'), 'errno'),
    (SyntaxError('invalid', ('report.py', 7, 1, 'x')), 'lineno'),
    (UnicodeDecodeError('utf-8', b'abcdefgh', 7, 8, 'invalid'), 'start'),
    (CalledProcessError(7, 'command'), 'returncode'),
    (TimeoutExpired('command', 7), 'timeout'),
    (HTTPError('https://example.invalid', 7, 'unavailable', None, None), 'code'),
])
def test_numeric_exception_controls_keep_their_semantics(error, field, monkeypatch):
    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', '["7"]')
    with pytest.raises(type(error)) as caught:
        with build_redaction({}):
            raise error
    assert getattr(caught.value, field) == 7
