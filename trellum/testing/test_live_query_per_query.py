"""Dev live queries retain their caps when using independent sessions."""

from unittest.mock import Mock

import pytest

from trellum.data import connections, retry
from trellum.data.live_query_guard import ROW_CAP
from trellum.runner.live_query_dev import _run


@pytest.mark.parametrize("stage", [None, "execute", "fetch"])
def test_dev_live_query_materializes_and_closes_each_retry(monkeypatch, stage):
    monkeypatch.setattr(retry.time, "sleep", Mock())
    cursors = [Mock(description=[("n",)]), Mock(description=[("n",)])]
    for cursor in cursors:
        cursor.fetchmany.return_value = [(n,) for n in range(ROW_CAP + 1)]
    if stage:
        method = cursors[0].execute if stage == "execute" else cursors[0].fetchmany
        method.side_effect = ConnectionResetError()
    raws = [Mock(cursor=Mock(return_value=cursor)) for cursor in cursors]
    factory = Mock(side_effect=raws)
    handle = retry.ManagedConnection("mysql", factory, new_connection_per_query=True)
    monkeypatch.setattr(connections, "resolve_connection", lambda name, sources: handle)
    columns, rows, truncated = _run("warehouse", "SELECT :name", {"name": "O'Brien"})
    assert columns == ["n"] and truncated and len(rows) == ROW_CAP
    count = 2 if stage else 1
    assert factory.call_count == count
    assert handle.closed and handle._factory is None
    for raw, cursor in zip(raws[:count], cursors[:count]):
        raw.close.assert_called_once()
        cursor.close.assert_called_once()
        cursor.execute.assert_called_once_with("SELECT 'O''Brien'")
    cursors[count - 1].fetchmany.assert_called_once_with(ROW_CAP + 1)


def test_dev_live_query_cancellation_closes_cursor_connection_and_handle(monkeypatch):
    cursor = Mock(description=[("n",)], fetchmany=Mock(side_effect=KeyboardInterrupt()))
    raw = Mock(cursor=Mock(return_value=cursor))
    factory = Mock(return_value=raw)
    handle = retry.ManagedConnection("postgres", factory, new_connection_per_query=True)
    monkeypatch.setattr(connections, "resolve_connection", lambda name, sources: handle)
    with pytest.raises(KeyboardInterrupt):
        _run("warehouse", "SELECT n FROM t", {})
    assert factory.call_count == 1 and handle.closed
    cursor.close.assert_called_once()
    raw.close.assert_called_once()


def test_dev_live_query_default_mode_retains_single_attempt_behavior(monkeypatch):
    cursor = Mock(description=[("n",)], fetchmany=Mock(side_effect=ConnectionResetError()))
    raw = Mock(cursor=Mock(return_value=cursor))
    factory = Mock(return_value=raw)
    handle = retry.ManagedConnection("postgres", factory)
    monkeypatch.setattr(connections, "resolve_connection", lambda name, sources: handle)
    with pytest.raises(ConnectionResetError):
        _run("warehouse", "SELECT n FROM t", {})
    assert factory.call_count == 1 and handle.closed
    cursor.close.assert_called_once()
    raw.close.assert_called_once()
