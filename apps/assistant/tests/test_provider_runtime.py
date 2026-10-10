"""Synthetic runtime checks, including the actual SDK SSE parser."""
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import openai
import pytest

from apps.assistant import llm
from apps.assistant.transport import TransportError, stream_openai

USAGE = {"input_tokens": 10, "output_tokens": 5,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
SCHEMA = {"name": "echo", "description": "Synthetic echo", "input_schema": {
    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}
CONFIG = llm.LLMConfig("openai", api_key="synthetic", model="gpt-4o")


class Toolbox:
    schemas = [SCHEMA]
    actor = None

    def __init__(self):
        self.executed = []

    def authorize(self, args):
        return None

    def execute(self, name, args):
        self.executed.append((name, args))
        return "synthetic result"

    def frame(self, name, args, result):
        return result


def response(blocks=None, usage=USAGE, private=None):
    return {"type": "response", "blocks": blocks if blocks is not None else [{"type": "text", "text": "OK"}],
            "stop_reason": "stop", "usage": usage, "provider_data": private}


def tool(args=None, name="echo", id="tool-1"):
    return {"type": "tool_use", "name": name, "id": id, "input": args if args is not None else {"value": "probe"}}


def run(monkeypatch, responses, *, config=CONFIG, state=None, toolbox=None, **kwargs):
    attempts = iter(responses)
    def adapter(*args, **kw):
        yield from next(attempts)
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    state, toolbox = state if state is not None else {}, toolbox or Toolbox()
    events = list(llm.stream_turn(config, state, "synthetic", toolbox=toolbox, system_blocks=[], **kwargs))
    return events, state, toolbox


@pytest.mark.parametrize("bad", [tool("{broken"), tool([]), tool({}), tool({"value": 5}),
                                  tool(name="unknown"), tool(id=""), tool({"value": True})])
def test_malformed_calls_are_metered_but_never_executed(monkeypatch, bad):
    events, state, toolbox = run(monkeypatch, [[response([bad])]])
    assert [event["type"] for event in events] == ["usage", "error"]
    assert events[0]["cost_delta_usd"] > 0
    assert toolbox.executed == []
    assert len(state["transcript"]) == 1


def test_duplicate_tool_ids_rejected_before_any_execution(monkeypatch):
    events, _, toolbox = run(monkeypatch, [[response([tool(), tool()])]])
    assert events[-1]["type"] == "error" and not toolbox.executed


def test_multiple_calls_roundtrip_and_private_continuation(monkeypatch):
    messages = []
    turns = iter([response([tool(), tool(id="tool-2")], private={"reasoning_content": "private reasoning"}), response()])
    def adapter(config, history, *args, **kw):
        messages.append(history)
        yield next(turns)
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    toolbox, state = Toolbox(), {}
    events = list(llm.stream_turn(CONFIG, state, "synthetic", toolbox=toolbox, system_blocks=[]))
    assert len(toolbox.executed) == 2 and events[-1]["type"] == "done"
    assert messages[1][1]["_provider"]["reasoning_content"] == "private reasoning"
    assert "private reasoning" not in json.dumps(state["transcript"])
    assert "private reasoning" not in json.dumps(events)
    events = list(llm.stream_turn(replace(CONFIG, model="different"), state, "next", toolbox=toolbox, system_blocks=[]))
    assert events[0]["code"] == "new_conversation"
    assert len(state["transcript"]) == 5


def test_missing_usage_stays_unknown_and_stops_capped_tool_execution(monkeypatch):
    events, state, toolbox = run(monkeypatch, [[response([tool()], usage=None)]], config=replace(CONFIG, budgeted=True))
    assert events[0]["cost_delta_usd"] is None and state["usage"]["cost_usd"] is None
    assert events[-1]["code"] == "metering" and not toolbox.executed
    events = list(llm.stream_turn(replace(CONFIG, budgeted=True), state, "next", toolbox=toolbox, system_blocks=[]))
    assert events[0]["code"] == "new_conversation"


@pytest.mark.django_db
@pytest.mark.parametrize("kind,progress", [("connection", True), ("server", True), ("connection", False)])
def test_interrupted_nonvisible_inference_blocks_later_capped_calls(org, viewer, make_assistant_config, monkeypatch, kind, progress):
    config = llm.LLMConfig.from_config(make_assistant_config(org, monthly_budget_usd=20))
    calls, state, toolbox = [], {}, Toolbox()
    toolbox.actor = viewer
    def adapter(*args, **kwargs):
        calls.append(1)
        if progress:
            yield {"type": "keepalive"}
        raise TransportError(kind, "Synthetic interrupted inference")
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: pytest.fail("must not retry an unmetered capped request"))
    events = list(llm.stream_turn(config, state, "synthetic", toolbox=toolbox, system_blocks=[]))
    assert events[-1]["type"] == "error" and state["usage"]["cost_usd"] is None
    assert not llm.is_available(org)[0] and len(calls) == 1
    events = list(llm.stream_turn(config, state, "next", toolbox=toolbox, system_blocks=[]))
    assert events[-1]["code"] == "budget" and len(calls) == 1


@pytest.mark.django_db
def test_successful_metering_recheck_cannot_replay_unfinished_calls(org, make_assistant_config, monkeypatch):
    cfg = make_assistant_config(org, monthly_budget_usd=20)
    config = llm.LLMConfig.from_config(cfg)
    _, state, toolbox = run(monkeypatch, [[response([tool()], usage=None)]], config=config)
    cfg.connection_check = {"revision": cfg.config_revision, "checks": {"chat": {"ok": True}, "usage": {"ok": True}}}
    cfg.save()
    assert llm.is_available(org)[0]
    events = list(llm.stream_turn(llm.LLMConfig.from_config(cfg), state, "next", toolbox=toolbox, system_blocks=[]))
    assert events[0]["code"] == "new_conversation" and not toolbox.executed


def test_forced_tool_failure_is_metered(monkeypatch):
    events, _, toolbox = run(monkeypatch, [[response()]], force_tool="echo")
    assert [event["type"] for event in events] == ["usage", "error"]
    assert events[0]["cost_delta_usd"] > 0 and not toolbox.executed


@pytest.mark.parametrize("retry_kind", ["rate_limit", "server"])
def test_pre_output_retry_and_midstream_no_retry(monkeypatch, retry_kind):
    calls = []
    def adapter(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TransportError(retry_kind, "Synthetic transient failure")
        yield {"type": "text_delta", "text": "visible"}
        raise RuntimeError("secret-credential")
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    events = list(llm.stream_turn(CONFIG, {}, "synthetic", toolbox=Toolbox(), system_blocks=[]))
    assert len(calls) == 2
    assert [event["type"] for event in events] == ["rate_limit", "text_delta", "error"]
    assert events[-1]["code"] == "cut_off" and "secret-credential" not in json.dumps(events)


def test_retry_never_sleeps_past_deadline(monkeypatch):
    def adapter(*args, **kwargs):
        raise TransportError("server", "Synthetic failure")
        yield
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: pytest.fail("must not sleep"))
    events = list(llm.stream_turn(CONFIG, {}, "synthetic", toolbox=Toolbox(), system_blocks=[], remaining=lambda: 0.5))
    assert events[-1]["code"] == "deadline"


def test_reasoning_only_progress_enforces_deadline(monkeypatch):
    expired = False
    def adapter(*args, **kwargs):
        nonlocal expired
        expired = True
        yield {"type": "keepalive"}
        pytest.fail("deadline must stop this stream")
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    state = {}
    events = list(llm.stream_turn(CONFIG, state, "synthetic", toolbox=Toolbox(), system_blocks=[], remaining=lambda: -1 if expired else 10))
    assert events[-1]["code"] == "deadline"
    assert state["usage"]["cost_usd"] is None


@pytest.mark.parametrize("provider", ["custom", "litellm", "openrouter", "deepseek", "azure", "bedrock", "vertex"])
def test_native_model_names_never_choose_other_provider_prices(provider):
    assert llm.LLMConfig(provider, model="gpt-4o").price() is None
    assert llm.LLMConfig(provider, model="claude-sonnet-4-6").price() is None


def test_longest_model_prefix_and_custom_native_endpoint_prices():
    assert llm.LLMConfig("openai", model="gpt-4o-mini-2024-07-18").price()["in"] == 0.15
    assert replace(CONFIG, custom_endpoint=True).price() is None


def test_actual_sdk_fragmented_tools_reasoning_usage_and_replay(monkeypatch):
    requests, closed = [], []
    def chunk(delta=None, finish=None, usage=None):
        return {"id": "chat-synthetic", "object": "chat.completion.chunk", "created": 1, "model": "synthetic",
                "choices": [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}], "usage": usage}
    first = [
        chunk({"role": "assistant", "reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "private ", "signature": "sig", "id": "r1", "format": "anthropic-claude-v1"}],
               "tool_calls": [{"index": 0, "id": "tool-1", "type": "function", "function": {"name": "echo", "arguments": '{"val'}}]}),
        chunk({"reasoning_details": [{"index": 0, "text": "continuation"}],
               "tool_calls": [{"index": 0, "function": {"arguments": 'ue":"probe"}'}}]}),
        chunk(finish="tool_calls"),
        chunk(usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "prompt_tokens_details": {"cached_tokens": 2}}),
    ]
    second = [chunk({"content": "OK"}), chunk(finish="stop"),
              chunk(usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})]
    sequences = iter([first, second])
    def handler(request):
        requests.append(json.loads(request.content))
        content = "".join("data: " + json.dumps(part) + "\n\n" for part in next(sequences)) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=content)
    def client(config):
        sdk = openai.OpenAI(api_key="synthetic", base_url="https://synthetic.invalid/v1", max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        closed.append(sdk)
        return sdk
    monkeypatch.setattr(llm, "_openai_client", client)
    toolbox, state = Toolbox(), {}
    config = replace(CONFIG, provider="openrouter", model="synthetic")
    events = list(llm.stream_turn(config, state, "synthetic", toolbox=toolbox, system_blocks=[]))
    assert events[-1]["type"] == "done" and toolbox.executed == [("echo", {"value": "probe"})]
    replayed = requests[1]["messages"][2]
    assert replayed["reasoning_details"][0]["text"] == "private continuation"
    assert replayed["reasoning_details"][0]["signature"] == "sig"
    assert "private continuation" not in json.dumps(events + state["transcript"])
    assert state["usage"]["input_tokens"] == 15 and state["usage"]["cache_read_input_tokens"] == 2
    assert state["usage"]["output_tokens"] == 10
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert all(client.is_closed() for client in closed)


def test_connection_checks_are_independent_and_all_probe_usage_counts(monkeypatch):
    captured = []
    turns = iter([response([tool(name="connection_probe")], usage=None),
                  {"type": "text_delta", "text": "OK"}, response(), response([tool(name="connection_probe")])])
    def adapter(config, messages, systems, schemas, **kw):
        captured.append(kw)
        item = next(turns)
        yield item
        if item["type"] == "text_delta":
            yield next(turns)
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    result = llm.check_connection(CONFIG)
    assert result["ok"] and result["checks"]["chat"]["ok"] and result["checks"]["alerts"]["ok"]
    assert not result["checks"]["usage"]["ok"]
    assert captured[2]["force_tool"] == "connection_probe" and captured[2]["purpose"] == "alert"


def test_connection_can_be_chat_ready_with_alert_failure(monkeypatch):
    turns = iter([[response([tool(name="connection_probe")])], [{"type": "text_delta", "text": "OK"}, response()], [response()]])
    monkeypatch.setattr(llm, "_adapter", lambda provider: lambda *a, **kw: iter(next(turns)))
    result = llm.check_connection(CONFIG)
    assert result["ok"] and not result["checks"]["alerts"]["ok"]
    assert result["checks"]["usage"]["ok"]


def test_interrupted_second_probe_cannot_hide_unknown_usage(monkeypatch):
    calls = []
    def adapter(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            yield response([tool(name="connection_probe")])
        elif len(calls) == 2:
            yield {"type": "text_delta", "text": "partial"}
            raise RuntimeError("private payload")
        else:
            yield response([tool(name="connection_probe")])
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    result = llm.check_connection(CONFIG)
    assert not result["checks"]["usage"]["ok"] and not result["checks"]["chat"]["ok"]
    assert not result["checks"]["alerts"]["ok"]


@pytest.mark.parametrize("changes", [{"cloud_config": {"region": "other"}}, {"org_id": "other"},
                                     {"config_revision": 2}, {"cloud_credentials": {"access_key_id": "other"}},
                                     {"base_url": "https://other.invalid/v1"}])
def test_private_continuation_bound_to_connection_identity(monkeypatch, changes):
    events, state, toolbox = run(monkeypatch, [[response(private={"reasoning_content": "private"})]])
    assert events[-1]["type"] == "done"
    events = list(llm.stream_turn(replace(CONFIG, **changes), state, "next", toolbox=toolbox, system_blocks=[]))
    assert events[0]["code"] == "new_conversation"
    assert "synthetic" not in state["_provider"]["identity"]


def test_each_page_context_is_replayed_consistently_without_transcript_changes(monkeypatch):
    histories = []
    def adapter(config, messages, *a, **kw):
        histories.append(messages)
        yield response(private={"reasoning_content": "private"})
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    state = {}
    for page in ("first", "second"):
        list(llm.stream_turn(CONFIG, state, "synthetic", toolbox=Toolbox(), system_blocks=[], page_context=page))
    assert histories[0][0]["content"] == histories[1][0]["content"]
    assert "first" in histories[0][0]["content"] and "second" in histories[1][2]["content"]
    assert state["transcript"][0]["content"] == state["transcript"][2]["content"] == "synthetic"


@pytest.mark.parametrize("provider", ["openai", "azure", "deepseek", "custom"])
def test_completion_token_option_and_deepseek_alert_non_thinking(monkeypatch, provider):
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"), finish_reason="stop")], usage=None)])
    monkeypatch.setattr(llm, "_openai_client", lambda config, **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    config = replace(CONFIG, provider=provider, custom_endpoint=provider in ("azure", "custom"))
    list(stream_openai(config, [], [], [], max_tokens=2048, purpose="alert"))
    key = "max_completion_tokens" if provider in ("openai", "azure") else "max_tokens"
    assert captured[key] == 2048
    if provider == "deepseek":
        assert captured["extra_body"]["thinking"] == {"type": "disabled"}


def test_discovery_uses_saved_client_and_closes_it(monkeypatch):
    closed, calls = [], []
    def listing(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(id="synthetic-model")]
    client = SimpleNamespace(models=SimpleNamespace(list=listing), close=lambda: closed.append(True))
    monkeypatch.setattr(llm, "_openai_client", lambda config: client)
    assert llm.list_models(CONFIG) == [{"id": "synthetic-model", "label": "synthetic-model"}]
    assert calls == [{"timeout": 10.0}] and closed == [True]
    with pytest.raises(TransportError, match="deployment name manually"):
        llm.list_models(replace(CONFIG, provider="azure"))


def test_missing_direct_credentials_never_use_ambient_keys(monkeypatch):
    with pytest.raises(TransportError, match="Add an API key"):
        llm._openai_client(replace(CONFIG, api_key=""))
    with pytest.raises(TransportError, match="explicit gateway"):
        llm._openai_client(replace(CONFIG, auth_mode="none", api_key="gateway-no-key", base_url="https://api.openai.com/v1"))


@pytest.mark.parametrize("provider", ["litellm", "custom"])
def test_missing_gateway_endpoint_never_constructs_an_sdk_client(monkeypatch, provider):
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: pytest.fail("must not forward credentials to an SDK default endpoint"))
    config = llm.LLMConfig(provider, api_key="synthetic-private-key", model="synthetic")
    with pytest.raises(TransportError, match="explicit endpoint"):
        llm._openai_client(config)
    with pytest.raises(TransportError, match="explicit endpoint"):
        llm.list_models(config)


@pytest.mark.parametrize("provider,url", [("openrouter", "https://openrouter.ai/api/v1"), ("deepseek", "https://api.deepseek.com")])
def test_direct_facade_uses_the_selected_provider_preset(monkeypatch, provider, url):
    captured = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: captured.update(kwargs))
    config = llm.LLMConfig(provider, api_key="synthetic", model="synthetic")
    llm._openai_client(config)
    assert captured["base_url"] == url and config.base_url == url
    assert llm.mismatch_hints(config) == []


def test_cache_breakpoint_never_decorates_signed_reasoning():
    reasoning = {"type": "thinking", "thinking": "private", "signature": "sig"}
    messages = [{"role": "assistant", "content": [reasoning]}]
    assert llm._with_cache_breakpoint(messages) == messages
    messages[0]["content"].insert(0, {"type": "text", "text": "visible"})
    cached = llm._with_cache_breakpoint(messages)
    assert "cache_control" in cached[0]["content"][0]
    assert "cache_control" not in cached[0]["content"][1]


@pytest.mark.django_db
def test_private_state_never_enters_session_browser_payload(org, viewer):
    from apps.assistant.models import AssistantSession

    session = AssistantSession(user=viewer, org=org, state={"transcript": [], "usage": {},
                              "_provider": {"identity": "fingerprint", "messages": {"1": {"reasoning": "private"}}}})
    assert "private" not in json.dumps(session.to_full())


def test_signed_anthropic_blocks_are_private_and_replayed(monkeypatch):
    from apps.assistant.transport import stream_anthropic
    from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, ToolUseBlock

    class Thinking:
        def model_dump(self):
            return {"type": "thinking", "thinking": "private reasoning", "signature": "signature"}

    calls = []
    native = FakeResponse([Thinking(), ToolUseBlock("tool-1", "echo", {"value": "probe"})], "tool_use")
    client = FakeClient([native, native], calls)
    monkeypatch.setattr(llm, "_anthropic_client", lambda config: client)
    config = replace(CONFIG, provider="anthropic", model="claude-sonnet-4-6")
    first = list(stream_anthropic(config, [{"role": "user", "content": "synthetic"}], [], [SCHEMA], max_tokens=4096))[-1]
    assert "private reasoning" not in json.dumps(first["blocks"])
    messages = [{"role": "assistant", "content": first["blocks"], "_provider": first["provider_data"]},
                {"role": "tool", "tool_use_id": "tool-1", "result": "synthetic result"}]
    list(stream_anthropic(config, messages, [], [SCHEMA], max_tokens=4096))
    assert calls[1]["messages"][0]["content"][0] == {"type": "thinking", "thinking": "private reasoning", "signature": "signature"}


@pytest.mark.django_db
def test_azure_from_saved_config_uses_completion_token_limit(org, make_assistant_config, monkeypatch):
    cfg = make_assistant_config(org, provider="azure", base_url="https://synthetic.openai.azure.com/openai/v1", model="deployment")
    config = llm.LLMConfig.from_config(cfg)
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"), finish_reason="stop")], usage=None)])
    monkeypatch.setattr(llm, "_openai_client", lambda config, **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    list(stream_openai(config, [], [], [], max_tokens=2048))
    assert captured["max_completion_tokens"] == 2048 and "max_tokens" not in captured


def test_provider_error_never_logs_or_returns_raw_data(caplog):
    error = RuntimeError("unusual-secret-token request private reasoning")
    event = llm._provider_error(error)
    assert "unusual-secret-token" not in json.dumps(event) + caplog.text


@pytest.mark.django_db
def test_missing_meter_blocks_later_capped_calls_and_revision_race(org, make_assistant_config, monkeypatch):
    cfg = make_assistant_config(org, monthly_budget_usd=20)
    config = llm.LLMConfig.from_config(cfg)
    run(monkeypatch, [[response(usage=None)]], config=config)
    cfg.refresh_from_db()
    assert cfg.connection_check["checks"]["usage"]["ok"] is False
    assert not llm.is_available(org)[0]
    cfg.config_revision += 1
    cfg.connection_check = {}
    cfg.save()
    llm._invalidate_metering(config)
    cfg.refresh_from_db()
    assert cfg.connection_check == {}


@pytest.mark.django_db
def test_chat_and_alert_readiness_are_separate(org, make_assistant_config):
    cfg = make_assistant_config(org, provider="custom", base_url="https://synthetic.invalid/v1", model="synthetic")
    assert not llm.is_available(org)[0]
    cfg.connection_check = {"revision": cfg.config_revision, "checks": {
        "chat": {"ok": True, "message": "OK"}, "alerts": {"ok": False, "message": "Forced tool unsupported"}}}
    cfg.save()
    assert llm.is_available(org) == (True, "")
    assert llm.is_available(org, purpose="alert") == (False, "Forced tool unsupported")
    cfg.connection_check["checks"] = {"chat": {"ok": False, "message": "Tool roundtrip failed"},
                                         "alerts": {"ok": True, "message": "Forced tool succeeded"}}
    cfg.save()
    assert llm.is_available(org, purpose="alert") == (False, "Tool roundtrip failed")
