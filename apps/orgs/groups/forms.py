from django import forms

from apps.core import roles
from apps.orgs.models import Organization, PermissionGroup
from apps.reports.models import Report
from apps.studios.models import Studio


class GroupForm(forms.ModelForm):
    class Meta:
        model = PermissionGroup
        fields = ["name", "description", "org_role", "default_studio_role"]

    def __init__(self, *args, org: Organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

    def clean_name(self):
        name = self.cleaned_data["name"]
        qs = PermissionGroup.objects.filter(org=self.org, name=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A group with this name already exists.")
        return name


class ReportChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, report):
        label = report.name or report.slug
        if not report.present_in_scan:
            return f"{label} (missing from repository)"
        if report.disabled:
            return f"{label} (inactive)"
        return label


class StudioAccessForm(forms.Form):
    studio = forms.ModelChoiceField(
        queryset=Studio.objects.none(), widget=forms.HiddenInput
    )
    role = forms.ChoiceField(choices=[("", "No access"), *roles.STUDIO_ROLE_CHOICES])
    viewer_scope = forms.ChoiceField(
        choices=[("all", "All reports"), ("selected", "Selected reports")],
        required=False,
    )
    reports = ReportChoiceField(
        queryset=Report.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(
        self, *args, org: Organization, studio: Studio | None = None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.fields["studio"].queryset = Studio.objects.filter(org=org)
        if studio is None and self.is_bound:
            raw_studio = str(self.data.get("studio") or "")
            if raw_studio.isdecimal():
                studio = self.fields["studio"].queryset.filter(pk=raw_studio).first()
        self.studio = studio
        if studio:
            self.auto_id = f"id_studio_{studio.pk}_%s"
        self.fields["reports"].queryset = (
            Report.objects.filter(studio=studio).order_by("name", "slug")
            if studio
            else Report.objects.none()
        )

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        scope = cleaned.get("viewer_scope")
        if role == roles.VIEWER and not scope:
            self.add_error("viewer_scope", "Choose which reports Viewer can access.")
        elif role != roles.VIEWER:
            cleaned["viewer_scope"] = "all"
        return cleaned
