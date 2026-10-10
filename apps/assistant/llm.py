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
import hashlib
import logging
import math
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

SUPPORTED_PROVIDERS = ("anthropic", "openai", "litellm", "openrouter", "deepseek",
                       "azure", "bedrock", "vertex", "custom")

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
    auth_mode: str = "api_key"
    cloud_config: dict = field(default_factory=dict)
    cloud_credentials: dict = field(default_factory=dict, repr=False)
    org_id: str | None = None
    config_id: int | None = None
    config_revision: int = 1
    connection_check: dict = field(default_factory=dict)
    budgeted: bool = False
    custom_endpoint: bool = False

    def __post_init__(self):
        from .provider_registry import effective_base_url
        endpoint = effective_base_url(self.provider, self.base_url)
        if self.base_url and endpoint != effective_base_url(self.provider):
            object.__setattr__(self, "custom_endpoint", True)
        object.__setattr__(self, "base_url", endpoint)
        if not self.model:
            object.__setattr__(self, "model", DEFAULT_MODELS.get(self.provider, ""))

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
        from .provider_registry import effective_base_url

        provider = (cfg.provider or "anthropic").strip().lower()
        base_url = effective_base_url(provider, cfg.base_url)
        key = (cfg.api_key or "").strip()
        auth_mode = getattr(cfg, "auth_mode", "api_key")
        if not key and base_url and auth_mode == "none" and provider in ("custom", "litellm", "anthropic", "openai"):
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
            auth_mode=auth_mode,
            cloud_config=getattr(cfg, "cloud_config", {}) or {},
            cloud_credentials=getattr(cfg, "cloud_credentials", {}) or {},
            org_id=str(cfg.org_id), config_id=cfg.pk,
            config_revision=getattr(cfg, "config_revision", 1),
            connection_check=getattr(cfg, "connection_check", {}) or {},
            budgeted=cfg.monthly_budget_usd is not None or cfg.per_user_budget_usd is not None,
            custom_endpoint=bool((cfg.base_url or "").strip()),
        )

    def price(self) -> dict | None:
        """$/MTok rates for this model: the admin override, else the table, else None.

        None means spend cannot be booked. Budgets require a known price;
        uncapped organizations can still use unpriced models.
        """
        if self.price_in is not None and self.price_out is not None:
            i, o = float(self.price_in), float(self.price_out)
            # ponytail: an override bills cached tokens at the input price; the
            # tables carry provider-specific cache rates an admin cannot enter.
            return {"in": i, "out": o, "cache_write": i, "cache_read": i, "cached_in": i}
        return None if self.custom_endpoint else _list_price(self.provider, self.model)


def _list_price(provider: str, model: str) -> dict | None:
    if provider == "openai":
        for known in sorted(OPENAI_PRICING, key=len, reverse=True):
            p = OPENAI_PRICING[known]
            if model == known or model.startswith(known + "-"):
                return p
        return None
    return ANTHROPIC_PRICING.get(_pricing_model(model)) if provider == "anthropic" else None


def is_available(org, purpose="chat") -> tuple[bool, str]:
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
        return False, "Unsupported LLM provider."
    if not config.model:
        return False, "Enter a model or deployment in AI settings."
    try:
        _validate_credentials(config)
    except Exception as error:
        from .transport import normalize_error
        return False, str(normalize_error(error))
    from .provider_registry import workload_allowed
    if config.auth_mode == "workload" and not workload_allowed(config.provider, config.org_id):
        return False, "Workload identity is not authorized for this organization."
    try:
        if config.provider in ("openai", "litellm", "openrouter", "deepseek", "azure", "custom"):
            import openai  # noqa: F401
        elif config.provider == "anthropic":
            import anthropic  # noqa: F401
        elif config.provider == "bedrock":
            import boto3  # noqa: F401
        else:
            from google import genai  # noqa: F401
    except ImportError:
        return False, f"The {config.provider} SDK is not installed on this server."
    if config.auth_mode == "api_key" and not config.api_key:
        return False, (
            "No LLM API key configured for this organization. An org admin "
            "can add one under AI settings."
        )
    if config.auth_mode in ("access_key", "client_secret", "service_account") and not config.cloud_credentials:
        return False, "Add cloud credentials in AI settings."
    if config.price() is None and (
        cfg.monthly_budget_usd is not None or cfg.per_user_budget_usd is not None
    ):
        return False, (
            f"Set input and output prices for model '{config.model}' in AI settings "
            "to enforce budgets, or leave both budgets blank."
        )
    checks = config.connection_check.get("checks", {}) if config.connection_check.get("revision") == config.config_revision else {}
    required = ["chat"] + (["alerts"] if purpose == "alert" else []) + (["usage"] if config.budgeted else [])
    for name in required:
        check = checks.get(name)
        if check is not None and not check.get("ok"):
            return False, check.get("message") or f"The connection failed its {name} check."
        if check is None and config.provider not in ("anthropic", "openai"):
            return False, f"Test the connection in AI settings to verify {name} support."
    return True, ""


