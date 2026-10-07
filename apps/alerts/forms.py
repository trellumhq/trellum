"""The Alerts page's create/edit form. Validation is the model's
(``AlertRule.clean`` is ``EmailSchedule.clean``); this only narrows the
choices to the studio: its reports, its members, its org's groups."""
from __future__ import annotations

import calendar

from django import forms
from django.contrib.auth import get_user_model

from apps.alerts.models import AlertRule
from apps.orgs.models import OrgMembership, PermissionGroup
from apps.reports.models import Report

#: Mirrors COMMON_TIMEZONES in static/report_delivery.js -- suggestions for
#: the timezone box, not the whole list; the model rejects unknown zones.
COMMON_TIMEZONES = [
    "UTC", "Europe/London", "Europe/Amsterdam", "Europe/Berlin", "Europe/Paris",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Sao_Paulo", "Asia/Kolkata", "Asia/Dubai", "Asia/Shanghai", "Asia/Tokyo",
    "Australia/Sydney",
]


class AlertRuleForm(forms.ModelForm):
    trigger = forms.ChoiceField(choices=AlertRule.TRIGGER_CHOICES, widget=forms.RadioSelect)
    weekday = forms.TypedChoiceField(
        choices=list(enumerate(calendar.day_name)), coerce=int, required=False,
        initial=0, empty_value=0,
    )
    recipient_roles = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Roles"
    )

    class Meta:
        model = AlertRule
        fields = [
            "name", "report", "instructions", "trigger",
            "freq", "send_hour", "send_minute", "weekday", "month_day", "timezone",
            "recipients", "recipient_roles", "recipient_groups",
            "cooldown_hours", "enabled",
        ]
        widgets = {
            "instructions": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Tell me if anything looks off"}
            ),
            "timezone": forms.TextInput(attrs={"list": "alert-timezones", "autocomplete": "off"}),
            "recipients": forms.CheckboxSelectMultiple,
            "recipient_groups": forms.CheckboxSelectMultiple,
        }
        labels = {
            "send_hour": "Hour", "send_minute": "Minute", "month_day": "Day of month",
            "cooldown_hours": "Cooldown (hours)", "recipients": "People",
            "recipient_groups": "Permission groups",
        }

    def __init__(self, *args, studio, **kwargs):
        super().__init__(*args, **kwargs)
        self.timezones = COMMON_TIMEZONES
        self.fields["report"].queryset = Report.objects.filter(
            studio=studio, present_in_scan=True, kind=Report.KIND_REPORT
        ).order_by("name", "slug")
        self.fields["report"].empty_label = None
        # Same eligibility as the delivery drawer's picker (reports.views.
        # api_studio_members): anyone with an effective role on this studio.
        from apps.core.permissions import bulk_role_for_studio

        org_users = [om.user for om in OrgMembership.objects.filter(org=studio.org).select_related("user")]
        role_by_id = bulk_role_for_studio(org_users, studio)
        people = self.fields["recipients"]
        people.queryset = get_user_model().objects.filter(
            pk__in=[u.pk for u in org_users if role_by_id.get(u.pk)]
        ).order_by("email")
        people.label_from_instance = lambda u: u.display_name or u.email
        self.fields["recipient_roles"].choices = [
            ("admins", "All admins"), ("developers", "All developers"),
            ("everyone", f"Everyone in {studio.name}"),
        ]
        groups = PermissionGroup.objects.filter(org=studio.org).order_by("name")
        if groups.exists():
            self.fields["recipient_groups"].queryset = groups
        else:
            del self.fields["recipient_groups"]

    def clean(self):
        data = super().clean()
        if not (data.get("recipients") or data.get("recipient_roles") or data.get("recipient_groups")):
            raise forms.ValidationError("Select at least one recipient (a person, a role, or a group).")
        return data
