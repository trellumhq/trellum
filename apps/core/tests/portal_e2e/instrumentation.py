"""Request-thread work measurements; no SQL, credentials or response body."""
from contextvars import ContextVar
from functools import wraps
import json
import os
import threading
import time
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
        try:
            with connection.execute_wrapper(measure):
                response = self.get_response(request)
        finally:
            _current.reset(token)
        role = request.headers.get('X-Portal-E2E-Role', 'unlabeled')
        phase = request.headers.get('X-Portal-E2E-Phase', 'unlabeled')
        # Header labels are attribution supplied by the test, not an auth bypass.
        if len(role)>40 or len(phase)>40:
            role,phase = 'invalid-label','invalid-label'
        def record(size):
            row = {'path': request.path, 'method': request.method, 'role': role, 'cache_phase': phase, 'actor_id': getattr(getattr(request, '_cached_user', None), 'pk', None),
                   'status': response.status_code, 'response_bytes': size, 'elapsed_ms': (time.perf_counter()-started)*1000, **counts}
            with _lock, open(os.environ['PORTAL_E2E_WORK_LOG'], 'a') as out:
                out.write(json.dumps(row)+'\n')
        if response.streaming:
            original = response.streaming_content
            def complete():
                token = _current.set(counts)
                size = 0
                with connection.execute_wrapper(measure):
                    try:
                        for chunk in original:
                            size += len(chunk)
                            yield chunk
                    finally:
                        record(size)
                        _current.reset(token)
            response.streaming_content = complete()
        else:
            record(len(response.content))
        return response
