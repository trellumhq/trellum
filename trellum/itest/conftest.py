"""Pytest configuration for the itest harness.

This harness lives outside the default test suite by construction: the
root ``pytest.ini`` roots collection at ``testpaths = testing``, and CI's
tests job runs ``python -m pytest ../testing`` explicitly, so nothing here
is ever picked up by the default run. This directory self-roots via its
own ``itest/pytest.ini`` (``testpaths = .``) instead. There is deliberately
no ``itest/__init__.py`` -- that would make this directory importable as
part of the ``trellum`` package, which it must never be. Because there's
no ``__init__.py``, pytest's default import mode inserts this directory
onto ``sys.path`` and imports sibling modules flat (``import engines``, not
``import itest.engines``) -- every module in this directory follows that
convention.

Engine selection is parametrization, not skipping: an engine not passed to
--engines is simply never collected -- there is no code path that marks a
test "skipped" for that reason. A *selected* engine that can't be reached
fails outright (after a connect-retry window), naming the compose command
that should have been run first. Nothing in this harness ever reports a
skip; that is the whole point of keeping it separate from the no-skip
default suite instead of gating it with ``pytest.mark.skipif``.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path

import pytest

# Make `import trellum` resolve the same way the demo project does: via
# demo/trellum/__init__.py, a dev-only shim that repoints its submodule
# search path at the repo root (see that file's docstring for why this is
# a shim and not a symlink or editable install -- short version: it has to
# survive `python -m trellum.run` subprocesses spawned with a different
# cwd, and a shim needs no PYTHONPATH/env var to do that).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEMO_DIR = _REPO_ROOT / "demo"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

from engines import ENGINES  # noqa: E402 -- after the sys.path setup above

_DEFAULT_ENGINES = "sqlite,duckdb,fakesnow,file,api"

# "file" and "api" aren't EngineSpecs -- test_file_sources.py has its own
# fixtures (tmp_path, a local http.server thread) and needs no engine
# selection or container at all. They're accepted here purely so
# `--engines` can name everything this run touches in one place (matching
# the CI job's `--engines postgres,mysql,...,file,api` invocation in a
# later phase) without erroring as "unknown". "s3" is the same idea for
# the optional MinIO lane in test_file_sources.py -- it isn't a SQL
# EngineSpec either, it just gates which tests get collected (see
# _CONDITIONAL_TESTS below).
_NON_SQL_ENGINE_NAMES = {"file", "api", "s3"}

# Some tests need more than one thing selected at once (a container pair,
# or a non-SQL lane like "s3") and don't fit the per-engine `engine`
# fixture parametrization above. For these, gate at collection time: a
# test whose nodeid contains one of these markers is only collected when
# every name in its required set is present in --engines. This is
# deselection, not skipping -- matching every other engine-selection path
# in this harness, an item that doesn't qualify is simply never in the
# collected set, so it never shows up as "skipped" in a run's results.
_CONDITIONAL_TESTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("test_reconnect.py", frozenset({"postgres"})),
    # itest/test_report_build.py needs both containers up to build a report.
    ("test_report_build.py", frozenset({"postgres", "clickhouse"})),
    # itest/test_file_sources.py's s3:// lane needs MinIO (profile "s3").
    ("test_read_source_csv_s3", frozenset({"s3"})),
    # itest/test_ssh_tunnel.py needs the bastion (and postgres behind it).
    ("test_ssh_tunnel.py", frozenset({"postgres_ssh"})),
)


def pytest_addoption(parser):
    parser.addoption(
        "--engines",
        action="store",
        default=None,
        help=(
            "Comma-separated list of engines to run against this session "
            f"(default: $ITEST_ENGINES env var, else '{_DEFAULT_ENGINES}'). "
            f"SQL engines: {', '.join(sorted(ENGINES))}. "
            f"Also accepted (no-op for selection, just accepted so one "
            f"--engines list can name everything): {', '.join(sorted(_NON_SQL_ENGINE_NAMES))}."
        ),
    )


def _selected_engine_names(config: pytest.Config) -> list[str]:
    raw = (
        config.getoption("--engines")
        or os.environ.get("ITEST_ENGINES")
        or _DEFAULT_ENGINES
    )
    names = [n.strip() for n in raw.split(",") if n.strip()]
    known = set(ENGINES) | _NON_SQL_ENGINE_NAMES
    unknown = [n for n in names if n not in known]
    if unknown:
        raise pytest.UsageError(
            f"Unknown engine(s) in --engines: {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(known))}."
        )
    return names


def pytest_generate_tests(metafunc):
    """Parametrize contract tests over only the selected SQL engines.

    Any test function requesting the ``engine`` fixture gets one
    parametrized instance per selected engine that has an EngineSpec (i.e.
    excluding "file"/"api", which aren't SQL engines and don't participate
    in this parametrization at all). ``scope="session"`` is what lets the
    session-scoped ``engine_conn`` fixture below cache one connection per
    engine for the whole run instead of reconnecting per test function.
    """
    if "engine" in metafunc.fixturenames:
        selected = [n for n in _selected_engine_names(metafunc.config) if n in ENGINES]
        specs = [ENGINES[name] for name in selected]
        metafunc.parametrize("engine", specs, ids=selected, scope="session")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Deselect (never skip) tests whose required names aren't all selected.

    Covers the cases pytest_generate_tests' per-engine parametrization
    above doesn't: tests that need more than one engine at once (the
    report-build lane needs postgres AND clickhouse) or a non-SQL lane
    that isn't an EngineSpec at all (the s3 file-source test needs "s3").
    See _CONDITIONAL_TESTS.
    """
    selected = set(_selected_engine_names(config))
    # When no SQL engine is selected at all (e.g. --engines s3,file),
    # pytest materializes the engine-fixture tests as empty-parameter-set
    # items carrying an automatic skip marker. Deselect those too --
    # "skipped" is reserved for nothing in this harness.
    any_sql = any(n in ENGINES for n in selected)
    keep, deselected = [], []
    for item in items:
        required = next(
            (req for marker, req in _CONDITIONAL_TESTS if marker in item.nodeid),
            None,
        )
        if required is not None and not required <= selected:
            deselected.append(item)
        elif not any_sql and "engine" in getattr(item, "fixturenames", ()):
            deselected.append(item)
        else:
            keep.append(item)
    if deselected:
        items[:] = keep
        config.hook.pytest_deselected(items=deselected)


