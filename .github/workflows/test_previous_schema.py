"""Safety checks for the explicitly loaded previous-schema CI plugin."""
from types import SimpleNamespace

import pytest

import previous_schema


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


def _collection(nodeids):
    deselected = []
    config = SimpleNamespace(
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
