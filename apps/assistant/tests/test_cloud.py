"""Cloud contract checks use real pinned SDK shapes and synthetic responses."""
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import types

from apps.assistant import cloud
from apps.assistant.transport import TransportError


SCHEMAS = [{"name": "lookup", "description": "Synthetic lookup", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]


def config(provider="bedrock", **kwargs):
    values = dict(provider=provider, model="synthetic-model", base_url="https://example.openai.azure.com", api_key="test-only-secret",
                  auth_mode="access_key", cloud_config={"region": "eu-west-1"}, cloud_credentials={"access_key_id": "synthetic-id", "secret_access_key": "test-only-secret"}, org_id="org-a")
    values.update(kwargs)
    return SimpleNamespace(**values)


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        yield from self.events

    def close(self):
        self.closed = True


def delta(index, **content):
    return {"contentBlockDelta": {"contentBlockIndex": index, "delta": content}}


def bedrock_mock(monkeypatch, events):
    stream = Stream(events)
    client = Mock()
    client.converse_stream.return_value = {"stream": stream}
    monkeypatch.setattr(cloud, "_aws_client", lambda *args: client)
    return client, stream


def test_bedrock_fragmented_tools_reasoning_usage_and_private_replay(monkeypatch):
    client, stream = bedrock_mock(monkeypatch, [
        delta(0, reasoningContent={"text": "private thought"}), delta(0, reasoningContent={"signature": "signed"}),
        delta(1, text="Hello "), delta(1, text="world"),
        {"contentBlockStart": {"contentBlockIndex": 2, "start": {"toolUse": {"toolUseId": "one", "name": "lookup"}}}},
        delta(2, toolUse={"input": '{"q":'}), delta(2, toolUse={"input": '"a"}'}),
        {"contentBlockStart": {"contentBlockIndex": 3, "start": {"toolUse": {"toolUseId": "two", "name": "lookup"}}}},
        delta(3, toolUse={"input": '{"q":"b"}'}),
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 3, "cacheWriteInputTokens": 2}}},
    ])
    cfg = config()
    events = list(cloud.stream_bedrock(cfg, [{"role": "user", "content": "Synthetic"}], [{"text": "System"}], SCHEMAS, max_tokens=100, force_tool="lookup"))
    response = events[-1]
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Hello ", "world"]
    assert response["blocks"] == [{"type": "text", "text": "Hello world"}, {"type": "tool_use", "id": "one", "name": "lookup", "input": {"q": "a"}}, {"type": "tool_use", "id": "two", "name": "lookup", "input": {"q": "b"}}]
    assert response["usage"] == {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}
    assert "private thought" not in json.dumps([e for e in events if e["type"] != "response"])
    assert "private thought" not in json.dumps(response["blocks"])
    private = json.loads(json.dumps(response["provider_data"]))
    messages = [{"role": "assistant", "content": response["blocks"], "_provider": private},
                {"role": "tool", "tool_use_id": "one", "result": "A"}, {"role": "tool", "tool_use_id": "two", "result": "B"}]
    replay = cloud._bedrock_messages(cfg, messages)
    assert replay[0]["content"][0] == {"reasoningContent": {"reasoningText": {"text": "private thought", "signature": "signed"}}}
    assert len(replay[1]["content"]) == 2
    assert client.converse_stream.call_args.kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": "lookup"}}
    assert stream.closed
    client.close.assert_called_once()


@pytest.mark.parametrize("arguments", ["{bad", "[]", "null", ""])
def test_bedrock_preserves_malformed_tool_arguments_for_shared_validation(monkeypatch, arguments):
    bedrock_mock(monkeypatch, [{"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "one", "name": "lookup"}}}},
                              delta(0, toolUse={"input": arguments}), {"messageStop": {"stopReason": "tool_use"}}])
    response = list(cloud.stream_bedrock(config(), [], [], SCHEMAS, max_tokens=100))[-1]
    assert not isinstance(response["blocks"][0]["input"], dict)


def test_bedrock_missing_usage_is_unknown_and_redacted_replay_is_json_safe(monkeypatch):
    bedrock_mock(monkeypatch, [delta(0, reasoningContent={"redactedContent": b"opaque"}), delta(1, text="done"), {"messageStop": {"stopReason": "end_turn"}}])
    response = list(cloud.stream_bedrock(config(), [], [], [], max_tokens=100))[-1]
    assert response["usage"] is None
    private = json.loads(json.dumps(response["provider_data"]))
    replay = cloud._bedrock_messages(config(), [{"role": "assistant", "content": response["blocks"], "_provider": private}])
    assert replay[0]["content"][0]["reasoningContent"]["redactedContent"] == b"opaque"


def test_bedrock_truncation_and_midstream_cleanup(monkeypatch):
    client, stream = bedrock_mock(monkeypatch, [delta(0, text="started")])
    with pytest.raises(TransportError, match="before completion"):
        list(cloud.stream_bedrock(config(), [], [], [], max_tokens=100))
    assert stream.closed
    client, stream = bedrock_mock(monkeypatch, [delta(0, reasoningContent={"text": "private"})])
    generator = cloud.stream_bedrock(config(), [], [], [], max_tokens=100)
    assert next(generator) == {"type": "keepalive"}
    generator.close()
    assert stream.closed
    client.close.assert_called_once()


def vertex_chunk(parts=(), *, finish=None, usage=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)), finish_reason=finish)], usage_metadata=usage)


