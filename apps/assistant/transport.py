"""Wire adapters. Only the common runtime may execute model tools."""
from __future__ import annotations

import json
import re
from contextlib import contextmanager


@contextmanager
def closing_client(client):
    try:
        yield client
    finally:
        if callable(getattr(client, "close", None)):
            client.close()


class TransportError(Exception):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


def normalize_error(error):
    if isinstance(error, TransportError):
        return error
    names = {cls.__name__ for cls in type(error).__mro__}
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    response = getattr(error, "response", None)
    if status is None and isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    overload = any(name.lower() in ("overloadederror", "overloaderror", "overloaded_error") for name in names)
    if isinstance(body, dict):
        detail = body.get("error", body)
        overload = overload or (isinstance(detail, dict) and (
            detail.get("type") == "overloaded_error" or detail.get("code") in (529, "529", "overloaded_error")
            or detail.get("status_code") in (529, "529") or detail.get("status") in (529, "529")))
    if overload or status in (529, "529"):
        return TransportError("server", "The provider is temporarily unavailable.")
    if status in (401, 403) or "AuthenticationError" in names:
        return TransportError("authentication", "The provider rejected the credentials.")
    if status == 429 or "RateLimitError" in names:
        return TransportError("rate_limit", "The provider is rate limiting requests.")
    if isinstance(status, int) and 500 <= status < 600:
        return TransportError("server", "The provider is temporarily unavailable.")
    if names & {"APIConnectionError", "APITimeoutError", "TimeoutError", "ConnectionError"}:
        return TransportError("connection", "Could not reach the model provider.")
    if status in (400, 404, 422):
        return TransportError("unsupported", "The endpoint or model does not support this request.")
    return TransportError("protocol", "The provider returned an invalid response.")


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _usage(raw, *, openai=False):
    if raw is None:
        return None
    if openai:
        prompt = _value(raw, "prompt_tokens")
        output = _value(raw, "completion_tokens")
        cached = _value(_value(raw, "prompt_tokens_details"), "cached_tokens", 0) or 0
        inputs, creation = prompt - cached if isinstance(prompt, int) else None, 0
    else:
        inputs, output = _value(raw, "input_tokens"), _value(raw, "output_tokens")
        cached = _value(raw, "cache_read_input_tokens", 0) or 0
        creation = _value(raw, "cache_creation_input_tokens", 0) or 0
    counts = (inputs, output, cached, creation)
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in counts):
        return None
    return dict(zip(("input_tokens", "output_tokens", "cache_read_input_tokens",
                     "cache_creation_input_tokens"), counts))


def _tool_input(raw):
    try:
        args = json.loads(raw)
    except (ValueError, TypeError):
        return raw  # shared validation rejects it after booking the response usage
    return args


