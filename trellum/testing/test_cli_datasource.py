"""`trellum datasource add` -- what its connection check says when it fails."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from trellum.cli.commands.datasource import _test_connection


def test_missing_connection_field_names_the_env_var(capsys):
    """A driver's bare KeyError('http_path') becomes an instruction.

    `datasource add` has no flag for fields like http_path or access_token,
    so a source that needs one connects with nothing under its prefix and
    the driver raises KeyError on the lookup. The message has to say which
    env var to set, not echo the symptom.
    """
    with patch("trellum.data.connections.resolve_connection",
               side_effect=KeyError("http_path")):
        rc = _test_connection("lake", None)

    assert rc == 1
    err = capsys.readouterr().err
    assert "missing connection field 'http_path'; set BI_LAKE_HTTP_PATH in .env" in err


@pytest.mark.parametrize("error", [None, ConnectionRefusedError("password=secret")])
def test_lazy_per_query_connection_is_actually_probed_and_closed(monkeypatch, capsys, error):
    from trellum.data import retry

    monkeypatch.setattr(retry.time, "sleep", Mock())
    raw = Mock()
    factory = Mock(side_effect=error) if error else Mock(return_value=raw)
    handle = retry.ManagedConnection("postgres", factory, new_connection_per_query=True)
    assert factory.call_count == 0
    with patch("trellum.data.connections.resolve_connection", return_value=handle):
        result = _test_connection("warehouse", "secret")
    output = capsys.readouterr()
    assert result == (1 if error else 0)
    assert factory.call_count == (3 if error else 1)
    assert handle.closed and handle._factory is None
    if error:
        assert "Could not connect" in output.err and "secret" not in output.err
        assert "connected --" not in output.out
    else:
        assert "connected --" in output.out
        raw.close.assert_called_once()
        raw.cursor.assert_not_called()
