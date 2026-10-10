"""Connection identities, encrypted secrets and revision-bound provider settings."""
import json
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.assistant.provider_registry import PROVIDERS, effective_base_url, workload_allowed
from apps.orgs.forms import AssistantConfigForm
from apps.orgs.models import OrgAssistantConfig

pytestmark = pytest.mark.django_db


def data(**changes):
    values = {"enabled": "on", "provider": "openai", "auth_mode": "api_key", "api_key": "new-key", "model": "gpt-4o"}
    values.update(changes)
    return values


def form(org, cfg=None, **changes):
    return AssistantConfigForm(data(**changes), instance=cfg, org=org)


def test_registry_defaults_and_workload_allowlist(org, settings):
    assert set(PROVIDERS) == {"anthropic", "openai", "litellm", "openrouter", "deepseek", "azure", "bedrock", "vertex", "custom"}
    assert effective_base_url("openai", "") == "https://api.openai.com/v1"
    assert effective_base_url("custom", "https://gateway.example/v1/") == "https://gateway.example/v1"
    assert not workload_allowed("bedrock", org.pk)
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {"bedrock": [str(org.pk)]}
    assert workload_allowed("bedrock", org.pk)
    assert not workload_allowed("azure", org.pk)
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {"bedrock": str(org.pk)}
    assert not workload_allowed("bedrock", org.pk)


def test_key_preserved_only_for_same_resolved_identity(org):
    cfg = OrgAssistantConfig.objects.create(org=org, provider="openai", api_key="stored")
    f = form(org, cfg, api_key="", base_url="https://api.openai.com/v1/", model="gpt-new")
    assert f.is_valid(), f.errors
    f.save(); cfg.refresh_from_db()
    assert cfg.api_key == "stored"
    assert cfg.config_revision == 2
    for changes in ({"provider": "openrouter"}, {"base_url": "https://other.example/v1"}):
        cfg.refresh_from_db()
        f = form(org, cfg, api_key="", **changes)
        assert not f.is_valid()
        assert "api_key" in f.errors


def test_none_requires_explicit_endpoint(org):
    f = form(org, auth_mode="none", api_key="")
    assert not f.is_valid() and "base_url" in f.errors
    f = form(org, auth_mode="none", api_key="", base_url="http://gateway.internal/v1")
    assert f.is_valid(), f.errors
    assert f.save().api_key == ""


@pytest.mark.parametrize("provider", ["openrouter", "deepseek", "custom", "litellm", "azure", "bedrock", "vertex"])
def test_new_provider_requires_model(org, provider):
    f = form(org, provider=provider, model="")
    assert not f.is_valid() and "model" in f.errors


@pytest.mark.parametrize("url", ["http://x.openai.azure.com", "https://x.openai.azure.com.evil.example", "https://key@x.openai.azure.com", "https://x.openai.azure.com/?secret=x", "https://x.openai.azure.com/deployments/x", "https://x.openai.azure.com:8443", "https://x.openai.azure.com:bad", "https://nested.x.openai.azure.com"])
def test_azure_rejects_untrusted_endpoints(org, url):
    f = form(org, provider="azure", base_url=url)
    assert not f.is_valid() and "base_url" in f.errors


def test_cloud_credentials_encrypted_write_only_and_replaced_on_region_change(org):
    values = {"provider": "bedrock", "model": "model-id", "auth_mode": "access_key", "api_key": "",
              "region": "eu-west-1", "access_key_id": "access-id", "secret_access_key": "secret-test"}
    f = form(org, **values)
    assert f.is_valid(), f.errors
    cfg = f.save()
    with connection.cursor() as cursor:
        cursor.execute("SELECT cloud_credentials FROM orgs_orgassistantconfig WHERE id = %s", [cfg.pk])
        stored = cursor.fetchone()[0]
    assert stored.startswith("enc$1$") and "secret-test" not in stored
    unbound = AssistantConfigForm(instance=cfg, org=org)
    assert "secret-test" not in unbound.as_p() and "access-id" not in unbound.as_p()
    values.update(access_key_id="", secret_access_key="")
    f = form(org, cfg, **values)
    assert f.is_valid(), f.errors
    assert f.save().cloud_credentials["secret_access_key"] == "secret-test"
    values["region"] = "us-east-1"
    f = form(org, cfg, **values)
    assert not f.is_valid() and "secret_access_key" in f.errors


def test_vertex_rejects_external_credentials_and_never_echoes_json(org):
    account = {"type": "service_account", "private_key": "private-test", "client_email": "bot@example.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token", "universe_domain": "googleapis.com"}
    values = dict(provider="vertex", model="gemini-model", auth_mode="service_account", project="test-project", location="europe-west4", service_account=json.dumps(account))
    f = form(org, **values)
    assert f.is_valid(), f.errors
    cfg = f.save()
    assert "private-test" not in f.as_p()
    assert cfg.cloud_credentials["service_account"]["token_uri"] == "https://oauth2.googleapis.com/token"
    for kind, uri in (("external_account", "https://oauth2.googleapis.com/token"), ("service_account", "https://evil.example/token")):
        account.update(type=kind, token_uri=uri)
        f = form(org, cfg, **(values | {"service_account": json.dumps(account)}))
        assert not f.is_valid() and "service_account" in f.errors


