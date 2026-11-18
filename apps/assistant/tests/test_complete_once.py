"""Saved-provider completions; every SDK request is synthetic."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from apps.assistant import llm, transport
from trellum.ai import ReportAIError

USAGE = {"input_tokens": 10, "output_tokens": 5,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
CONFIG = llm.LLMConfig("openai", api_key="synthetic-private-key", model="gpt-4o")


def test_completion_retry_and_safe_errors(monkeypatch, caplog):
    errors = [type("RateLimitError", (Exception,), {})(),
              type("OverloadedError", (Exception,), {})(),
              {"text": "ok", "finish_reason": "stop", "usage": USAGE}]
    request = Mock(side_effect=errors)
    monkeypatch.setattr(transport, "complete_request", request)
    monkeypatch.setattr(llm.time, "sleep", Mock())
    assert llm.complete_once(CONFIG, system="system", user="user").text == "ok"
    assert request.call_count == 3
    request.side_effect = type("AuthenticationError", (Exception,), {})("Authorization: synthetic-private-key")
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(CONFIG, system="system", user="user")
    assert caught.value.code == "provider"
    assert request.call_count == 4
    assert "synthetic-private-key" not in str(caught.value) + caplog.text


def test_retry_exhaustion(monkeypatch):
    request = Mock(side_effect=type("ProviderError", (Exception,), {"status_code": 500})())
    monkeypatch.setattr(transport, "complete_request", request)
    monkeypatch.setattr(llm.time, "sleep", Mock())
    with pytest.raises(ReportAIError, match="temporarily unavailable"):
        llm.complete_once(CONFIG, system="system", user="user")
    assert request.call_count == llm.RETRY_MAX_ATTEMPTS


@pytest.mark.parametrize("provider", ["openai", "litellm", "openrouter", "deepseek", "azure", "custom"])
def test_openai_protocol_native_json_and_saved_endpoint(monkeypatch, provider):
    client = Mock()
    client.chat.completions.create.return_value = NS(choices=[NS(message=NS(content='{"ok":true}', tool_calls=[]), finish_reason="stop")],
                                                        usage=NS(prompt_tokens=10, completion_tokens=5))
    constructor = Mock(return_value=client)
    monkeypatch.setattr(llm, "_openai_client", constructor)
    config = replace(CONFIG, provider=provider, base_url="https://synthetic.invalid/v1", model="saved-model")
    result = llm.complete_once(config, system="system", user="user", response_format="json", max_tokens=8000)
    assert result.model == "saved-model"
    assert constructor.call_args.args[0].base_url == "https://synthetic.invalid/v1"
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["stream"] is False and sent["timeout"] <= 90
    assert not sent.get("tools")
    client.close.assert_called_once()


@pytest.mark.parametrize("provider", ["anthropic", "bedrock", "vertex"])
@pytest.mark.parametrize("text,valid", [('{"ok":true}', True), ("not JSON", False)])
def test_prompt_json_cloud_providers(monkeypatch, provider, text, valid):
    from apps.assistant import cloud
    if provider == "anthropic":
        client = Mock()
        client.messages.create.return_value = NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=NS(input_tokens=10, output_tokens=5))
        monkeypatch.setattr(llm, "_anthropic_client", lambda config: client)
        config = replace(CONFIG, provider=provider)
        request = client.messages.create
    elif provider == "bedrock":
        client = Mock()
        client.converse.return_value = {"output": {"message": {"content": [{"text": text}]}}, "stopReason": "end_turn",
                                        "usage": {"inputTokens": 10, "outputTokens": 5}}
        monkeypatch.setattr(cloud, "_aws_client", lambda *args: client)
        config = replace(CONFIG, provider=provider, auth_mode="access_key")
        request = client.converse
    else:
        import sys
        types = NS(GenerateContentConfig=lambda **kwargs: kwargs)
        monkeypatch.setitem(sys.modules, "google.genai", NS(types=types))
        monkeypatch.setitem(sys.modules, "google.genai.types", types)
        client = Mock()
        client.models.generate_content.return_value = NS(candidates=[NS(content=NS(parts=[NS(text=text, thought=False, function_call=None)]), finish_reason="STOP")],
                                                          usage_metadata=NS(prompt_token_count=10, candidates_token_count=5))
        monkeypatch.setattr(cloud, "_vertex_client", lambda *args: client)
        config = replace(CONFIG, provider=provider, auth_mode="service_account")
        request = client.models.generate_content
    if valid:
        assert llm.complete_once(config, system="system", user="user", response_format="json").text == text
    else:
        with pytest.raises(ReportAIError) as caught:
            llm.complete_once(config, system="system", user="user", response_format="json")
        assert caught.value.code == "invalid_response"
    sent = request.call_args.kwargs
    assert "response_format" not in sent
    assert "Return only valid JSON" in str(sent)


def test_budgeted_missing_usage_does_not_invalidate_connection_check(monkeypatch):
    monkeypatch.setattr(transport, "complete_request", Mock(return_value={"text": "ok", "finish_reason": "stop", "usage": None}))
    invalidation = Mock()
    monkeypatch.setattr(llm, "_invalidate_metering", invalidation)
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(replace(CONFIG, budgeted=True), system="system", user="user")
    assert caught.value.code == "provider"
    invalidation.assert_not_called()


@pytest.mark.django_db
def test_report_readiness_ignores_connection_checks(org, make_assistant_config):
    make_assistant_config(org=org, provider="litellm", base_url="https://synthetic.invalid/v1", model="saved", api_key="synthetic",
                          price_in_per_mtok=Decimal("1"), price_out_per_mtok=Decimal("2"), connection_check={})
    assert llm.is_available(org, purpose="report") == (True, "")
    assert not llm.is_available(org, purpose="chat")[0]


@pytest.mark.parametrize("body", [{"error": {"type": "overloaded_error"}}, {"error": {"code": 529}}])
def test_normalize_body_only_overload(body):
    error = type("ProviderError", (Exception,), {"body": body})()
    assert transport.normalize_error(error).kind == "server"


@pytest.mark.django_db
def test_connection_probe_and_completion_resolve_same_saved_record(org, make_assistant_config, monkeypatch):
    cfg = make_assistant_config(org=org, provider="litellm", base_url="https://saved.invalid/v1", model="saved-model", auth_mode="none", api_key="")
    seen = []
    def probe(config, *args, **kwargs):
        seen.append(config)
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "response", "blocks": [{"type": "text", "text": "ok"}], "stop_reason": "stop", "usage": USAGE}
    monkeypatch.setattr(llm, "_adapter", lambda provider: probe)
    llm.check_connection(llm.LLMConfig.from_config(cfg))
    client = Mock()
    client.chat.completions.create.return_value = NS(choices=[NS(message=NS(content="ok", tool_calls=[]), finish_reason="stop")],
                                                        usage=NS(prompt_tokens=10, completion_tokens=5))
    def constructor(config):
        seen.append(config)
        return client
    monkeypatch.setattr(llm, "_openai_client", constructor)
    llm.complete_once(llm.LLMConfig.from_config(cfg), system="system", user="user")
    assert len(seen) >= 2
    assert all(config.base_url == "https://saved.invalid/v1" and config.model == "saved-model"
               and config.api_key == llm.GATEWAY_PLACEHOLDER_KEY and config.auth_mode == "none" for config in seen)


def test_model_override_is_local_to_call(monkeypatch):
    request = Mock(return_value={"text": "ok", "finish_reason": "stop", "usage": USAGE})
    monkeypatch.setattr(transport, "complete_request", request)
    assert llm.complete_once(CONFIG, system="system", user="user", model="gpt-4o-mini").model == "gpt-4o-mini"
    assert CONFIG.model == "gpt-4o"


def test_invalid_json_still_exposes_metered_spend_to_broker(monkeypatch):
    monkeypatch.setattr(transport, "complete_request", Mock(return_value={"text": "invalid", "finish_reason": "stop", "usage": USAGE}))
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(CONFIG, system="system", user="user", response_format="json")
    assert caught.value.code == "invalid_response" and caught.value.cost_usd > 0


@pytest.mark.parametrize("status", [401, 400, 404, 422])
def test_nonretryable_errors_make_one_attempt(monkeypatch, status):
    request = Mock(side_effect=type("ProviderError", (Exception,), {"status_code": status})("secret"))
    monkeypatch.setattr(transport, "complete_request", request)
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(CONFIG, system="system", user="user")
    assert caught.value.code == "provider" and request.call_count == 1
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("provider,saved,selected", [
    ("openai", "gpt-4o-mini", "gpt-4o"),
    ("anthropic", "claude-haiku-4-5-20251001", "claude-sonnet-4-6"),
])
@pytest.mark.parametrize("saved_price", [Decimal("0"), Decimal("0.15")])
def test_model_override_uses_its_own_native_prices(monkeypatch, provider, saved, selected, saved_price):
    usage = {**USAGE, "input_tokens": 1000, "output_tokens": 100}
    request = Mock(return_value={"text": "ok", "finish_reason": "stop", "usage": usage})
    monkeypatch.setattr(transport, "complete_request", request)
    config = llm.LLMConfig(provider, api_key="synthetic", model=saved,
                           price_in=saved_price, price_out=saved_price, budgeted=True)
    result = llm.complete_once(config, system="system", user="user", model=selected)
    rates = llm.LLMConfig(provider, model=selected).price()
    assert result.cost_usd == (1000 * rates["in"] + 100 * rates["out"]) / 1_000_000
    selected_config = request.call_args.args[0]
    assert selected_config.price_in is None and selected_config.price_out is None
    assert config.price_in == saved_price and config.model == saved


@pytest.mark.parametrize("model", [None, "saved-model"])
def test_same_model_keeps_saved_gateway_prices(monkeypatch, model):
    request = Mock(return_value={"text": "ok", "finish_reason": "stop", "usage": USAGE})
    monkeypatch.setattr(transport, "complete_request", request)
    config = llm.LLMConfig("litellm", api_key="synthetic", model="saved-model",
                           base_url="https://synthetic.invalid/v1", price_in=Decimal("1"),
                           price_out=Decimal("2"), budgeted=True)
    result = llm.complete_once(config, system="system", user="user", model=model)
    assert result.cost_usd == 0.00002
    assert request.call_args.args[0] is config


@pytest.mark.parametrize("provider,endpoint,model", [
    ("openai", "", "unknown-model"),
    ("anthropic", "", "unknown-model"),
    ("openai", "https://synthetic.invalid/v1", "gpt-4o"),
    ("anthropic", "https://synthetic.invalid", "claude-sonnet-4-6"),
    *[(provider, "https://synthetic.invalid/v1", "gpt-4o")
      for provider in ("litellm", "openrouter", "deepseek", "azure", "custom", "bedrock", "vertex")],
])
def test_unpriced_override_is_blocked_before_provider(monkeypatch, provider, endpoint, model):
    request = Mock()
    monkeypatch.setattr(transport, "complete_request", request)
    config = llm.LLMConfig(provider, api_key="synthetic", model="saved-model", base_url=endpoint,
                           price_in=Decimal("0"), price_out=Decimal("0"), budgeted=True,
                           auth_mode={"bedrock": "access_key", "vertex": "service_account"}.get(provider, "api_key"))
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(config, system="system", user="user", model=model)
    assert caught.value.code == "budget"
    request.assert_not_called()


def test_uncapped_unpriced_override_does_not_borrow_saved_rates(monkeypatch):
    request = Mock(return_value={"text": "ok", "finish_reason": "stop", "usage": USAGE})
    monkeypatch.setattr(transport, "complete_request", request)
    config = replace(CONFIG, price_in=Decimal("1"), price_out=Decimal("2"))
    result = llm.complete_once(config, system="system", user="user", model="unknown-model")
    assert result.cost_usd is None and result.usage == USAGE


@pytest.mark.django_db
def test_report_readiness_prices_the_selected_model(org, make_assistant_config):
    cfg = make_assistant_config(org=org, provider="openai", model="unknown-model", api_key="synthetic",
                                monthly_budget_usd=Decimal("10"), price_in_per_mtok=None, price_out_per_mtok=None)
    assert not llm.is_available(org, purpose="report")[0]
    assert llm.is_available(org, purpose="report", model="gpt-4o") == (True, "")
    cfg.price_in_per_mtok = cfg.price_out_per_mtok = Decimal("0")
    cfg.save()
    assert llm.is_available(org, purpose="report") == (True, "")
    assert not llm.is_available(org, purpose="report", model="another-unknown-model")[0]


def _stub_completion(monkeypatch, provider, response):
    from apps.assistant import cloud

    client = Mock()
    config = llm.LLMConfig(provider, api_key="synthetic", model="saved-model", budgeted=True,
                           price_in=Decimal("1"), price_out=Decimal("2"),
                           auth_mode={"bedrock": "access_key", "vertex": "service_account"}.get(provider, "api_key"))
    if provider == "openai":
        monkeypatch.setattr(llm, "_openai_client", lambda *args, **kwargs: client)
        request = client.chat.completions.create
    elif provider == "anthropic":
        monkeypatch.setattr(llm, "_anthropic_client", lambda *args: client)
        request = client.messages.create
    elif provider == "bedrock":
        monkeypatch.setattr(cloud, "_aws_client", lambda *args: client)
        request = client.converse
    else:
        monkeypatch.setattr(cloud, "_vertex_client", lambda *args: client)
        request = client.models.generate_content
    request.return_value = response
    return config, request


@pytest.mark.parametrize("provider,response", [
    ("openai", NS(choices=[], usage=NS(prompt_tokens=1000, completion_tokens=0))),
    ("openai", NS(choices=[NS(message=NS(tool_calls=[NS(name="unexpected")]))],
                   usage=NS(prompt_tokens=1000, completion_tokens=0))),
    ("anthropic", NS(content=[NS(type="tool_use")], usage=NS(input_tokens=1000, output_tokens=0))),
    ("anthropic", NS(content=[NS(type="text", text=None)], usage=NS(input_tokens=1000, output_tokens=0))),
    ("bedrock", {"output": {"message": {"content": [{"toolUse": {}}]}},
                 "usage": {"inputTokens": 1000, "outputTokens": 0}}),
    ("bedrock", {"output": {"message": {"content": []}},
                 "usage": {"inputTokens": 1000, "outputTokens": 0}}),
    ("vertex", NS(candidates=[], usage_metadata=NS(prompt_token_count=1000, candidates_token_count=0))),
    ("vertex", NS(candidates=[NS(content=NS(parts=[NS(function_call=NS(name="unexpected"))]))],
                   usage_metadata=NS(prompt_token_count=1000, candidates_token_count=0))),
])
def test_rejected_completion_preserves_metered_usage_once(monkeypatch, provider, response):
    config, request = _stub_completion(monkeypatch, provider, response)
    cost = Mock(wraps=llm._normalized_cost)
    monkeypatch.setattr(llm, "_normalized_cost", cost)
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(config, system="system", user="user")
    assert caught.value.code == "invalid_response"
    assert caught.value.usage == {**USAGE, "input_tokens": 1000, "output_tokens": 0}
    assert caught.value.cost_usd == 0.001
    request.assert_called_once()
    cost.assert_called_once()


def test_vertex_blocked_prompt_with_omitted_output_count_preserves_usage(monkeypatch):
    from google.genai import types

    response = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY"),
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=1000, total_token_count=1000))
    config, request = _stub_completion(monkeypatch, "vertex", response)
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(config, system="system", user="user")
    assert caught.value.code == "invalid_response" and caught.value.cost_usd == 0.001
    assert caught.value.usage["output_tokens"] == 0
    request.assert_called_once()


@pytest.mark.parametrize("provider,response", [
    ("openai", NS(choices=[], usage=None)),
    ("openai", NS(choices=[], usage=NS(prompt_tokens=True, completion_tokens=0))),
    ("openai", NS(choices=[], usage=NS(prompt_tokens=1000, completion_tokens=0,
                   prompt_tokens_details=NS(cached_tokens=False)))),
    ("anthropic", NS(content=[NS(type="tool_use")], usage=NS(input_tokens=-1, output_tokens=0))),
    ("bedrock", {"output": {"message": {"content": [{"toolUse": {}}]}},
                 "usage": {"inputTokens": 1000, "outputTokens": "0"}}),
    ("vertex", NS(candidates=[], usage_metadata=NS(prompt_token_count=True, candidates_token_count=0))),
    ("vertex", NS(candidates=[], usage_metadata=NS(prompt_token_count=1000, candidates_token_count=-1))),
    ("vertex", NS(candidates=[NS(content=NS(parts=[NS(function_call=NS(name="unexpected"))]))],
                   usage_metadata=NS(prompt_token_count=1000))),
])
def test_rejected_completion_never_guesses_missing_or_invalid_usage(monkeypatch, provider, response):
    config, request = _stub_completion(monkeypatch, provider, response)
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(config, system="system", user="user")
    assert caught.value.code == "provider"
    assert getattr(caught.value, "cost_usd", None) is None
    request.assert_called_once()


def _cloud_error(provider, status):
    if provider == "bedrock":
        from botocore.exceptions import ClientError
        return ClientError({"Error": {"Code": "UnknownServiceException", "Message": "synthetic-private-key"},
                            "ResponseMetadata": {"HTTPStatusCode": status}}, "Converse")
    from google.genai.errors import APIError
    return APIError(status, {"error": {"code": status, "message": "synthetic-private-key"}})


def _cloud_response(provider):
    if provider == "bedrock":
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "stopReason": "end_turn",
                "usage": {"inputTokens": 1000, "outputTokens": 0}}
    return NS(candidates=[NS(content=NS(parts=[NS(text="ok", thought=False, function_call=None)]), finish_reason="STOP")],
              usage_metadata=NS(prompt_token_count=1000, candidates_token_count=0))


@pytest.mark.parametrize("provider", ["bedrock", "vertex"])
@pytest.mark.parametrize("status", [429, 500, 503, 529])
def test_cloud_http_overload_retries_with_shared_policy(monkeypatch, provider, status):
    response = _cloud_response(provider)
    config, request = _stub_completion(monkeypatch, provider, response)
    request.side_effect = [_cloud_error(provider, status), response]
    sleep = Mock()
    monkeypatch.setattr(llm.time, "sleep", sleep)
    result = llm.complete_once(config, system="system", user="user")
    assert result.text == "ok" and result.cost_usd == 0.001
    assert request.call_count == 2
    sleep.assert_called_once_with(llm.RETRY_BASE_DELAY)


@pytest.mark.parametrize("code", ["overloaded_error", "529", "Overload"])
def test_bedrock_body_only_overload_retries(monkeypatch, code):
    from botocore.exceptions import ClientError

    response = _cloud_response("bedrock")
    config, request = _stub_completion(monkeypatch, "bedrock", response)
    request.side_effect = [ClientError({"Error": {"Code": code, "Message": "synthetic-private-key"}}, "Converse"), response]
    monkeypatch.setattr(llm.time, "sleep", Mock())
    assert llm.complete_once(config, system="system", user="user").text == "ok"
    assert request.call_count == 2


@pytest.mark.parametrize("provider", ["bedrock", "vertex"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_cloud_nonretryable_http_errors_remain_single_attempt(monkeypatch, caplog, provider, status):
    config, request = _stub_completion(monkeypatch, provider, None)
    request.side_effect = _cloud_error(provider, status)
    with pytest.raises(ReportAIError) as caught:
        llm.complete_once(config, system="system", user="user")
    assert caught.value.code == "provider"
    assert "synthetic-private-key" not in str(caught.value) + caplog.text
    request.assert_called_once()


@pytest.mark.parametrize("code", ["AccessDeniedException", "ValidationException", "ResourceNotFoundException", "ModelNotFoundException"])
def test_cloud_service_errors_remain_nonretryable_even_with_server_status(monkeypatch, code):
    from botocore.exceptions import ClientError

    config, request = _stub_completion(monkeypatch, "bedrock", None)
    request.side_effect = ClientError({"Error": {"Code": code, "Message": "synthetic-private-key"},
                                       "ResponseMetadata": {"HTTPStatusCode": 529}}, "Converse")
    with pytest.raises(ReportAIError):
        llm.complete_once(config, system="system", user="user")
    request.assert_called_once()
