"""Measure SQL in the actual HTTP request thread; omit SQL and credentials."""
import json
import os
import threading
import time
from django.db import connection

_lock = threading.Lock()

class MeasureRequests:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        counts = {'sql_count': 0, 'sql_ms': 0.0}
        def measure(execute, sql, params, many, context):
            tick = time.perf_counter()
            try:
                delay = float(os.environ.get('PORTAL_E2E_SQL_DELAY_MS', '0'))
                if delay:
                    time.sleep(delay / 1000)
                return execute(sql, params, many, context)
            finally:
                counts['sql_count'] += 1
                counts['sql_ms'] += (time.perf_counter() - tick) * 1000
        with connection.execute_wrapper(measure):
            response = self.get_response(request)
        def record():
            row = {'path': request.path, 'method': request.method,
                   'status': response.status_code, 'elapsed_ms': (time.perf_counter()-started)*1000,
                   **counts}
            with _lock, open(os.environ['PORTAL_E2E_WORK_LOG'], 'a') as out:
                out.write(json.dumps(row) + '\n')
        if response.streaming:
            original = response.streaming_content
            def complete():
                with connection.execute_wrapper(measure):
                    try:
                        yield from original
                    finally:
                        record()
            response.streaming_content = complete()
        else:
            record()
        return response
