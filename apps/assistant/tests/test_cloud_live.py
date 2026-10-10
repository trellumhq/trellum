"""Opt-in synthetic cloud probes; no real report content or database is used.

PowerShell invocation (the JSON file must be outside the repository):
  $env:ASSISTANT_RUN_LIVE_TESTS = '1'
  $env:ASSISTANT_LIVE_TEST_CONFIG_FILE = 'C:/private/assistant-probes.json'
  python -m pytest apps/assistant/tests/test_cloud_live.py -q -rs

JSON structure:
  {"connections": [{"provider": "azure", "auth_mode": "api_key",
    "model": "your-deployment", "base_url": "https://RESOURCE.openai.azure.com",
    "api_key": "YOUR-EXPLICIT-KEY", "org_id": "synthetic-probe"}],
   "workload_identity_orgs": {"azure": ["synthetic-probe"]}}

Connection fields follow LLMConfig: Bedrock uses cloud_config.region and
cloud_credentials.access_key_id/secret_access_key/optional session_token;
Vertex uses cloud_config.project/location and cloud_credentials.service_account;
Azure client_secret uses cloud_config.tenant_id/client_id and
cloud_credentials.client_secret. Workload connections must be independently
listed in workload_identity_orgs by the operator. Each configured pair runs one
bounded check_connection (streaming, synthetic tool roundtrip, forced tool,
usage); every missing pair is explicitly skipped as unverified. Ordinary test
runs never acquire credentials or make cloud requests.
"""
import json
import os
from pathlib import Path

import pytest


CLOUD_AUTH_CASES = [("azure", "api_key"), ("azure", "client_secret"), ("azure", "workload"),
                    ("bedrock", "access_key"), ("bedrock", "workload"),
                    ("vertex", "service_account"), ("vertex", "workload")]


@pytest.fixture(scope="module", params=CLOUD_AUTH_CASES, ids=[":".join(pair) for pair in CLOUD_AUTH_CASES])
def cloud_live_result(request):
    if os.environ.get("ASSISTANT_RUN_LIVE_TESTS") != "1":
        pytest.skip("unverified: live synthetic cloud probes require ASSISTANT_RUN_LIVE_TESTS=1")
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
        pytest.skip("unverified: no explicit credentials/configuration supplied for this cloud auth mode")
    if len(selected) != 1:
        pytest.fail("Supply exactly one connection per provider/auth mode in the live probe file.", pytrace=False)
    from django.test import override_settings
    from apps.assistant.llm import LLMConfig, check_connection
    try:
        config = LLMConfig(**selected[0])
    except (TypeError, ValueError):
        pytest.fail("The live probe connection contains invalid LLMConfig fields.", pytrace=False)
    with override_settings(ASSISTANT_WORKLOAD_IDENTITY_ORGS=payload.get("workload_identity_orgs", {})):
        return check_connection(config, timeout=30)


@pytest.mark.parametrize("capability", ["chat", "alerts", "usage"])
def test_live_cloud_capability(cloud_live_result, capability):
    check = cloud_live_result["checks"][capability]
    assert check["ok"], check["message"]
