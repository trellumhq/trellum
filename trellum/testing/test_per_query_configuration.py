"""Lifecycle options resolve independently of native driver settings."""

from unittest.mock import Mock

import pytest

from trellum.data import connections, datasource_config, drivers, resolvers, retry


@pytest.fixture(autouse=True)
def isolated_resolution(monkeypatch):
    monkeypatch.setattr(resolvers, "_dotenv_loaded", True)
    monkeypatch.setattr(resolvers, "_resolvers", [(0, resolvers.LocalEnvResolver())])
    monkeypatch.delenv("PER_QUERY_NEW_CONNECTION_PER_QUERY", raising=False)


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), (1, True), (0, False),
    ("true", True), ("false", False), ("1", True), ("0", False),
    ("yes", True), ("no", False), ("on", True), ("off", False), (" TRUE ", True),
])
def test_lifecycle_boolean_parsing(value, expected):
    assert resolvers.parse_new_connection_per_query(value) is expected


@pytest.mark.parametrize("value", ["password=secret", "", "maybe", 2, None, [], 1.0])
def test_invalid_lifecycle_boolean_does_not_echo_values(value):
    with pytest.raises(ValueError, match="new_connection_per_query must be a boolean") as caught:
        resolvers.parse_new_connection_per_query(value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("source_value,env_value,expected", [
    (True, None, True), (False, None, False), (True, "false", False),
    (False, "true", True), ("false", "on", True), ("true", "off", False),
])
def test_env_overlays_yaml_boolean(source_value, env_value, expected, monkeypatch):
    if env_value is not None:
        monkeypatch.setenv("PER_QUERY_NEW_CONNECTION_PER_QUERY", env_value)
    info = resolvers.LocalEnvResolver().resolve({
        "local_env": "PER_QUERY", "host": "db", "new_connection_per_query": source_value,
    })
    assert info["new_connection_per_query"] is expected


def test_option_env_suffix_can_resolve_a_source(monkeypatch):
    monkeypatch.setenv("PER_QUERY_NEW_CONNECTION_PER_QUERY", "off")
    source = {"local_env": "PER_QUERY"}
    assert resolvers.LocalEnvResolver().can_resolve(source)
    assert resolvers.resolve_credentials(source) == {"new_connection_per_query": False}


@pytest.mark.parametrize("where", ["yaml", "env", "custom"])
def test_invalid_option_stops_before_any_driver_open(monkeypatch, where):
    source = {"name": "warehouse", "type": "vertica", "local_env": "PER_QUERY", "host": "db"}
    if where == "env":
        monkeypatch.setenv("PER_QUERY_NEW_CONNECTION_PER_QUERY", "password=secret")
    elif where == "yaml":
        source["new_connection_per_query"] = "password=secret"
    else:
        custom = Mock(can_resolve=Mock(return_value=True),
                      resolve=Mock(return_value={"new_connection_per_query": "password=secret"}))
        resolvers.register_resolver(custom, priority=100)
    factory = Mock()
    monkeypatch.setattr(drivers, "connect", factory)
    with pytest.raises(ValueError, match="new_connection_per_query") as caught:
        connections.resolve_connection("warehouse", [source])
    assert "secret" not in str(caught.value)
    factory.assert_not_called()


@pytest.mark.parametrize("resolved,expected", [({}, True), ({"new_connection_per_query": "off"}, False)])
def test_custom_resolver_can_override_source_option_without_mutating_result(monkeypatch, resolved, expected):
    result = {"password": "secret", **resolved}
    custom = Mock(can_resolve=Mock(return_value=True), resolve=Mock(return_value=result))
    resolvers.register_resolver(custom, priority=100)
    info = resolvers.resolve_credentials({"secret": "vault/key", "new_connection_per_query": True})
    assert info["new_connection_per_query"] is expected
    assert result == {"password": "secret", **resolved}


@pytest.mark.parametrize("setting", ["true", "false"])
def test_named_yaml_configuration_reaches_managed_handle(monkeypatch, tmp_path, setting):
    config = tmp_path / "config.yaml"
    config.write_text(
        "sources:\n  warehouse:\n    type: vertica\n    host: db\n"
        "    credentials:\n      local: PER_QUERY\n"
        f"    new_connection_per_query: {setting}\n", encoding="utf-8",
    )
    monkeypatch.setattr(datasource_config, "_config_path", lambda: str(config))
    monkeypatch.setattr(datasource_config, "_cache", None)
    native = Mock()
    factory = Mock(return_value=native)
    monkeypatch.setattr(drivers, "connect", factory)
    handle = connections.resolve_connection("warehouse", ["warehouse"])
    assert isinstance(handle, retry.ManagedConnection)
    assert handle._new_connection_per_query is (setting == "true")
    assert factory.call_count == (0 if setting == "true" else 1)
    if factory.called:
        assert factory.call_args.args == ("vertica", {"host": "db"})
    handle.close()


@pytest.mark.parametrize("source", ["sqlite", "duckdb"])
def test_embedded_lifecycle_is_unchanged(monkeypatch, source):
    native = Mock()
    driver = Mock(connect=Mock(return_value=native))
    monkeypatch.setattr(drivers, "get_driver", lambda name: driver)
    assert retry.connect_managed(source, {"path": ":memory:", "new_connection_per_query": True}) is native
    driver.connect.assert_called_once_with({"path": ":memory:"})


def test_native_connection_boundary_strips_framework_setting_without_mutation(monkeypatch):
    info = {"host": "db", "new_connection_per_query": True}
    driver = Mock()
    monkeypatch.setattr(drivers, "get_driver", lambda name: driver)
    drivers.connect("custom", info)
    driver.connect.assert_called_once_with({"host": "db"})
    assert info["new_connection_per_query"] is True


def test_adhoc_query_cache_hit_uses_no_probe_connection(monkeypatch, tmp_path):
    from trellum.data import adhoc, query

    monkeypatch.setattr(connections, "_source_names", {})
    monkeypatch.setattr(adhoc, "_infer_root", lambda: None)
    monkeypatch.setattr(datasource_config, "_cache", {"warehouse": {
        "name": "warehouse", "type": "vertica", "host": "db",
        "credentials": {"local": "PER_QUERY"}, "new_connection_per_query": True,
    }})
    cursor = Mock(description=[("n",)], fetchall=Mock(return_value=[(1,), (2,)]))
    raw = Mock(cursor=Mock(return_value=cursor))
    factory = Mock(return_value=raw)
    monkeypatch.setattr(drivers, "connect", factory)
    monkeypatch.setattr(query, "_cache_dir", lambda: tmp_path)
    query.enable_cache()
    for _ in range(2):
        assert adhoc.query("warehouse", "SELECT n FROM t", cache_ttl=60).n.tolist() == [1, 2]
    assert factory.call_count == 1
    cursor.close.assert_called_once()
    raw.close.assert_called_once()
    assert not connections._source_names
