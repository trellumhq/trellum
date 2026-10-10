"""Native cloud transports; credentials never leave the selected cloud service."""
from __future__ import annotations

import base64
import json
import os
import re
import uuid
from urllib.parse import urlsplit

from .transport import TransportError


def _close(resource):
    if resource is not None:
        try:
            resource.close()
        except Exception:
            pass


def _usage_counts(inputs, outputs, cache_read=0, cache_write=0):
    values = (inputs, outputs, cache_read, cache_write)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    return dict(zip(("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"), values))


def _failure(exc):
    if isinstance(exc, TransportError):
        return exc
    response = getattr(exc, "response", {})
    code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if (code in {"AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException", "InvalidSignatureException"}
            or status in (401, 403) or type(exc).__name__ in {"ClientAuthenticationError", "CredentialUnavailableError", "DefaultCredentialsError", "RefreshError", "NoCredentialsError", "PartialCredentialsError"}):
        return TransportError("authentication", "Cloud authentication failed. Check the saved credentials and service permissions.")
    if code == "ThrottlingException" or status == 429:
        return TransportError("rate_limit", "The cloud provider rate limit was reached. Try again later.")
    if code == "ValidationException" or status in (400, 404, 422):
        return TransportError("unsupported", "The cloud provider rejected this model or request capability. Check the model and region.")
    if code in {"InternalServerException", "ServiceUnavailableException", "ModelStreamErrorException"} or isinstance(status, int) and status >= 500:
        return TransportError("server", "The cloud provider could not complete the request. Try again later.")
    return TransportError("connection", "Could not connect to the cloud provider. Check credentials, region and network access.")


def _workload(config):
    from .provider_registry import workload_allowed
    if not workload_allowed(config.provider, config.org_id):
        raise TransportError("authentication", "Workload identity is not authorized for this organization and provider.")


def _required(values, *names):
    if not isinstance(values, dict) or any(not isinstance(values.get(n), str) or not values[n].strip() for n in names):
        raise TransportError("authentication", "Required cloud credentials or configuration are missing.")


def _aws_client(config, service, timeout):
    import boto3
    from botocore.config import Config
    region = config.cloud_config.get("region", "")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise TransportError("unsupported", "Enter a valid AWS region.")
    credentials = config.cloud_credentials
    if config.auth_mode == "access_key":
        _required(credentials, "access_key_id", "secret_access_key")
        session = boto3.Session(
            aws_access_key_id=credentials["access_key_id"],
            aws_secret_access_key=credentials["secret_access_key"],
            aws_session_token=credentials.get("session_token") or None,
            region_name=region,
        )
    elif config.auth_mode == "workload":
        _workload(config)
        session = boto3.Session(region_name=region)
    else:
        raise TransportError("authentication", "Choose AWS access keys or an authorized workload identity.")
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    return session.client(service, region_name=region, endpoint_url=f"https://{service}.{region}.{suffix}",
                          config=Config(connect_timeout=timeout or 30, read_timeout=timeout or 30, retries={"max_attempts": 0}))


def azure_openai_client(config):
    """Use Azure's v1 endpoint and a refreshable SDK bearer-token callback."""
    from openai import OpenAI
    credential = None
    try:
        endpoint = urlsplit(config.base_url)
        host = endpoint.hostname or ""
        if (endpoint.scheme != "https" or endpoint.username or endpoint.password or endpoint.port not in (None, 443)
                or endpoint.query or endpoint.fragment or not re.fullmatch(r"[a-zA-Z0-9-]+\.(?:openai|services\.ai)\.azure\.com", host)
                or endpoint.path.rstrip("/") not in ("", "/openai/v1")):
            raise TransportError("authentication", "Azure credentials require a trusted Azure OpenAI HTTPS endpoint.")
        base_url = f"https://{host}/openai/v1/"
        if config.auth_mode == "api_key":
            if not config.api_key:
                raise TransportError("authentication", "An Azure API key is required.")
            key = config.api_key
        else:
            from azure.identity import AzureAuthorityHosts, ClientSecretCredential, ManagedIdentityCredential, WorkloadIdentityCredential, get_bearer_token_provider
            if config.auth_mode == "client_secret":
                _required(config.cloud_config, "tenant_id", "client_id")
                _required(config.cloud_credentials, "client_secret")
                credential = ClientSecretCredential(tenant_id=config.cloud_config["tenant_id"], client_id=config.cloud_config["client_id"],
                                                    client_secret=config.cloud_credentials["client_secret"], authority=AzureAuthorityHosts.AZURE_PUBLIC_CLOUD)
            elif config.auth_mode == "workload":
                _workload(config)
                # Only deployed workload/managed identities, never a developer CLI login.
                credential = WorkloadIdentityCredential() if os.environ.get("AZURE_FEDERATED_TOKEN_FILE") else ManagedIdentityCredential()
            else:
                raise TransportError("authentication", "Choose an Azure API key, client secret or authorized workload identity.")
            token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
            def key():
                try:
                    return token_provider()
                except Exception as exc:
                    raise _failure(exc) from None
        client = OpenAI(base_url=base_url, api_key=key, max_retries=0)
        if credential is not None:
            original_close = client.close
            def close():
                try:
                    original_close()
                finally:
                    _close(credential)
            client.close = close
        return client
    except Exception as exc:
        _close(credential)
        raise _failure(exc) from None