# ---------------------------------------------------------------------------
# Clients (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------


def _anthropic_client(config: LLMConfig):
    _validate_credentials(config)
    from anthropic import Anthropic

    # The turn loops retry rate limits and 5xx themselves; SDK retries on top
    # would multiply the per-attempt timeout and overrun the turn deadline.
    kwargs: dict[str, Any] = {"api_key": config.api_key or GATEWAY_PLACEHOLDER_KEY, "max_retries": 0}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return Anthropic(**kwargs)


def _openai_client(config: LLMConfig, timeout=None):
    _validate_credentials(config)
    if getattr(config, "provider", None) == "azure":
        from .cloud import azure_openai_client
        return azure_openai_client(config, timeout=timeout)
    import openai

    kwargs: dict[str, Any] = {"api_key": config.api_key or GATEWAY_PLACEHOLDER_KEY, "max_retries": 0}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return openai.OpenAI(**kwargs)


def _validate_credentials(config):
    from .provider_registry import PROVIDERS
    from .transport import TransportError
    provider = getattr(config, "provider", "openai")
    mode = getattr(config, "auth_mode", "api_key")
    if provider in ("litellm", "custom") and not config.base_url:
        raise TransportError("connection", "Add an explicit endpoint URL in AI settings.")
    if mode not in PROVIDERS.get(provider, {}).get("auth_modes", ()):
        raise TransportError("authentication", "Select a supported authentication mode.")
    if mode == "api_key" and not config.api_key:
        raise TransportError("authentication", "Add an API key in AI settings.")
    if mode == "none" and (not config.base_url or (provider in ("openai", "anthropic") and not getattr(config, "custom_endpoint", False))):
        raise TransportError("authentication", "Unauthenticated connections require an explicit gateway endpoint.")


# ---------------------------------------------------------------------------
# Session-state helpers (state is AssistantSession.state — a plain dict)
# ---------------------------------------------------------------------------


def transcript_of(state: dict) -> list:
    return state.setdefault("transcript", [])


