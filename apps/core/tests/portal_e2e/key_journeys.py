"""Shared plumbing for the small key user-journey set (not a permission matrix).

Each journey is one pytest node that drives a real browser against the owned
Gunicorn/PostgreSQL unit. It records only the bindings declared for that node in
``bindings.KEY_JOURNEYS``; the event for each binding is the server-observed
request (route/method/action/actor), never a response status guess.
"""
from contextlib import contextmanager
import json
from pathlib import Path

from .bindings import BINDINGS, KEY_CONDITIONS

DESKTOP = {'engine': 'chromium', 'width': 1440, 'height': 900, 'mobile': False}
MOBILE = {'engine': 'webkit', 'width': 390, 'height': 844, 'mobile': True}
VARIANTS = {'chromium-desktop': DESKTOP, 'webkit-mobile': MOBILE}


def variant_id(spec):
    return 'webkit-mobile' if spec['mobile'] else 'chromium-desktop'


@contextmanager
def browser_session(journey, principal, spec=DESKTOP, *, condition, login_first=True):
    """Authenticated page whose requests carry this journey's attribution headers."""
    from playwright.sync_api import sync_playwright
    from .fixtures import login
    with sync_playwright() as playwright:
        browser = getattr(playwright, spec['engine']).launch()
        context = browser.new_context(
            viewport={'width': spec['width'], 'height': spec['height']},
            has_touch=spec['mobile'], is_mobile=spec['mobile'] and spec['engine'] != 'firefox',
            extra_http_headers=journey.headers(principal, condition))
        context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            if login_first:
                login(page, journey.unit.origin, principal)
                journey.assert_actor(context, principal)
            yield page, context
            assert not errors, errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2'), 'horizontal overflow'
        finally:
            context.tracing.stop(path=str(Path(journey.unit.env['PORTAL_E2E_ARTIFACTS']) / f'{journey.correlation}-private-trace.zip'))
            browser.close()


def observed(journey, route, method, *, action=None, status=None, index=-1):
    """The server-observed request for a route, completed and attributed to this window."""
    rows = [row for row in journey.events() if row['route'] == route and row['method'] == method
            and row['action'] == action and row['complete'] and (status is None or row['status'] == status)]
    assert rows, f'no observed {method} {route} {action} status={status}'
    return rows[index]


def bind(journey, route, method, action, principal, spec, checkpoints, *, status=None, index=-1, every=False):
    """Record one reviewed binding after the behaviour assertions that justify it.

    ``every`` records all matching observed requests (for example a success and a
    timeout probe), otherwise the single event at ``index``.
    """
    node = journey.node_id
    matches = [row for row in BINDINGS if row.node_id == node and row.route == route and row.method == method
               and row.action == action and row.principal == principal
               and row.browser == spec['engine'] and row.viewport == f"{spec['width']}x{spec['height']}"]
    assert len(matches) == 1, f'binding for {node} {route} {method} {action} must be declared exactly once'
    binding = matches[0]
    assert binding.condition in KEY_CONDITIONS
    events = [observed(journey, route, method, action=action, status=status, index=index)]
    if every:
        events = [row for row in journey.events() if row['route'] == route and row['method'] == method
                  and row['action'] == action and row['complete'] and (status is None or row['status'] == status)]
    for event in events:
        journey.assert_observed_actor(event)
    for name in checkpoints:
        journey.checkpoint(name)
    assert set(binding.obligations) <= journey.checkpoints, set(binding.obligations) - journey.checkpoints
    journey.record(key=binding.key, event_ids=[event['event_id'] for event in events], scope='journey')


def record_metrics(journey, page):
    from .fixtures import browser_metrics
    with (Path(journey.unit.env['PORTAL_E2E_ARTIFACTS']) / 'browser-metrics.jsonl').open('a') as output:
        output.write(json.dumps({'node_id': journey.node_id, **browser_metrics(page)}) + '\n')
