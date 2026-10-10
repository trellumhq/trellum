import copy
import json
import re
from urllib.parse import urlsplit

from django import forms
from django.db import transaction

from apps.assistant.provider_registry import AUTH_CHOICES, PROVIDERS, effective_base_url, workload_allowed

from apps.core import roles
from apps.studios.models import Studio

from .models import Organization, OrgAssistantConfig, PermissionGroup, slug_validator


class StudioCreateForm(forms.Form):
    slug = forms.SlugField(max_length=64, validators=[slug_validator])
    name = forms.CharField(max_length=200)
    description = forms.CharField(max_length=400, required=False)

    def __init__(self, *args, org: Organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if Studio.objects.filter(org=self.org, slug=slug).exists():
            raise forms.ValidationError("A studio with this slug already exists.")
        return slug


class WriteOnlyTextarea(forms.Textarea):
    def format_value(self, value):
        return ""


class AssistantConfigForm(forms.ModelForm):
    """Write-only credentials are preserved only for the same connection identity."""

    api_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    auth_mode = forms.ChoiceField(choices=AUTH_CHOICES, required=False)
    config_revision = forms.IntegerField(required=False, widget=forms.HiddenInput)
    region = forms.CharField(required=False, max_length=64)
    tenant_id = forms.CharField(required=False, max_length=128)
    client_id = forms.CharField(required=False, max_length=128)
    project = forms.CharField(required=False, max_length=128)
    location = forms.CharField(required=False, max_length=64)
    access_key_id = forms.CharField(required=False, max_length=256, widget=forms.PasswordInput(render_value=False))
    secret_access_key = forms.CharField(required=False, max_length=2048, widget=forms.PasswordInput(render_value=False))
    session_token = forms.CharField(required=False, max_length=8192, widget=forms.PasswordInput(render_value=False))
    client_secret = forms.CharField(required=False, max_length=2048, widget=forms.PasswordInput(render_value=False))
    service_account = forms.CharField(required=False, max_length=32768, widget=WriteOnlyTextarea(attrs={"rows": 4, "autocomplete": "off"}))

    class Meta:
        model = OrgAssistantConfig
        fields = [
            "enabled", "provider", "auth_mode", "api_key", "model", "base_url",
            "price_in_per_mtok", "price_out_per_mtok", "share_report_source",
            "actions_enabled", "monthly_budget_usd", "per_user_budget_usd",
        ]
        help_texts = {
            "model": "Anthropic and OpenAI retain their defaults. Other providers require an explicit model ID; Azure requires your deployment name.",
            "base_url": "HTTP(S) provider endpoint or compatible gateway. AWS and Google use native cloud endpoints.",
            "monthly_budget_usd": "Total spend cap for the whole org per calendar month. Blank = no cap.",
            "per_user_budget_usd": "Spend cap per user per calendar month. Blank = no cap.",
        }

    def __init__(self, *args, org: Organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        self.initial["config_revision"] = self.instance.config_revision
        self._original = {
            name: copy.deepcopy(getattr(self.instance, name))
            for name in ("provider", "base_url", "auth_mode", "cloud_config", "api_key",
                         "cloud_credentials", "model", "config_revision", "connection_check")
        }
        for name, value in (self.instance.cloud_config or {}).items():
            if name in self.fields:
                self.initial[name] = value
        self.fields["api_key"].help_text = "Blank keeps the stored key only when the provider, endpoint and authentication identity are unchanged."
        # Never render stored credentials, including an unbound textarea.
        for name in ("api_key", "access_key_id", "secret_access_key", "session_token", "client_secret", "service_account"):
            self.initial[name] = ""
        if self.is_bound and "auth_mode" not in self.data:
            self.data = self.data.copy()
            self.data["auth_mode"] = self._original["auth_mode"]

    def advanced_open(self):
        return any(self[f].value() not in (None, "") or self[f].errors
                   for f in ("base_url", "price_in_per_mtok", "price_out_per_mtok"))

    @staticmethod
    def identity(data):
        return (data.get("provider"), effective_base_url(data.get("provider"), data.get("base_url", "")),
                data.get("auth_mode"), data.get("cloud_config") or {})

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("config_revision") not in (None, self._original["config_revision"]):
            self.add_error(None, "AI settings changed since this page was loaded. Review the current connection before saving.")
        provider = cleaned.get("provider")
        preset = PROVIDERS.get(provider)
        if preset is None:
            return cleaned
        mode = cleaned["auth_mode"] = cleaned.get("auth_mode") or "api_key"
        if mode not in preset["auth_modes"]:
            self.add_error("auth_mode", "Choose an authentication mode supported by this provider.")
        endpoint = cleaned["base_url"] = (cleaned.get("base_url") or "").rstrip("/")
        if endpoint:
            try:
                url = urlsplit(endpoint)
                port = url.port
            except ValueError:
                self.add_error("base_url", "Enter a valid endpoint URL and port.")
                url = urlsplit("")
                port = None
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                self.add_error("base_url", "Use an HTTP(S) endpoint without credentials, a query or a fragment.")
            if provider in {"bedrock", "vertex"}:
                self.add_error("base_url", "This provider uses native cloud endpoints; leave the URL blank.")
            if provider == "azure":
                azure_host = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.(?:openai|services\.ai)\.azure\.com", url.hostname or ""))
                if url.scheme != "https" or not azure_host or port not in (None, 443) or url.path not in ("", "/", "/openai/v1"):
                    self.add_error("base_url", "Use your HTTPS Azure OpenAI resource endpoint, without a deployment path.")
        if (provider in {"custom", "litellm", "azure"} or mode == "none") and not endpoint and cleaned.get("enabled"):
            self.add_error("base_url", "Enter an explicit endpoint URL for this connection.")
        if cleaned.get("enabled") and not preset["default_model"] and not cleaned.get("model"):
            self.add_error("model", "Enter a model ID or Azure deployment name. Discovery is optional.")
        cloud = {}
        names = {"bedrock": ("region",), "azure": ("tenant_id", "client_id") if mode == "client_secret" else (),
                 "vertex": ("project", "location")}.get(provider, ())
        for name in names:
            value = cleaned.get(name, "")
            if not value and cleaned.get("enabled"):
                self.add_error(name, "This field is required for this connection.")
            elif value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value):
                self.add_error(name, "Use a cloud identifier, without URL or configuration syntax.")
            cloud[name] = value
        cleaned["cloud_config"] = cloud
        same_identity = self.identity(cleaned) == self.identity(self._original)
        old_secrets = self._original["cloud_credentials"] if same_identity else {}
        old_secrets = old_secrets or {}
        secrets = {}
        key = cleaned.get("api_key", "")
        if mode == "api_key":
            key = key or (self._original["api_key"] if same_identity else "")
            if cleaned.get("enabled") and not key:
                self.add_error("api_key", "Enter a key for this connection. Changing the destination requires a new key.")
        else:
            key = ""
        if mode == "access_key":
            supplied = any(cleaned.get(n) for n in ("access_key_id", "secret_access_key", "session_token"))
            secrets = {n: cleaned.get(n, "") for n in ("access_key_id", "secret_access_key", "session_token")} if supplied else old_secrets
            for name in ("access_key_id", "secret_access_key"):
                if not secrets.get(name) and (cleaned.get("enabled") or supplied):
                    self.add_error(name, "Enter both AWS access key fields for this connection.")
        elif mode == "client_secret":
            secret = cleaned.get("client_secret") or old_secrets.get("client_secret")
            if not secret and cleaned.get("enabled"):
                self.add_error("client_secret", "Enter a client secret for this connection.")
            secrets = {"client_secret": secret} if secret else {}
        elif mode == "service_account":
            supplied = cleaned.get("service_account")
            if supplied:
                try:
                    account = json.loads(supplied)
                except (ValueError, TypeError):
                    account = None
            else:
                account = old_secrets.get("service_account")
            if not supplied and not cleaned.get("enabled"):
                secrets = old_secrets
            elif (not isinstance(account, dict) or account.get("type") != "service_account"
                    or not account.get("private_key") or not account.get("client_email")
                    or account.get("token_uri") != "https://oauth2.googleapis.com/token"
                    or account.get("universe_domain", "googleapis.com") != "googleapis.com"
                    or any(k in account for k in ("credential_source", "service_account_impersonation_url"))):
                self.add_error("service_account", "Enter Google service_account JSON with a private key, client email and the trusted Google token URI.")
            else:
                # Keep only service-account fields consumed by the credential constructor.
                secrets = {"service_account": {k: account[k] for k in
                    ("type", "project_id", "private_key_id", "private_key", "client_email", "client_id", "token_uri") if k in account}}
        elif mode == "workload" and cleaned.get("enabled") and not workload_allowed(provider, self.org.pk):
            self.add_error("auth_mode", "The instance operator has not authorized this organization's workload identity for this provider.")
        cleaned["api_key"] = key
        cleaned["cloud_credentials"] = secrets
        if (cleaned.get("price_in_per_mtok") is None) != (cleaned.get("price_out_per_mtok") is None):
            self.add_error(None, "Set both the input and the output price, or neither.")
        for name in ("price_in_per_mtok", "price_out_per_mtok", "monthly_budget_usd", "per_user_budget_usd"):
            if cleaned.get(name) is not None and cleaned[name] < 0:
                self.add_error(name, "Use zero or a positive amount.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.org = self.org
        obj.cloud_config = self.cleaned_data["cloud_config"]
        obj.cloud_credentials = self.cleaned_data["cloud_credentials"]
        changed = (self.identity(self.cleaned_data) != self.identity(self._original)
                   or any(getattr(obj, name) != self._original[name]
                          for name in ("api_key", "model", "cloud_credentials")))
        if changed:
            obj.config_revision = self._original["config_revision"] + (1 if obj.pk else 0)
            obj.connection_check = {}
        if commit:
            # Serialize only local persistence; external probes never hold this lock.
            with transaction.atomic():
                if obj.pk:
                    saved = type(obj).objects.select_for_update().get(pk=obj.pk)
                    if saved.config_revision != self._original["config_revision"]:
                        raise forms.ValidationError("AI settings changed while saving. Review the current connection and save again.")
                    if not changed:
                        obj.connection_check = saved.connection_check
                obj.save()
        return obj


class InviteForm(forms.Form):
    email = forms.EmailField()
    org_role = forms.ChoiceField(choices=roles.ORG_ROLE_CHOICES, initial=roles.ORG_MEMBER)
    groups = forms.ModelMultipleChoiceField(
        queryset=PermissionGroup.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, org: Organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        self.fields["groups"].queryset = org.permission_groups.order_by("name")
        # One role choice per studio: applied as direct StudioMemberships
        # the moment the invitation is accepted.
        self.studios = list(Studio.objects.filter(org=org).order_by("slug"))
        for studio in self.studios:
            self.fields[f"studio_role_{studio.pk}"] = forms.ChoiceField(
                required=False,
                choices=[("", "No access"), *roles.STUDIO_ROLE_CHOICES],
            )

    def studio_role_fields(self):
        """(studio, bound field) pairs for the template."""
        for studio in self.studios:
            yield studio, self[f"studio_role_{studio.pk}"]

    def studio_grants(self) -> list[dict]:
        grants = []
        for studio in self.studios:
            role = self.cleaned_data.get(f"studio_role_{studio.pk}")
            if role:
                grants.append({"studio_id": studio.pk, "role": role})
        return grants
