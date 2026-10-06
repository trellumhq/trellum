"""Instance-level forms (operator scope)."""
from django import forms

from .models import InstanceConfig


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
            row.save()
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