def vertex_mock(monkeypatch, chunks):
    stream = Stream(chunks)
    client = Mock()
    client.models.generate_content_stream.return_value = stream
    monkeypatch.setattr(cloud, "_vertex_client", lambda *args: client)
    return client, stream


def test_vertex_real_shapes_signature_tools_forcing_usage_and_replay(monkeypatch):
    client, stream = vertex_mock(monkeypatch, [
        vertex_chunk([types.Part(text="private thought", thought=True)]),
        vertex_chunk([types.Part(text="Hello")]),
        vertex_chunk([types.Part(function_call=types.FunctionCall(name="lookup", args={"q": "a"}), thought_signature=b"signature")]),
        vertex_chunk([types.Part(function_call=types.FunctionCall(id="two", name="lookup", args={"q": "b"}))], finish="STOP",
                     usage=types.GenerateContentResponseUsageMetadata(prompt_token_count=20, candidates_token_count=5, thoughts_token_count=7, cached_content_token_count=4)),
    ])
    cfg = config("vertex")
    events = list(cloud.stream_vertex(cfg, [{"role": "user", "content": "Synthetic"}], [{"text": "System"}], SCHEMAS, max_tokens=100, force_tool="lookup"))
    response = events[-1]
    assert response["stop_reason"] == "tool_use"
    assert response["usage"] == {"input_tokens": 16, "output_tokens": 12, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 0}
    assert "private thought" not in json.dumps(response["blocks"])
    private = json.loads(json.dumps(response["provider_data"]))
    first_id = response["blocks"][1]["id"]
    messages = [{"role": "assistant", "content": response["blocks"], "_provider": private}, {"role": "tool", "tool_use_id": first_id, "result": "A"}, {"role": "tool", "tool_use_id": "two", "result": "B"}]
    replay = cloud._vertex_messages(cfg, messages)
    part = types.Content(**replay[0]).parts[2]
    assert part.thought_signature == b"signature"
    assert replay[1]["parts"][0]["function_response"]["name"] == "lookup"
    assert len(replay[1]["parts"]) == 2
    options = client.models.generate_content_stream.call_args.kwargs["config"]
    assert options.automatic_function_calling.disable is True
    assert options.tool_config.function_calling_config.allowed_function_names == ["lookup"]
    assert options.tool_config.function_calling_config.stream_function_call_arguments is False
    assert stream.closed


def test_vertex_rejects_unrequested_partial_arguments_and_missing_completion(monkeypatch):
    vertex_mock(monkeypatch, [vertex_chunk([types.Part(function_call=types.FunctionCall(name="lookup", will_continue=True))], finish="STOP")])
    with pytest.raises(TransportError) as error:
        list(cloud.stream_vertex(config("vertex"), [], [], SCHEMAS, max_tokens=100))
    assert error.value.kind == "unsupported"
    vertex_mock(monkeypatch, [vertex_chunk([types.Part(text="started")])])
    with pytest.raises(TransportError, match="before completion"):
        list(cloud.stream_vertex(config("vertex"), [], [], [], max_tokens=100))


def test_vertex_missing_usage_and_reasoning_progress_cleanup(monkeypatch):
    _, stream = vertex_mock(monkeypatch, [vertex_chunk([types.Part(text="private", thought=True)], finish="STOP")])
    response = list(cloud.stream_vertex(config("vertex"), [], [], [], max_tokens=100))[-1]
    assert response["usage"] is None
    assert response["blocks"] == []
    _, stream = vertex_mock(monkeypatch, [vertex_chunk([types.Part(text="private", thought=True)])])
    generator = cloud.stream_vertex(config("vertex"), [], [], [], max_tokens=100)
    assert next(generator) == {"type": "keepalive"}
    generator.close()
    assert stream.closed


