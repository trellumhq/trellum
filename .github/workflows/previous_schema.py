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


# These frozen tests predate a separate Docker-host lane. Keep them in the
# schema gate, but run them where disposable containers and loopback ports work.
# Match the test function, including every parametrization, not a whole module.
DOCKER_HOST_TESTS = frozenset({
    "apps/core/tests/test_deployment_helpers.py::test_real_postgres_restore_and_failed_swap",
    "apps/datasources/tests/test_materialize_live.py::test_materialized_source_queries_itest_database",
})


def pytest_addoption(parser):
    parser.addoption(
        "--previous-schema-lane", choices=("all", "browser", "docker"), default="all",
        help="Partition frozen-release tests between the pinned browser container and Docker host",
    )


def pytest_collection_modifyitems(config, items):
    lane = config.getoption("--previous-schema-lane")
    flakes = FROZEN_RELEASE_FLAKES.get(os.environ.get("TRELLUM_PREVIOUS_RELEASE", ""), {})
    deselected = []
    selected = []
    for item in items:
        docker = item.nodeid.split("[", 1)[0] in DOCKER_HOST_TESTS
        if item.nodeid in flakes or (lane == "browser" and docker) or (lane == "docker" and not docker):
            deselected.append(item)
        else:
            selected.append(item)
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter and lane != "all":
        reporter.write_line(f"Previous-schema {lane} lane: {len(selected)} selected, {len(deselected)} routed out")
    for item in deselected:
        if reporter and item.nodeid in flakes:
            reporter.write_line(f"deselected frozen-release flake {item.nodeid}: {flakes[item.nodeid]}")


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
