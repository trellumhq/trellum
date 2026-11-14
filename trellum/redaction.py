"""Build-local secret filtering at framework output boundaries."""
from __future__ import annotations

import contextlib
import contextvars
import functools
import html
import json
import logging
import os
import sys
import threading
import traceback
from urllib.parse import quote

_values = contextvars.ContextVar('trellum_scalar_secrets', default=())
_lock = threading.RLock()
_users = 0
_active = {}
_thread_secrets = ()


def _secret_values():
    # Report-created threads do not inherit ContextVars. Logs from those
    # threads still pass through the active build's filter.
    return _values.get() or _thread_secrets


def redact(value):
    """Filter raw, HTML-escaped and JSON-escaped text before serialization/write."""
    if not _secret_values():
        return value
    if isinstance(value, bytes):
        return redact(value.decode('utf-8')).encode('utf-8')
    if isinstance(value, str):
        for secret in _secret_values():
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, dict):
        return {redact(k): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, (int, float)) and (str(value) in _secret_values()
            or json.dumps(value) in _secret_values()):
        return '[REDACTED]'
    return value


class _Stream:
    def __init__(self, stream):
        self.stream = stream
        self.pending = contextvars.ContextVar('redaction_pending', default='')

    def write(self, text):
        if not _secret_values():
            return self.stream.write(text)
        pending = redact(self.pending.get() + text)
        # Keep any suffix that could become a secret on a later write. This
        # covers split writes and multiline text secrets without buffering logs.
        keep = 0
        for secret in _secret_values():
            for length in range(min(len(secret) - 1, len(pending)), keep, -1):
                if pending.endswith(secret[:length]):
                    keep = length
                    break
        self.pending.set(pending[-keep:] if keep else '')
        self.stream.write(pending[:-keep] if keep else pending)
        return len(text)

    def finish(self):
        self.stream.write(redact(self.pending.get()))
        self.pending.set('')
        self.stream.flush()

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _secrets(config):
    from trellum.data.datasource_config import get_datasource
    from trellum.data.scalar import SCALAR_TYPES, scalar_value
    values = []
    try:
        supplied = json.loads(os.environ.get('TRELLUM_SCALAR_SECRETS', '[]'))
    except ValueError:
        raise ValueError('TRELLUM_SCALAR_SECRETS must be a JSON list of strings') from None
    if not isinstance(supplied, list) or any(not isinstance(v, str) for v in supplied):
        raise ValueError('TRELLUM_SCALAR_SECRETS must be a JSON list of strings')
    try:
        names = json.loads(os.environ.get('TRELLUM_SCALAR_SECRET_NAMES', '[]'))
    except ValueError:
        raise ValueError('TRELLUM_SCALAR_SECRET_NAMES must be a JSON list of environment names') from None
    if (not isinstance(names, list) or any(not isinstance(name, str)
            or not name.startswith('TRELLUM_DS_') or not name.endswith('_VALUE')
            for name in names)):
        raise ValueError('TRELLUM_SCALAR_SECRET_NAMES must be a JSON list of environment names')
    # The host passes names so a group of multiline secrets does not exceed
    # the operating system's limit on one environment variable.
    for name in names:
        value = os.environ.get(name, '')
        if value:
            values.extend([value, value.strip()])
    values.extend(supplied)
    for entry in config.get('data_sources', []):
        source = (get_datasource(entry) if isinstance(entry, str)
                  else get_datasource(entry.get('name')) or entry)
        if (source and source.get('type') in SCALAR_TYPES
                and (source.get('type') == 'secret' or source.get('secret') is True)):
            value = scalar_value(source)
            if value:
                values.extend([value, value.strip()])
                if source.get('type') == 'json':
                    # A secret JSON object may be rendered as separate fields.
                    # Register its leaves as well as the original JSON text.
                    def leaves(item):
                        if isinstance(item, dict):
                            for key, child in item.items():
                                yield str(key)
                                yield from leaves(child)
                        elif isinstance(item, list):
                            for child in item:
                                yield from leaves(child)
                        elif isinstance(item, str):
                            yield item
                        else:
                            yield json.dumps(item)
                    try:
                        values.extend(leaves(json.loads(value)))
                    except ValueError:
                        pass

    variants = set()
    for value in values:
        if value:
            variants.update((value, html.escape(value), html.escape(value, quote=False),
                             json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1],
                             quote(value, safe='')))
    return tuple(sorted(variants, key=len, reverse=True))


@contextlib.contextmanager
def build_redaction(config):
    global _users, _thread_secrets
    secrets = _secrets(config)
    token = _values.set(secrets)
    with _lock:
        for secret in secrets:
            _active[secret] = _active.get(secret, 0) + 1
        _thread_secrets = tuple(sorted(_active, key=len, reverse=True))
        if _users == 0:
            sys.stdout, sys.stderr = _Stream(sys.stdout), _Stream(sys.stderr)
            old_factory = logging.getLogRecordFactory()
            def factory(*args, **kwargs):
                record = old_factory(*args, **kwargs)
                if _secret_values():
                    record.msg, record.args = redact(record.getMessage()), ()
                    if record.exc_info:
                        record.exc_text = redact(''.join(traceback.format_exception(*record.exc_info)))
                        record.exc_info = None
                return record
            factory.previous = old_factory
            logging.setLogRecordFactory(factory)
        _users += 1
    try:
        yield
    except Exception as exc:
        # Preserve the exception type for callers, remove unsafe exception chains.
        seen = set()
        def scrub(error):
            if error is None or id(error) in seen:
                return
            seen.add(id(error))
            error.args = tuple(redact(arg) for arg in error.args)
            # OSError.__str__ uses these attributes independently of args.
            if isinstance(error, OSError):
                for attribute in ('filename', 'filename2', 'strerror'):
                    value = getattr(error, attribute, None)
                    if isinstance(value, (str, bytes)):
                        setattr(error, attribute, redact(value))
            if getattr(error, '__notes__', None):
                error.__notes__ = [redact(note) for note in error.__notes__]
            scrub(error.__cause__)
            scrub(error.__context__)
            for child in getattr(error, 'exceptions', ()):
                scrub(child)
        scrub(exc)
        raise
    finally:
        with _lock:
            sys.stdout.finish()
            sys.stderr.finish()
            _users -= 1
            for secret in secrets:
                _active[secret] -= 1
                if not _active[secret]:
                    del _active[secret]
            _thread_secrets = tuple(sorted(_active, key=len, reverse=True))
            if _users == 0:
                sys.stdout, sys.stderr = sys.stdout.stream, sys.stderr.stream
                logging.setLogRecordFactory(logging.getLogRecordFactory().previous)
        _values.reset(token)


def protected_build(function):
    @functools.wraps(function)
    def wrapped(report_dir, *args, **kwargs):
        from trellum.report import BaseReport
        with build_redaction(BaseReport.load_config(report_dir)):
            return function(report_dir, *args, **kwargs)
    return wrapped


def safe_json_bytes(value, **kwargs):
    import orjson
    encoded = orjson.dumps(value, **kwargs)
    if not _secret_values():
        return encoded
    # Normalize serializer-specific values (numpy, dates) before filtering.
    # Redact JSON values rather than raw bytes so numeric secrets cannot turn
    # a larger JSON number into invalid syntax.
    return orjson.dumps(redact(json.loads(encoded)), **kwargs)


def safe_json_document(encoded):
    """Keep pre-serialized chunk bytes unchanged unless redaction is active."""
    if not _secret_values():
        return encoded
    return safe_json_bytes(json.loads(encoded))
