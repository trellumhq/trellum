"""Python debug representations stay protected at text and byte log boundaries."""
import json
import logging

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
