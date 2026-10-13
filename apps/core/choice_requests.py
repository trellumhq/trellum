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
