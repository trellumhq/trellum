"""Deadline cancellation evidence drives bounded fresh-session read recovery."""
from unittest.mock import Mock

import pytest

from trellum import diagnostics
from trellum.data import drivers, query, retry
from trellum.data.retry_errors import is_retryable_connection_error
from trellum.data.vertica_deadline import VerticaQueryTimeout
from trellum.testing.test_query_retry import Connection


@pytest.mark.parametrize("confirmed", [True, False])
def test_deadline_replay_requires_server_confirmation(monkeypatch, tmp_path, confirmed):
    path = tmp_path / "evidence.json"
    monkeypatch.setenv("TRELLUM_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(retry.time, "sleep", Mock())
    expired = VerticaQueryTimeout(confirmed)
    cancelled = type("QueryCanceled", (Exception,), {"sqlstate": "57014"})("private SQL")
    expired.__cause__ = cancelled
    first, fresh = Connection(expired), Connection()
    factory = Mock(side_effect=[first, fresh])
    monkeypatch.setattr(drivers, "connect", factory)
    handle = retry.connect_managed("vertica", {})
    try:
        if confirmed:
            result = query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
            assert result.n.tolist() == [1, 2]
            assert factory.call_count == 2
            evidence = diagnostics.read_diagnostics(path)
            assert evidence["retry_history"][0]["reason"] == "query_timeout"
            assert evidence["state"] == "completed"
            assert not evidence["query_timeout"]
        else:
            with pytest.raises(VerticaQueryTimeout):
                query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
            assert factory.call_count == 1
            evidence = diagnostics.read_diagnostics(path)
            assert evidence["cancellation_unconfirmed"]
            assert not evidence["transient"]
            assert evidence["query_timeout"]
        assert "private SQL" not in path.read_text()
    finally:
        handle.close()


def test_confirmed_deadline_exhaustion_keeps_query_evidence(monkeypatch, tmp_path):
    path = tmp_path / "evidence.json"
    monkeypatch.setenv("TRELLUM_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(retry.time, "sleep", Mock())
    factory = Mock(side_effect=[Connection(VerticaQueryTimeout(True)) for _ in range(3)])
    monkeypatch.setattr(drivers, "connect", factory)
    handle = retry.connect_managed("vertica", {})
    with pytest.raises(VerticaQueryTimeout):
        query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert factory.call_count == 3
    evidence = diagnostics.read_diagnostics(path)
    assert evidence["attempt"] == 3 and evidence["retry_count"] == 2
    assert evidence["transient"] and evidence["query_timeout"]
    assert not evidence["cancellation_unconfirmed"]
    assert evidence["errors"][0]["class"] == "VerticaQueryTimeout"


def test_local_deadline_does_not_authorize_writes(monkeypatch):
    factory = Mock(return_value=Connection(VerticaQueryTimeout(True)))
    monkeypatch.setattr(drivers, "connect", factory)
    handle = retry.connect_managed("vertica", {})
    with pytest.raises(VerticaQueryTimeout):
        query.query_df(handle, "UPDATE t SET n=1", cache_ttl=0)
    assert factory.call_count == 1
    handle.close()


def test_unconfirmed_cancellation_survives_later_stage(monkeypatch, tmp_path):
    path = tmp_path / "evidence.json"
    monkeypatch.setenv("TRELLUM_DIAGNOSTICS_PATH", str(path))
    with pytest.raises(VerticaQueryTimeout):
        with diagnostics.stage("execute", source_type="vertica", query_retry_safe=True):
            raise VerticaQueryTimeout(False)
    with diagnostics.stage("render"):
        pass
    assert diagnostics.read_diagnostics(path)["cancellation_unconfirmed"]


def test_only_local_confirmed_timeout_overrides_cancel_classification():
    cancelled = type("QueryCanceled", (Exception,), {"sqlstate": "57014"})("cancelled")
    assert not is_retryable_connection_error(cancelled, "vertica")
    timeout = VerticaQueryTimeout(True)
    timeout.__cause__ = cancelled
    assert is_retryable_connection_error(timeout, "vertica")
    assert not is_retryable_connection_error(timeout, "postgres")
    wrapper = ValueError("permanent")
    wrapper.__cause__ = timeout
    assert not is_retryable_connection_error(wrapper, "vertica")


@pytest.mark.parametrize("value,valid", [(120, True), (0.5, True), (3600, True),
                                         (0, False), (-1, False), (True, False),
                                         ("120", False), (float("inf"), False), (3601, False)])
def test_report_schema_checks_inline_vertica_deadline(value, valid):
    from types import SimpleNamespace
    from trellum.validation.checks.yaml_schema import _check_yaml_schema
    from trellum.validation.result import ValidationResult
    ctx = SimpleNamespace(config={"data_sources": [{"type": "vertica", "query_timeout_seconds": value}]},
                          _connections={})
    result = ValidationResult(slug="test")
    _check_yaml_schema(ctx, result)
    failures = [item for item in result.checks if item.id == "yaml-query-timeout-invalid"]
    assert bool(failures) is not valid


def test_query_deadline_source_config_reaches_custom_credential_resolver(monkeypatch):
    from trellum.data import resolvers
    resolver = Mock()
    resolver.can_resolve.return_value = True
    resolver.resolve.return_value = {"host": "warehouse"}
    monkeypatch.setattr(resolvers, "_resolvers", [(99, resolver)])
    assert resolvers.resolve_credentials({"query_timeout_seconds": 180})["query_timeout_seconds"] == 180


def test_query_deadline_environment_override_is_numeric(monkeypatch):
    from trellum.data import resolvers
    monkeypatch.setattr(resolvers, "_load_dotenv_once", lambda: None)
    monkeypatch.setenv("TEST_DEADLINE_QUERY_TIMEOUT_SECONDS", "240")
    source = {"local_env": "TEST_DEADLINE", "query_timeout_seconds": 120}
    assert resolvers.LocalEnvResolver().resolve(source)["query_timeout_seconds"] == 240
    monkeypatch.setenv("TEST_DEADLINE_QUERY_TIMEOUT_SECONDS", "secret")
    with pytest.raises(ValueError, match="must be numeric") as error:
        resolvers.LocalEnvResolver().resolve(source)
    assert "secret" not in str(error.value)
