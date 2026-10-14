"""No pytest-django database fixtures: all live writes target owned clones."""
import json
import os
from pathlib import Path
import pytest


def _artifact(name):
    directory = os.environ.get('PORTAL_E2E_ARTIFACTS')
    return Path(directory) / name if directory else None


def pytest_collection_finish(session):
    path = _artifact('collection.json')
    if path:
        path.write_text(json.dumps([item.nodeid for item in session.items], indent=2))


def pytest_runtest_logreport(report):
    path = _artifact('outcomes.jsonl')
    if path:
        row = {'node_id': report.nodeid, 'phase': report.when,
               'outcome': report.outcome, 'xfail': bool(getattr(report, 'wasxfail', False))}
        with path.open('a') as output:
            output.write(json.dumps(row) + '\n')


@pytest.fixture(scope='session')
def owned_runtime():
    from .runtime import Runtime
    descriptor = os.environ.get('PORTAL_E2E_RUNTIME')
    if not descriptor:
        pytest.skip('run scripts/portal_e2e.py --infrastructure')
    runtime = Runtime.attach(descriptor)
    yield runtime
    assert not runtime.units


@pytest.fixture
def journey(owned_runtime, request):
    from .bindings import binding_digest
    from .coverage import source_digest
    from .fixtures import Journey, configure_database
    from .runtime import ROOT
    from django.db import connections
    with owned_runtime.unit(request.node.name) as unit:
        configure_database(unit)
        value = Journey(unit, request.node.nodeid, source_digest(ROOT), binding_digest())
        yield value
        connections.close_all()
