"""Read-only choice parameters for ordinary GET and large JSON POST reads, and the
shared shape of every picker's server search: ``{results, selected, pagination}``."""
import json
from functools import reduce
from operator import or_

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse

from apps.core.limits import INLINE_LIMIT

_KEYS = {"q", "page", "kind", "selected", "selected_people", "selected_groups"}


class ChoiceParameters:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getlist(self, key):
        value = self.values.get(key, [])
        return value if isinstance(value, list) else [value]


def choice_request_params(request):
    if request.method == "GET":
        return request.GET
    if request.method != "POST" or request.content_type != "application/json":
        raise ValueError("Choice reads require GET or a JSON POST.")
    try:
        values = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid choice request.") from exc
    if not isinstance(values, dict) or values.keys() - _KEYS:
        raise ValueError("Invalid choice request.")
    for key, value in values.items():
        if key == "selected":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("Selected choices must be strings.")
        elif not isinstance(value, str):
            raise ValueError("Choice parameters must be strings.")
    return ChoiceParameters(values)


# Pickers render every choice inline up to this many, so the dropdown filters
# instantly in the browser exactly like a plain select. Only a longer list gets
# a short preview plus server search-as-you-type; it never gets pages.
INLINE_CHOICE_LIMIT = INLINE_LIMIT
SEARCH_PREVIEW = 25
#: Rows in one page of the server search. The browser never shows pages: it keeps
#: typing narrowing the list ("Keep typing to narrow the list.").
CHOICE_PAGE_SIZE = 25
_MAX_BIGINT = 2**63 - 1


def inline_choices(rows, *, limit=None, preview=SEARCH_PREVIEW):
    """Return ``(choices, searchable)`` for an ordered queryset or sequence.

    ``searchable`` is true only when more than ``limit`` choices exist; the
    caller then wires the server search endpoint and renders ``preview`` rows."""
    limit = INLINE_CHOICE_LIMIT if limit is None else limit
    sample = list(rows[:limit + 1])
    if len(sample) <= limit:
        return sample, False
    return sample[:preview], True


def choice_id(value, *, prefix=''):
    """The database id a picker's ``selected`` value names, else None.

    Digits only (ASCII), within a 64-bit key: a malformed or oversized value
    selects nothing instead of reaching the database. ``prefix`` is a leading
    marker some pickers send (``id:42``).
    """
    value = str(value or '')
    if prefix and value.startswith(prefix):
        value = value[len(prefix):]
    if value.isascii() and value.isdigit() and len(value) <= 19 and 0 < int(value) <= _MAX_BIGINT:
        return int(value)
    return None


def selected_by_id(rows, value, *, field='pk', prefix=''):
    """The row of ``rows`` a ``selected`` value names, or None.

    ``rows`` carries the caller's permission scope and is *not* narrowed by the
    search text, so the current selection resolves whatever is typed.
    """
    pk = choice_id(value, prefix=prefix)
    return rows.filter(**{field: pk}).first() if pk is not None else None


def search_page(rows, query='', *, fields=(), page=None, size=CHOICE_PAGE_SIZE):
    """One page of ``rows`` whose ``fields`` contain ``query`` (case-insensitive).

    The caller supplies the ordering and decides how ``query`` is trimmed. A page
    number that is not a number, or is past the end, gets the first or last page.
    """
    if query and fields:
        rows = rows.filter(reduce(or_, (Q(**{f'{field}__icontains': query}) for field in fields)))
    return Paginator(rows, size).get_page(page)


def choice_response(page, item, *, selected=None, pagination):
    """The JSON every picker search answers with, marked private and uncacheable.

    ``selected`` is one row or None, or a list of rows for a multi-select picker;
    ``pagination`` is the caller's metadata dict (its keys are part of the
    endpoint's contract).
    """
    chosen = [item(row) for row in selected] if isinstance(selected, (list, tuple)) else (
        item(selected) if selected else None)
    response = JsonResponse({
        'results': [item(row) for row in page.object_list],
        'selected': chosen,
        'pagination': pagination,
    })
    response['Cache-Control'] = 'private, no-store'
    return response
