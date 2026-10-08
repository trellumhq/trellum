"""Instance-level forms (operator scope)."""
import json
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables

from .models import EmailApiConnection, InstanceConfig
from .email_providers import PROVIDER_CHOICES, validate_api_profile
from .email_mapping import default_custom_config, validate_custom_config


class InstanceSettingsForm(forms.ModelForm):
    """Instance name, public URL and SMTP.

    Shared by the first-run wizard and the ``/system`` page so there is one
    definition of what an operator may change. The SMTP password is
    write-only: rendering a stored secret back into a form is how secrets end
    up in browser caches and screenshots.
    """

    email_host_password = forms.CharField(
        label="SMTP password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Blank = keep current.",
    )
    # Declared explicitly so the scheme assumed for a bare hostname is pinned
    # here rather than following Django's shifting default.
    public_base_url = forms.URLField(required=False, assume_scheme="https")
    # Optional so an existing caller that does not know about the limit — the
    # first-run wizard, a scripted POST — leaves it at whatever is stored.
    max_upload_mb = forms.IntegerField(
        min_value=0, required=False,
        label="Max upload size (MB)",
        help_text=(
            "Largest data-source file a tenant may upload. 0 = no limit. Your "
            "reverse proxy must allow at least this much too — nginx's "
            "client_max_body_size defaults to 1 MB."
        ),
    )

    class Meta:
        model = InstanceConfig
        fields = [
            "instance_name",
            "public_base_url",
            "email_host",
            "email_port",
            "email_use_tls",
            "email_host_user",
            "email_host_password",
            "email_from",
            "max_upload_mb",
        ]
        labels = {
            "instance_name": "Instance name",
            "public_base_url": "Public URL",
            "email_host": "SMTP host",
            "email_port": "Port",
            "email_use_tls": "Use STARTTLS",
            "email_host_user": "SMTP username",
            "email_from": "Send mail from",
            "max_upload_mb": "Max upload size (MB)",
        }

    def clean_public_base_url(self):
        return (self.cleaned_data.get("public_base_url") or "").strip().rstrip("/")

    def clean_max_upload_mb(self):
        value = self.cleaned_data.get("max_upload_mb")
        if value is None:
            return InstanceConfig.load().max_upload_mb
        return value

    def clean(self):
        cleaned = super().clean()
        # A host with no From: address produces mail most servers reject.
        if cleaned.get("email_host") and not cleaned.get("email_from"):
            self.add_error(
                "email_from",
                "Set the address mail should come from — SMTP servers reject messages without one.",
            )
        return cleaned

    def save(self, commit=True):
        row = super().save(commit=False)
        if not self.cleaned_data.get("email_host_password"):
            # Write-only field left blank: keep whatever is already stored.
            row.email_host_password = InstanceConfig.load().email_host_password
        if commit:
            if row._state.adding:
                row.save()
            else:
                row.save(update_fields=self.Meta.fields)
        return row


class SecuritySettingsForm(forms.ModelForm):
    """Instance-scoped session policy, lockout thresholds and the operator
    MFA requirement. Rendered as its own card on ``/system`` next to
    :class:`InstanceSettingsForm` -- see apps.core.views.system_page.

    Remember-me is deliberately absent: the field exists on the model
    (stubbed off) but its checkbox is deferred v1 UI -- see
    ``InstanceConfig.session_remember_me_enabled``'s docstring.
    """

    class Meta:
        model = InstanceConfig
        fields = [
            "session_idle_minutes",
            "session_absolute_hours",
            "session_expire_at_browser_close",
            "lockout_account_threshold",
            "lockout_ip_threshold",
            "lockout_window_minutes",
            "lockout_cooloff_minutes",
            "require_mfa_operators",
        ]
        labels = {
            "session_idle_minutes": "Idle timeout (minutes)",
            "session_absolute_hours": "Absolute session cap (hours)",
            "session_expire_at_browser_close": "Expire sessions when the browser closes",
            "lockout_account_threshold": "Failed attempts per account",
            "lockout_ip_threshold": "Failed attempts per address",
            "lockout_window_minutes": "Counting window (minutes)",
            "lockout_cooloff_minutes": "Base cooloff (minutes)",
            "require_mfa_operators": "Require MFA for instance operators",
        }
        help_texts = {
            "session_idle_minutes": "Sign out after this much inactivity. 0 = disabled.",
            "session_absolute_hours": "Hard cap on a session's age from login. 0 = disabled.",
            "lockout_cooloff_minutes": "Doubles on each repeat lockout within 24h, capped at 240.",
            "require_mfa_operators": (
                "Operators are exempt from org SSO enforcement by design (lockout "
                "safety), which makes this the compensating control."
            ),
        }

    def save(self, commit=True):
        row = super().save(commit=False)
        if commit:
            row.save(update_fields=self.Meta.fields)
        return row