def add_usage(state: dict, usage_obj: Any, cost_delta: float | None) -> None:
    usage = state.setdefault("usage", {})
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = usage_obj.get(name, 0) if isinstance(usage_obj, dict) else getattr(usage_obj, name, 0)
        usage[name] = (usage.get(name) or 0) + (value or 0)
    previous = usage.get("cost_usd", 0.0)
    usage["cost_usd"] = (
        None if previous is None or cost_delta is None
        else round(previous + cost_delta, 6)
    )


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
    purpose: str = "chat",
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
    if state.get("_continuation_error"):
        yield {"type": "error", "code": "new_conversation",
               "message": "This conversation has unfinished provider tool calls. Start a new conversation."}
        return
    private = state.get("_provider") or {}
    identity = _connection_identity(config)
    if private and private.get("identity") != identity:
        yield {"type": "error", "code": "new_conversation",
               "message": "This model cannot continue the private provider state. Start a new conversation."}
        return
    if user_message is not None:
        entry = {"role": "user", "content": user_message, "ts": time.time()}
        if page_context:
            entry["page"] = page_context
        transcript_of(state).append(entry)
    clock = _Clock(remaining, deadline_s)
    from .transport import TransportError, normalize_error
    try:
        adapter = _adapter(config.provider)
    except TransportError as error:
        yield _provider_error(error)
        return
    transcript = transcript_of(state)
    for turn in range(max_turns):
        if config.budgeted and config.org_id and getattr(toolbox, "actor", None) is not None:
            from . import budget
            allowed, reason = budget.precheck(config.org_id, toolbox.actor)
            if not allowed:
                yield {"type": "error", "code": "budget", "message": reason}
                return
        if clock.remaining() <= 0 or (turn and clock.remaining() < MIN_REMAINING_S):
            yield clock.expired_event()
            return
        messages = _with_page_context([dict(entry) for entry in transcript], transcript)
        for index, metadata in (state.get("_provider", {}).get("messages", {})).items():
            if int(index) < len(messages):
                messages[int(index)]["_provider"] = metadata
        response, delay = None, RETRY_BASE_DELAY
        for attempt in range(RETRY_MAX_ATTEMPTS):
            if clock.remaining() <= 0:
                yield clock.expired_event()
                return
            emitted = False
            received_progress = False
            heartbeat = time.monotonic()
            try:
                timeout = None if math.isinf(clock.remaining()) else max(clock.remaining(), 0.001)
                for event in adapter(config, messages, system_blocks, toolbox.schemas,
                                     max_tokens=max_tokens, force_tool=force_tool,
                                     timeout=timeout, purpose=purpose):
                    if clock.remaining() <= 0:
                        add_usage(state, None, None)
                        _invalidate_metering(config)
                        yield clock.expired_event()
                        return
                    if event.get("type") in ("text_delta", "keepalive", "response"):
                        received_progress = True
                    if event.get("type") == "text_delta":
                        if not isinstance(event.get("text"), str):
                            raise TransportError("protocol", "The provider returned invalid text.")
                        if event["text"]:
                            emitted = True
                            heartbeat = time.monotonic()
                            yield event
                    elif event.get("type") == "response":
                        if response is not None:
                            raise TransportError("protocol", "The provider returned multiple final responses.")
                        response = event
                    elif event.get("type") == "keepalive":
                        if time.monotonic() - heartbeat >= TOOL_WAIT_SLICE_S:
                            heartbeat = time.monotonic()
                            yield {"type": "keepalive"}
                        continue
                if response is None:
                    raise TransportError("protocol", "The stream ended before a final response.")
                break
            except Exception as error:
                safe = normalize_error(error)
                if received_progress or safe.kind in ("connection", "protocol"):
                    add_usage(state, None, None)
                    _invalidate_metering(config)
                    yield (clock.expired_event() if clock.remaining() <= 0 else
                           _cut_off() if emitted else _provider_error(safe, config.base_url))
                    return
                if safe.kind not in ("rate_limit", "server") or attempt == RETRY_MAX_ATTEMPTS - 1:
                    yield _call_failed(clock, safe, config.base_url)
                    return
                if clock.remaining() <= delay:
                    yield clock.expired_event()
                    return
                yield {"type": "rate_limit", "attempt": attempt + 1, "delay_s": delay}
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_DELAY)
                response = None
        usage = _valid_usage(response.get("usage"))
        cost = _normalized_cost(config, usage)
        if usage is None:
            _invalidate_metering(config)
        add_usage(state, usage, cost)
        validation_error = None
        try:
            _validate_response(response, toolbox.schemas, force_tool)
        except TransportError as error:
            validation_error = error
        if validation_error is None:
            transcript.append({"role": "assistant", "content": [_block_to_dict(b) for b in response["blocks"]],
                               "ts": time.time(), "stop_reason": response["stop_reason"]})
            if any(block["type"] == "tool_use" for block in response["blocks"]):
                state["_continuation_error"] = True
            if response.get("provider_data"):
                private = state.setdefault("_provider", {"identity": identity, "messages": {}})
                private["messages"][str(len(transcript) - 1)] = response["provider_data"]
        yield {"type": "usage", "model": config.model,
               "input_tokens": (usage or {}).get("input_tokens", 0),
               "output_tokens": (usage or {}).get("output_tokens", 0),
               "cache_read": (usage or {}).get("cache_read_input_tokens", 0),
               "cache_write": (usage or {}).get("cache_creation_input_tokens", 0),
               "cost_delta_usd": cost, "session_cost_usd": state["usage"]["cost_usd"]}
        if validation_error is not None:
            yield _provider_error(validation_error, config.base_url)
            return
        tools = [block for block in response["blocks"] if block["type"] == "tool_use"]
        if not tools:
            yield {"type": "done", "stop_reason": response["stop_reason"]}
            return
        if usage is None and config.budgeted:
            yield {"type": "error", "code": "metering",
                   "message": "The provider omitted usage. Test the connection before another budgeted request."}
            return
        for tool_index, block in enumerate(tools):
            name, args = block["name"], block["input"]
            yield {"type": "tool_use", "id": block["id"], "name": name, "input": args}
            result = yield from _run_tool(toolbox, name, args, clock)
            if result is None:
                yield clock.expired_event()
                return
            is_error = result.startswith("Error")
            framed = toolbox.frame(name, args, result)
            provenance, proposal = getattr(toolbox, "provenance", None), getattr(toolbox, "proposal", None)
            entry = {"role": "tool", "tool_use_id": block["id"], "name": name,
                     "input": args, "result": framed, "is_error": is_error,
                     "provenance": provenance, "ts": time.time()}
            if proposal:
                entry["proposal"] = proposal
            transcript.append(entry)
            if tool_index == len(tools) - 1:
                state.pop("_continuation_error", None)
            yield {"type": "tool_result", "id": block["id"], "name": name,
                   "excerpt": _excerpt(result, 400), "is_error": is_error, "provenance": provenance}
            if proposal:
                yield {"type": "proposal", **proposal}
    yield _turn_limit(max_turns)


