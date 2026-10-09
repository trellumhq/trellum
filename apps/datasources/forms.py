"""One form for every source type; the server sorts fields into the
non-secret config JSON vs the encrypted credentials JSON. Secret fields are
write-only: blank on edit = keep the stored value."""
from __future__ import annotations

from django import forms

from apps.datasources.models import (
    CONFIG_KEYS,
    CREDENTIAL_KEYS,
    INLINE_TYPES,
    ORG_ALLOWED_TYPES,
    TYPE_FIELDS,
    DataSource,
    name_validator,
)

_CONFIG_FIELDS = [
    ("host", forms.CharField(required=False)),
    ("port", forms.IntegerField(required=False)),
    ("database", forms.CharField(required=False)),
    ("path", forms.CharField(
        required=False,
        help_text="Relative to this studio's project root — NOT to your "
                  "repository root. Git sync copies the report directory out of "
                  "your repo into reports/, so a CSV committed beside the sales "
                  "report is reports/sales/data.csv here. Anywhere else (e.g. "
                  "data-sources/files/budget.csv) is yours and git never touches "
                  "it. Blank = data-sources/files/<name>, set on first upload.",
    )),
    ("upload", forms.BooleanField(
        required=False, initial=True,
        label="Allow replacing this file from the portal",
    )),
    ("project", forms.CharField(required=False, label="Project (BigQuery)")),
    ("account", forms.CharField(required=False, label="Account (Snowflake)")),
    ("warehouse", forms.CharField(required=False)),
    ("schema", forms.CharField(required=False)),
    ("catalog", forms.CharField(required=False)),
    ("http_path", forms.CharField(required=False, label="HTTP path (Databricks)")),
    ("secure", forms.BooleanField(required=False, label="Use HTTPS")),
    ("tenant_id", forms.CharField(required=False, label="Tenant ID (Entra)")),
    ("client_id", forms.CharField(required=False)),
    ("site_url", forms.CharField(required=False, label="Site URL (SharePoint)")),
    ("credentials_path", forms.CharField(required=False)),
    ("ssh_host", forms.CharField(required=False, label="SSH host")),
    ("ssh_port", forms.IntegerField(required=False, label="SSH port")),
    ("ssh_user", forms.CharField(required=False, label="SSH user")),
    ("ssh_host_key", forms.CharField(required=False, label="SSH host key")),
]

_SECRET_FIELDS = [
    ("user", forms.CharField(required=False)),
    ("password", forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))),
    ("client_secret", forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))),
    ("access_token", forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        label="Access token (Databricks)",
    )),
    ("credentials_json", forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}),
        label="Credentials JSON (service account)",
    )),
    ("ssh_private_key", forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}),
        label="SSH private key",
    )),
    ("ssh_password", forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        label="SSH password",
    )),
]



class DataSourceForm(forms.Form):
    name = forms.CharField(max_length=64, validators=[name_validator])
    type = forms.ChoiceField(choices=DataSource.TYPES)
    description = forms.CharField(max_length=400, required=False)
    scope = forms.ChoiceField(
        choices=[("studio", "This studio only"), ("org", "Entire organization")],
        initial="studio", widget=forms.RadioSelect, required=False,
    )

    def __init__(
        self, *args, instance: DataSource | None = None, studio=None, org=None,
        allow_org_scope: bool = False, fixed_scope: str | None = None, **kwargs,
    ):
        initial = kwargs.pop("initial", {})
        if instance is not None:
            initial.update(
                {
                    "name": instance.name,
                    "type": instance.type,
                    "description": instance.description,
                    "scope": instance.scope,
                    **{k: v for k, v in (instance.config or {}).items()},
                    "user": (instance.credentials or {}).get("user", ""),
                    # password/client_secret/credentials_json stay blank (write-only)
                }
            )
        super().__init__(*args, initial=initial, **kwargs)
        self.instance = instance
        self.studio = studio
        self.org = org or (studio.org if studio else None)
        self.fixed_scope = fixed_scope
        self.allow_org_scope = allow_org_scope or fixed_scope == "org"
        if fixed_scope:
            self.fields["scope"].initial = fixed_scope
        if not self.allow_org_scope or fixed_scope:
            # No choice to make: the widget disappears from the page.
            self.fields["scope"].widget = forms.HiddenInput()
        for key, field in _CONFIG_FIELDS + _SECRET_FIELDS:
            self.fields[key] = field

    def clean(self):
        cleaned = super().clean()
        scope = self.fixed_scope or cleaned.get("scope") or "studio"
        if scope == "org" and not self.allow_org_scope:
            scope = "studio"
        cleaned["scope"] = scope
        ds_type = cleaned.get("type")
        if scope == "org" and ds_type and ds_type not in ORG_ALLOWED_TYPES:
            raise forms.ValidationError(
                f"“{ds_type}” sources cannot be shared across an organization."
            )
        # A file source needs SOMEWHERE to read from: either a path that is
        # already populated, or permission for the portal to create one.
        if ds_type in INLINE_TYPES:
            path = (cleaned.get("path") or "").strip()
            if path:
                from types import SimpleNamespace

                from apps.datasources.materialize import resolve_path

                candidate = SimpleNamespace(
                    org_id=self.org.pk if scope == "org" else None,
                    org=self.org,
                    studio=self.studio,
                )
                try:
                    resolve_path(candidate, path)
                except ValueError as exc:
                    self.add_error("path", str(exc))
            if not (cleaned.get("path") or "").strip() and not cleaned.get("upload"):
                self.add_error(
                    "path",
                    "Give the path of the file, or allow replacing it from the "
                    "portal so an upload can create it.",
                )
        if scope == "studio" and self.studio is None:
            raise forms.ValidationError("Pick a studio scope from a studio's settings page.")
        # Name unique within the TARGET scope.
        name = cleaned.get("name")
        if name:
            if scope == "org":
                qs = DataSource.objects.filter(org=self.org, name=name)
            else:
                qs = DataSource.objects.filter(studio=self.studio, name=name)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("name", "A source with this name already exists in that scope.")
        return cleaned

    def save(self, user=None) -> DataSource:
        data = self.cleaned_data
        # Only the selected type's fields are kept — switching type drops
        # stale values (incl. old credentials) instead of carrying them.
        relevant = set(TYPE_FIELDS.get(data["type"], []))
        config = {
            k: data[k]
            for k, _ in _CONFIG_FIELDS
            if k in CONFIG_KEYS and k in relevant and data.get(k) not in (None, "", False)
        }
        if data.get("upload") and "upload" in relevant:
            config["upload"] = True

        stored = dict((self.instance.credentials or {}) if self.instance else {})
        for k, _ in _SECRET_FIELDS:
            if k in CREDENTIAL_KEYS and str(data.get(k) or "").strip():
                stored[k] = data[k]
        # "user" is stored with secrets so it travels with them.
        if data.get("user"):
            stored["user"] = data["user"]
        stored = {k: v for k, v in stored.items() if k in relevant}

        ds = self.instance if self.instance is not None else DataSource()
        # Scope is switchable on edit: moving studio->org shares it with
        # every studio; org->studio pins it to this studio.
        if data["scope"] == "org":
            ds.org = self.org
            ds.studio = None
        else:
            ds.studio = self.studio
            ds.org = None
        ds.name = data["name"]
        ds.type = data["type"]
        ds.description = data.get("description", "")
        ds.config = config
        ds.credentials = stored or None
        ds.updated_by = user
        ds.last_check_at = None
        ds.last_check_ok = None
        ds.last_check_error = ""
        ds.save()
        return ds
