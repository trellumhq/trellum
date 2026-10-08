import copy
import json

import pytest
from django.core.exceptions import ValidationError

from apps.core.email_mapping import default_custom_config, render_payload, synthetic_message_context, validate_custom_config


def test_default_and_two_distinct_contracts_preserve_typed_addresses_and_content():
    config = validate_custom_config(default_custom_config())
    context = synthetic_message_context("report")
    result = render_payload(config["payload"], context)
    assert result["to"] == [{"email": "recipient@example.net"}]
    assert result["attachments"][0]["content"] == context["message"]["attachments"][0]["content_base64"]

    second = copy.deepcopy(config)
    second["payload"] = {
        "from": {"$value": "message.from.formatted"},
        "recipients": {"$each": "message.to", "$template": {"$value": "item.address"}},
        "carbon": {"$value": "message.cc", "$omit_if_empty": True},
        "blind": {"$value": "message.bcc", "$omit_if_empty": True},
        "reply": {"$value": "message.reply_to", "$omit_if_empty": True},
        "title": {"$value": "message.subject"},
        "content": {"plain": {"$value": "message.text"}, "rich": {"$value": "message.html"}},
        "files": {"$value": "message.attachments"},
    }
    validate_custom_config(second)
    rendered = render_payload(second["payload"], context)
    assert rendered["recipients"] == ["recipient@example.net"]
    assert rendered["files"][1] == context["message"]["attachments"][1]
    assert "blind" not in rendered


@pytest.mark.parametrize("change", [
    lambda c: c.update(schema_version=2),
    lambda c: c.update(unknown="x"),
    lambda c: c.update(endpoint="http://api.example.com/send"),
    lambda c: c.update(endpoint="https://127.0.0.1/send"),
    lambda c: c.update(endpoint="https://api.example.com:8443/send"),
    lambda c: c.update(endpoint="https://api.example.com/send?token=x"),
    lambda c: c.update(header_names=["Host"]),
    lambda c: c.update(header_names=["X-Key", "x-key"]),
    lambda c: c.update(auth={"type": "api_key_header", "header_name": "Content-Type"}),
    lambda c: c.update(auth={"type": []}),
    lambda c: c.update(attachments={"snapshot_mode": []}),
    lambda c: c.update(max_request_bytes=26 * 1024 * 1024),
    lambda c: c["payload"].update(subject={"$value": "message.__class__"}),
    lambda c: c["payload"].update(subject={"$value": "message.subject", "oops": 1}),
    lambda c: c["payload"].update(to={"$each": "message.to", "$template": {"$each": "message.cc", "$template": {"$value": "item.address"}}}),
    lambda c: c["payload"].update(to={"$each": [], "$template": {"$value": "item.address"}}),
])
def test_invalid_configuration_rejected(change):
    config = default_custom_config()
    change(config)
    with pytest.raises(ValidationError):
        validate_custom_config(config)


def test_duplicate_json_keys_nonfinite_depth_nodes_size_and_omission_limits():
    for source in ['{"schema_version":1,"schema_version":1}', '{"x":NaN}']:
        with pytest.raises(ValidationError):
            validate_custom_config(source)
    config = default_custom_config()
    nested = "too deep"
    for _ in range(13):
        nested = [nested]
    config["payload"]["extra"] = nested
    with pytest.raises(ValidationError):
        validate_custom_config(config)
    config = default_custom_config()
    config["payload"]["extra"] = [0] * 501
    with pytest.raises(ValidationError):
        validate_custom_config(config)
    config = default_custom_config()
    config["payload"]["extra"] = "x" * 65536
    with pytest.raises(ValidationError):
        validate_custom_config(config)
    config = default_custom_config()
    config["payload"]["extra"] = []
    nested = config["payload"]["extra"]
    for _ in range(1100):
        child = []
        nested.append(child)
        nested = child
    with pytest.raises(ValidationError):
        validate_custom_config(config)
    with pytest.raises(ValidationError):
        validate_custom_config("\ud800")
    config = default_custom_config()
    config["payload"]["extra"] = [{"$value": "message.html", "$omit_if_empty": True}]
    with pytest.raises(ValidationError):
        validate_custom_config(config)


def test_missing_binding_and_nonempty_recipient_cannot_disappear():
    config = default_custom_config()
    del config["payload"]["attachments"]
    with pytest.raises(ValidationError):
        validate_custom_config(config)
    config = default_custom_config()
    del config["payload"]["bcc"]
    context = synthetic_message_context()
    context["message"]["bcc"] = [{"address": "blind@example.net", "name": "", "formatted": "blind@example.net"}]
    with pytest.raises(ValidationError):
        render_payload(config["payload"], context)


def test_each_template_must_preserve_its_own_fields():
    config = default_custom_config()
    config["payload"]["second_to"] = {"$each": "message.to", "$template": {"name": {"$value": "item.name"}}}
    with pytest.raises(ValidationError):
        validate_custom_config(config)
    config = default_custom_config()
    config["payload"]["second_attachment"] = {"$each": "message.attachments", "$template": {"name": {"$value": "item.filename"}}}
    with pytest.raises(ValidationError):
        validate_custom_config(config)


def test_literal_html_escaped_by_json_serializer_and_boolean_preserved():
    config = default_custom_config()
    config["payload"]["literal"] = "{{message.secret}}"
    config["payload"]["zero"] = 0
    config["payload"]["false"] = False
    context = synthetic_message_context()
    context["message"]["html"] = '<p title="x">\u00e9\n</p>'
    result = render_payload(config["payload"], context)
    assert result["literal"] == "{{message.secret}}"
    assert result["zero"] == 0 and result["false"] is False
    assert json.loads(json.dumps(result, ensure_ascii=False))["html"] == context["message"]["html"]
