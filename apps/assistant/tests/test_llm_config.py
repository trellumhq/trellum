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
    """A model the price list does not know is refused, not billed as free."""

    def test_unknown_model_is_unavailable(self, org, make_assistant_config):
        make_assistant_config(org, model="claude-future-9")
        ok, reason = is_available(org)
        assert ok is False
        assert reason == "Set a price for model 'claude-future-9' in the AI assistant settings"

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
