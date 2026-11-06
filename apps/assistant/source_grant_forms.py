"""Explicit studio source policy editor; credentials remain on the source."""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.assistant.source_models import MAX_PRINCIPALS, validate_relations
from apps.datasources.models import DataSource, SSH_FIELDS
from apps.orgs.models import PermissionGroup
from apps.reports.recipient_choices import cap_model_choices


def source_choices(studio):
    rows = DataSource.objects.filter(Q(studio=studio) | Q(studio__isnull=True, org=studio.org), type="postgres")
    for field in SSH_FIELDS:
        if field not in {"ssh_password", "ssh_private_key"}:
            rows = rows.filter(Q(**{f"config__{field}__isnull": True}) | Q(**{f"config__{field}": ""}))
    return rows.order_by("name", "pk")


def principal_choices(studio):
    return (
        get_user_model().objects.filter(org_memberships__org=studio.org, is_active=True).order_by("email", "pk"),
        PermissionGroup.objects.filter(org=studio.org).order_by("name", "pk"),
    )


class BoundedPrincipals(forms.ModelMultipleChoiceField):
    def clean(self, value):
        if value and len(value) > MAX_PRINCIPALS:
            raise forms.ValidationError("Choose at most 500 principals of each kind.")
        return super().clean(value)


class SourceGrantForm(forms.Form):
    datasource = forms.ModelChoiceField(queryset=DataSource.objects.none(), label="Source")
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    relations = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), max_length=13000,
                               label="Allowed tables and views", help_text="One schema.table per line. Only these relations may be read.")
    users = BoundedPrincipals(queryset=get_user_model().objects.none(), required=False, widget=forms.CheckboxSelectMultiple)
    groups = BoundedPrincipals(queryset=PermissionGroup.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)
    enabled = forms.BooleanField(required=False, label="Enable source reads for the selected people and groups")

    def __init__(self, *args, studio, grant=None, base_url, **kwargs):
        initial = dict(kwargs.pop("initial", {}))
        if grant:
            initial.update(datasource=grant.datasource_id, revision=grant.revision, relations="\n".join(grant.relations),
                           enabled=grant.enabled, users=list(grant.users.values_list("pk", flat=True)[:MAX_PRINCIPALS]),
                           groups=list(grant.groups.values_list("pk", flat=True)[:MAX_PRINCIPALS]))
        super().__init__(*args, initial=initial, **kwargs)
        self.fields["datasource"].queryset = source_choices(studio)
        self.fields["datasource"].label_from_instance = lambda ds: ds.name
        users, groups = principal_choices(studio)
        self.fields["users"].queryset = users
        self.fields["users"].label_from_instance = lambda u: f"{u.name} ({u.email})" if u.name else u.email
        self.fields["groups"].queryset = groups
        self.fields["groups"].label_from_instance = lambda g: g.name
        def selected(name):
            return self.data.getlist(name)[:MAX_PRINCIPALS] if self.is_bound else initial.get(name, [])
        if cap_model_choices(self.fields["datasource"], [self.data.get("datasource")] if self.is_bound else [initial.get("datasource")]):
            self.fields["datasource"].widget.attrs["data-choice-source"] = base_url + "/sources"
        self.recipient_source = ""
        large = cap_model_choices(self.fields["users"], lambda: selected("users"))
        large = cap_model_choices(self.fields["groups"], lambda: selected("groups")) or large
        if large:
            self.recipient_source = base_url + "/principals"

    def clean_datasource(self):
        source = self.cleaned_data["datasource"]
        if any(v for k, v in source.all_fields().items() if k.startswith("ssh_")):
            raise forms.ValidationError("Only direct PostgreSQL sources without SSH support assistant reads.")
        return source

    def clean_relations(self):
        relations = self.cleaned_data["relations"].splitlines()
        relations = [line.strip() for line in relations if line.strip()]
        validate_relations(relations)
        return relations

    def clean(self):
        data = super().clean()
        if data.get("enabled") and not (data.get("users") or data.get("groups")):
            raise forms.ValidationError("Select people or groups before enabling source reads.")
        return data