def _vertex_client(config, timeout):
    from google import genai
    from google.genai import types
    values = config.cloud_config
    _required(values, "project", "location")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", values["project"]) or not re.fullmatch(r"[a-z][a-z0-9-]*", values["location"]):
        raise TransportError("unsupported", "Enter a valid Google Cloud project and location.")
    if config.auth_mode == "service_account":
        from google.oauth2 import service_account
        info = config.cloud_credentials.get("service_account")
        if (not isinstance(info, dict) or info.get("type") != "service_account" or info.get("token_uri") != "https://oauth2.googleapis.com/token"
                or info.get("universe_domain", "googleapis.com") != "googleapis.com"):
            raise TransportError("authentication", "Supply a Google service account with the trusted Google token endpoint.")
        credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    elif config.auth_mode == "workload":
        _workload(config)
        import google.auth
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    else:
        raise TransportError("authentication", "Choose a Google service account or authorized workload identity.")
    location = values["location"]
    base_url = "https://aiplatform.googleapis.com" if location == "global" else f"https://{location}-aiplatform.googleapis.com"
    return genai.Client(enterprise=True, project=values["project"], location=location, credentials=credentials,
                        http_options=types.HttpOptions(base_url=base_url, api_version="v1", timeout=int((timeout or 30) * 1000),
                                                       retry_options=types.HttpRetryOptions(attempts=1)))


def _entries(messages):
    """Accept the canonical transcript, including flat tool-result entries."""
    for entry in messages:
        role = entry.get("role")
        if role == "tool" and entry.get("tool_use_id"):
            yield "user", [{"type": "tool_result", "tool_use_id": entry["tool_use_id"], "content": entry.get("result", "")}], None
        elif role in ("user", "assistant"):
            content = entry.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            yield role, content, entry.get("_provider")


def _private(config, data):
    if data and (data.get("provider") != config.provider or data.get("model") != config.model):
        raise TransportError("unsupported", "This conversation needs to restart after changing provider or model.")
    return data.get("content") if data else None


def _bedrock_messages(config, messages):
    output = []
    for role, blocks, private in _entries(messages):
        content = _private(config, private) if role == "assistant" else None
        if content is not None:
            content = [dict(part) for part in content]
            for part in content:
                reasoning = part.get("reasoningContent", {})
                if "redactedContent" in reasoning:
                    part["reasoningContent"] = {"redactedContent": base64.b64decode(reasoning["redactedContent"])}
        else:
            content = []
            for block in blocks:
                kind = block.get("type")
                if kind == "text" and block.get("text"):
                    content.append({"text": block["text"]})
                elif kind == "tool_use":
                    content.append({"toolUse": {"toolUseId": block["id"], "name": block["name"], "input": block["input"]}})
                elif kind == "tool_result":
                    result = block.get("content", "")
                    content.append({"toolResult": {"toolUseId": block["tool_use_id"], "content": [{"text": result if isinstance(result, str) else json.dumps(result)}]}})
        if content:
            if output and output[-1]["role"] == role:
                output[-1]["content"].extend(content)
            else:
                output.append({"role": role, "content": content})
    return output