def _connect_with_retry(spec):
    from trellum.data.drivers import connect

    info = spec.conn_info()
    deadline = time.time() + spec.startup_timeout
    attempt = 0
    last_exc: Exception | None = None
    while True:
        attempt += 1
        try:
            return connect(spec.name, info)

        except Exception as exc:  # noqa: BLE001 -- any connect failure retries
            last_exc = exc
            if time.time() >= deadline:
                break
            time.sleep(2)
    pytest.fail(
        f"{spec.name}: could not connect after {attempt} attempt(s) over "
        f"{spec.startup_timeout}s ({type(last_exc).__name__}: {last_exc}). "
        f"If '{spec.name}' needs a container, start it first with: "
        f"docker compose -f itest/docker-compose.yml up -d --wait",
        pytrace=False,
    )


@pytest.fixture(scope="session", autouse=True)
def _no_query_cache():
    """Disable trellum.data.query's cache for the entire itest session.

    The cache key is derived from SQL text alone, with no engine in it --
    a hit cached by one engine (or a previous run) would silently return
    another engine's result. Every contract test also passes cache_ttl=0
    explicitly at the call site as a second, redundant guard; this is the
    session-wide belt to that belt-and-suspenders.
    """
    from trellum.data.query import disable_cache, enable_cache

    disable_cache()
    yield
    enable_cache()


@pytest.fixture(scope="session")
def engine_conn(engine):
    """Connect + seed exactly once per selected engine for the whole session.

    Indirectly parametrized via ``engine`` (see pytest_generate_tests), so
    pytest caches one instance of this fixture per distinct engine
    parameter -- every test function that asks for ``engine_conn`` with
    the same engine reuses the same connection and the same seeded data,
    rather than re-seeding per test.
    """
    spec = engine
    ctx = spec.setup() if spec.setup is not None else contextlib.nullcontext()
    with ctx:
        conn = _connect_with_retry(spec)
        spec.seed(conn)
        yield conn