@pytest.mark.parametrize("provider,convert", [("bedrock", cloud._bedrock_messages), ("vertex", cloud._vertex_messages)])
def test_private_continuation_never_crosses_model_or_provider(provider, convert):
    for other in ({"provider": "other", "model": "synthetic-model", "content": []}, {"provider": provider, "model": "other-model", "content": []}):
        with pytest.raises(TransportError, match="restart"):
            convert(config(provider), [{"role": "assistant", "content": [], "_provider": other}])


def test_bedrock_stored_credentials_and_pinned_sdk_shape(monkeypatch):
    import boto3
    from botocore.validate import validate_parameters
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://arbitrary.example")
    cfg = config()
    client = cloud._aws_client(cfg, "bedrock-runtime", 9)
    assert client.meta.endpoint_url == "https://bedrock-runtime.eu-west-1.amazonaws.com"
    credentials = client._request_signer._credentials
    assert credentials.access_key == "synthetic-id"
    assert credentials.secret_key == "test-only-secret"
    model = client.meta.service_model
    assert "reasoningContent" in model.shape_for("ContentBlockDelta").members
    assert "cacheReadInputTokens" in model.shape_for("TokenUsage").members
    validate_parameters({"modelId": cfg.model, "messages": [{"role": "assistant", "content": [{"reasoningContent": {"reasoningText": {"text": "private", "signature": "signed"}}}]}]}, model.operation_model("ConverseStream").input_shape)
    client.close()
    session = Mock()
    monkeypatch.setattr(boto3, "Session", session)
    with pytest.raises(TransportError, match="missing"):
        cloud._aws_client(config(cloud_credentials={}), "bedrock", 5)
    session.assert_not_called()


@pytest.mark.parametrize("provider", ["bedrock", "vertex", "azure"])
def test_workload_identity_requires_operator_org_allowlist(settings, monkeypatch, provider):
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {provider: ["org-b"]}
    cfg = config(provider, auth_mode="workload", cloud_config={"region": "eu-west-1", "project": "example-project", "location": "europe-west4"})
    if provider == "azure":
        call = cloud.azure_openai_client
    elif provider == "bedrock":
        call = lambda c: cloud._aws_client(c, "bedrock", 5)
    else:
        call = lambda c: cloud._vertex_client(c, 5)
    with pytest.raises(TransportError, match="not authorized"):
        call(cfg)
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {provider: ["org-a"]}
    cloud._workload(cfg)


def test_vertex_stored_credentials_native_destination_and_no_environment_mutation(monkeypatch):
    from google import genai
    from google.oauth2 import service_account
    credential = Mock()
    load = Mock(return_value=credential)
    constructor = Mock()
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", load)
    monkeypatch.setattr(genai, "Client", constructor)
    monkeypatch.setenv("GOOGLE_VERTEX_BASE_URL", "https://arbitrary.example")
    before = dict(cloud.os.environ)
    info = {"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token", "client_email": "synthetic@example-project.iam.gserviceaccount.com", "private_key": "synthetic"}
    cfg = config("vertex", auth_mode="service_account", cloud_config={"project": "example-project", "location": "europe-west4"}, cloud_credentials={"service_account": info})
    cloud._vertex_client(cfg, 7)
    values = constructor.call_args.kwargs
    assert values["credentials"] is credential
    assert values["enterprise"] is True
    assert values["http_options"].base_url == "https://europe-west4-aiplatform.googleapis.com"
    assert values["http_options"].timeout == 7000
    assert values["http_options"].retry_options.attempts == 1
    assert dict(cloud.os.environ) == before
    for invalid in ({"type": "external_account", "token_uri": "https://oauth2.googleapis.com/token"}, dict(info, token_uri="https://arbitrary.example"), dict(info, universe_domain="arbitrary.example")):
        cfg.cloud_credentials = {"service_account": invalid}
        with pytest.raises(TransportError, match="trusted"):
            cloud._vertex_client(cfg, 5)
    assert load.call_count == 1