def stream_bedrock(config, messages, system_blocks, schemas, *, max_tokens, force_tool=None, timeout=None, purpose="chat"):
    client = stream = None
    try:
        client = _aws_client(config, "bedrock-runtime", timeout)
        request = {"modelId": config.model, "messages": _bedrock_messages(config, messages),
                   "system": [{"text": b["text"]} for b in system_blocks if b.get("text")], "inferenceConfig": {"maxTokens": max_tokens}}
        if schemas:
            request["toolConfig"] = {"tools": [{"toolSpec": {"name": s["name"], "description": s.get("description", ""),
                                                                          "inputSchema": {"json": s["input_schema"]}}} for s in schemas]}
            if force_tool:
                request["toolConfig"]["toolChoice"] = {"tool": {"name": force_tool}}
        elif force_tool:
            raise TransportError("unsupported", "A forced tool requires a tool schema.")
        stream = client.converse_stream(**request)["stream"]
        parts = {}
        stop = None
        usage = None
        for event in stream:
            yield {"type": "keepalive"}
            for name in event:
                if name.endswith("Exception"):
                    exc = type("CloudStreamError", (Exception,), {"response": {"Error": {"Code": name[0].upper() + name[1:]}}})()
                    raise _failure(exc)
            start = event.get("contentBlockStart")
            if start:
                parts[start["contentBlockIndex"]] = {"toolUse": dict(start["start"]["toolUse"]), "arguments": ""} if "toolUse" in start["start"] else {}
            delta_event = event.get("contentBlockDelta")
            if delta_event:
                part = parts.setdefault(delta_event["contentBlockIndex"], {})
                delta = delta_event["delta"]
                if "text" in delta:
                    part["text"] = part.get("text", "") + delta["text"]
                    yield {"type": "text_delta", "text": delta["text"]}
                elif "toolUse" in delta:
                    part["arguments"] = part.get("arguments", "") + delta["toolUse"].get("input", "")
                elif "reasoningContent" in delta:
                    reasoning = part.setdefault("reasoningContent", {})
                    for key, value in delta["reasoningContent"].items():
                        reasoning[key] = reasoning.get(key, b"" if isinstance(value, bytes) else "") + value
                else:
                    raise TransportError("unsupported", "The model returned unsupported content.")
            if "messageStop" in event:
                stop = event["messageStop"].get("stopReason")
            raw = event.get("metadata", {}).get("usage")
            if raw and "inputTokens" in raw and "outputTokens" in raw:
                # Bedrock reports uncached input separately from its cache counters.
                usage = _usage_counts(raw["inputTokens"], raw["outputTokens"], raw.get("cacheReadInputTokens", 0), raw.get("cacheWriteInputTokens", 0))
        if stop is None:
            raise TransportError("protocol", "The cloud response ended before completion.")
        blocks, content = [], []
        for _, part in sorted(parts.items()):
            if "toolUse" in part:
                try:
                    args = json.loads(part["arguments"])
                except (ValueError, TypeError):
                    args = part["arguments"]  # Shared validation rejects it after recording usage.
                part.pop("arguments", None)
                tool = part["toolUse"]
                tool["input"] = args
                blocks.append({"type": "tool_use", "id": tool["toolUseId"], "name": tool["name"], "input": args})
            elif "text" in part:
                blocks.append({"type": "text", "text": part["text"]})
            elif "reasoningContent" in part:
                reasoning = part["reasoningContent"]
                if "redactedContent" in reasoning:
                    part["reasoningContent"] = {"redactedContent": base64.b64encode(reasoning["redactedContent"]).decode("ascii")}
                else:
                    if not reasoning.get("signature"):
                        raise TransportError("unsupported", "The model's reasoning continuation cannot be replayed safely. Start a new conversation.")
                    part["reasoningContent"] = {"reasoningText": reasoning}
            else:
                raise TransportError("protocol", "The model returned an incomplete content block.")
            content.append(part)
        yield {"type": "response", "blocks": blocks, "stop_reason": stop, "usage": usage,
               "provider_data": {"provider": "bedrock", "model": config.model, "content": content}}
    except Exception as exc:
        raise _failure(exc) from None
    finally:
        _close(stream)
        _close(client)


def _vertex_messages(config, messages):
    output, tool_names = [], {}
    for role, blocks, private in _entries(messages):
        for block in blocks:
            if block.get("type") == "tool_use":
                tool_names[block["id"]] = block["name"]
        content = _private(config, private) if role == "assistant" else None
        if content is None:
            content = []
            for block in blocks:
                kind = block.get("type")
                if kind == "text" and block.get("text"):
                    content.append({"text": block["text"]})
                elif kind == "tool_use":
                    content.append({"function_call": {"id": block["id"], "name": block["name"], "args": block["input"]}})
                elif kind == "tool_result":
                    call_id = block["tool_use_id"]
                    if call_id not in tool_names:
                        raise TransportError("protocol", "A tool result has no matching model tool call.")
                    content.append({"function_response": {"id": call_id, "name": tool_names[call_id], "response": {"result": block.get("content", "")}}})
        if content:
            native_role = "model" if role == "assistant" else "user"
            if output and output[-1]["role"] == native_role:
                output[-1]["parts"].extend(content)
            else:
                output.append({"role": native_role, "parts": content})
    return output


