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
        executor=os.environ.get('PORTAL_E2E_EXECUTOR')
        if executor:
            path.with_name('execution-collection.json').write_text(json.dumps([{'executor':executor,'node_id':item.nodeid} for item in session.items],indent=2))


def pytest_configure(config):
    config.addinivalue_line('markers','cohort_executor(name): explicitly scoped live cohort executor')
    config.addinivalue_line('markers','worker: journey unit also runs an owned runner-role worker')


def pytest_collection_modifyitems(config,items):
    executor=os.environ.get('PORTAL_E2E_EXECUTOR')
    if not executor:return
    selected=[];deselected=[]
    for item in items:
        marker=item.get_closest_marker('cohort_executor')
        (selected if marker and marker.args[0]==executor else deselected).append(item)
    items[:]=selected
    if deselected:config.hook.pytest_deselected(items=deselected)


def pytest_runtest_logreport(report):
    path = _artifact('outcomes.jsonl')
    if path:
        row = {'node_id': report.nodeid, 'phase': report.when,
               'outcome': report.outcome, 'xfail': bool(getattr(report, 'wasxfail', False))}
        if os.environ.get('PORTAL_E2E_EXECUTOR'):
            row['executor']=os.environ['PORTAL_E2E_EXECUTOR']
        with path.open('a') as output:
            output.write(json.dumps(row) + '\n')


@pytest.fixture(scope='session')
def owned_runtime():
    from .runtime import Runtime
    descriptor = os.environ.get('PORTAL_E2E_RUNTIME')
    if not descriptor:
        pytest.skip('run scripts/portal_e2e.py --infrastructure')
    runtime = Runtime.attach(descriptor)
    runtime.browser=os.environ.get('PORTAL_E2E_EXECUTOR',os.environ.get('PORTAL_E2E_BROWSER',runtime.browser))
    yield runtime
    assert not runtime.units


@pytest.fixture
def journey(owned_runtime, request):
    from .bindings import binding_digest
    from .coverage import source_digest
    from .fixtures import Journey, configure_database
    from .runtime import ROOT
    from django.db import connections
    with owned_runtime.unit(request.node.name, worker=request.node.get_closest_marker('worker') is not None) as unit:
        configure_database(unit)
        value = Journey(unit, request.node.nodeid, source_digest(ROOT), binding_digest())
        yield value
        connections.close_all()


@pytest.fixture(scope='module')
def readonly_cohort(owned_runtime):
    from .fixtures import configure_database
    from .journey_fixtures import build,business_state
    from django.db import connections
    with owned_runtime.unit('cohort-business-readonly') as unit:
        configure_database(unit)
        data=build(); before=business_state()
        yield unit,data
        configure_database(unit)
        assert business_state()==before,'read-only cohort changed business state'
        connections.close_all()


@pytest.fixture
def cohort_case(owned_runtime,readonly_cohort,request):
    from .fixtures import Journey,configure_database
    from .coverage import source_digest
    from .bindings import binding_digest
    from .runtime import ROOT
    from .journey_fixtures import build
    from django.db import connections
    marker=request.node.get_closest_marker('cohort_executor')
    executor=marker.args[0]
    variant=request.node.callspec.params.get('variant') if hasattr(request.node,'callspec') else None
    mutate=request.node.name.startswith('test_operator') or (variant and variant[1]=='access')
    if mutate:
        with owned_runtime.unit('cohort-isolated-mutation') as unit:
            configure_database(unit);data=build()
            yield Journey(unit,request.node.nodeid,source_digest(ROOT),binding_digest()),data,executor
            connections.close_all()
    else:
        unit,data=readonly_cohort
        configure_database(unit)
        yield Journey(unit,request.node.nodeid,source_digest(ROOT),binding_digest()),data,executor
        connections.close_all()
