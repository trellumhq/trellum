"""Read-only JSON choice transport keeps ordinary GET semantics and limits."""
import json
import pytest
from django.core.exceptions import RequestDataTooBig
from django.test import RequestFactory
from apps.core.choice_requests import choice_request_params


def test_get_preserves_repeated_selected_strings():
    request = RequestFactory().get("/choices?selected=1&selected=2&q=report&page=2")
    parameters = choice_request_params(request)
    assert parameters.getlist("selected") == ["1", "2"]
    assert parameters.get("q") == "report" and parameters.get("page") == "2"


def test_json_accepts_full_thousand_selection_without_form_field_limit():
    values = [str(i) for i in range(1000)]
    request = RequestFactory().post("/choices", {"selected": values, "q": "report", "page": "2"}, content_type="application/json")
    parameters = choice_request_params(request)
    assert parameters.getlist("selected") == values
    assert parameters.get("q") == "report"
    assert not request.POST


@pytest.mark.parametrize("body", ['[]', 'null', '{', '{"q": {}}', '{"page": 1}', '{"selected": "1"}', '{"selected": [1]}', '{"action": "save"}'])
def test_malformed_choice_body_rejected(body):
    request = RequestFactory().post("/choices", body, content_type="application/json")
    with pytest.raises(ValueError):
        choice_request_params(request)


def test_post_requires_json_and_uses_existing_body_limit(settings):
    with pytest.raises(ValueError):
        choice_request_params(RequestFactory().post("/choices", {"q": "report"}))
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 50
    request = RequestFactory().post("/choices", {"q": "x" * 100}, content_type="application/json")
    with pytest.raises(RequestDataTooBig):
        choice_request_params(request)
