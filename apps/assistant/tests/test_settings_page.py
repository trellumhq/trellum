"""The org-admin AI assistant settings page (bring your own key)."""
from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.orgs.models import OrgAssistantConfig

pytestmark = pytest.mark.django_db


@pytest.fixture
def url(org):
    return f"/orgs/{org.slug}/settings/assistant"


def form_data(**overrides):
    data = {
        "enabled": "on",
        "provider": "anthropic",
        "api_key": "sk-first-key",
        "model": "claude-sonnet-4-6",
        "base_url": "",
        "monthly_budget_usd": "100",
        "per_user_budget_usd": "10",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


class TestSave:
    def test_invalid_native_save_keeps_saved_pricing_status_and_draft(self, login, org_admin, org, url):
        cfg = OrgAssistantConfig.objects.create(
            org=org, enabled=True, api_key="saved-key", model="claude-future-9",
            price_in_per_mtok=Decimal("2"), price_out_per_mtok=Decimal("10"),
        )
        response = login(org_admin).post(url, form_data(
            api_key="", model=cfg.model, price_in_per_mtok="99", price_out_per_mtok="",
        ))
        assert response.status_code == 200
        assert response.context["form"].errors
        assert response.context["form"]["price_in_per_mtok"].value() == "99"
        assert response.context["pricing_state"] == "Manual prices are set; cost estimates are available."
        assert response.context["readiness_ok"] is True
        cfg.refresh_from_db()
        assert cfg.price_in_per_mtok == Decimal("2")
        assert cfg.price_out_per_mtok == Decimal("10")

    def test_creates_config(self, login, org_admin, org, url):
        resp = login(org_admin).post(url, form_data())
        assert resp.status_code == 302
        cfg = OrgAssistantConfig.objects.get(org=org)
        assert cfg.enabled is True
        assert cfg.api_key == "sk-first-key"
        assert cfg.monthly_budget_usd == Decimal("100.00")
        assert cfg.per_user_budget_usd == Decimal("10.00")

    def test_blank_key_keeps_stored_one(self, login, org_admin, org, url):
        c = login(org_admin)
        c.post(url, form_data())
        c.post(url, form_data(api_key="", model="claude-haiku-4-5-20251001"))
        cfg = OrgAssistantConfig.objects.get(org=org)
        assert cfg.api_key == "sk-first-key"
        assert cfg.model == "claude-haiku-4-5-20251001"

    def test_key_is_never_rendered_back(self, login, org_admin, org, url):
        c = login(org_admin)
        c.post(url, form_data())
        html = c.get(url).content.decode()
        assert "sk-first-key" not in html
        assert "a key is stored" in html

    def test_key_encrypted_at_rest(self, login, org_admin, org, url):
        from django.db import connection

        login(org_admin).post(url, form_data())
        with connection.cursor() as cur:
            cur.execute("SELECT api_key FROM orgs_orgassistantconfig")
            stored = cur.fetchone()[0]
        assert "sk-first-key" not in stored

    def test_enabling_without_any_credential_is_rejected(self, login, org_admin, org, url):
        resp = login(org_admin).post(url, form_data(api_key=""))
        assert resp.status_code == 200  # re-rendered with the error
        assert not OrgAssistantConfig.objects.filter(org=org).exists()

    def test_gateway_url_counts_as_credential(self, login, org_admin, org, url):
        resp = login(org_admin).post(
            url, form_data(api_key="", base_url="http://gateway.internal/v1")
        )
        assert resp.status_code == 302
        assert OrgAssistantConfig.objects.get(org=org).enabled is True

    def test_blank_budgets_mean_no_cap(self, login, org_admin, org, url):
        login(org_admin).post(url, form_data(monthly_budget_usd="", per_user_budget_usd=""))
        cfg = OrgAssistantConfig.objects.get(org=org)
        assert cfg.monthly_budget_usd is None
        assert cfg.per_user_budget_usd is None

    def test_unlisted_model_saves_with_both_budgets_blank(self, login, org_admin, org, url):
        response = login(org_admin).post(
            url,
            form_data(model="claude-future-9", monthly_budget_usd="", per_user_budget_usd=""),
        )
        assert response.status_code == 302
        assert OrgAssistantConfig.objects.get(org=org).model == "claude-future-9"
        from apps.assistant.llm import is_available
        assert is_available(org) == (True, "")
        assert "Cost estimates are unavailable without pricing" in login(org_admin).get(url).content.decode()

    def test_enhanced_save_returns_saved_readiness_and_opens_pricing_when_capped(self, login, org_admin, org, url):
        response = login(org_admin).post(
            url,
            form_data(model="claude-future-9", monthly_budget_usd="10"),
            HTTP_X_TRELLUM_FORM="1",
            HTTP_ACCEPT="application/json",
        )
        payload = response.json()
        assert response.status_code == 200
        assert payload["assistant_ready"] is False
        assert payload["assistant_open_pricing"] is True
        assert payload["updates"]["assistant-readiness-reason"] == (
            "Set input and output prices for model 'claude-future-9' in AI settings "
            "to enforce budgets, or leave both budgets blank."
        )
        assert "Cost estimates are unavailable" in payload["updates"]["assistant-pricing-state"]

    def test_save_is_audited(self, login, org_admin, org, url):
        login(org_admin).post(url, form_data())
        entry = AuditLog.objects.get(action="assistant.config.update")
        assert entry.org == org
        assert entry.actor == org_admin
        assert entry.metadata["enabled"] is True
        assert "sk-first-key" not in str(entry.metadata)


class TestMismatchHints:
    KEY_A = "This key looks like an Anthropic key, but the provider is OpenAI."
    KEY_O = "This key looks like an OpenAI key, but the provider is Anthropic."
    MODEL_A = "This model looks like an Anthropic model, but the provider is OpenAI."
    MODEL_O = "This model looks like an OpenAI model, but the provider is Anthropic."

    def test_sentences(self):
        from apps.assistant.llm import LLMConfig, mismatch_hints as hints

        assert hints(LLMConfig("openai", api_key="sk-ant-api03-x", model="claude-sonnet-4-6")) == [self.KEY_A, self.MODEL_A]
        assert hints(LLMConfig("anthropic", api_key="sk-proj-x", model="gpt-4o")) == [self.KEY_O, self.MODEL_O]
        assert hints(LLMConfig("anthropic", api_key="sk-x", model="o3-mini")) == [self.KEY_O, self.MODEL_O]
        assert hints(LLMConfig("anthropic", api_key="sk-ant-api03-x", model="claude-sonnet-4-6")) == []
        assert hints(LLMConfig("openai", api_key="gateway-no-key", model="gpt-4o")) == []

    def test_save_flashes_the_hint_without_the_key(self, login, org_admin, org, url):
        c = login(org_admin)
        resp = c.post(url, form_data(provider="openai", api_key="sk-ant-api03-secret", model="claude-sonnet-4-6"), follow=True)
        html = resp.content.decode()
        assert self.KEY_A in html and self.MODEL_A in html
        assert "sk-ant-api03-secret" not in html
        assert OrgAssistantConfig.objects.get(org=org).enabled is True  # a hint, not a rejection

    def test_model_hint_sentences_ride_the_page(self, login, org_admin, url):
        html = login(org_admin).get(url).content.decode()
        assert 'id="model-hint"' in html and self.MODEL_A in html and self.MODEL_O in html


class TestEndpointCopy:
    def test_label_and_both_dialect_sentences_ride_the_page(self, login, org_admin, url):
        html = login(org_admin).get(url).content.decode()
        assert "Custom endpoint URL" in html
        assert 'id="gateway-hint"' in html and 'id="gateway-hint" hidden' not in html
        assert "Must implement the Anthropic Messages API, for example a LiteLLM proxy in Anthropic mode." in html
        assert "Any OpenAI-compatible server works: LiteLLM, Azure OpenAI, vLLM, Ollama, or a corporate gateway." in html

    def test_pricing_help_explains_optional_uncapped_use(self, login, org_admin, url):
        html = login(org_admin).get(url).content.decode()
        assert "Optional with both budgets blank" in html
        assert "required for unlisted models when either budget is set" in html
        assert "Set both prices or leave both blank" in html


class TestAdvancedFold:
    def test_opens_only_when_something_is_in_it(self, login, org_admin, url):
        c = login(org_admin)
        assert "<details>" in c.get(url).content.decode()
        c.post(url, form_data(base_url="https://gw.example.com/v1"))
        assert "<details open>" in c.get(url).content.decode()


class _Req:
    """Only what error_detail reads off an SDK exception's request."""

    def __init__(self, url):
        self.url = url


class AuthenticationError(Exception):
    """Named for the SDK class: check_connection maps on __mro__ names."""

    def __init__(self, message, url, body=None):
        super().__init__(message)
        self.request = _Req(url)
        self.body = body


class TestConnectionTest:
    """The Test connection button: one tiny call through the SAVED config."""

    @pytest.fixture
    def test_url(self, url):
        return url + "/test"

    def test_nothing_saved_yet(self, login, org_admin, test_url):
        j = login(org_admin).post(test_url).json()
        assert j == {
            "ok": False, "message": "Save the settings first.", "latency_ms": 0,
            "assistant_available": False,
            "assistant_reason": "The AI assistant is not configured for this organization.",
        }

    def test_member_cannot_probe(self, login, member, test_url):
        assert login(member).post(test_url).status_code == 403

    def test_failures_become_a_sentence(self, login, org_admin, org, test_url, monkeypatch):
        from apps.assistant import llm

        OrgAssistantConfig.objects.create(
            org=org, enabled=True, api_key="k", base_url="https://gw.example.com/v1"
        )

        class ForeignStream:  # the incident: a 200 that is not a Messages stream
            text_stream = ()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                raise AssertionError()

        def rejected(**kwargs):
            raise AuthenticationError(
                "invalid x-api-key", "https://gw.example.com/v1/messages"
            )

        cases = [
            (rejected, "The provider rejected the key.", "AuthenticationError: invalid x-api-key (gw.example.com)"),
            (lambda **kw: ForeignStream(),
             "gw.example.com did not answer like an Anthropic endpoint — check the provider and custom endpoint URL.",
             "AssertionError (gw.example.com)"),
        ]
        for stream, message, detail in cases:
            class Client:
                messages = type("M", (), {"stream": staticmethod(stream)})

            monkeypatch.setattr(llm, "_anthropic_client", lambda config, c=Client: c())
            j = login(org_admin).post(test_url).json()
            assert (j["ok"], j["message"], j["detail"]) == (False, message, detail)
            assert isinstance(j["latency_ms"], int)

    def test_detail_carries_the_provider_sentence_with_the_key_redacted(
        self, login, org_admin, org, test_url, monkeypatch
    ):
        # An OpenAI 401 echoes the masked key inside a dict dump. The admin
        # gets the provider's sentence, never any of the key -- on the test
        # button and on the chat stream's error frame alike.
        from apps.assistant import llm

        OrgAssistantConfig.objects.create(
            org=org, enabled=True, provider="openai", api_key="sk-ant-api03-secret", model="claude-sonnet-4-6"
        )
        # The body shape one SDK keeps on a 401: the sentence sits under
        # "error", and it echoes the masked key back at you.
        body = {"error": {"message": (
            "Incorrect API key provided: sk-ant-a*****…JgAA. You can find your API key "
            "at https://platform.openai.com/account/api-keys."
        ), "type": "invalid_request_error", "param": None, "code": "invalid_api_key"}}
        err = AuthenticationError(
            f"Error code: 401 - {body}",
            "https://api.openai.com/v1/chat/completions",
            body=body,
        )

        def rejected(**kwargs):
            raise err

        class Client:
            chat = type("C", (), {"completions": type("CC", (), {"stream": staticmethod(rejected)})})

        monkeypatch.setattr(llm, "_openai_client", lambda config: Client())
        j = login(org_admin).post(test_url).json()
        assert j["message"] == "The provider rejected the key."
        assert j["detail"] == (
            "AuthenticationError: Incorrect API key provided: sk-…. You can find your API key "
            "at https://platform.openai.com/account/api-keys. (api.openai.com)"
        )
        for detail in (j["detail"], llm._provider_error(err)["detail"]):
            assert "sk-ant-a" not in detail and "******" not in detail and "{" not in detail
        # The stored key and the model are judged server-side; the key itself stays there.
        assert j["hints"] == [TestMismatchHints.KEY_A, TestMismatchHints.MODEL_A]
        assert "sk-ant-api03" not in str(j)

    def test_success_is_tiny_and_not_billed(self, login, org_admin, org, test_url, monkeypatch):
        from apps.assistant import llm
        from apps.assistant.models import LlmUsage
        from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, TextBlock

        OrgAssistantConfig.objects.create(org=org, enabled=True, api_key="k")
        calls = []
        monkeypatch.setattr(
            llm, "_anthropic_client",
            lambda config: FakeClient([FakeResponse([TextBlock("OK")])], calls),
        )
        j = login(org_admin).post(test_url).json()
        assert j["ok"] is True and j["message"] == "claude-sonnet-4-6 answered."
        assert j["assistant_available"] is True and j["assistant_reason"] == ""
        assert calls[0]["max_tokens"] == 8 and calls[0]["timeout"] == 20
        assert calls[0]["messages"] == [{"role": "user", "content": "Say OK"}]
        assert not LlmUsage.objects.exists()  # exempt from the ledger by design

    def test_successful_probe_still_reports_unknown_model_blocker(
        self, login, org_admin, org, test_url, monkeypatch
    ):
        from apps.assistant import llm
        from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, TextBlock

        OrgAssistantConfig.objects.create(
            org=org, enabled=True, api_key="secret-key", model="claude-future-9",
            monthly_budget_usd=Decimal("10"),
        )
        calls = []
        monkeypatch.setattr(
            llm, "_anthropic_client",
            lambda config: FakeClient([FakeResponse([TextBlock("OK")])], calls),
        )
        result = login(org_admin).post(test_url).json()
        assert result["ok"] is True
        assert result["assistant_available"] is False
        assert result["assistant_reason"] == "Set input and output prices for model 'claude-future-9' in AI settings to enforce budgets, or leave both budgets blank."
        assert calls and "secret-key" not in str(result)

    def test_settings_page_explains_saved_config_readiness(self, login, org_admin, org, url):
        OrgAssistantConfig.objects.create(
            org=org, enabled=True, api_key="secret-key", model="claude-future-9",
            per_user_budget_usd=Decimal("10"),
        )
        html = login(org_admin).get(url).content.decode()
        assert "Set input and output prices for model &#x27;claude-future-9&#x27; in AI settings to enforce budgets, or leave both budgets blank." in html
        assert "<details open>" in html
        assert "secret-key" not in html

    def test_disabled_config_is_still_probed(
        self, login, org_admin, org, test_url, monkeypatch
    ):
        from apps.assistant import llm
        from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, TextBlock

        OrgAssistantConfig.objects.create(org=org, enabled=False, api_key="secret-key")
        calls = []
        monkeypatch.setattr(
            llm, "_anthropic_client",
            lambda config: FakeClient([FakeResponse([TextBlock("OK")])], calls),
        )
        result = login(org_admin).post(test_url).json()
        assert result["ok"] is True and calls
        assert result["assistant_available"] is False
        assert result["assistant_reason"] == "The AI assistant is disabled for this organization."


class TestAccess:
    def test_member_cannot_open(self, login, member, url):
        assert login(member).get(url).status_code == 403

    def test_member_cannot_post(self, login, member, org, url):
        assert login(member).post(url, form_data()).status_code == 403
        assert not OrgAssistantConfig.objects.filter(org=org).exists()

    def test_anonymous_redirected_to_login(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 302
        assert resp.url.startswith("/login")

    def test_outsider_gets_404(self, login, make_user, other_org, url):
        outsider = make_user("spy@rival.com", org=other_org)
        assert login(outsider).get(url).status_code == 404

    def test_admin_sees_month_spend(self, login, org_admin, org, url, make_user):
        from apps.assistant.models import LlmUsage

        OrgAssistantConfig.objects.create(org=org, enabled=True, api_key="k")
        spender = make_user("spender@demo.example", org=org)
        LlmUsage.add_cost(org, spender, Decimal("1.25"))
        html = login(org_admin).get(url).content.decode()
        assert "Recorded spend this month" in html
        assert "Totals exclude usage without known prices" in html
        assert "spender@demo.example" in html
        assert "1.25" in html