def _adapter(provider):
    from .transport import stream_openai, stream_anthropic, TransportError
    if provider == "anthropic":
        return stream_anthropic
    if provider in ("openai", "litellm", "openrouter", "deepseek", "azure", "custom"):
        return stream_openai
    if provider in ("bedrock", "vertex"):
        from .cloud import stream_bedrock, stream_vertex
        return stream_bedrock if provider == "bedrock" else stream_vertex
    raise TransportError("unsupported", "Unsupported LLM provider.")


def _validate_response(response, schemas, force_tool=None):
    from .transport import TransportError
    blocks = response.get("blocks")
    if not isinstance(blocks, list) or not isinstance(response.get("stop_reason"), str):
        raise TransportError("protocol", "The provider returned an invalid final response.")
    known = {schema["name"]: schema["input_schema"] for schema in schemas}
    ids, names = set(), []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ("text", "tool_use"):
            raise TransportError("protocol", "The provider returned an unsupported response block.")
        if block["type"] == "text":
            if not isinstance(block.get("text"), str):
                raise TransportError("protocol", "The provider returned invalid text.")
            continue
        tool_id, name, args = block.get("id"), block.get("name"), block.get("input")
        if not isinstance(tool_id, str) or not tool_id or tool_id in ids or not isinstance(name, str) or name not in known or not isinstance(args, dict):
            raise TransportError("protocol", "The model returned an invalid tool call.")
        _validate_arguments(args, known[name])
        ids.add(tool_id)
        names.append(name)
    if force_tool and force_tool not in names:
        raise TransportError("unsupported", "The model did not call the required tool.")


def _valid_usage(usage):
    if usage is None or not isinstance(usage, dict) or any(
        not isinstance(usage.get(key), int) or isinstance(usage.get(key), bool) or usage[key] < 0
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    ):
        return None
    return usage


