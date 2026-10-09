"""LLMConfig resolution + availability matrix (per org, bring-your-own-key)."""
import pytest

from apps.assistant.llm import GATEWAY_PLACEHOLDER_KEY, LLMConfig, is_available
from apps.orgs.models import OrgAssistantConfig

from .conftest import FAKE_KEY

pytestmark = pytest.mark.django_db


class TestForOrg:
    def test_no_config_row(self, org):
        assert LLMConfig.for_org(org) is None
        ok, reason = is_available(org)
        assert ok is False
        assert "not configured" in reason

    def test_disabled(self, org, make_assistant_config):
        make_assistant_config(org, enabled=False)
        assert LLMConfig.for_org(org) is None
        ok, reason = is_available(org)
        assert ok is False
        assert "disabled" in reason

    def test_enabled_without_key(self, org, make_assistant_config):
        make_assistant_config(org, api_key="")
        config = LLMConfig.for_org(org)
        assert config.api_key == ""
        ok, reason = is_available(org)
        assert ok is False
        assert "API key" in reason

    def test_configured(self, org, assistant_config):
        config = LLMConfig.for_org(org)
        assert config.provider == "anthropic"
        assert config.api_key == FAKE_KEY
        assert config.model == "claude-sonnet-4-6"
        assert is_available(org) == (True, "")

    def test_unsupported_provider(self, org, make_assistant_config):
        cfg = make_assistant_config(org)
        OrgAssistantConfig.objects.filter(pk=cfg.pk).update(provider="bedrock")
        ok, reason = is_available(org)
        assert ok is False
        assert "Unsupported LLM provider" in reason

    def test_model_defaults_per_provider(self, org, make_assistant_config):
        make_assistant_config(org, provider="openai", model="")
        assert LLMConfig.for_org(org).model == "gpt-4o"
        make_assistant_config(org, provider="anthropic", model="")
        assert LLMConfig.for_org(org).model == "claude-sonnet-4-6"

    def test_gateway_without_key_is_available(self, org, make_assistant_config):
        """A gateway on the internal network legitimately has no key."""
        make_assistant_config(org, api_key="", base_url="http://llm-gateway.internal/v1")
        config = LLMConfig.for_org(org)
        assert config.api_key == GATEWAY_PLACEHOLDER_KEY
        assert config.base_url == "http://llm-gateway.internal/v1"
        assert is_available(org)[0] is True

    def test_no_org(self):
        assert LLMConfig.for_org(None) is None
        assert is_available(None)[0] is False


