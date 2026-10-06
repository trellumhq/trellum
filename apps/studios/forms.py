from django import forms

from .models import StudioRepo


class StudioRepoForm(forms.ModelForm):
    """Repo config; secrets are write-only (blank submit = keep current)."""

    token = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Personal access token / deploy token. Leave blank to keep the current one.",
    )
    webhook_secret = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Shared secret for the push webhook (X-Hub-Signature-256). Leave blank to keep.",
    )

    class Meta:
        model = StudioRepo
        fields = [
            "repo_url", "branch", "path", "auth_method", "token",
            "sync_interval_minutes", "auto_run_changed", "publish_mode", "webhook_secret",
        ]

    def save(self, commit=True):
        obj = super().save(commit=False)
        # Preserve stored secrets when the field came back empty.
        if not self.cleaned_data.get("token") and self.instance.pk:
            obj.token = type(obj).objects.get(pk=obj.pk).token
        if not self.cleaned_data.get("webhook_secret") and self.instance.pk:
            obj.webhook_secret = type(obj).objects.get(pk=obj.pk).webhook_secret
        if commit:
            obj.save()
        return obj
