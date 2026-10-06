from django import forms

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


class AssistantConfigForm(forms.ModelForm):
    """AI assistant config; the API key is write-only (blank submit = keep)."""

    api_key = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Leave empty only if your custom endpoint authenticates for you.",
    )

    class Meta:
        model = OrgAssistantConfig
        fields = [
            "enabled", "provider", "api_key", "model", "base_url",
            "price_in_per_mtok", "price_out_per_mtok", "share_report_source",
            "actions_enabled", "monthly_budget_usd", "per_user_budget_usd",
        ]
        help_texts = {
            "model": "Blank uses the provider default (claude-sonnet-4-6 / gpt-4o).",
            "base_url": (
                "Leave empty to use the provider's own API. Set it only if you route "
                "requests through a proxy or run a model on your own infrastructure; "
                "the server must speak the selected provider's API."
            ),
            "monthly_budget_usd": "Total spend cap for the whole org per calendar month. Blank = no cap.",
            "per_user_budget_usd": "Spend cap per user per calendar month. Blank = no cap.",
        }

    def __init__(self, *args, org: Organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

    def advanced_open(self) -> bool:
        """The page's Advanced fold opens when any field in it has a value or an error."""
        return any(
            self[f].value() not in (None, "") or self[f].errors
            for f in ("base_url", "price_in_per_mtok", "price_out_per_mtok")
        )

    def clean(self):
        cleaned = super().clean()
        # Enabling without any usable credential just makes the widget report
        # itself unavailable — say so at save time instead.
        if cleaned.get("enabled") and not (cleaned.get("api_key") or cleaned.get("base_url")):
            has_stored = bool(self.instance.pk and self.instance.api_key)
            if not has_stored:
                raise forms.ValidationError(
                    "Add an API key (or a custom endpoint URL) before enabling the AI assistant."
                )
        if (cleaned.get("price_in_per_mtok") is None) != (cleaned.get("price_out_per_mtok") is None):
            raise forms.ValidationError("Set both the input and the output price, or neither.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.org = self.org
        if not self.cleaned_data.get("api_key") and obj.pk:
            obj.api_key = type(obj).objects.get(pk=obj.pk).api_key
        if commit:
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
