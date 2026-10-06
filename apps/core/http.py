"""Request-parsing helpers shared by the JSON API views."""

from __future__ import annotations

import json


def json_body(request) -> dict:
    """The request's JSON body as a dict; ``{}`` for an empty, malformed or
    non-object body.

    Valid JSON that isn't an object ("[1,2]", "42", "null") parses fine but has
    no ``.get()`` — every caller treats the result as a dict, so it reads as an
    empty one rather than crashing downstream.
    """
    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def int_param(value, default: int) -> int:
    """A query-string integer, or ``default`` when absent, non-numeric or
    negative."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default
