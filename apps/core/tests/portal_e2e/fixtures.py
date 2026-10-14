"""Live journey contexts; actor checks use sessions and DB, never role headers."""
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import uuid


def configure_database(unit):
    from django.conf import settings
    from django.db import connections
    import environ
    connections.close_all()
    database = environ.Env.db_url_config(unit.env['DATABASE_URL'])
    settings.DATABASES['default'].update(database)
    connections['default'].settings_dict.update(database)
    settings.DATA_DIR = unit.data_dir


class Journey:
    def __init__(self, unit, node_id, source, binding_hash):
        self.unit = unit
        self.node_id = node_id
        self.source = source
        self.binding_hash = binding_hash
        self.correlation = uuid.uuid4().hex
        self.checkpoints = set()
        self.actor = None
        self.contexts = []

    def headers(self, principal, condition):
        return {'X-Portal-E2E-Node': self.node_id,
                'X-Portal-E2E-Correlation': self.correlation,
                'X-Portal-E2E-Role': principal,
                'X-Portal-E2E-Condition': condition}

    def events(self):
        path = Path(self.unit.env['PORTAL_E2E_WORK_LOG'])
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if json.loads(line).get('correlation') == self.correlation]

    def assert_actor(self, context, principal):
        from django.conf import settings
        from django.contrib.auth import get_user_model
        from django.contrib.sessions.models import Session
        from .identities import ROLES
        cookies = {cookie['name']: cookie['value'] for cookie in context.cookies(self.unit.origin)}
        key = cookies.get(settings.SESSION_COOKIE_NAME)
        if principal == 'anonymous':
            assert not key or '_auth_user_id' not in Session.objects.get(session_key=key).get_decoded()
            user_id, impersonator = None, None
        else:
            assert key, 'authenticated session cookie missing'
            session = Session.objects.get(session_key=key).get_decoded()
            user = get_user_model().objects.get(email=ROLES[principal])
            assert user.is_active
            assert session['_auth_user_id'] == str(user.pk)
            user_id = user.pk
            impersonator = session.get('impersonator_id')
            if principal == 'impersonated':
                assert impersonator == get_user_model().objects.get(email=ROLES['operator']).pk
            else:
                assert impersonator is None
        digest = hmac.new(self.unit.env['PORTAL_E2E_SESSION_SALT'].encode(), key.encode(), hashlib.sha256).hexdigest() if key else None
        self.actor = {'principal': principal, 'actor_id': user_id,
                      'session_digest': digest, 'impersonator_id': impersonator}
        self.checkpoints.add('actor')
        return self.actor

    def assert_observed_actor(self, event):
        assert self.actor is not None
        for name in ('actor_id', 'session_digest', 'impersonator_id'):
            assert event[name] == self.actor[name]
        self.checkpoints.add('observed-actor')

    def checkpoint(self, name):
        # Call only after the corresponding behavior assertion succeeds. Names
        # are obligations, not statuses inferred from HTTP response codes.
        self.checkpoints.add(name)

    def record(self, *, key=None, event_ids=(), scope='infrastructure'):
        row = {'scope': scope, 'node_id': self.node_id, 'source': self.source,
               'binding_hash': self.binding_hash, 'correlation': self.correlation,
               'checkpoints': sorted(self.checkpoints), 'actor': self.actor,
               'events': list(event_ids), 'key': list(key) if key else None}
        path = Path(self.unit.env['PORTAL_E2E_ARTIFACTS']) / 'assertions.jsonl'
        with path.open('a') as output:
            output.write(json.dumps(row) + '\n')


def login(page, origin, principal):
    from playwright.sync_api import expect
    from .identities import ROLES
    from .seed import PASSWORD
    page.goto(origin + '/login')
    page.locator('input[type=email]').fill(ROLES[principal])
    page.locator('input[type=password]').fill(PASSWORD)
    page.locator('button[type=submit]').click()
    page.wait_for_url(lambda url: '/login' not in url)
    page.goto(origin + '/account')
    expect(page.locator('.account-grid')).to_be_visible()


def visible_geometry(locator):
    from playwright.sync_api import expect
    expect(locator).to_be_visible()
    box = locator.bounding_box()
    assert box and box['width'] > 0 and box['height'] > 0
    assert locator.evaluate("node => !node.closest('[data-picker-fallback],[hidden]')")
    return box


def browser_metrics(page):
    return page.evaluate("""() => ({dom: document.querySelectorAll('*').length,
      frames: document.querySelectorAll('iframe').length,
      charts: document.querySelectorAll('canvas,[data-chart],.plotly').length,
      documentWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth,
      resources: performance.getEntriesByType('resource').map(e => ({
        type:e.initiatorType, transferBytes:e.transferSize,
        encodedBytes:e.encodedBodySize, decodedBytes:e.decodedBodySize}))})""")