class EmailApiConnectionForm(forms.Form):
    """Write-only credentials and provider-specific options for one saved route."""

    name = forms.CharField(max_length=100)
    provider = forms.ChoiceField(choices=PROVIDER_CHOICES)
    from_email = forms.EmailField(label="Verified sender address")
    region = forms.CharField(required=False)
    credential_source = forms.ChoiceField(
        choices=(("deployment", "Deployment IAM role / AWS credentials"),
                 ("access_keys", "Access keys stored here")), required=False,
    )
    sender_domain = forms.CharField(required=False)
    message_stream = forms.CharField(required=False)
    api_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    secret_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    server_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    api_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    aws_access_key_id = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    aws_secret_access_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    aws_session_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    clear_aws_session_token = forms.BooleanField(required=False, label="Remove saved session token")
    endpoint = forms.URLField(required=False, assume_scheme="https")
    auth_type = forms.ChoiceField(
        choices=(("bearer", "Bearer token"), ("api_key_header", "API key header"),
                 ("basic", "Basic username and password")), required=False,
    )
    auth_header_name = forms.CharField(required=False)
    token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    username = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    payload = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 18, "spellcheck": "false"}))
    accepted_statuses = forms.CharField(required=False, initial="200, 201, 202")
    response_condition_pointer = forms.CharField(required=False)
    response_condition_equals = forms.CharField(required=False)
    message_id_pointer = forms.CharField(required=False)
    snapshot_mode = forms.ChoiceField(
        choices=(("attachment", "File attachment"), ("inline", "Inline image")), required=False,
    )
    max_request_bytes = forms.IntegerField(required=False, min_value=1, max_value=25 * 1024 * 1024)

    def __init__(self, *args, instance=None, copy_from=None, **kwargs):
        self.instance = instance
        unbound = (not args or args[0] is None) and kwargs.get("data") is None
        if instance is None and copy_from is None and unbound:
            example = default_custom_config()
            kwargs["initial"] = {
                "provider": "sendgrid", "region": "global", "credential_source": "deployment",
                "auth_type": example["auth"]["type"], "endpoint": example["endpoint"],
                "payload": json.dumps(example["payload"], indent=2, ensure_ascii=False),
                "accepted_statuses": "200, 201, 202", "snapshot_mode": "attachment",
                "max_request_bytes": example["max_request_bytes"],
                **kwargs.get("initial", {}),
            }
        seed = instance or copy_from
        if seed is not None and unbound:
            config = seed.provider_config or {}
            custom = seed.custom_config or {}
            response = custom.get("response", {})
            initial = {
                "name": instance.name if instance else f"{copy_from.name[:94]} copy",
                "provider": seed.provider,
                "from_email": seed.from_email,
                "region": config.get("region", ""),
                "credential_source": config.get("credential_source", "deployment"),
                "sender_domain": config.get("sender_domain", ""),
                "message_stream": config.get("message_stream", ""),
                "endpoint": custom.get("endpoint", ""),
                "auth_type": custom.get("auth", {}).get("type", "bearer"),
                "auth_header_name": custom.get("auth", {}).get("header_name", ""),
                "payload": json.dumps(custom.get("payload", {}), indent=2, ensure_ascii=False),
                "accepted_statuses": ", ".join(map(str, response.get("accepted_statuses", [200, 201, 202]))),
                "response_condition_pointer": response.get("condition", {}).get("pointer", ""),
                "response_condition_equals": json.dumps(response["condition"]["equals"])
                    if "condition" in response else "",
                "message_id_pointer": response.get("message_id_pointer", ""),
                "snapshot_mode": custom.get("attachments", {}).get("snapshot_mode", "attachment"),
                "max_request_bytes": custom.get("max_request_bytes", 10 * 1024 * 1024),
            }
            kwargs["initial"] = {**initial, **kwargs.get("initial", {})}
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.fields["provider"].widget.attrs["disabled"] = True
        for index in range(10):
            self.fields[f"header_name_{index}"] = forms.CharField(required=False, label="Header name")
            self.fields[f"header_value_{index}"] = forms.CharField(
                required=False, label="Header value", widget=forms.PasswordInput(render_value=False),
            )
            self.fields[f"header_clear_{index}"] = forms.BooleanField(required=False, label="Remove header")
        names = (seed.custom_config or {}).get("header_names", []) if seed else []
        for index, name in enumerate(names[:10]):
            self.fields[f"header_name_{index}"].initial = name
        self.header_rows = [
            (self[f"header_name_{i}"], self[f"header_value_{i}"], self[f"header_clear_{i}"])
            for i in range(10)
        ]

    def clean_provider(self):
        provider = self.cleaned_data["provider"]
        if self.instance and provider != self.instance.provider:
            raise ValidationError("Create a new connection to change provider.")
        return provider

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("provider") == "custom_https" and not cleaned.get("auth_type"):
            self.add_error("auth_type", "Choose an authentication method.")
        return cleaned

    @sensitive_variables()
    def build_profile(self, *, preview=False):
        """Return non-secret config and merged secrets after form validation."""
        data = self.cleaned_data
        provider = data["provider"]
        previous = self.instance.credentials if self.instance and not preview else {}
        previous = previous or {}
        credentials = dict(previous)
        provider_config = {}
        custom_config = {}
        if provider == "sendgrid":
            provider_config = {"region": data["region"] or "global"}
        elif provider == "amazon_ses":
            provider_config = {"region": data["region"],
                               "credential_source": data["credential_source"] or "deployment"}
        elif provider == "mailgun":
            provider_config = {"region": data["region"], "sender_domain": data["sender_domain"]}
        elif provider == "postmark":
            if data["message_stream"]:
                provider_config = {"message_stream": data["message_stream"]}

        if provider == "custom_https":
            base = default_custom_config()
            try:
                if len(data["payload"].encode("utf-8")) > 64 * 1024:
                    raise ValueError("Payload mapping too large")
                payload = json.loads(data["payload"], object_pairs_hook=_unique_pairs)
                statuses = [int(item.strip()) for item in data["accepted_statuses"].split(",")]
                response = {"accepted_statuses": statuses}
                if data["response_condition_pointer"]:
                    response["condition"] = {
                        "pointer": data["response_condition_pointer"],
                        "equals": json.loads(data["response_condition_equals"], object_pairs_hook=_unique_pairs),
                    }
            except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
                raise ValidationError("Enter valid JSON and HTTP statuses for the custom contract.") from exc
            if data["message_id_pointer"]:
                response["message_id_pointer"] = data["message_id_pointer"]
            auth = {"type": data["auth_type"]}
            if data["auth_type"] == "api_key_header":
                auth["header_name"] = data["auth_header_name"]
            headers = {}
            previous_names = set((self.instance.custom_config or {}).get("header_names", [])) if self.instance else set()
            cleared_names = set()
            for index in range(10):
                name = data[f"header_name_{index}"].strip()
                value = data[f"header_value_{index}"]
                clear = data[f"header_clear_{index}"]
                if name and clear:
                    cleared_names.add(name)
                if name and not clear:
                    if name in headers:
                        raise ValidationError("Header names must be unique.")
                    if name in previous_names and not value and not preview:
                        value = previous.get("headers", {}).get(name, "")
                    headers[name] = value or next(
                        (old_value for old_name, old_value in previous.get("headers", {}).items()
                         if old_name == name), ""
                    )
            if previous_names - set(headers) - cleared_names:
                raise ValidationError("Use Remove header to delete a saved header.")
            custom_config = {
                "schema_version": 1, "endpoint": data["endpoint"], "auth": auth,
                "header_names": list(headers), "payload": payload, "response": response,
                "attachments": {"snapshot_mode": data["snapshot_mode"] or "attachment"},
                "max_request_bytes": data["max_request_bytes"] or base["max_request_bytes"],
            }
            old_config = self.instance.custom_config if self.instance else {}
            if old_config and (
                _endpoint_origin(old_config.get("endpoint", "")) != _endpoint_origin(custom_config["endpoint"])
                or old_config.get("auth") != custom_config["auth"]
            ):
                credentials = {}
                headers = {name: data[f"header_value_{i}"] for i in range(10)
                           if (name := data[f"header_name_{i}"].strip()) and not data[f"header_clear_{i}"]}
            key_names = {"bearer": ("token",), "api_key_header": ("api_key",),
                         "basic": ("username", "password")}[data["auth_type"]]
            credentials = {key: (data[key] or credentials.get(key, "")) for key in key_names}
            credentials["headers"] = headers
        else:
            credential_keys = {
                "sendgrid": ("api_key",), "amazon_ses": ("aws_access_key_id", "aws_secret_access_key", "aws_session_token"),
                "mailgun": ("api_key",), "postmark": ("server_token",), "brevo": ("api_key",),
                "resend": ("api_key",), "mailjet": ("api_key", "secret_key"),
                "mailersend": ("api_token",), "mailtrap": ("api_token",),
            }[provider]
            credentials = {key: (data[key] or previous.get(key, "")) for key in credential_keys}
            if provider == "amazon_ses":
                if provider_config["credential_source"] == "deployment":
                    credentials = {}
                elif self.instance and self.instance.provider_config.get("credential_source") != "access_keys":
                    credentials = {key: data[key] for key in credential_keys if data[key]}
                if data["clear_aws_session_token"]:
                    credentials.pop("aws_session_token", None)
            credentials = {key: value for key, value in credentials.items() if value}

        if preview:
            validate_custom_config(custom_config)
        else:
            validate_api_profile(provider, provider_config, custom_config, credentials)
        return provider_config, custom_config, credentials


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _endpoint_origin(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or 443)
    except ValueError:
        return None