@pytest.mark.parametrize("url", ["https://arbitrary.example", "http://example.openai.azure.com", "https://example.openai.azure.com.evil.example", "https://secret@example.openai.azure.com", "https://example.openai.azure.com:8443", "https://example.openai.azure.com/openai/v1/?secret=yes", "https://example.openai.azure.com/other", "https://example.openai.azure.us"])
def test_azure_rejects_credential_destination_before_sdk_construction(monkeypatch, url):
    import openai
    constructor = Mock()
    monkeypatch.setattr(openai, "OpenAI", constructor)
    with pytest.raises(TransportError, match="trusted"):
        cloud.azure_openai_client(config("azure", auth_mode="api_key", base_url=url))
    constructor.assert_not_called()


def test_azure_api_key_and_client_secret_refresh(monkeypatch):
    import azure.identity
    import openai
    from azure.core.credentials import AccessToken
    constructor = Mock()
    monkeypatch.setattr(openai, "OpenAI", constructor)
    cloud.azure_openai_client(config("azure", auth_mode="api_key"))
    assert constructor.call_args.kwargs["base_url"] == "https://example.openai.azure.com/openai/v1/"
    assert constructor.call_args.kwargs["api_key"] == "test-only-secret"
    credential = Mock(spec=["get_token"])
    credential.get_token.side_effect = [AccessToken("token-one", int(time.time()) + 30), AccessToken("token-two", int(time.time()) + 3600)]
    load = Mock(return_value=credential)
    monkeypatch.setattr(azure.identity, "ClientSecretCredential", load)
    cfg = config("azure", auth_mode="client_secret", cloud_config={"tenant_id": "synthetic-tenant", "client_id": "synthetic-client"}, cloud_credentials={"client_secret": "test-only-secret"})
    cloud.azure_openai_client(cfg)
    provider = constructor.call_args.kwargs["api_key"]
    assert provider() == "token-one"
    assert provider() == "token-two"
    assert provider() == "token-two"
    assert credential.get_token.call_count == 2
    assert load.call_args.kwargs["client_secret"] == "test-only-secret"
    assert load.call_args.kwargs["authority"] == azure.identity.AzureAuthorityHosts.AZURE_PUBLIC_CLOUD


def test_authorized_workload_modes_use_only_the_selected_provider(settings, monkeypatch):
    import azure.identity
    import boto3
    import google.auth
    from google import genai
    import openai
    settings.ASSISTANT_WORKLOAD_IDENTITY_ORGS = {p: ["org-a"] for p in ("bedrock", "azure", "vertex")}
    session = Mock()
    monkeypatch.setattr(boto3, "Session", session)
    cloud._aws_client(config(auth_mode="workload"), "bedrock", 5)
    session.assert_called_once_with(region_name="eu-west-1")
    credentials = Mock()
    default = Mock(return_value=(credentials, "ignored-project"))
    monkeypatch.setattr(google.auth, "default", default)
    constructor = Mock()
    monkeypatch.setattr(genai, "Client", constructor)
    cloud._vertex_client(config("vertex", auth_mode="workload", cloud_config={"project": "explicit-project", "location": "global"}), 5)
    default.assert_called_once_with(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    assert constructor.call_args.kwargs["project"] == "explicit-project"
    assert constructor.call_args.kwargs["credentials"] is credentials
    managed, federated = Mock(), Mock()
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", managed)
    monkeypatch.setattr(azure.identity, "WorkloadIdentityCredential", federated)
    monkeypatch.setattr(openai, "OpenAI", Mock())
    monkeypatch.delenv("AZURE_FEDERATED_TOKEN_FILE", raising=False)
    cloud.azure_openai_client(config("azure", auth_mode="workload"))
    managed.assert_called_once()
    federated.assert_not_called()
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", "operator-configured-token-file")
    cloud.azure_openai_client(config("azure", auth_mode="workload"))
    federated.assert_called_once()


def test_azure_client_and_constructor_failure_close_identity_transport(monkeypatch):
    import azure.identity
    import openai
    credential = Mock()
    monkeypatch.setattr(azure.identity, "ClientSecretCredential", Mock(return_value=credential))
    client = Mock()
    original_close = client.close
    constructor = Mock(return_value=client)
    monkeypatch.setattr(openai, "OpenAI", constructor)
    cfg = config("azure", auth_mode="client_secret", cloud_config={"tenant_id": "synthetic-tenant", "client_id": "synthetic-client"}, cloud_credentials={"client_secret": "test-only-secret"})
    returned = cloud.azure_openai_client(cfg)
    returned.close()
    original_close.assert_called_once()
    credential.close.assert_called_once()
    credential.reset_mock()
    constructor.side_effect = ValueError("test-only-secret")
    with pytest.raises(TransportError) as error:
        cloud.azure_openai_client(cfg)
    credential.close.assert_called_once()
    assert "test-only-secret" not in str(error.value)


def test_vertex_actual_sdk_serializes_native_request_and_signature(monkeypatch):
    import httpx
    from google import genai
    from google.oauth2 import credentials, service_account
    actual_client = genai.Client
    captured = []
    def handler(request):
        captured.append(request)
        data = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Synthetic response"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="data: " + json.dumps(data) + "\n\n")
    def constructor(**kwargs):
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(handler)}
        return actual_client(**kwargs)
    monkeypatch.setattr(genai, "Client", constructor)
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", lambda *args, **kwargs: credentials.Credentials("synthetic-token"))
    cfg = config("vertex", auth_mode="service_account", model="gemini-synthetic", cloud_config={"project": "example-project", "location": "europe-west4"},
                 cloud_credentials={"service_account": {"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"}})
    private = {"provider": "vertex", "model": cfg.model, "content": [{"function_call": {"name": "lookup", "id": "one", "args": {}}, "thought_signature": "c2lnbmVk"}]}
    messages = [{"role": "user", "content": "Synthetic"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "one", "name": "lookup", "input": {}}], "_provider": private},
                {"role": "tool", "tool_use_id": "one", "result": "Synthetic result"}]
    response = list(cloud.stream_vertex(cfg, messages, [], SCHEMAS, max_tokens=50, force_tool="lookup"))[-1]
    assert response["blocks"] == [{"type": "text", "text": "Synthetic response"}]
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url).startswith("https://europe-west4-aiplatform.googleapis.com/v1/projects/example-project/locations/europe-west4/publishers/google/models/gemini-synthetic:")
    body = json.loads(request.content)
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "c2lnbmVk"
    calling = body["toolConfig"]["functionCallingConfig"]
    assert calling.get("streamFunctionCallArguments", calling.get("stream_function_call_arguments")) is False


