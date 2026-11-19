"""Build-local secret filtering at framework output boundaries."""
from __future__ import annotations

import contextlib
import contextvars
import functools
import html
import json
import logging
import os
import re
import sys
import threading
import traceback
import types
from subprocess import CalledProcessError, TimeoutExpired
from urllib.error import HTTPError
from urllib.parse import quote

from trellum.secret_prefix import SecretPrefixes

_values = contextvars.ContextVar('trellum_scalar_secrets', default=())
_build_depth = contextvars.ContextVar('trellum_redaction_depth', default=0)
_lock = threading.RLock()
_users = 0
_active = {}
_thread_secrets = ()

_NUMERIC_EXCEPTION_CONTROLS = (
    (BaseException, {'__suppress_context__'}),
    (SystemExit, {'code'}),
    (OSError, {'errno', 'winerror', 'characters_written'}),
    (SyntaxError, {'lineno', 'offset', 'end_lineno', 'end_offset', 'print_file_and_line'}),
    (UnicodeError, {'start', 'end'}),
    (CalledProcessError, {'returncode'}),
    (TimeoutExpired, {'timeout'}),
    (HTTPError, {'code'}),
)


def _secret_values():
    # Report-created threads do not inherit ContextVars. Logs from those
    # threads still pass through the active build's filter.
    return _values.get() or _thread_secrets


def redact(value, _ancestors=None):
    """Filter raw, HTML-escaped and JSON-escaped text before serialization/write."""
    if not _secret_values():
        return value
    if isinstance(value, bytes):
        pattern = b'|'.join(re.escape(part.encode('utf-8'))
                            for part in sorted({'[REDACTED]', *_secret_values()}, key=len, reverse=True))
        return re.sub(pattern, b'[REDACTED]', value)
    if isinstance(value, str):
        # One pass prevents a short secret from corrupting a replacement,
        # including when an artifact is filtered again during publication.
        pattern = '|'.join(re.escape(part) for part in sorted({'[REDACTED]', *_secret_values()}, key=len, reverse=True))
        return re.sub(pattern, '[REDACTED]', value)
    if isinstance(value, (dict, tuple, list)):
        ancestors = set() if _ancestors is None else _ancestors
        identity = id(value)
        if identity in ancestors:
            return '[REDACTED]'
        ancestors.add(identity)
        try:
            if isinstance(value, dict):
                return {redact(k, ancestors): redact(v, ancestors) for k, v in value.items()}
            if isinstance(value, tuple):
                return tuple(redact(v, ancestors) for v in value)
            return [redact(v, ancestors) for v in value]
        finally:
            # Shared children in different branches are not cycles.
            ancestors.remove(identity)
    if isinstance(value, (int, float)) and (str(value) in _secret_values()
            or json.dumps(value) in _secret_values()):
        return '[REDACTED]'
    return value


class _Stream:
    def __init__(self, stream):
        self.stream = stream
        self.context = contextvars.ContextVar('redaction_stream_context', default=None)
        self.pending = {}
        self.lock = threading.RLock()
        self.prefixes = None

    def write(self, text):
        if not _secret_values():
            return self.stream.write(text)
        key = self.context.get()
        if key is None:
            key = object()
            self.context.set(key)
        with self.lock:
            pending = redact(self.pending.pop(key, '') + text)
            # Retain only a suffix that could become a secret on a later write.
            # The registry lets build cleanup drain contexts of joined workers.
            secrets = _secret_values()
            if self.prefixes is None or self.prefixes.secrets != secrets:
                self.prefixes = SecretPrefixes(secrets)
            keep = self.prefixes.retained_length(pending)
            if keep:
                self.pending[key] = pending[-keep:]
            self.stream.write(pending[:-keep] if keep else pending)
        return len(text)

    def finish(self, *, all_contexts=False):
        with self.lock:
            pending = (''.join(self.pending.values()) if all_contexts
                       else self.pending.pop(self.context.get(), ''))
            if all_contexts:
                self.pending.clear()
            self.stream.write(redact(pending))
            self.stream.flush()

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def environment_secrets():
    """Host-registered scalar values, without importing report or project code."""
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
    # A JSON secret can be consumed as separate fields by native/child code.
    # The host registration contains the original object; retain its leaves
    # for the process-level filter too, without importing project config.
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
    for value in tuple(values):
        try:
            obj = json.loads(value)
            if isinstance(obj, dict):
                values.extend(leaves(obj))
        except (ValueError, RecursionError):
            pass
    return values


def secret_variants(values):
    """Literal and common escaped representations at text output boundaries."""
    variants = set()
    for value in values:
        if value:
            variants.update((value, html.escape(value), html.escape(value, quote=False),
                             json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1],
                             repr(value)[1:-1], ascii(value)[1:-1],
                             repr(value.encode('utf-8'))[2:-1],
                             quote(value, safe='')))
    return tuple(sorted(variants, key=len, reverse=True))


def _secrets(config):
    from trellum.data.datasource_config import resolve_scalar_source
    from trellum.data.scalar import SCALAR_TYPES, scalar_value
    values = environment_secrets()
    sources = config.get('data_sources', [])
    for entry in sources:
        try:
            source = resolve_scalar_source(sources, entry if isinstance(entry, str) else entry.get('name'))
        except ValueError:
            continue  # Missing values stay lazy and catchable by the report.
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

    return secret_variants(values)