def test_workload_requires_operator_authorization_and_can_be_disabled(org, settings):
    values = dict(provider="bedrock", auth_mode="workload", region="eu-west-1", model="model-id", api_key="")
    f = form(org, **values)
    assert not f.is_valid() and "auth_mode" in f.errors
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {"bedrock": [str(org.pk)]}
    f = form(org, **values)
    assert f.is_valid(), f.errors
    cfg = f.save()
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {}
    f = form(org, cfg, **(values | {"enabled": ""}))
    assert f.is_valid(), f.errors
    assert not f.save().enabled


def test_can_disable_broken_connection_without_replacement(org):
    cfg = OrgAssistantConfig.objects.create(org=org, enabled=True, provider="vertex", auth_mode="service_account")
    f = form(org, cfg, enabled="", provider="vertex", auth_mode="service_account", api_key="", model="")
    assert f.is_valid(), f.errors
    assert not f.save().enabled


def test_pricing_save_preserves_concurrent_failed_usage_check(org):
    cfg = OrgAssistantConfig.objects.create(org=org, provider="openai", api_key="stored", model="gpt-4o",
        connection_check={"revision": 1, "checks": {"usage": {"ok": True}}})
    f = form(org, cfg, api_key="", price_in_per_mtok="2", price_out_per_mtok="4")
    assert f.is_valid(), f.errors
    failed = {"revision": 1, "checks": {"usage": {"ok": False, "message": "Missing usage"}}}
    OrgAssistantConfig.objects.filter(pk=cfg.pk).update(connection_check=failed)
    f.save(); cfg.refresh_from_db()
    assert cfg.config_revision == 1 and cfg.connection_check == failed


def test_stale_save_rejected_without_overwriting(org):
    cfg = OrgAssistantConfig.objects.create(org=org, provider="openai", api_key="stored")
    f = form(org, cfg, api_key="", model="new-model")
    assert f.is_valid(), f.errors
    OrgAssistantConfig.objects.filter(pk=cfg.pk).update(config_revision=2)
    with pytest.raises(ValidationError):
        f.save()
    cfg.refresh_from_db()
    assert cfg.model == ""
    assert not form(org, cfg, config_revision=1).is_valid()


def test_probe_result_not_persisted_after_connection_changes(login, org_admin, org, monkeypatch):
    from apps.assistant import llm
    cfg = OrgAssistantConfig.objects.create(org=org, provider="openai", api_key="stored")
    def check(config):
        OrgAssistantConfig.objects.filter(pk=cfg.pk).update(config_revision=2)
        return {"ok": True, "message": "Success", "checks": {"chat": {"ok": True}}}
    monkeypatch.setattr(llm, "check_connection", check)
    response = login(org_admin).post(f"/orgs/{org.slug}/settings/assistant/test")
    assert response.json()["stale"] and response.json()["config_revision"] == 1
    cfg.refresh_from_db()
    assert cfg.connection_check == {}


def test_discovery_uses_saved_credentials_and_manual_errors_are_safe(login, org_admin, org, monkeypatch):
    from apps.assistant import llm
    cfg = OrgAssistantConfig.objects.create(org=org, provider="openai", api_key="stored")
    def discover(config):
        assert config.api_key == "stored"
        return [{"id": "model-x", "label": "Model X"}]
    monkeypatch.setattr(llm, "list_models", discover, raising=False)
    client = login(org_admin)
    url = f"/orgs/{org.slug}/settings/assistant/models"
    assert client.post(url).json()["models"][0]["id"] == "model-x"
    def failure(config):
        raise ValueError("secret-stored credential URL")
    monkeypatch.setattr(llm, "list_models", failure)
    payload = client.post(url).json()
    assert not payload["ok"] and "manually" in payload["message"] and "secret-stored" not in str(payload)


def test_discovery_and_testing_require_admin_and_csrf(login, member, org, org_admin):
    from django.test import Client
    url = f"/orgs/{org.slug}/settings/assistant"
    for endpoint in ("/test", "/models"):
        assert login(member).post(url + endpoint).status_code == 403
        client = Client(enforce_csrf_checks=True)
        client.force_login(org_admin)
        assert client.post(url + endpoint).status_code == 403


