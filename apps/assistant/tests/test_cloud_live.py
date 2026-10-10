"""Opt-in synthetic provider probes; no real report content or database is used.

PowerShell invocation (the JSON file must be outside the repository):
  $env:ASSISTANT_RUN_LIVE_TESTS = '1'
  $env:ASSISTANT_LIVE_TEST_CONFIG_FILE = 'C:/private/assistant-probes.json'
  python -m pytest apps/assistant/tests/test_cloud_live.py -q -rs

JSON structure:
  {"connections": [{"provider": "azure", "auth_mode": "api_key",
    "model": "your-deployment", "base_url": "https://RESOURCE.openai.azure.com",
    "api_key": "YOUR-EXPLICIT-KEY", "org_id": "synthetic-probe"}],
   "workload_identity_orgs": {"azure": ["synthetic-probe"]}}

Connection fields follow LLMConfig. Direct providers/gateways use provider,
auth_mode, model, api_key and optional base_url; none auth requires an explicit
base_url. Preset provider URLs/default models are resolved before the probe.
Bedrock uses cloud_config.region and
cloud_credentials.access_key_id/secret_access_key/optional session_token;
Vertex uses cloud_config.project/location and cloud_credentials.service_account;
Azure client_secret uses cloud_config.tenant_id/client_id and
cloud_credentials.client_secret. Workload connections must be independently
listed in workload_identity_orgs by the operator. Each configured pair runs one
bounded check_connection (streaming, synthetic tool roundtrip, forced tool,
usage); every missing pair is explicitly skipped as unverified. Ordinary test
runs never acquire credentials or make external requests. All nine named
providers and every registry authentication mode have separately labeled cases.
"""
import json
import os
from pathlib import Path

import pytest


PROVIDER_AUTH_CASES = [("anthropic", "api_key"), ("anthropic", "none"),
                       ("openai", "api_key"), ("openai", "none"),
                       ("litellm", "api_key"), ("litellm", "none"),
                       ("openrouter", "api_key"), ("deepseek", "api_key"),
                       ("custom", "api_key"), ("custom", "none"),
                       ("azure", "api_key"), ("azure", "client_secret"), ("azure", "workload"),
                       ("bedrock", "access_key"), ("bedrock", "workload"),
                       ("vertex", "service_account"), ("vertex", "workload")]


def _build_config(row):
    from apps.assistant.llm import GATEWAY_PLACEHOLDER_KEY, LLMConfig
    from apps.assistant.provider_registry import PROVIDERS, effective_base_url
    values = dict(row)
    provider = values["provider"]
    values["base_url"] = effective_base_url(provider, values.get("base_url", ""))
    values["model"] = values.get("model") or PROVIDERS[provider]["default_model"]
    values["custom_endpoint"] = bool(row.get("base_url"))
    if values.get("auth_mode") == "none":
        if not row.get("base_url"):
            raise ValueError
        values["api_key"] = GATEWAY_PLACEHOLDER_KEY
    return LLMConfig(**values)


@pytest.fixture(scope="module", params=PROVIDER_AUTH_CASES, ids=[":".join(pair) for pair in PROVIDER_AUTH_CASES])
def live_provider_result(request):
    if os.environ.get("ASSISTANT_RUN_LIVE_TESTS") != "1":
        pytest.skip("unverified: live synthetic provider probes require ASSISTANT_RUN_LIVE_TESTS=1")
    path = os.environ.get("ASSISTANT_LIVE_TEST_CONFIG_FILE")
    if not path:
        pytest.fail("Live probes require an explicitly supplied ASSISTANT_LIVE_TEST_CONFIG_FILE.", pytrace=False)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        connections = payload["connections"]
        if not isinstance(connections, list):
            raise ValueError
        selected = [row for row in connections if (row.get("provider"), row.get("auth_mode", "api_key")) == request.param]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pytest.fail("The explicit live probe file is unreadable or has an invalid connections list.", pytrace=False)
    if not selected:
        pytest.skip("unverified: no explicit credentials/configuration supplied for this provider auth mode")
    if len(selected) != 1:
        pytest.fail("Supply exactly one connection per provider/auth mode in the live probe file.", pytrace=False)
    from django.test import override_settings
    from apps.assistant.llm import check_connection
    try:
        config = _build_config(selected[0])
    except (TypeError, ValueError):
        pytest.fail("The live probe connection contains invalid LLMConfig fields.", pytrace=False)
    with override_settings(ASSISTANT_WORKLOAD_IDENTITY_ORGS=payload.get("workload_identity_orgs", {})):
        return check_connection(config, timeout=30)


@pytest.mark.parametrize("capability", ["chat", "alerts", "usage"])
def test_live_provider_capability(live_provider_result, capability):
    check = live_provider_result["checks"][capability]
    assert check["ok"], check["message"]