def test_cloud_discovery_aws_profiles_paginated_vertex_base_models(monkeypatch):
    client = Mock()
    client.list_foundation_models.return_value = {"modelSummaries": [{"modelId": "base", "modelName": "Base", "responseStreamingSupported": True}, {"modelId": "no-stream", "responseStreamingSupported": False}]}
    client.list_inference_profiles.side_effect = [{"inferenceProfileSummaries": [{"inferenceProfileId": "profile-one", "inferenceProfileName": "Profile one"}], "nextToken": "next"}, {"inferenceProfileSummaries": [{"inferenceProfileId": "profile-two", "inferenceProfileName": "Profile two"}]}]
    monkeypatch.setattr(cloud, "_aws_client", lambda *args: client)
    assert cloud.list_cloud_models(config()) == [{"id": "base", "label": "Base"}, {"id": "profile-one", "label": "Profile one"}, {"id": "profile-two", "label": "Profile two"}]
    assert client.list_inference_profiles.call_args.kwargs == {"nextToken": "next"}
    client = Mock()
    client.models.list.return_value = [types.Model(name="publishers/google/models/gemini-synthetic", display_name="Gemini synthetic"), types.Model(name="publishers/google/models/imagen-synthetic")]
    monkeypatch.setattr(cloud, "_vertex_client", lambda *args: client)
    assert cloud.list_cloud_models(config("vertex")) == [{"id": "gemini-synthetic", "label": "Gemini synthetic"}]
    client.models.list.assert_called_once_with(config={"query_base": True})


@pytest.mark.parametrize("code,kind", [("AccessDeniedException", "authentication"), ("ThrottlingException", "rate_limit"), ("ValidationException", "unsupported"), ("ServiceUnavailableException", "server")])
def test_native_errors_are_safe(monkeypatch, code, kind):
    from botocore.exceptions import ClientError
    exc = ClientError({"Error": {"Code": code, "Message": "test-only-secret"}}, "ConverseStream")
    monkeypatch.setattr(cloud, "_aws_client", Mock(side_effect=exc))
    with pytest.raises(TransportError) as error:
        list(cloud.stream_bedrock(config(), [], [], [], max_tokens=10))
    assert error.value.kind == kind
    assert "test-only-secret" not in str(error.value)
    assert error.value.__cause__ is None
