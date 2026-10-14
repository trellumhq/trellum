"""Request-thread work measurements; no SQL, credentials or response body."""
from contextvars import ContextVar
from functools import wraps
import json
import os
import threading
import time
import hashlib
import hmac
from pathlib import Path
import uuid
import re
from django.db import connection

_lock = threading.Lock()
_current = ContextVar('portal_e2e_measurement', default=None)
_storage_depth = ContextVar('portal_e2e_storage_depth', default=0)

def install_storage_measurements():
    from apps.core import storage
    for name in ('read_meta', 'read_live_queries', 'output_root', 'html_index', 'live_query_index', 'output_bytes', 'current_build', '_get_object_bytes'):
        original = getattr(storage, name)
        if getattr(original, '_portal_e2e_measured', False):
            continue
        def wrap(function, operation):
            @wraps(function)
            def measured(*args, **kwargs):
                row = _current.get()
                if row is None:
                    return function(*args, **kwargs)
                tick = time.perf_counter()
                depth = _storage_depth.get()
                token = _storage_depth.set(depth+1)
                try:
                    return function(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter()-tick)*1000
                    _storage_depth.reset(token)
                    if depth == 0:
                        row['storage_count'] += 1
                        row['storage_ms'] += elapsed
                    row['storage_operations'][operation] = row['storage_operations'].get(operation, 0)+1
            measured._portal_e2e_measured = True
            return measured
        setattr(storage, name, wrap(original, name))

class MeasureRequests:
    def __init__(self, get_response):
        self.get_response = get_response
        install_storage_measurements()
        ledger = json.loads(Path(__file__).with_name('routes.json').read_text())
        self.routes = {row['pattern']: row for row in ledger['routes']}
        self.principals = set(ledger['principal_universe']) | {'unlabeled'}
        self.salt = os.environ.get('PORTAL_E2E_SESSION_SALT', uuid.uuid4().hex).encode()

    def __call__(self, request):
        phase = request.headers.get('X-Portal-E2E-Phase', 'unlabeled')
        if phase == 'registry-cache-cold' and request.path == request.headers.get('X-Portal-E2E-Cache-Target'):
            from apps.reports.scan import invalidate_registry_cache
            invalidate_registry_cache()
        started = time.perf_counter()
        counts = {'sql_count': 0, 'sql_ms': 0.0, 'storage_count': 0, 'storage_ms': 0.0, 'storage_operations': {}}
        token = _current.set(counts)
        def measure(execute, sql, params, many, context):
            tick = time.perf_counter()
            try:
                delay = float(os.environ.get('PORTAL_E2E_SQL_DELAY_MS', '0'))
                if delay:
                    time.sleep(delay / 1000)
                return execute(sql, params, many, context)
            finally:
                counts['sql_count'] += 1
                counts['sql_ms'] += (time.perf_counter()-tick)*1000
        identity = {}
        try:
            with connection.execute_wrapper(measure):
                response = self.get_response(request)
                user = getattr(request, 'user', None)
                session = getattr(request, 'session', None)
                key = getattr(session, 'session_key', None)
                identity = {'actor_id': user.pk if user is not None and user.is_authenticated else None,
                            'impersonator_id': session.get('impersonator_id') if session is not None else None,
                            'session_digest': hmac.new(self.salt, key.encode(), hashlib.sha256).hexdigest() if key else None}
        finally:
            _current.reset(token)
        role = request.headers.get('X-Portal-E2E-Role', 'unlabeled')
        phase = request.headers.get('X-Portal-E2E-Phase', 'unlabeled')
        # Header labels are attribution supplied by the test, not an auth bypass.
        if len(role)>40 or len(phase)>40:
            role,phase = 'invalid-label','invalid-label'
        if role not in self.principals:
            role = 'unclassified'
        if phase not in {'unlabeled', 'registry-cache-cold', 'registry-cache-warm', 'cold', 'warm'}:
            phase = 'invalid-label'
        def label(name, pattern, maximum):
            value = request.headers.get(name, '')
            return value if len(value) <= maximum and re.fullmatch(pattern, value) else ''
        def record(size, complete=True):
            match = getattr(request, 'resolver_match', None)
            pattern = getattr(match, 'route', None)
            route = self.routes.get(pattern)
            action = None
            if route:
                declared = {item.get('action') for item in route['scenarios']}
                try:
                    # Only reviewed selectors enter portable evidence. Never
                    # serialize arbitrary body values, query strings or tokens.
                    selectors = request.POST if request.method == 'POST' else request.GET
                    for name in ('action', 'tab'):
                        candidate = name + '=' + selectors.get(name, '')
                        if candidate in declared:
                            action = candidate
                            break
                except Exception:
                    action = '<unreadable-body>'
            row = {'path': '/' + pattern if pattern is not None else '<unresolved>', 'route': route['id'] if route else None,
                   'method': request.method, 'action': action, 'role': role, 'cache_phase': phase, **identity,
                   'event_id': uuid.uuid4().hex, 'complete': complete,
                   'node_id': label('X-Portal-E2E-Node', r'apps/core/tests/portal_e2e/[\w./:\[\], -]+', 300),
                   'correlation': label('X-Portal-E2E-Correlation', r'[0-9a-f]{32}', 32),
                   'principal': role if role in self.principals else 'unclassified',
                   'condition': label('X-Portal-E2E-Condition', r'[a-z0-9-]+', 80),
                   'status': response.status_code, 'response_bytes': size, 'elapsed_ms': (time.perf_counter()-started)*1000, **counts}
            with _lock, open(os.environ['PORTAL_E2E_WORK_LOG'], 'a') as out:
                out.write(json.dumps(row)+'\n')
        if response.streaming:
            original = response.streaming_content
            def complete():
                token = _current.set(counts)
                size = 0
                finished = False
                with connection.execute_wrapper(measure):
                    try:
                        for chunk in original:
                            size += len(chunk)
                            yield chunk
                        finished = True
                    finally:
                        record(size, complete=finished)
                        _current.reset(token)
            response.streaming_content = complete()
        else:
            record(len(response.content))
        return response
