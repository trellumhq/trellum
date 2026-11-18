"""Named scalar inputs; values stay in credentials, never source descriptors."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

SCALAR_TYPES = frozenset({'string', 'secret', 'text', 'number', 'boolean', 'json'})


def scalar_value(source: dict) -> str | None:
    from trellum.data.resolvers import _load_dotenv_once
    _load_dotenv_once()
    credentials = source.get('credentials') or {}
    prefix = credentials.get('local') if isinstance(credentials, dict) else credentials
    prefix = prefix or source.get('local_env')
    return os.environ.get(f'{prefix}_VALUE') if prefix else None


def _required(source: dict, name: str) -> str:
    value = scalar_value(source)
    if value is None or not value.strip():
        raise ValueError(f"Data source '{name}' has no value configured")
    return value


def read_text(source: dict, name: str) -> str:
    kind = source.get('type')
    if kind in {'string', 'secret', 'text'}:
        return _required(source, name)
    if kind == 'file' and source.get('mode') == 'text':
        from trellum.project import get_project_root
        path = Path(get_project_root()) / source.get('path', '')
        if path.suffix.lower() not in {'.txt', '.json', '.yaml', '.yml'}:
            raise ValueError(f"Data source '{name}' requires a .txt, .json, .yaml or .yml text file")
        try:
            value = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            raise ValueError(f"Data source '{name}' text file is unavailable or invalid UTF-8") from None
        if not value.strip():
            raise ValueError(f"Data source '{name}' has no value configured")
        return value
    raise ValueError(f"Data source '{name}' does not support read_text")


def read_value(source: dict, name: str):
    kind = source.get('type')
    if kind not in {'number', 'boolean', 'json'}:
        raise ValueError(f"Data source '{name}' does not support read_value")
    raw = _required(source, name)
    try:
        def invalid_constant(_value):
            raise ValueError('Invalid JSON constant')
        value = json.loads(raw, parse_constant=invalid_constant)
    except (ValueError, TypeError):
        raise ValueError(f"Data source '{name}' requires a valid {kind} value") from None
    valid = (kind == 'number' and type(value) in (int, float)
             and (not isinstance(value, float) or math.isfinite(value)))
    valid |= kind == 'boolean' and type(value) is bool
    valid |= kind == 'json' and isinstance(value, dict)
    if not valid:
        raise ValueError(f"Data source '{name}' requires a valid {kind} value")
    return value
