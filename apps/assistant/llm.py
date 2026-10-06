"""AI assistant LLM orchestration — provider-pluggable, org-scoped.

Ported from the legacy single-tenant portal's chat module. The only structural change: provider,
key, model and base URL come from the org's :class:`OrgAssistantConfig` row via
:class:`LLMConfig` instead of the process environment — every org brings its
own API key. The tool-use loop, retry/backoff behaviour, transcript block
shapes and emitted event shapes are unchanged, so the widget and stored
transcripts stay compatible with the legacy portal.

The decrypted API key lives only on the :class:`LLMConfig` instance and in
the SDK client. It is excluded from ``repr()`` and never enters the session
transcript, an event payload or a log line.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Generator
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 4096
MAX_TURNS_PER_MESSAGE = 30

#: How long to block on a running tool before emitting a keepalive so the
#: browser (and any proxy in between) sees the connection is alive.
TOOL_WAIT_SLICE_S = 10.0
#: Do not start another tool iteration with less than this left on the turn
#: deadline -- it could not finish anyway.
MIN_REMAINING_S = 5.0

#: First backoff after a 429/5xx. Both SDKs already retry transient failures
#: internally with their own jittered backoff, so by the time an error reaches
#: this loop the quick recovery has been tried and lost. A 10s opening bid
#: (the previous value) charged every user a flat ten seconds for a rate limit
#: that usually clears in about one, and the doubling gets to patience quickly
#: enough on its own.
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 90.0
RETRY_MAX_ATTEMPTS = 5

SUPPORTED_PROVIDERS = ("anthropic", "openai")

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}

#: A gateway on the internal network (LiteLLM, vLLM, Ollama) legitimately
#: has no key, but both SDKs refuse to construct without one.
GATEWAY_PLACEHOLDER_KEY = "gateway-no-key"

# $ per MTok. List prices at time of writing; actual billing may be lower.
ANTHROPIC_PRICING = {
    "claude-opus-4-7":           {"in": 15.0, "out": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6":         {"in": 3.0,  "out": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"in": 1.0,  "out": 5.0,  "cache_write": 1.25,  "cache_read": 0.10},
}

OPENAI_PRICING = {
    "gpt-4o":        {"in": 2.50, "out": 10.00, "cached_in": 1.25},
    "gpt-4o-mini":   {"in": 0.15, "out": 0.60,  "cached_in": 0.075},
    "gpt-4.1":       {"in": 2.00, "out": 8.00,  "cached_in": 0.50},
    "gpt-4.1-mini":  {"in": 0.40, "out": 1.60,  "cached_in": 0.10},
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to talk to one org's model provider.

    ``api_key`` is deliberately excluded from ``repr()``: these objects end up
    in tracebacks and debug logs, and the key is the org's own secret.
    """

    provider: str
    api_key: str = field(default="", repr=False)
    model: str = ""
    base_url: str = ""
    #: Admin-set USD per MTok; both or neither. Overrides the price tables.
    price_in: Decimal | None = None
    price_out: Decimal | None = None
    #: May read_doc send report source files to the provider?
    share_report_source: bool = True
    #: May the model propose portal actions (OrgAssistantConfig.actions_enabled)?
    actions_enabled: bool = False

    @classmethod
    def for_org(cls, org) -> "LLMConfig | None":
        """Resolve an org's LLM settings, or None when the assistant is off/unset."""
        from apps.orgs.models import OrgAssistantConfig

        if org is None:
            return None
        cfg = OrgAssistantConfig.objects.filter(org=org).first()
        if cfg is None or not cfg.enabled:
            return None
        return cls.from_config(cfg)

    @classmethod
    def from_config(cls, cfg) -> "LLMConfig":
        provider = (cfg.provider or "anthropic").strip().lower()
        base_url = (cfg.base_url or "").strip()
        key = (cfg.api_key or "").strip()
        if not key and base_url:
            key = GATEWAY_PLACEHOLDER_KEY
        return cls(
            provider=provider,
            api_key=key,
            model=(cfg.model or "").strip() or DEFAULT_MODELS.get(provider, ""),
            base_url=base_url,
            price_in=cfg.price_in_per_mtok,
            price_out=cfg.price_out_per_mtok,
            share_report_source=cfg.share_report_source,
            actions_enabled=cfg.actions_enabled,
        )

    def price(self) -> dict | None:
        """$/MTok rates for this model: the admin override, else the table, else None.

        None means spend cannot be booked, and :func:`is_available` refuses
        the turn rather than billing it as free.
        """
        if self.price_in is not None and self.price_out is not None:
            i, o = float(self.price_in), float(self.price_out)
            # ponytail: an override bills cached tokens at the input price; the
            # tables carry provider-specific cache rates an admin cannot enter.
            return {"in": i, "out": o, "cache_write": i, "cache_read": i, "cached_in": i}
        return _list_price(self.provider, self.model)