def stream_vertex(config, messages, system_blocks, schemas, *, max_tokens, force_tool=None, timeout=None, purpose="chat"):
    client = chunks = None
    try:
        from google.genai import types
        client = _vertex_client(config, timeout)
        options = {"system_instruction": "\n\n".join(b["text"] for b in system_blocks if b.get("text")), "max_output_tokens": max_tokens,
                   "automatic_function_calling": {"disable": True}}
        if schemas:
            options["tools"] = [{"function_declarations": [{"name": s["name"], "description": s.get("description", ""),
                                                          "parameters_json_schema": s["input_schema"]} for s in schemas]}]
            options["tool_config"] = {"function_calling_config": {"stream_function_call_arguments": False}}
        if force_tool:
            if not schemas:
                raise TransportError("unsupported", "A forced tool requires a tool schema.")
            options["tool_config"]["function_calling_config"].update(mode="ANY", allowed_function_names=[force_tool])
        chunks = client.models.generate_content_stream(model=config.model, contents=_vertex_messages(config, messages), config=types.GenerateContentConfig(**options))
        blocks, content = [], []
        usage = None
        stop = None
        for chunk in chunks:
            yield {"type": "keepalive"}
            if chunk.usage_metadata is not None:
                raw = chunk.usage_metadata
                if raw.prompt_token_count is not None and raw.candidates_token_count is not None:
                    cache = raw.cached_content_token_count or 0
                    usage = _usage_counts(raw.prompt_token_count - cache, raw.candidates_token_count + (raw.thoughts_token_count or 0), cache)
            for candidate in chunk.candidates or []:
                if candidate.finish_reason:
                    stop = getattr(candidate.finish_reason, "value", candidate.finish_reason)
                for part in candidate.content.parts if candidate.content else []:
                    raw_part = part.model_dump(mode="json", exclude_none=True)
                    if part.function_call is not None:
                        call = part.function_call
                        if call.partial_args or call.will_continue:
                            raise TransportError("unsupported", "Incremental Vertex function arguments are not enabled for this connection.")
                        call_id = call.id or "vertex_" + uuid.uuid4().hex
                        raw_part["function_call"]["id"] = call_id
                        blocks.append({"type": "tool_use", "id": call_id, "name": call.name, "input": call.args})
                    elif part.text and not part.thought:
                        if blocks and blocks[-1]["type"] == "text":
                            blocks[-1]["text"] += part.text
                        else:
                            blocks.append({"type": "text", "text": part.text})
                        yield {"type": "text_delta", "text": part.text}
                    elif not part.thought and not part.thought_signature:
                        raise TransportError("unsupported", "The model returned unsupported content.")
                    content.append(raw_part)
        if stop is None:
            raise TransportError("protocol", "The cloud response ended before completion.")
        yield {"type": "response", "blocks": blocks, "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in blocks) else str(stop).lower(),
               "usage": usage, "provider_data": {"provider": "vertex", "model": config.model, "content": content}}
    except Exception as exc:
        raise _failure(exc) from None
    finally:
        _close(chunks)
        _close(client)


def list_cloud_models(config, timeout=10):
    client = None
    try:
        found = {}
        if config.provider == "bedrock":
            client = _aws_client(config, "bedrock", timeout)
            for item in client.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]:
                if item.get("responseStreamingSupported"):
                    found[item["modelId"]] = item.get("modelName") or item["modelId"]
            token = None
            while True:
                page = client.list_inference_profiles(**({"nextToken": token} if token else {}))
                for item in page.get("inferenceProfileSummaries", []):
                    found[item["inferenceProfileId"]] = item.get("inferenceProfileName") or item["inferenceProfileId"]
                token = page.get("nextToken")
                if not token:
                    break
        elif config.provider == "vertex":
            client = _vertex_client(config, timeout)
            for model in client.models.list(config={"query_base": True}):
                name = model.name
                if name and "gemini" in name.lower():
                    model_id = name.rsplit("/", 1)[-1]
                    found[model_id] = model.display_name or model_id
        else:
            raise TransportError("unsupported", "Cloud model discovery is available for Bedrock and Vertex.")
        return [{"id": key, "label": label} for key, label in sorted(found.items())]
    except Exception as exc:
        raise _failure(exc) from None
    finally:
        _close(client)