def test_concurrent_native_save_renders_error(login, org_admin, org):
    from apps.orgs.forms import AssistantConfigForm
    OrgAssistantConfig.objects.create(org=org, provider="openai", api_key="stored")
    with patch.object(AssistantConfigForm, "save", side_effect=ValidationError("Changed while saving")):
        response = login(org_admin).post(f"/orgs/{org.slug}/settings/assistant", data(api_key=""))
    assert response.status_code == 200
    assert "Changed while saving" in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_legacy_connection_migration_preserves_settings_and_encryption():
    from decimal import Decimal
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("orgs", "0021_assistant_model_pricing_help_text")]
    after = [("orgs", "0022_orgassistantconfig_auth_mode_and_more")]
    try:
        executor.migrate(before)
        historical = executor.loader.project_state(before).apps
        Organization = historical.get_model("orgs", "Organization")
        Config = historical.get_model("orgs", "OrgAssistantConfig")
        expected = []
        for index, (provider, endpoint, key) in enumerate([
            ("openai", "http://gateway.internal/v1", ""),
            ("anthropic", "https://messages.example/v1", ""),
            ("openai", "https://keyed.example/v1", "keyed-secret"),
            ("anthropic", "", "native-secret"),
        ]):
            org = Organization.objects.create(slug=f"legacy-{index}", name="Migration fixture")
            values = dict(provider=provider, base_url=endpoint, api_key=key, model="",
                          enabled=True, monthly_budget_usd=Decimal("100"), per_user_budget_usd=Decimal("3"),
                          price_in_per_mtok=Decimal("1.25"), price_out_per_mtok=Decimal("2.50"),
                          share_report_source=False, actions_enabled=True)
            cfg = Config.objects.create(org=org, **values)
            expected.append((cfg.pk, values))
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        Config = executor.loader.project_state(after).apps.get_model("orgs", "OrgAssistantConfig")
        for pk, values in expected:
            cfg = Config.objects.get(pk=pk)
            assert all(getattr(cfg, name) == value for name, value in values.items())
            assert cfg.auth_mode == ("none" if cfg.base_url and not cfg.api_key else "api_key")
            assert cfg.config_revision == 1 and cfg.connection_check == {} and cfg.cloud_credentials == {}
            with connection.cursor() as cursor:
                cursor.execute("SELECT api_key, cloud_credentials FROM orgs_orgassistantconfig WHERE id = %s", [pk])
                key, cloud = cursor.fetchone()
            assert not values["api_key"] or (key.startswith("enc$1$") and values["api_key"] not in key)
            assert cloud.startswith("enc$1$")
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_disabled_connection_can_save_credentials_before_model_discovery(org):
    f = form(org, enabled="", provider="custom", base_url="https://gateway.example/v1", model="")
    assert f.is_valid(), f.errors
    cfg = f.save()
    assert cfg.api_key == "new-key" and cfg.model == "" and not cfg.enabled
    f = form(org, cfg, api_key="", provider="custom", base_url=cfg.base_url, model="")
    assert not f.is_valid() and "model" in f.errors


@pytest.mark.parametrize("key,endpoint,mode", [
    ("stored-secret", "", "api_key"),
    ("", "http://gateway.internal/v1", "none"),
])
def test_previous_release_insert_survives_current_schema(org, login, org_admin, settings, key, endpoint, mode):
    from django.db.migrations.executor import MigrationExecutor
    from apps.assistant.llm import GATEWAY_PLACEHOLDER_KEY, LLMConfig

    # Load historical state even when local behavior checks use --nomigrations.
    settings.MIGRATION_MODULES = {}
    # The old model omits every column added by 0022 on INSERT.
    historical = MigrationExecutor(connection).loader.project_state(
        [("orgs", "0021_assistant_model_pricing_help_text")]).apps
    Config = historical.get_model("orgs", "OrgAssistantConfig")
    old = Config.objects.create(org_id=org.pk, enabled=True, provider="openai",
                                api_key=key, base_url=endpoint, model="gpt-4o")
    cfg = OrgAssistantConfig.objects.get(pk=old.pk)
    assert all(getattr(cfg, name) is None for name in
               ("auth_mode", "cloud_config", "cloud_credentials", "config_revision", "connection_check"))
    runtime = LLMConfig.from_config(cfg)
    assert runtime.auth_mode == mode
    assert runtime.api_key == (key or GATEWAY_PLACEHOLDER_KEY)
    assert runtime.cloud_config == runtime.cloud_credentials == runtime.connection_check == {}
    assert login(org_admin).get(f"/orgs/{org.slug}/settings/assistant").status_code == 200
    f = form(org, cfg, auth_mode=mode, api_key="", base_url=endpoint, config_revision=1)
    assert f.is_valid(), f.errors
    cfg = f.save()
    assert cfg.api_key == key and cfg.auth_mode == mode and cfg.config_revision == 1
    assert cfg.connection_check == cfg.cloud_credentials == {}
    with connection.cursor() as cursor:
        cursor.execute("SELECT api_key, cloud_credentials FROM orgs_orgassistantconfig WHERE id = %s", [cfg.pk])
        stored_key, stored_cloud = cursor.fetchone()
    assert not key or (stored_key.startswith("enc$1$") and key not in stored_key)
    assert stored_cloud.startswith("enc$1$")