def _validate_arguments(value, schema):
    """Validate the JSON-schema subset used by our static tool definitions."""
    from .transport import TransportError
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    valid = kind is None or isinstance(value, types[kind])
    if kind in ("integer", "number") and isinstance(value, bool):
        valid = False
    if "enum" in schema and value not in schema["enum"]:
        valid = False
    if kind == "number" and isinstance(value, float) and not math.isfinite(value):
        valid = False
    if not valid:
        raise TransportError("protocol", "The model returned tool arguments that do not match the tool schema.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            raise TransportError("protocol", "The model omitted required tool arguments.")
        for key, item in value.items():
            if key in properties:
                _validate_arguments(item, properties[key])
            elif schema.get("additionalProperties") is False:
                raise TransportError("protocol", "The model returned unexpected tool arguments.")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate_arguments(item, schema["additionalProperties"])
    elif isinstance(value, list) and "items" in schema:
        for item in value:
            _validate_arguments(item, schema["items"])
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            raise TransportError("protocol", "The model returned out-of-range tool arguments.")


def _normalized_cost(config, usage):
    rates = config.price()
    if usage is None or rates is None:
        return None
    cache_rate = rates.get("cached_in", rates.get("cache_read", rates["in"])) if config.provider != "anthropic" else rates["cache_read"]
    return round((usage["input_tokens"] * rates["in"] + usage["output_tokens"] * rates["out"]
                  + usage["cache_read_input_tokens"] * cache_rate
                  + usage["cache_creation_input_tokens"] * rates.get("cache_write", rates["in"])) / 1_000_000, 6)


def _invalidate_metering(config):
    if config.config_id is None:
        return
    from apps.orgs.models import OrgAssistantConfig
    from django.db import transaction
    with transaction.atomic():
        cfg = OrgAssistantConfig.objects.select_for_update().filter(
            pk=config.config_id, org_id=config.org_id, config_revision=config.config_revision).first()
        if cfg is None:
            return
        checked = cfg.connection_check or {}
        checks = dict(checked.get("checks", {})) if checked.get("revision") == config.config_revision else {}
        checks["usage"] = {"ok": False, "message": "The provider omitted usage. Test the connection to verify metering."}
        cfg.connection_check = {"revision": config.config_revision, "checks": checks}
        cfg.save(update_fields=["connection_check"])


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
    """Decorate the private copy of each stored user message consistently."""
    out = []
    for message in messages:
        copy = dict(message)
        page = copy.get("page")
        if page and copy.get("role") == "user" and isinstance(copy.get("content"), str):
            copy["content"] += (f"\n\n[Context: the user is currently viewing {page}. "
                                "If the question refers to 'this report' or 'this chart', it means that page.]")
        out.append(copy)
    return out


def _connection_identity(config):
    # A fingerprint binds signed continuation without storing credentials.
    data = {"provider": config.provider, "model": config.model, "endpoint": config.base_url,
            "org": config.org_id, "config": config.config_id, "revision": config.config_revision,
            "auth": config.auth_mode, "cloud": config.cloud_config,
            "key": config.api_key, "credentials": config.cloud_credentials}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def _pricing_model(model: str) -> str:
    if "anthropic." in model:
        model = model.split("anthropic.", 1)[1]
    return model


def compute_cost_anthropic(usage: Any, model: str, config: LLMConfig | None = None) -> float | None:
    if usage is None:
        return None
    p = config.price() if config is not None else _list_price("anthropic", model)
    if not p:
        return None
    return round(
        (getattr(usage, "input_tokens", 0) or 0) * p["in"] / 1_000_000
        + (getattr(usage, "output_tokens", 0) or 0) * p["out"] / 1_000_000
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * p["cache_write"] / 1_000_000
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * p["cache_read"] / 1_000_000,
        6,
    )


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
    from .transport import normalize_error
    safe = normalize_error(e)
    logger.warning("assistant: provider call failed (%s)", safe.kind)
    return {
        "type": "error", "code": "provider",
        "message": "The model provider returned an error. Try again in a moment.",
        "detail": error_detail(safe, base_url),
    }


def error_detail(e: Exception, base_url: str = "") -> str:
    """Only safe transport messages may leave the runtime, even for admins."""
    from .transport import normalize_error
    safe = normalize_error(e)
    text = f"{safe.kind}: {safe}"
    host = urlsplit(base_url).hostname if base_url else None
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

    Missing usage is unknown cost even when the display counters remain zero.
    """

    def __init__(self, usage):
        self.available = usage is not None
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        self.input_tokens = max((getattr(usage, "prompt_tokens", 0) or 0) - cached, 0)
        self.output_tokens = getattr(usage, "completion_tokens", 0) or 0
        self.cache_read_input_tokens = cached
        self.cache_creation_input_tokens = 0


def compute_cost_openai(shim: _UsageShim, model: str, config: LLMConfig | None = None) -> float | None:
    if not shim.available:
        return None
    p = config.price() if config is not None else _list_price("openai", model)
    if not p:
        return None
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


# ---------------------------------------------------------------------------
# Connection test (the settings page's button)
# ---------------------------------------------------------------------------

_PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI"}


def mismatch_hints(config: LLMConfig) -> list[str]:
    """Cheap plausibility checks on provider vs key vs model, as hints only:
    a gateway may legitimately serve one vendor's model in the other's dialect,
    so none of this blocks a save. The key never leaves this function."""
    if config.provider not in ("anthropic", "openai"):
        return []
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

def list_models(config: LLMConfig, timeout: float = 10.0) -> list[dict]:
    """Discover saved-connection models; Azure catalogs are not deployments."""
    from .transport import TransportError, normalize_error, closing_client
    if config.provider == "azure":
        raise TransportError("unsupported", "Enter your Azure deployment name manually; model catalogs do not discover deployments.")
    try:
        _validate_credentials(config)
        if config.provider in ("bedrock", "vertex"):
            from .cloud import list_cloud_models
            return list_cloud_models(config, timeout=timeout)
        client = _anthropic_client(config) if config.provider == "anthropic" else _openai_client(config)
        with closing_client(client):
            page = client.models.list(timeout=timeout)
            return [{"id": model.id, "label": model.id} for model in page if isinstance(model.id, str)]
    except Exception as error:
        raise normalize_error(error) from None


def check_connection(config: LLMConfig, timeout: float = 45.0) -> dict:
    """Synthetic streamed chat/tool roundtrip, forced alert tool and metering."""
    from .transport import TransportError, normalize_error
    started = time.monotonic()
    deadline = started + timeout
    checks, metered = {}, []
    schema = {"name": "connection_probe", "description": "Echo a synthetic connection-test value.",
              "input_schema": {"type": "object", "properties": {"value": {"type": "string", "enum": ["probe"]}},
                               "required": ["value"], "additionalProperties": False}}
    system = [{"type": "text", "text": "This is a synthetic connection test. Follow its tool instructions exactly."}]
    def probe(messages, purpose="chat", forced=None):
        response, streamed, counted = None, False, False
        try:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TransportError("connection", "The connection test deadline expired.")
            for event in _adapter(config.provider)(config, messages, system, [schema],
                    max_tokens=DEFAULT_MAX_TOKENS, force_tool=forced, timeout=left, purpose=purpose):
                if time.monotonic() >= deadline:
                    raise TransportError("connection", "The connection test deadline expired.")
                if event.get("type") == "text_delta" and event.get("text"):
                    streamed = True
                elif event.get("type") == "response":
                    if response is not None:
                        raise TransportError("protocol", "The provider returned multiple final responses.")
                    response = event
            if response is None:
                raise TransportError("protocol", "The stream ended before a final response.")
            metered.append(_valid_usage(response.get("usage")) is not None)
            counted = True
            _validate_response(response, [schema], forced)
            return response, streamed
        except Exception as error:
            safe = normalize_error(error)
            if not counted and safe.kind != "unsupported":
                metered.append(False)
            raise safe from None
    original_prompt = "Call connection_probe with value probe, then say OK after the tool result."
    try:
        first, _ = probe([{"role": "user", "content": original_prompt}])
        calls = [b for b in first["blocks"] if b["type"] == "tool_use"]
        if not calls:
            raise TransportError("unsupported", "The model did not call the synthetic chat tool.")
        assistant = {"role": "assistant", "content": first["blocks"]}
        if first.get("provider_data"):
            assistant["_provider"] = first["provider_data"]
        history = [{"role": "user", "content": original_prompt}, assistant]
        history.extend({"role": "tool", "tool_use_id": call["id"], "name": call["name"], "input": call["input"],
                        "result": "Synthetic probe succeeded. Now say OK without any more tools."} for call in calls)
        final, streamed = probe(history)
        if not streamed or not any(b["type"] == "text" and b["text"] for b in final["blocks"]):
            raise TransportError("unsupported", "The model did not stream a chat answer after the tool result.")
        if any(b["type"] == "tool_use" for b in final["blocks"]):
            raise TransportError("unsupported", "The model did not complete the synthetic tool roundtrip.")
        checks["chat"] = {"ok": True, "message": "Streaming and the synthetic tool roundtrip succeeded."}
    except Exception as error:
        safe = normalize_error(error)
        checks["chat"] = {"ok": False, "message": str(safe)}
    try:
        probe([{"role": "user", "content": "Call connection_probe with value probe."}], "alert", "connection_probe")
        checks["alerts"] = {"ok": True, "message": "The forced synthetic alert tool succeeded."}
    except Exception as error:
        safe = normalize_error(error)
        checks["alerts"] = {"ok": False, "message": str(safe)}
    if not checks["chat"]["ok"] and checks["alerts"]["ok"]:
        checks["alerts"] = {"ok": False, "message": "The forced tool succeeded, but alerts also require streaming and the chat tool roundtrip."}
    usage_ok = bool(metered) and all(metered)
    checks["usage"] = {"ok": usage_ok, "message": "Every probe returned usage." if usage_ok else "One or more probes omitted usage or did not complete. Budgeted inference requires metering."}
    return {"ok": checks["chat"]["ok"], "message": f"{config.model} answered." if checks["chat"]["ok"] else checks["chat"]["message"],
            "latency_ms": int((time.monotonic() - started) * 1000), "checks": checks,
            "hints": mismatch_hints(config)}


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
        return {}
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
        for block in reversed(new_content):
            if isinstance(block, dict) and block.get("type") in ("text", "tool_use", "tool_result"):
                block["cache_control"] = {"type": "ephemeral"}
                break
        last["content"] = new_content
    out[-1] = last
    return out
