"""CI-only cleanup for a previous app registry testing an expanded schema."""
import os

import pytest

# Tests known to be flaky in a published release. Its test source is frozen,
# so a fix on main never reaches the copy this job runs. Keyed by tag, so an
# entry stops applying once the next release (which carries the fix) becomes
# the previous release. Only timing flakes belong here, never a failure that
# could be a schema incompatibility.
FROZEN_RELEASE_FLAKES = {
    "v0.7.0": {
        "apps/reports/tests/test_metrics_detail_browser.py::"
        "test_resize_reopen_disconnects_observer_and_restores_chart[webkit]":
            "measured mid-refit while Chart.js was still resizing its canvas",
        "apps/reports/tests/test_portal_mobile_runtime.py::"
        "test_operations_state_filters_follow_live_state_and_scope[webkit]":
            "the background status poll could overwrite a hand-applied state",
    },
}


def pytest_collection_modifyitems(config, items):
    flakes = FROZEN_RELEASE_FLAKES.get(os.environ.get("TRELLUM_PREVIOUS_RELEASE", ""), {})
    deselected = [item for item in items if item.nodeid in flakes]
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    items[:] = [item for item in items if item.nodeid not in flakes]
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    for item in deselected:
        message = f"deselected frozen-release flake {item.nodeid}: {flakes[item.nodeid]}"
        if reporter:
            reporter.write_line(message)


@pytest.fixture(scope="session", autouse=True)
def cascade_previous_schema_flush(django_db_setup):
    from django.db import connection

    expected = os.environ.get("TRELLUM_PREVIOUS_SCHEMA_TEST_DB", "")
    if (
        not expected.startswith("test_")
        or connection.vendor != "postgresql"
        or os.environ.get("DJANGO_SETTINGS_MODULE") != "trellum_portal.settings.test"
        or connection.settings_dict["NAME"] != expected
    ):
        raise pytest.UsageError("Previous-schema cleanup requires an explicit PostgreSQL test database")
    original = connection.ops.sql_flush

    def sql_flush(style, tables, *, reset_sequences=False, allow_cascade=False):
        if connection.settings_dict["NAME"] != expected:
            raise pytest.UsageError("Refusing previous-schema cleanup outside its configured test database")
        return original(style, tables, reset_sequences=reset_sequences, allow_cascade=True)

    connection.ops.sql_flush = sql_flush
    try:
        yield
    finally:
        connection.ops.sql_flush = original
