"""Provider presets and allowed authentication modes (no Django import at load time)."""

PROVIDERS = {
    "anthropic": {"label": "Anthropic", "protocol": "anthropic", "base_url": "https://api.anthropic.com", "default_model": "claude-sonnet-4-6", "auth_modes": ("api_key", "none")},
    "openai": {"label": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o", "auth_modes": ("api_key", "none")},
    "litellm": {"label": "LiteLLM", "protocol": "openai", "base_url": "", "default_model": "", "auth_modes": ("api_key", "none")},
    "openrouter": {"label": "OpenRouter", "protocol": "openai", "base_url": "https://openrouter.ai/api/v1", "default_model": "", "auth_modes": ("api_key",)},
    "deepseek": {"label": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com", "default_model": "", "auth_modes": ("api_key",)},
    "azure": {"label": "Azure OpenAI", "protocol": "openai", "base_url": "", "default_model": "", "auth_modes": ("api_key", "client_secret", "workload")},
    "bedrock": {"label": "Amazon Bedrock", "protocol": "bedrock", "base_url": "", "default_model": "", "auth_modes": ("access_key", "workload")},
    "vertex": {"label": "Vertex AI", "protocol": "vertex", "base_url": "", "default_model": "", "auth_modes": ("service_account", "workload")},
    "custom": {"label": "Custom OpenAI-compatible endpoint", "protocol": "openai", "base_url": "", "default_model": "", "auth_modes": ("api_key", "none")},
}
PROVIDER_CHOICES = [(key, value["label"]) for key, value in PROVIDERS.items()]
AUTH_CHOICES = [
    ("api_key", "API key"), ("none", "No authentication (gateway handles it)"),
    ("access_key", "AWS access key"), ("client_secret", "Microsoft Entra client secret"),
    ("service_account", "Google service account"), ("workload", "Operator-authorized workload identity"),
]


def effective_base_url(provider, base_url=""):
    return (base_url or PROVIDERS.get(provider, {}).get("base_url", "")).strip().rstrip("/")


def workload_allowed(provider, org_id):
    from django.conf import settings

    allowed = getattr(settings, "ASSISTANT_WORKLOAD_IDENTITY_ORGS", {})
    return (provider in {"azure", "bedrock", "vertex"} and isinstance(allowed, dict)
            and isinstance(allowed.get(provider), list)
            and str(org_id) in allowed[provider])