def _list_price(provider: str, model: str) -> dict | None:
    if provider == "openai":
        for known, p in OPENAI_PRICING.items():
            if model == known or model.startswith(known + "-"):
                return p
        return None
    return ANTHROPIC_PRICING.get(_pricing_model(model))


def is_available(org) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for one org. Reason is user-visible."""
    from apps.orgs.models import OrgAssistantConfig

    if org is None:
        return False, "The AI assistant needs an organization context."
    cfg = OrgAssistantConfig.objects.filter(org=org).first()
    if cfg is None:
        return False, "The AI assistant is not configured for this organization."
    if not cfg.enabled:
        return False, "The AI assistant is disabled for this organization."

    config = LLMConfig.from_config(cfg)
    if config.provider not in SUPPORTED_PROVIDERS:
        return False, (
            f"Unsupported LLM provider {config.provider!r}. Use anthropic or "
            "openai (point the base URL at a gateway for anything else)."
        )
    try:
        if config.provider == "openai":
            import openai  # noqa: F401
        else:
            import anthropic  # noqa: F401
    except ImportError:
        return False, f"The {config.provider} SDK is not installed on this server."
    if not config.api_key:
        return False, (
            "No LLM API key configured for this organization. An org admin "
            "can add one under Settings → AI Assistant."
        )
    if config.price() is None:
        return False, f"Set a price for model '{config.model}' in the AI assistant settings"
    return True, ""


# ---------------------------------------------------------------------------
# Clients (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------


def _anthropic_client(config: LLMConfig):
    from anthropic import Anthropic

    # The turn loops retry rate limits and 5xx themselves; SDK retries on top
    # would multiply the per-attempt timeout and overrun the turn deadline.
    kwargs: dict[str, Any] = {"api_key": config.api_key, "max_retries": 0}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return Anthropic(**kwargs)


def _openai_client(config: LLMConfig):
    import openai

    kwargs: dict[str, Any] = {"api_key": config.api_key, "max_retries": 0}  # see _anthropic_client
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return openai.OpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Session-state helpers (state is AssistantSession.state — a plain dict)
# ---------------------------------------------------------------------------


def transcript_of(state: dict) -> list:
    return state.setdefault("transcript", [])


def add_usage(state: dict, usage_obj: Any, cost_delta: float) -> None:
    usage = state.setdefault("usage", {})
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        usage[name] = (usage.get(name) or 0) + (getattr(usage_obj, name, 0) or 0)
    usage["cost_usd"] = round((usage.get("cost_usd") or 0.0) + cost_delta, 6)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def stream_turn(
    config: LLMConfig,
    state: dict,
    user_message: str | None,
    *,
    toolbox,
    system_blocks: list,
    page_context: str | None = None,
    max_turns: int = MAX_TURNS_PER_MESSAGE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    remaining: Callable[[], float] | None = None,
    deadline_s: float | None = None,
    force_tool: str | None = None,
) -> Generator[dict, None, None]:
    """Run one user turn including internal tool-use iterations.

    ``force_tool`` names a tool the model must call on every step of this
    turn (the provider's ``tool_choice``); the alert evaluator uses it to end
    with a structured ``decide`` call instead of prose.

    ``page_context`` is where the user currently is in the portal (page title
    + path). It is appended to the model-visible copy of the user message —
    NOT to the stored transcript text — so the assistant can resolve "this
    report" / "this chart" references.

    ``remaining`` returns the seconds left on the turn's wall-clock deadline
    (``deadline_s`` is its total, for the error message). Every provider call
    gets it as its timeout, tools run off-thread with ``keepalive`` events
    yielded while they work, and no new tool iteration starts with less than
    :data:`MIN_REMAINING_S` left. Without it the turn is unbounded.

    Mutates ``state`` (transcript + usage). Persisting it, recording spend and
    writing SSE frames are the view's job, same contract as the legacy portal.
    """
    if user_message is not None:
        entry: dict = {"role": "user", "content": user_message, "ts": time.time()}
        if page_context:
            entry["page"] = page_context
        transcript_of(state).append(entry)

    clock = _Clock(remaining, deadline_s)
    if config.provider == "openai":
        yield from _stream_turn_openai(
            config, state, toolbox, system_blocks, max_turns, max_tokens, clock, force_tool
        )
    elif config.provider == "anthropic":
        yield from _stream_turn_anthropic(
            config, state, toolbox, system_blocks, max_turns, max_tokens, clock, force_tool
        )
    else:
        yield {
            "type": "error", "code": "unavailable",
            "message": f"Unsupported LLM provider: {config.provider!r}",
        }


class _Clock:
    """The turn deadline as the provider loops see it."""

    def __init__(self, remaining, deadline_s):
        self.remaining = remaining or (lambda: math.inf)
        self.deadline_s = deadline_s

    def timeout_kwargs(self) -> dict:
        r = self.remaining()
        return {} if math.isinf(r) else {"timeout": max(r, 1.0)}

    def expired_event(self) -> dict:
        n = int(self.deadline_s or 0)
        return {
            "type": "error", "code": "deadline",
            "message": f"Stopped after {n} seconds. Ask a narrower question.",
        }


def _run_tool(toolbox, name: str, args: dict, clock: _Clock):
    """Execute one tool off-thread, yielding ``keepalive`` while it runs.

    Returns the tool's result string, or ``None`` when the deadline passed
    first. The worker closes its own DB connections on the way out; the
    request thread's are untouched.
    """
    from django.db import connections

    error = toolbox.authorize(args)
    if error:
        return f"Error: {error}"

    def work():
        try:
            return toolbox.execute(name, args)
        finally:
            connections.close_all()

    # ponytail: not a context manager -- __exit__ would wait for the tool,
    # which is exactly what the deadline must not do. A tool that overruns
    # keeps running to completion in its thread; nothing waits on it.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(work)
    try:
        while True:
            try:
                return future.result(timeout=max(min(TOOL_WAIT_SLICE_S, clock.remaining()), 0.0))
            except concurrent.futures.TimeoutError:
                if clock.remaining() <= 0:
                    return None
                yield {"type": "keepalive"}
    finally:
        pool.shutdown(wait=False)


def _with_page_context(messages: list, transcript: list) -> list:
    """Append the page context of the LATEST user turn to its API message.

    Only the live turn gets context — historical turns answer with their own
    context already baked into the assistant replies.
    """
    if not messages or not transcript:
        return messages
    last_entry = transcript[-1] if transcript[-1].get("role") == "user" else None
    if last_entry is None:
        for e in reversed(transcript):
            if e.get("role") == "user":
                last_entry = e
                break
    page = (last_entry or {}).get("page")
    if not page:
        return messages
    out = list(messages)
    last = dict(out[-1])
    if last.get("role") == "user" and isinstance(last.get("content"), str):
        last["content"] = (
            last["content"]
            + f"\n\n[Context: the user is currently viewing {page}. "
            "If the question refers to 'this report' or 'this chart', it "
            "means that page.]"
        )
        out[-1] = last
    return out


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def _pricing_model(model: str) -> str:
    if "anthropic." in model:
        model = model.split("anthropic.", 1)[1]
    return model


def compute_cost_anthropic(usage: Any, model: str, config: LLMConfig | None = None) -> float:
    p = config.price() if config is not None else _list_price("anthropic", model)
    if not p:
        return 0.0
    return round(
        (getattr(usage, "input_tokens", 0) or 0) * p["in"] / 1_000_000
        + (getattr(usage, "output_tokens", 0) or 0) * p["out"] / 1_000_000
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * p["cache_write"] / 1_000_000
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * p["cache_read"] / 1_000_000,
        6,
    )


def _stream_turn_anthropic(
    config, state, toolbox, system_blocks, max_turns, max_tokens, clock, force_tool=None
) -> Generator[dict, None, None]:
    from anthropic import APIStatusError, RateLimitError

    client = _anthropic_client(config)
    model = config.model or DEFAULT_MODELS["anthropic"]
    transcript = transcript_of(state)
    messages = _with_page_context(_transcript_to_api_messages(transcript), transcript)
    forced = {"tool_choice": {"type": "tool", "name": force_tool}} if force_tool else {}

    for _turn in range(max_turns):
        if _turn and clock.remaining() < MIN_REMAINING_S:
            yield clock.expired_event()
            return
        response = None
        delay = RETRY_BASE_DELAY
        cached_messages = _with_cache_breakpoint(messages)
        for attempt in range(RETRY_MAX_ATTEMPTS):
            # Text is forwarded as the model produces it. `emitted` guards the
            # retry: once a token has reached the browser we cannot start the
            # call over without repeating what the user has already read, so a
            # mid-stream failure is reported rather than retried.
            emitted = False
            try:
                with client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_blocks,
                    tools=toolbox.schemas,
                    messages=cached_messages,
                    **forced,
                    **clock.timeout_kwargs(),
                ) as stream:
                    for chunk in stream.text_stream:
                        if chunk:
                            emitted = True
                            yield {"type": "text_delta", "text": chunk}
                    response = stream.get_final_message()
                break
            except RateLimitError:
                if emitted:
                    yield _cut_off()
                    return
                yield {"type": "rate_limit", "attempt": attempt + 1, "delay_s": delay}
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_DELAY)
            except APIStatusError as e:
                if not emitted and e.status_code and 500 <= e.status_code < 600:
                    yield {"type": "rate_limit", "attempt": attempt + 1, "delay_s": delay}
                    time.sleep(delay)
                    delay = min(delay * 2, RETRY_MAX_DELAY)
                    continue
                yield _provider_error(e, config.base_url)
                return
            except Exception as e:
                yield _call_failed(clock, e, config.base_url)
                return

        if response is None:
            yield _retries_exhausted()
            return

        cost = compute_cost_anthropic(response.usage, model, config)
        add_usage(state, response.usage, cost)
        yield {
            "type": "usage",
            "model": model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            "cost_delta_usd": round(cost, 6),
            "session_cost_usd": round(state.get("usage", {}).get("cost_usd", 0.0), 6),
        }

        transcript.append({
            "role": "assistant",
            "content": [_block_to_dict(b) for b in response.content],
            "ts": time.time(),
            "stop_reason": response.stop_reason,
        })

        messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in response.content]})

        if response.stop_reason != "tool_use":
            yield {"type": "done", "stop_reason": response.stop_reason}
            return

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            result = yield from _run_tool(toolbox, block.name, block.input, clock)
            if result is None:
                yield clock.expired_event()
                return
            is_error = result.startswith("Error")
            framed = toolbox.frame(block.name, block.input, result)
            provenance = getattr(toolbox, "provenance", None)
            proposal = getattr(toolbox, "proposal", None)
            transcript.append({
                "role": "tool",
                "tool_use_id": block.id,
                "name": block.name,
                "input": block.input,
                "result": framed,
                "is_error": is_error,
                "provenance": provenance,
                "ts": time.time(),
            })
            if proposal:
                transcript[-1]["proposal"] = proposal
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": framed,
            })
            yield {
                "type": "tool_result", "id": block.id, "name": block.name,
                "excerpt": _excerpt(result, 400), "is_error": is_error,
                "provenance": provenance,
            }
            if proposal:
                yield {"type": "proposal", **proposal}

        messages.append({"role": "user", "content": tool_results})

    yield _turn_limit(max_turns)


# ---------------------------------------------------------------------------
# Error events (one shape, one place)
# ---------------------------------------------------------------------------


def _cut_off() -> dict:
    return {
        "type": "error", "code": "cut_off",
        "message": "The response was cut off. Ask again.",
    }


def _call_failed(clock: "_Clock", e: Exception, base_url: str = "") -> dict:
    """A request that dies once the deadline has passed *is* the deadline --
    the provider timed out because we told it to -- so say that, without a
    traceback. Anything earlier is the provider's."""
    if clock.remaining() <= 0:
        return clock.expired_event()
    return _provider_error(e, base_url)


def _provider_error(e: Exception, base_url: str = "") -> dict:
    # The full exception goes to the log, never to the browser: provider
    # errors echo request details, and the org's key rides in the client.
    # ``detail`` is the one-line exception for org admins only -- the view
    # strips it for everyone else before the frame leaves the server.
    logger.exception("assistant: provider call failed (%s)", type(e).__name__)
    return {
        "type": "error", "code": "provider",
        "message": "The model provider returned an error. Try again in a moment.",
        "detail": error_detail(e, base_url),
    }


#: Anything shaped like an API key, including the masked echo providers put
#: in their own 401 message ("sk-ant-a*****…JgAA"), and any bare asterisk run.
_KEYISH = re.compile(r"sk-[A-Za-z0-9*_…-]{6,}|\*{6,}")


def redact_keys(text: str) -> str:
    return _KEYISH.sub("sk-…", text)


def _provider_message(e: Exception) -> str:
    """The provider's own sentence when the SDK kept the response body (the
    inner ``error`` dict for one SDK, the whole envelope for the other), else
    ``str(e)`` -- which for a status error is "Error code: 401 - {…the dict…}"."""
    body = getattr(e, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return msg if isinstance(msg, str) and msg else str(e)


def error_detail(e: Exception, base_url: str = "") -> str:
    """One technical line: exception class, head of the provider's message
    with key-like material redacted, the host it hit (the SDK's request URL
    when it has one, else the configured gateway)."""
    text = f"{type(e).__name__}: {redact_keys(_provider_message(e))[:200]}".rstrip(": ")
    url = getattr(getattr(e, "request", None), "url", None) or base_url
    host = urlsplit(str(url)).hostname if url else None
    return f"{text} ({host})" if host else text


def _retries_exhausted() -> dict:
    return {
        "type": "error", "code": "provider",
        "message": "The model provider is rate limiting. Try again in a minute.",
    }


def _turn_limit(max_turns: int) -> dict:
    return {
        "type": "error", "code": "turn_limit",
        "message": f"Stopped after {max_turns} tool steps. Ask a narrower question.",
    }


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class _UsageShim:
    """Adapt OpenAI usage to the attribute names add_usage() expects.

    ``usage`` is ``None`` when a streamed completion carries no usage block;
    everything then reads as zero rather than raising, because a missing
    meter must not cost the user their answer.
    """

    def __init__(self, usage):
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        self.input_tokens = max((getattr(usage, "prompt_tokens", 0) or 0) - cached, 0)
        self.output_tokens = getattr(usage, "completion_tokens", 0) or 0
        self.cache_read_input_tokens = cached
        self.cache_creation_input_tokens = 0


def compute_cost_openai(shim: _UsageShim, model: str, config: LLMConfig | None = None) -> float:
    p = config.price() if config is not None else _list_price("openai", model)
    if not p:
        return 0.0
    return round(
        shim.input_tokens * p["in"] / 1_000_000
        + shim.cache_read_input_tokens * p["cached_in"] / 1_000_000
        + shim.output_tokens * p["out"] / 1_000_000,
        6,
    )


def _openai_tools(schemas: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in schemas
    ]


def _transcript_to_openai_messages(transcript: list[dict], system_text: str) -> list[dict]:
    """Our (Anthropic-shaped) transcript → OpenAI chat messages."""
    out: list[dict] = [{"role": "system", "content": system_text}]
    for entry in transcript:
        role = entry.get("role")
        if role == "user":
            out.append({"role": "user", "content": entry["content"]})
        elif role == "assistant":
            content = entry.get("content")
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            texts, tool_calls = [], []
            for block in content or []:
                btype = block.get("type") if isinstance(block, dict) else None
                if btype == "text":
                    texts.append(block.get("text") or "")
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(t for t in texts if t) or None,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        elif role == "tool" and entry.get("tool_use_id"):
            # A decision entry (approve/reject, no tool_use_id) is the
            # panel's record; the model hears of it in the follow-up turn.
            out.append({
                "role": "tool",
                "tool_call_id": entry.get("tool_use_id"),
                "content": entry.get("result") or "",
            })
    return out


def _stream_turn_openai(
    config, state, toolbox, system_blocks, max_turns, max_tokens, clock, force_tool=None
) -> Generator[dict, None, None]:
    import openai

    client = _openai_client(config)
    model = config.model or DEFAULT_MODELS["openai"]
    system_text = "\n\n".join(b["text"] for b in system_blocks)
    tools = _openai_tools(toolbox.schemas)
    forced = (
        {"tool_choice": {"type": "function", "function": {"name": force_tool}}} if force_tool else {}
    )
    transcript = transcript_of(state)
    messages = _with_page_context(
        _transcript_to_openai_messages(transcript, system_text), transcript
    )

    for _turn in range(max_turns):
        if _turn and clock.remaining() < MIN_REMAINING_S:
            yield clock.expired_event()
            return
        response = None
        delay = RETRY_BASE_DELAY
        for attempt in range(RETRY_MAX_ATTEMPTS):
            # Same contract as the Anthropic branch: forward text as it
            # arrives, and refuse to retry once any of it has been shown.
            emitted = False
            try:
                with client.chat.completions.stream(
                    model=model,
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=tools,
                    **forced,
                    # Not the default on a streamed completion, and without it
                    # the final completion carries no usage block -- which
                    # would silently stop spend being recorded and leave the
                    # budget caps never firing for OpenAI organizations.
                    stream_options={"include_usage": True},
                    **clock.timeout_kwargs(),
                ) as stream:
                    for event in stream:
                        if event.type == "content.delta" and event.delta:
                            emitted = True
                            yield {"type": "text_delta", "text": event.delta}
                    response = stream.get_final_completion()
                break
            except openai.RateLimitError:
                if emitted:
                    yield _cut_off()
                    return
                yield {"type": "rate_limit", "attempt": attempt + 1, "delay_s": delay}
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_DELAY)
            except openai.APIStatusError as e:
                if not emitted and e.status_code and 500 <= e.status_code < 600:
                    yield {"type": "rate_limit", "attempt": attempt + 1, "delay_s": delay}
                    time.sleep(delay)
                    delay = min(delay * 2, RETRY_MAX_DELAY)
                    continue
                yield _provider_error(e, config.base_url)
                return
            except Exception as e:
                yield _call_failed(clock, e, config.base_url)
                return

        if response is None:
            yield _retries_exhausted()
            return

        shim = _UsageShim(response.usage)  # tolerant of a missing usage block
        cost = compute_cost_openai(shim, model, config)
        add_usage(state, shim, cost)
        yield {
            "type": "usage",
            "model": model,
            "input_tokens": shim.input_tokens,
            "output_tokens": shim.output_tokens,
            "cache_read": shim.cache_read_input_tokens,
            "cache_write": 0,
            "cost_delta_usd": round(cost, 6),
            "session_cost_usd": round(state.get("usage", {}).get("cost_usd", 0.0), 6),
        }

        choice = response.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_calls = list(msg.tool_calls or [])

        # Store using the same block shapes as Anthropic so transcripts stay
        # provider-agnostic.
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for tc in tool_calls:
            try:
                parsed_args = json.loads(tc.function.arguments or "{}")
            except Exception:
                parsed_args = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": parsed_args,
            })
        transcript.append({
            "role": "assistant",
            "content": blocks,
            "ts": time.time(),
            "stop_reason": choice.finish_reason,
        })

        api_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            api_msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                              "arguments": tc.function.arguments or "{}"}}
                for tc in tool_calls
            ]
        messages.append(api_msg)

        if choice.finish_reason != "tool_calls" or not tool_calls:
            yield {"type": "done", "stop_reason": choice.finish_reason}
            return

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            yield {"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args}
            result = yield from _run_tool(toolbox, tc.function.name, args, clock)
            if result is None:
                yield clock.expired_event()
                return
            is_error = result.startswith("Error")
            framed = toolbox.frame(tc.function.name, args, result)
            provenance = getattr(toolbox, "provenance", None)
            proposal = getattr(toolbox, "proposal", None)
            transcript.append({
                "role": "tool",
                "tool_use_id": tc.id,
                "name": tc.function.name,
                "input": args,
                "result": framed,
                "is_error": is_error,
                "provenance": provenance,
                "ts": time.time(),
            })
            if proposal:
                transcript[-1]["proposal"] = proposal
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": framed})
            yield {
                "type": "tool_result", "id": tc.id, "name": tc.function.name,
                "excerpt": _excerpt(result, 400), "is_error": is_error,
                "provenance": provenance,
            }
            if proposal:
                yield {"type": "proposal", **proposal}

    yield _turn_limit(max_turns)


# ---------------------------------------------------------------------------
# Connection test (the settings page's button)
# ---------------------------------------------------------------------------

_PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI"}


def mismatch_hints(config: LLMConfig) -> list[str]:
    """Cheap plausibility checks on provider vs key vs model, as hints only:
    a gateway may legitimately serve one vendor's model in the other's dialect,
    so none of this blocks a save. The key never leaves this function."""
    hints = []
    key, model = config.api_key, config.model.lower()
    provider = _PROVIDER_NAMES.get(config.provider, config.provider)
    key_vendor = "anthropic" if key.startswith("sk-ant-") else "openai" if key.startswith("sk-") else ""
    if key_vendor and key_vendor != config.provider:
        hints.append(f"This key looks like an {_PROVIDER_NAMES[key_vendor]} key, but the provider is {provider}.")
    model_vendor = (
        "anthropic" if model.startswith("claude-")
        else "openai" if model.startswith(("gpt-", "o1", "o3", "o4")) else ""
    )
    if model_vendor and model_vendor != config.provider:
        hints.append(f"This model looks like an {_PROVIDER_NAMES[model_vendor]} model, but the provider is {provider}.")
    return hints

#: The endpoint answered, just not in this provider's dialect: the SDK got a
#: body it could not shape into a message (the incident was an AssertionError
#: out of ``get_final_message`` against an OpenAI-compatible gateway).
# ponytail: class names, not imports -- both SDKs use the same names and
# neither is guaranteed installed. Tighten if a real SDK bug lands in here.
_FOREIGN_SHAPE = {
    "AssertionError", "AttributeError", "IndexError", "KeyError", "TypeError",
    "ValueError", "APIResponseValidationError",
}


def check_connection(config: LLMConfig, timeout: float = 20.0) -> dict:
    """The smallest call the saved config allows, answered as one plain sentence.

    Same client construction and stream call as :func:`stream_turn`, so a
    gateway that breaks a real turn breaks this too. Deliberately exempt from
    the budget ledger: eight output tokens once per admin click is noise next
    to a single turn, and there is no user turn to book them against.
    """
    if config.provider not in SUPPORTED_PROVIDERS:
        return {"ok": False, "message": f"Unsupported provider {config.provider!r}.", "latency_ms": 0}
    host = urlsplit(config.base_url).hostname or f"api.{config.provider}.com"
    prompt = [{"role": "user", "content": "Say OK"}]
    t0 = time.monotonic()
    try:
        if config.provider == "openai":
            with _openai_client(config).chat.completions.stream(
                model=config.model, max_tokens=8, messages=prompt, timeout=timeout,
            ) as stream:
                for _ in stream:
                    pass
                stream.get_final_completion()
        else:
            with _anthropic_client(config).messages.stream(
                model=config.model, max_tokens=8, messages=prompt, timeout=timeout,
            ) as stream:
                for _ in stream.text_stream:
                    pass
                stream.get_final_message()
    except Exception as e:  # noqa: BLE001 -- every failure becomes a sentence
        names = {c.__name__ for c in type(e).__mro__}
        if "AuthenticationError" in names:
            message = "The provider rejected the key."
        elif "APIConnectionError" in names:
            message = f"Could not reach {host}."
        elif getattr(e, "status_code", None) == 404 or names & _FOREIGN_SHAPE:
            message = (
                f"{host} did not answer like an {_PROVIDER_NAMES[config.provider]} "
                "endpoint — check the provider and custom endpoint URL."
            )
        else:
            message = type(e).__name__
        result = {
            "ok": False, "message": message, "detail": error_detail(e, config.base_url),
            "hints": mismatch_hints(config),
        }
    else:
        result = {"ok": True, "message": f"{config.model} answered."}
    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return result


# ---------------------------------------------------------------------------
# Transcript / message helpers (ported from portal/chat/llm.py)
# ---------------------------------------------------------------------------


#: What the Messages API accepts back per content-block type. The SDK's
#: streaming snapshot decorates blocks with extras (``parsed_output`` on text,
#: ``caller`` on tool_use, ``citations: None``) that the API rejects with
#: "Extra inputs are not permitted" when a transcript is replayed, so blocks
#: are reduced to these keys before they are stored or re-sent.
_BLOCK_KEYS = {
    "text": ("type", "text", "citations"),
    "tool_use": ("type", "id", "name", "input"),
}


def _block_to_dict(block: Any) -> dict:
    raw = block.model_dump() if hasattr(block, "model_dump") else dict(block)
    keys = _BLOCK_KEYS.get(raw.get("type"))
    if keys is None:
        return {k: v for k, v in raw.items() if v is not None}
    return {k: raw[k] for k in keys if raw.get(k) is not None}


def _transcript_to_api_messages(transcript: list[dict]) -> list[dict]:
    """Our transcript shape → Anthropic messages format.

    Consecutive tool_results must be consolidated into a single user message
    with multiple tool_result blocks.
    """
    out: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tools():
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for entry in transcript:
        role = entry.get("role")
        if role == "user":
            flush_tools()
            out.append({"role": "user", "content": entry["content"]})
        elif role == "assistant":
            flush_tools()
            # Sanitise on replay too: sessions stored before the whitelist
            # existed still carry the SDK extras.
            out.append({"role": "assistant", "content": [_block_to_dict(b) for b in entry["content"]]})
        elif role == "tool" and entry.get("tool_use_id"):  # see the OpenAI twin
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": entry["tool_use_id"],
                "content": entry["result"],
            })
    flush_tools()
    return out


def _excerpt(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"… [+{len(s) - n} chars]"


def _with_cache_breakpoint(messages: list) -> list:
    """Shallow-clone ``messages`` with a cache_control breakpoint on the LAST
    message so prior history is cached across turns."""
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        new_content = [dict(b) if isinstance(b, dict) else b for b in content]
        if isinstance(new_content[-1], dict):
            new_content[-1]["cache_control"] = {"type": "ephemeral"}
        last["content"] = new_content
    out[-1] = last
    return out