def stream_openai(config, messages, system_blocks, schemas, *, max_tokens,
                  force_tool=None, timeout=None, purpose="chat"):
    from . import llm

    sdk = llm._openai_client(config, timeout=timeout) if config.provider == "azure" else llm._openai_client(config)
    with closing_client(sdk) as client:
        converted = llm._transcript_to_openai_messages(
            messages, "\n\n".join(b["text"] for b in system_blocks))
        # Reasoning fields are private and must accompany tool-call continuations.
        assistants = [m for m in converted if m["role"] == "assistant"]
        for original, converted_message in zip((m for m in messages if m["role"] == "assistant"), assistants):
            private = original.get("_provider") or {}
            for field in ("reasoning_content", "reasoning", "reasoning_details"):
                if field in private:
                    converted_message[field] = private[field]
        kwargs = dict(model=config.model, messages=converted,
                      stream_options={"include_usage": True})
        kwargs["max_completion_tokens" if config.provider == "azure" or (config.provider == "openai" and not config.custom_endpoint) else "max_tokens"] = max_tokens
        if schemas:
            kwargs["tools"] = llm._openai_tools(schemas)
        if force_tool:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if config.provider == "deepseek" and purpose == "alert":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        text, reasoning, calls, usage, stop = "", {}, {}, None, None
        details = {}
        completions = client.chat.completions
        if hasattr(completions, "create"):
            stream = completions.create(stream=True, **kwargs)
            try:
                for chunk in stream:
                    yield {"type": "keepalive"}
                    if _value(chunk, "usage") is not None:
                        usage = _value(chunk, "usage")
                    choices = _value(chunk, "choices", []) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    stop = _value(choice, "finish_reason") or stop
                    delta = _value(choice, "delta")
                    part = _value(delta, "content", "") or ""
                    if part:
                        text += part
                        yield {"type": "text_delta", "text": part}
                    for field in ("reasoning_content", "reasoning"):
                        part = _value(delta, field, "") or ""
                        if part:
                            reasoning[field] = reasoning.get(field, "") + part
                    for detail in _value(delta, "reasoning_details", []) or []:
                        raw = detail.model_dump() if hasattr(detail, "model_dump") else detail
                        if not isinstance(raw, dict):
                            raise TransportError("protocol", "The provider returned invalid reasoning metadata.")
                        index = raw.get("index")
                        if index is None:
                            index = raw.get("id") or len(details)
                        merged = details.setdefault(index, {})
                        for key in ("type", "id", "format", "index", "text", "summary", "data", "signature"):
                            if raw.get(key) is not None:
                                if key in ("text", "summary", "data", "signature") and key in merged:
                                    merged[key] += raw[key]
                                else:
                                    merged[key] = raw[key]
                    for tc in _value(delta, "tool_calls", []) or []:
                        index = _value(tc, "index")
                        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                            raise TransportError("protocol", "The provider omitted a tool call index.")
                        call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        call["id"] += _value(tc, "id", "") or ""
                        function = _value(tc, "function")
                        call["name"] += _value(function, "name", "") or ""
                        call["arguments"] += _value(function, "arguments", "") or ""
            finally:
                if hasattr(stream, "close"):
                    stream.close()
        else:
            # Preserve the public client seam used by existing installations/tests.
            with completions.stream(**kwargs) as stream:
                for event in stream:
                    yield {"type": "keepalive"}
                    if event.type == "content.delta" and event.delta:
                        yield {"type": "text_delta", "text": event.delta}
                response = stream.get_final_completion()
            usage = response.usage
            choice = response.choices[0]
            text, stop = choice.message.content or "", choice.finish_reason
            reasoning = {field: _value(choice.message, field) for field in ("reasoning_content", "reasoning", "reasoning_details")
                         if _value(choice.message, field)}
            calls = {i: {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                     for i, tc in enumerate(choice.message.tool_calls or [])}
        if stop is None:
            raise TransportError("protocol", "The stream ended before a final response.")
        blocks = [{"type": "text", "text": text}] if text else []
        blocks.extend({"type": "tool_use", "id": call["id"], "name": call["name"],
                       "input": _tool_input(call["arguments"])} for _, call in sorted(calls.items()))
        if details:
            reasoning["reasoning_details"] = list(details.values())
        yield {"type": "response", "blocks": blocks, "stop_reason": stop,
               "usage": _usage(usage, openai=True),
               "provider_data": reasoning or None}

def stream_anthropic(config, messages, system_blocks, schemas, *, max_tokens,
                     force_tool=None, timeout=None, purpose="chat"):
    from . import llm

    with closing_client(llm._anthropic_client(config)) as client:
        converted = llm._transcript_to_api_messages(messages)
        for original, sent in zip((m for m in messages if m["role"] == "assistant"),
                                  (m for m in converted if m["role"] == "assistant")):
            private = original.get("_provider") or {}
            if private.get("blocks"):
                sent["content"] = private["blocks"]
        kwargs = dict(model=config.model, max_tokens=max_tokens, system=system_blocks,
                      messages=llm._with_cache_breakpoint(converted))
        if schemas:
            kwargs["tools"] = schemas
        if force_tool:
            # These model families reject forced tool choice with HTTP 400.
            # The runtime still validates the required call before executing it.
            if re.fullmatch(r"claude-(?:(?:sonnet|opus)-5-5|(?:fable|mythos)-5-1)(?:-\d{8})?", config.model):
                kwargs["tool_choice"] = {"type": "auto"}
                kwargs["system"] = [*system_blocks, {
                    "type": "text",
                    "text": f"You must call the {force_tool} tool in this response. A prose answer alone does not complete this task.",
                }]
            else:
                kwargs["tool_choice"] = {"type": "tool", "name": force_tool}
        if timeout is not None:
            kwargs["timeout"] = timeout
        with client.messages.stream(**kwargs) as stream:
            if hasattr(stream, "__iter__"):
                for event in stream:
                    yield {"type": "keepalive"}
                    if _value(event, "type") == "content_block_delta":
                        delta = _value(event, "delta")
                        if _value(delta, "type") == "text_delta" and _value(delta, "text"):
                            yield {"type": "text_delta", "text": _value(delta, "text")}
            else:
                for text in stream.text_stream:
                    yield {"type": "keepalive"}
                    if text:
                        yield {"type": "text_delta", "text": text}
            response = stream.get_final_message()
        visible, native = [], []
        has_private = False
        for block in response.content:
            raw = block.model_dump() if hasattr(block, "model_dump") else dict(block)
            kind = raw.get("type")
            if kind in ("thinking", "redacted_thinking"):
                allowed = ("type", "thinking", "signature") if kind == "thinking" else ("type", "data")
                native.append({key: raw[key] for key in allowed if key in raw})
                has_private = True
            elif kind in ("text", "tool_use"):
                safe = llm._block_to_dict(block)
                visible.append(safe)
                native.append(safe)
        yield {"type": "response", "blocks": visible, "stop_reason": response.stop_reason,
               "usage": _usage(response.usage), "provider_data": {"blocks": native} if has_private else None}


def complete_request(config, *, system, user, max_tokens, response_format=None,
                     timeout=90, purpose="report"):
    """Non-streaming counterpart using the same credential-owning clients.

    Cloud JSON mode deliberately uses prompting; no tool schemas are sent.
    The caller validates JSON and owns retries, metering and the hard deadline.
    """
    from . import llm

    if response_format == "json":
        system += "\n\nReturn only valid JSON. Do not include Markdown fences or explanatory text."
    if config.provider in ("openai", "litellm", "openrouter", "deepseek", "azure", "custom"):
        sdk = llm._openai_client(config, timeout=timeout) if config.provider == "azure" else llm._openai_client(config)
        with closing_client(sdk) as client:
            kwargs = {"model": config.model, "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": user}], "stream": False, "timeout": timeout}
            token_key = "max_completion_tokens" if config.provider == "azure" or (config.provider == "openai" and not config.custom_endpoint) else "max_tokens"
            kwargs[token_key] = max_tokens
            if response_format == "json":
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
            choices = _value(response, "choices", [])
            if not choices:
                raise TransportError("protocol", "The provider returned no completion.")
            choice = choices[0]
            message = _value(choice, "message")
            if _value(message, "tool_calls"):
                raise TransportError("protocol", "The provider returned an unexpected tool call.")
            return {"text": _value(message, "content"), "finish_reason": _value(choice, "finish_reason"),
                    "usage": _usage(_value(response, "usage"), openai=True)}
    if config.provider == "anthropic":
        with closing_client(llm._anthropic_client(config)) as client:
            response = client.messages.create(model=config.model, system=system,
                messages=[{"role": "user", "content": user}], max_tokens=max_tokens, timeout=timeout)
            blocks = _value(response, "content", [])
            if any(_value(block, "type") == "tool_use" for block in blocks):
                raise TransportError("protocol", "The provider returned an unexpected tool call.")
            return {"text": "".join(_value(block, "text", "") for block in blocks if _value(block, "type") == "text"),
                    "finish_reason": _value(response, "stop_reason"), "usage": _usage(_value(response, "usage"))}
    from . import cloud
    client = None
    try:
        if config.provider == "bedrock":
            client = cloud._aws_client(config, "bedrock-runtime", timeout)
            response = client.converse(modelId=config.model, system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}], inferenceConfig={"maxTokens": max_tokens})
            blocks = response.get("output", {}).get("message", {}).get("content", [])
            if any("toolUse" in block for block in blocks):
                raise TransportError("protocol", "The provider returned an unexpected tool call.")
            raw = response.get("usage", {})
            usage = cloud._usage_counts(raw.get("inputTokens"), raw.get("outputTokens"),
                                       raw.get("cacheReadInputTokens", 0), raw.get("cacheWriteInputTokens", 0))
            return {"text": "".join(block.get("text", "") for block in blocks),
                    "finish_reason": response.get("stopReason"), "usage": usage}
        if config.provider == "vertex":
            from google.genai import types
            client = cloud._vertex_client(config, timeout)
            response = client.models.generate_content(model=config.model,
                contents=[{"role": "user", "parts": [{"text": user}]}],
                config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens,
                                                   automatic_function_calling={"disable": True}))
            candidates = _value(response, "candidates", []) or []
            if not candidates:
                raise TransportError("protocol", "The provider returned no completion.")
            candidate = candidates[0]
            parts = _value(_value(candidate, "content"), "parts", []) or []
            if any(_value(part, "function_call") for part in parts):
                raise TransportError("protocol", "The provider returned an unexpected tool call.")
            raw = _value(response, "usage_metadata")
            inputs, outputs = _value(raw, "prompt_token_count"), _value(raw, "candidates_token_count")
            cache = _value(raw, "cached_content_token_count", 0) or 0
            usage = cloud._usage_counts(inputs - cache, outputs + (_value(raw, "thoughts_token_count", 0) or 0), cache) if isinstance(inputs, int) and isinstance(outputs, int) else None
            finish = _value(candidate, "finish_reason")
            return {"text": "".join(_value(part, "text", "") or "" for part in parts if not _value(part, "thought")),
                    "finish_reason": str(getattr(finish, "value", finish)).lower() if finish else None, "usage": usage}
        raise TransportError("unsupported", "Unsupported LLM provider.")
    except Exception as error:
        raise cloud._failure(error) from None
    finally:
        cloud._close(client)
