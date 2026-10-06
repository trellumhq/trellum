"""`trellum datasource add` -- what its connection check says when it fails."""

from __future__ import annotations

from unittest.mock import patch

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
