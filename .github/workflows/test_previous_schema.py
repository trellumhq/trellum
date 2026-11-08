"""Safety checks for the explicitly loaded previous-schema CI plugin."""
from types import SimpleNamespace

import previous_schema
import pytest


@pytest.fixture
def flush_connection(monkeypatch):
    import django.db

    def original(style, tables, **options):
        return options

    connection = SimpleNamespace(
        vendor="postgresql",
        settings_dict={"NAME": "test_trellum_portal"},
        ops=SimpleNamespace(sql_flush=original),
    )
    monkeypatch.setattr(django.db, "connection", connection)
    monkeypatch.setenv("TRELLUM_PREVIOUS_SCHEMA_TEST_DB", "test_trellum_portal")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "trellum_portal.settings.test")
    return connection, original


@pytest.mark.parametrize("unsafe", ["missing_database", "wrong_database", "non_test_database", "wrong_settings", "wrong_backend"])
def test_cleanup_refuses_unsafe_configuration(flush_connection, monkeypatch, unsafe):
    connection, original = flush_connection
    if unsafe == "missing_database":
        monkeypatch.delenv("TRELLUM_PREVIOUS_SCHEMA_TEST_DB")
    elif unsafe == "wrong_database":
        connection.settings_dict["NAME"] = "test_another_database"
    elif unsafe == "non_test_database":
        monkeypatch.setenv("TRELLUM_PREVIOUS_SCHEMA_TEST_DB", "trellum_portal")
        connection.settings_dict["NAME"] = "trellum_portal"
    elif unsafe == "wrong_settings":
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "trellum_portal.settings.dev")
    else:
        connection.vendor = "sqlite"
    cleanup = previous_schema.cascade_previous_schema_flush.__wrapped__(None)
    with pytest.raises(pytest.UsageError):
        next(cleanup)
    assert connection.ops.sql_flush is original


def test_cleanup_cascades_only_on_configured_database_and_restores(flush_connection):
    connection, original = flush_connection
    cleanup = previous_schema.cascade_previous_schema_flush.__wrapped__(None)
    next(cleanup)
    assert connection.ops.sql_flush(None, ["accounts_user"], reset_sequences=True) == {
        "reset_sequences": True, "allow_cascade": True,
    }
    connection.settings_dict["NAME"] = "trellum_portal"
    with pytest.raises(pytest.UsageError):
        connection.ops.sql_flush(None, ["accounts_user"])
    cleanup.close()
    assert connection.ops.sql_flush is original


def _collection(nodeids, lane="all"):
    deselected = []
    config = SimpleNamespace(
        getoption=lambda name: lane,
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: None),
    )
    items = [SimpleNamespace(nodeid=nodeid) for nodeid in nodeids]
    return config, items, deselected


def test_frozen_flakes_are_deselected_only_for_their_release(monkeypatch):
    monkeypatch.setitem(previous_schema.FROZEN_RELEASE_FLAKES, "v9.9.9", {"apps/test_x.py::test_flaky[webkit]": "timing"})
    nodeids = ["apps/test_x.py::test_flaky[webkit]", "apps/test_x.py::test_flaky[chromium]"]

    monkeypatch.setenv("TRELLUM_PREVIOUS_RELEASE", "v9.9.9")
    config, items, deselected = _collection(nodeids)
    previous_schema.pytest_collection_modifyitems(config, items)
    assert [item.nodeid for item in items] == ["apps/test_x.py::test_flaky[chromium]"]
    assert [item.nodeid for item in deselected] == ["apps/test_x.py::test_flaky[webkit]"]

    for release in ("v9.9.8", ""):
        monkeypatch.setenv("TRELLUM_PREVIOUS_RELEASE", release)
        config, items, deselected = _collection(nodeids)
        previous_schema.pytest_collection_modifyitems(config, items)
        assert [item.nodeid for item in items] == nodeids
        assert deselected == []


def test_runtime_lanes_partition_every_case_without_dropping_heavy_or_schema_tests(monkeypatch):
    monkeypatch.setenv("TRELLUM_PREVIOUS_RELEASE", "v0.10.0")
    docker = [
        "apps/core/tests/test_deployment_helpers.py::test_real_postgres_restore_and_failed_swap",
        "apps/datasources/tests/test_materialize_live.py::test_materialized_source_queries_itest_database[postgres]",
        "apps/datasources/tests/test_materialize_live.py::test_materialized_source_queries_itest_database[mysql]",
    ]
    browser = [
        "apps/core/tests/test_deployment_helpers.py::test_upgrade_preflight",
        "apps/datasources/tests/test_materialize_live.py::test_other_case",
        "apps/reports/tests/test_metrics_detail_browser.py::test_retained_dates[webkit]",
        "apps/reports/tests/test_large_query.py::test_heavy_query_bound",
        "apps/core/tests/test_schema.py::test_previous_model_writes",
    ]
    nodeids = docker + browser
    for lane, expected in (("all", nodeids), ("docker", docker), ("browser", browser)):
        config, items, deselected = _collection(nodeids, lane)
        previous_schema.pytest_collection_modifyitems(config, items)
        assert [item.nodeid for item in items] == expected
        assert {item.nodeid for item in deselected} == set(nodeids) - set(expected)


@pytest.mark.parametrize("lane", ["browser", "docker"])
def test_frozen_flakes_still_apply_with_runtime_routing(monkeypatch, lane):
    docker = "apps/core/tests/test_deployment_helpers.py::test_real_postgres_restore_and_failed_swap"
    flaky = "apps/test_x.py::test_flaky[webkit]"
    healthy = "apps/test_x.py::test_flaky[chromium]"
    monkeypatch.setitem(previous_schema.FROZEN_RELEASE_FLAKES, "v9.9.9", {flaky: "timing"})
    monkeypatch.setenv("TRELLUM_PREVIOUS_RELEASE", "v9.9.9")
    config, items, deselected = _collection([docker, flaky, healthy], lane)
    previous_schema.pytest_collection_modifyitems(config, items)
    assert [item.nodeid for item in items] == ([docker] if lane == "docker" else [healthy])
    assert flaky in [item.nodeid for item in deselected]