class TestPricingFailsClosed:
    """Budgets require prices; uncapped orgs may still use unknown models."""

    def test_unknown_model_is_unavailable(self, org, make_assistant_config):
        make_assistant_config(org, model="claude-future-9", monthly_budget_usd=25)
        ok, reason = is_available(org)
        assert ok is False
        assert reason == ("Set input and output prices for model 'claude-future-9' in AI settings "
                          "to enforce budgets, or leave both budgets blank.")

    @pytest.mark.parametrize("budget", ["monthly_budget_usd", "per_user_budget_usd"])
    @pytest.mark.parametrize("amount", [0, 25])
    def test_any_configured_budget_requires_price(self, org, make_assistant_config, budget, amount):
        make_assistant_config(org, model="claude-future-9", **{budget: amount})
        assert is_available(org)[0] is False

    def test_unknown_model_available_when_budgets_are_blank(self, org, make_assistant_config):
        make_assistant_config(org, model="claude-future-9")
        assert is_available(org) == (True, "")

    def test_price_override_makes_it_available_and_charged(self, org, make_assistant_config):
        from decimal import Decimal
        from types import SimpleNamespace

        from apps.assistant.llm import compute_cost_anthropic

        make_assistant_config(
            org, model="claude-future-9",
            price_in_per_mtok=Decimal("2"), price_out_per_mtok=Decimal("10"),
        )
        assert is_available(org) == (True, "")
        config = LLMConfig.for_org(org)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=500,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        # 1000 in @ $2/MTok + 500 out @ $10/MTok
        assert compute_cost_anthropic(usage, config.model, config) == pytest.approx(0.007)

    def test_explicit_zero_prices_are_valid(self, org, make_assistant_config):
        from decimal import Decimal
        from types import SimpleNamespace
        from apps.assistant.llm import compute_cost_anthropic

        make_assistant_config(org, model="claude-free", price_in_per_mtok=Decimal("0"),
                              price_out_per_mtok=Decimal("0"), monthly_budget_usd=0)
        config = LLMConfig.for_org(org)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        assert compute_cost_anthropic(usage, config.model, config) == 0
        assert is_available(org) == (True, "")

    def test_provider_costs_preserve_unknown(self):
        from types import SimpleNamespace
        from apps.assistant.llm import _UsageShim, compute_cost_anthropic, compute_cost_openai

        usage = SimpleNamespace(input_tokens=1, output_tokens=1,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        assert compute_cost_anthropic(usage, "claude-future-9") is None
        assert compute_cost_openai(_UsageShim(SimpleNamespace(prompt_tokens=1, completion_tokens=1)),
                                   "gpt-future-9") is None

    def test_unknown_cost_is_sticky_and_keeps_tokens(self):
        from types import SimpleNamespace
        from apps.assistant.llm import add_usage

        state = {}
        add_usage(state, SimpleNamespace(input_tokens=4, output_tokens=2), None)
        add_usage(state, SimpleNamespace(input_tokens=3, output_tokens=1), 0.5)
        assert state["usage"] == {
            "input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "cost_usd": None,
        }


class TestKeySecrecy:
    def test_key_absent_from_repr_and_str(self, org, assistant_config):
        config = LLMConfig.for_org(org)
        assert FAKE_KEY not in repr(config)
        assert FAKE_KEY not in str(config)
        assert "anthropic" in repr(config)

    def test_key_absent_from_availability_reason(self, org, make_assistant_config):
        make_assistant_config(org, api_key="")
        assert FAKE_KEY not in is_available(org)[1]


class TestOpenAIStreamAsksForUsage:
    """A streamed OpenAI completion carries no usage block unless asked.

    Without it ``response.usage`` is None, every turn books zero spend, and
    the per-org and per-user budget caps never fire -- a silent failure on
    the money path, which is why it is asserted rather than assumed.
    """

    def test_stream_options_include_usage_is_sent(self, monkeypatch):
        from apps.assistant import llm

        captured = {}

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter(())

            def get_final_completion(self):
                raise AssertionError("not reached: the call args are the subject")

        class Completions:
            def stream(self, **kwargs):
                captured.update(kwargs)
                return Stream()

        class Client:
            chat = type("Chat", (), {"completions": Completions()})()

        monkeypatch.setattr(llm, "_openai_client", lambda config: Client())
        config = llm.LLMConfig(provider="openai", api_key="k", model="gpt-4o")

        gen = llm.stream_turn(
            config, {}, "hi",
            toolbox=type("TB", (), {"schemas": []})(),
            system_blocks=[{"type": "text", "text": "sys"}],
        )
        list(gen)  # drain; the AssertionError above surfaces as an error event

        assert captured["stream_options"] == {"include_usage": True}

    @pytest.mark.parametrize("model,expected_cost", [("gpt-future-9", None), ("gpt-4o", 0.007375)])
    def test_stream_completes_with_known_or_unknown_price(self, monkeypatch, model, expected_cost):
        from types import SimpleNamespace

        from apps.assistant import llm

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter([SimpleNamespace(type="content.delta", delta="Hello")])

            def get_final_completion(self):
                return SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=1000, completion_tokens=500,
                        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
                    ),
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="Hello", tool_calls=[]), finish_reason="stop",
                    )],
                )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(stream=lambda **kw: Stream())))
        monkeypatch.setattr(llm, "_openai_client", lambda config: client)
        state = {}
        events = list(llm.stream_turn(
            llm.LLMConfig(provider="openai", api_key="fake-key", model=model), state, "Hi",
            toolbox=SimpleNamespace(schemas=[]), system_blocks=[{"type": "text", "text": "Test"}],
        ))
        assert [event["type"] for event in events] == ["text_delta", "usage", "done"]
        assert events[1]["cost_delta_usd"] == expected_cost
        assert events[1]["session_cost_usd"] == expected_cost
        assert state["usage"]["cost_usd"] == expected_cost
        assert state["usage"]["input_tokens"] == 900
        assert state["usage"]["cache_read_input_tokens"] == 100
        assert state["transcript"][-1]["content"] == [{"type": "text", "text": "Hello"}]