def _exception_value(value, seen):
    """Filter values that exception formatters may stringify after cleanup."""
    if isinstance(value, BaseException):
        return _scrub_exception(value, seen)
    if isinstance(value, (dict, list, tuple)):
        if id(value) in seen:
            return seen[id(value)]
        result = {} if isinstance(value, dict) else []
        seen[id(value)] = result
        if isinstance(value, dict):
            result.update((_exception_value(k, seen), _exception_value(v, seen))
                          for k, v in value.items())
        else:
            result.extend(_exception_value(child, seen) for child in value)
        if isinstance(value, tuple):
            result = seen[id(value)] = tuple(result)
        return result
    if isinstance(value, (str, bytes, int, float)) or value is None:
        return redact(value)
    for formatter in (str, repr):
        try:
            text = formatter(value)
        except Exception:
            continue
        filtered = redact(text)
        if filtered != text:
            return filtered
    return value


def _scrub_exception(error, seen):
    """Filter ordinary attributes and built-in slots, retaining exception types."""
    if error is None or id(error) in seen:
        return seen.get(id(error))
    seen[id(error)] = None  # Break cycles while an immutable group is rebuilt.
    group = isinstance(error, BaseExceptionGroup)
    if group:
        children = [_scrub_exception(child, seen) for child in error.exceptions]
        safe = BaseExceptionGroup.__new__(type(error), redact(error.message), children)
        safe.__dict__.update(vars(error))
        safe.__traceback__ = error.__traceback__
    else:
        safe = error
        safe.args = tuple(_exception_value(arg, seen) for arg in error.args)
    seen[id(error)] = safe
    attributes = dict(vars(error))
    for cls in type(error).__mro__:
        for name, descriptor in vars(cls).items():
            if isinstance(descriptor, types.MemberDescriptorType):
                attributes[name] = getattr(error, name, None)
    for attribute, value in attributes.items():
        # Group message/children are immutable and already rebuilt above.
        if group and attribute in {'message', 'exceptions'}:
            continue
        # Preserve numeric fields used for exception control flow/formatting;
        # custom numeric payloads can contain scalar secrets just like strings.
        if isinstance(value, (int, float)) and any(
            isinstance(error, cls) and attribute in fields
            for cls, fields in _NUMERIC_EXCEPTION_CONTROLS
        ):
            continue
        setattr(safe, attribute, _exception_value(value, seen))
    cause, context, suppress = error.__cause__, error.__context__, error.__suppress_context__
    safe.__cause__ = _scrub_exception(cause, seen)
    safe.__context__ = _scrub_exception(context, seen)
    safe.__suppress_context__ = suppress
    return safe


@contextlib.contextmanager
def build_redaction(config):
    global _users, _thread_secrets
    secrets = _secrets(config)
    inherited = set(_values.get())
    token = _values.set(tuple(sorted(inherited.union(secrets), key=len, reverse=True)))
    depth = _build_depth.get()
    depth_token = _build_depth.set(depth + 1)
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
            old_make_record = logging.Logger.makeRecord
            def make_record(logger, name, level, fn, lno, msg, args, exc_info,
                            func=None, extra=None, sinfo=None):
                # Extra fields are attached after the record factory runs.
                # Preserve formatter field names while filtering their values.
                if extra and _secret_values():
                    extra = {key: redact(value) for key, value in extra.items()}
                return old_make_record(logger, name, level, fn, lno, msg, args,
                                       exc_info, func, extra, sinfo)
            make_record.previous = old_make_record
            logging.Logger.makeRecord = make_record
            old_format = logging.Handler.format
            def format_record(handler, record):
                # Formatters can stringify objects or otherwise transform
                # fields. Filter that final text before any standard sink.
                return redact(old_format(handler, record))
            format_record.previous = old_format
            logging.Handler.format = format_record
        _users += 1
    try:
        yield
    except BaseException as exc:
        if not _secret_values():
            raise
        safe = _scrub_exception(exc, {})
        if safe is exc:
            raise
        context = safe.__context__
        try:
            raise safe
        finally:
            # Python attaches the original group while raising its replacement.
            safe.__context__ = context
    finally:
        with _lock:
            # A nested scope shares its parent's pending text. Keep possible
            # secret prefixes buffered until that outer scope finishes.
            if depth == 0 or _users == 1:
                sys.stdout.finish(all_contexts=_users == 1)
                sys.stderr.finish(all_contexts=_users == 1)
            _users -= 1
            for secret in secrets:
                _active[secret] -= 1
                if not _active[secret]:
                    del _active[secret]
            _thread_secrets = tuple(sorted(_active, key=len, reverse=True))
            if _users == 0:
                sys.stdout, sys.stderr = sys.stdout.stream, sys.stderr.stream
                logging.setLogRecordFactory(logging.getLogRecordFactory().previous)
                logging.Logger.makeRecord = logging.Logger.makeRecord.previous
                logging.Handler.format = logging.Handler.format.previous
        _values.reset(token)
        _build_depth.reset(depth_token)


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
