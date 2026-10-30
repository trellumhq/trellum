"""Required alert calls on models that reject forced tool choice."""
from types import SimpleNamespace

import pytest

from apps.assistant import llm
from apps.assistant.transport import stream_anthropic
from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, TextBlock, ToolUseBlock
from apps.assistant.tests.test_provider_runtime import SCHEMA, Toolbox


@pytest.mark.parametrize("model,auto", [
    ("claude-sonnet-5-5", True), ("claude-sonnet-5-5-20261001", True),
    ("claude-opus-5-5", True), ("claude-fable-5-1", True),
    ("claude-mythos-5-1", True), ("claude-sonnet-4-6", False),
    ("claude-haiku-5-5", False), ("claude-opus-5", False),
])
def test_required_choice_matches_model_capability(monkeypatch, model, auto):
    calls = []
    client = FakeClient([FakeResponse([ToolUseBlock("t1", "echo", {"value": "probe"})])], calls)
    monkeypatch.setattr(llm, "_anthropic_client", lambda config: client)
    config = llm.LLMConfig("anthropic", api_key="synthetic", model=model)
    system = [{"type": "text", "text": "Original instructions"}]
    list(stream_anthropic(config, [{"role": "user", "content": "Call echo"}], system,
                          [SCHEMA], max_tokens=4096, force_tool="echo", purpose="alert"))
    assert calls[0]["tool_choice"] == ({"type": "auto"} if auto else {"type": "tool", "name": "echo"})
    if auto:
        assert "must call the echo tool" in calls[0]["system"][-1]["text"]
    assert system == [{"type": "text", "text": "Original instructions"}]


@pytest.mark.parametrize("decision", ["valid", "missing", "invalid"])
def test_connection_probe_uses_supported_choice_and_validates_call(monkeypatch, decision):
    calls = []
    probe = ToolUseBlock("t1", "connection_probe", {"value": "probe"})
    last = {"valid": probe, "missing": TextBlock("OK"),
            "invalid": ToolUseBlock("t2", "connection_probe", {"value": "wrong"})}[decision]
    fake = FakeClient([FakeResponse([probe], "tool_use"), FakeResponse([TextBlock("OK")]),
                       FakeResponse([last])], calls)
    def stream(**kwargs):
        # Emulate the provider's documented rejection; the old adapter fails here.
        assert kwargs.get("tool_choice", {}).get("type") not in ("tool", "any")
        return fake.messages.stream(**kwargs)
    monkeypatch.setattr(llm, "_anthropic_client", lambda config: SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    result = llm.check_connection(llm.LLMConfig("anthropic", api_key="synthetic", model="claude-sonnet-5-5"))
    assert result["checks"]["chat"]["ok"]
    assert result["checks"]["usage"]["ok"]
    assert result["checks"]["alerts"]["ok"] == (decision == "valid")
    assert len(calls) == 3
    assert "tool_choice" not in calls[0] and "tool_choice" not in calls[1]
    assert calls[2]["tool_choice"] == {"type": "auto"}


@pytest.mark.parametrize("block", [TextBlock("Decision complete"), ToolUseBlock("t1", "echo", {})])
def test_required_runtime_call_never_executes_missing_or_invalid_tool(monkeypatch, block):
    calls = []
    client = FakeClient([FakeResponse([block])], calls)
    monkeypatch.setattr(llm, "_anthropic_client", lambda config: client)
    toolbox = Toolbox()
    config = llm.LLMConfig("anthropic", api_key="synthetic", model="claude-sonnet-5-5")
    events = list(llm.stream_turn(config, {}, "Call echo", toolbox=toolbox, system_blocks=[],
                                  force_tool="echo", purpose="alert"))
    assert events[-1]["type"] == "error"
    assert toolbox.executed == []
    assert len(calls) == 1
