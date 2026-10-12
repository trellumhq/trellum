"""The Alerts page's create/edit form. Validation is the model's
(``AlertRule.clean`` is ``EmailSchedule.clean``); this only narrows the
choices to the studio: its reports, its members, its org's groups."""
from __future__ import annotations

import calendar
from datetime import time
from zoneinfo import available_timezones

from django import forms
from django.contrib.auth import get_user_model

from apps.alerts.models import AlertRule
from apps.orgs.models import OrgMembership, PermissionGroup
from apps.reports.models import Report


class RecipientCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        user = getattr(value, "instance", None)
        if user is not None and user.email:
            attrs = {**(attrs or {}), "data-recipient-name": user.name or user.email}
            if user.name:
                attrs["data-recipient-identity"] = user.email
        return super().create_option(name, value, label, selected, index, subindex, attrs)


class AlertRuleForm(forms.ModelForm):
    send_time = forms.TimeField(required=False, input_formats=["%H:%M"], label="Local time",
                                widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "step": 60}))
    hourly_minute = forms.TypedChoiceField(required=False, coerce=int, label="Minute past the hour",
                                           choices=[(i, f":{i:02d}") for i in range(60)])
    trigger = forms.ChoiceField(choices=AlertRule.TRIGGER_CHOICES, widget=forms.RadioSelect)
    weekday = forms.TypedChoiceField(
        choices=list(enumerate(calendar.day_name)), coerce=int, required=False,
        initial=0, empty_value=0,
    )
    month_day = forms.TypedChoiceField(
        choices=[(day, str(day)) for day in range(1, 29)], coerce=int, required=False,
        initial=1, empty_value=1,
    )
    recipient_roles = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Roles"
    )

    class Meta:
        model = AlertRule
        fields = [
            "name", "report", "instructions", "trigger",
            "freq", "send_hour", "send_minute", "send_time", "hourly_minute", "weekday", "month_day", "timezone",
            "recipients", "recipient_roles", "recipient_groups",
            "cooldown_hours", "notification_policy", "enabled",
        ]
        widgets = {
            "instructions": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Tell me if anything looks off"}
            ),
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
        self.timezones = sorted(available_timezones())
        timezone_choices = [(zone, zone.replace("_", " ")) for zone in self.timezones]
        posted_timezone = self.data.get(self.add_prefix("timezone")) if self.is_bound else None
        retained_timezone = posted_timezone or self.instance.timezone or "UTC"
        if retained_timezone not in self.timezones:
            timezone_choices.append((retained_timezone, retained_timezone))
        self.fields["timezone"] = forms.ChoiceField(
            choices=timezone_choices,
            initial=self.instance.timezone or "UTC", label="Timezone",
        )
        # Retain legacy POST clients that submit these model fields directly.
        self.fields["send_hour"].required = False
        self.fields["send_minute"].required = False
        for name in ("freq", "weekday", "month_day", "timezone"):
            self.fields[name].required = False
        self.fields["send_time"].initial = time(self.instance.send_hour or 0, self.instance.send_minute or 0)
        self.fields["hourly_minute"].initial = self.instance.send_minute
        self.fields["notification_policy"].initial = (self.instance.notification_policy or AlertRule.POLICY_REPEAT)
        if not self.is_bound:
            self.initial["notification_policy"] = self.instance.notification_policy or AlertRule.POLICY_REPEAT
        self.fields["report"].queryset = Report.objects.filter(
            studio=studio, present_in_scan=True, kind=Report.KIND_REPORT
        ).order_by("name", "slug")
        self.fields["report"].empty_label = None
        self.fields["report"].label_from_instance = lambda report: report.name or report.slug
        # Same eligibility as the delivery drawer's picker (reports.views.
        # api_studio_members): anyone with an effective role on this studio.
        from apps.core.permissions import bulk_role_for_studio

        org_users = [om.user for om in OrgMembership.objects.filter(org=studio.org).select_related("user")]
        role_by_id = bulk_role_for_studio(org_users, studio)
        people = self.fields["recipients"]
        people.widget = RecipientCheckboxSelectMultiple()
        people.queryset = get_user_model().objects.filter(
            pk__in=[u.pk for u in org_users if role_by_id.get(u.pk)]
        ).order_by("email")
        people.label_from_instance = lambda u: u.name or u.email
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
        data["freq"] = data.get("freq") or self.instance.freq or AlertRule.FREQ_DAILY
        data["timezone"] = data.get("timezone") or self.instance.timezone or "UTC"
        scheduled_time = data.get("send_time")
        schedule_active = data.get("trigger") == AlertRule.TRIGGER_SCHEDULE
        hourly = schedule_active and data.get("freq") == AlertRule.FREQ_HOURLY
        has_hourly_minute = self.add_prefix("hourly_minute") in self.data
        has_send_time = self.add_prefix("send_time") in self.data
        if hourly and has_hourly_minute:
            data["send_minute"] = data.get("hourly_minute")
            data["send_hour"] = self.instance.send_hour if self.instance.send_hour is not None else 0
            if not self.data.get(self.add_prefix("hourly_minute")):
                self.add_error("hourly_minute", "Choose a minute past the hour.")
        elif schedule_active and not hourly and has_send_time and not scheduled_time:
            self.add_error("send_time", "Enter a local time.")
        elif scheduled_time:
            data["send_hour"], data["send_minute"] = scheduled_time.hour, scheduled_time.minute
        else:
            data["send_hour"] = data.get("send_hour") if data.get("send_hour") is not None else (
                self.instance.send_hour if self.instance.send_hour is not None else 8)
            data["send_minute"] = data.get("send_minute") if data.get("send_minute") is not None else (
                self.instance.send_minute if self.instance.send_minute is not None else 0)
        for name in ("send_hour", "send_minute"):
            if data.get(name) not in (None, ""):
                data[name] = int(data[name])
            else:
                existing = self.instance.send_hour if name == "send_hour" else self.instance.send_minute
                data[name] = existing if existing is not None else (8 if name == "send_hour" else 0)
        data["weekday"] = data.get("weekday") if data.get("weekday") is not None else (self.instance.weekday or 0)
        data["month_day"] = data.get("month_day") if data.get("month_day") is not None else (self.instance.month_day or 1)
        data["notification_policy"] = data.get("notification_policy") or self.instance.notification_policy or AlertRule.POLICY_REPEAT
        if not (data.get("recipients") or data.get("recipient_roles") or data.get("recipient_groups")):
            raise forms.ValidationError("Select at least one recipient (a person, a role, or a group).")
        return data

    def save(self, commit=True):
        rule = super().save(commit=False)
        if commit:
            if rule.pk:
                concrete = {f.name for f in rule._meta.concrete_fields}
                rule.save(update_fields=[name for name in self.Meta.fields if name in concrete])
            else:
                rule.save()
            self.save_m2m()
        return rule
