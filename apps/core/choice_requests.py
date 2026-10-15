"""Read-only choice parameters for ordinary GET and large JSON POST reads."""
import json

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
INLINE_CHOICE_LIMIT = 500
SEARCH_PREVIEW = 25


def inline_choices(rows, *, limit=None, preview=SEARCH_PREVIEW):
    """Return ``(choices, searchable)`` for an ordered queryset or sequence.

    ``searchable`` is true only when more than ``limit`` choices exist; the
    caller then wires the server search endpoint and renders ``preview`` rows."""
    limit = INLINE_CHOICE_LIMIT if limit is None else limit
    sample = list(rows[:limit + 1])
    if len(sample) <= limit:
        return sample, False
    return sample[:preview], True
