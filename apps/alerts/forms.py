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
from apps.orgs.models import PermissionGroup
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

    def __init__(self, *args, studio, presentation=None, **kwargs):
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
        from apps.reports.recipient_choices import inline_snapshot, recipient_snapshot, selected_ids
        presentation = presentation or {}
        snapshot = recipient_snapshot(studio, include_inactive=True)
        eligible_ids = [user['id'] for user in snapshot['members']]
        people = self.fields['recipients']
        people.widget = RecipientCheckboxSelectMultiple()
        people.queryset = get_user_model().objects.filter(pk__in=eligible_ids).order_by('email')
        people.label_from_instance = lambda u: f'{u.name} ({u.email})' if u.name else u.email
        self.fields['recipient_roles'].choices = [
            ('admins', 'All admins'), ('developers', 'All developers'),
            ('everyone', f'Everyone in {studio.name}')]
        group_field = self.fields['recipient_groups']
        group_field.queryset = PermissionGroup.objects.filter(org_id=studio.org_id).order_by('name', 'pk')
        group_field.label_from_instance = lambda group: group.name
        def submitted(name):
            key = self.add_prefix(name)
            if hasattr(self.data, 'getlist'):
                return self.data.getlist(key)
            value = self.data.get(key, [])
            return list(value) if isinstance(value, (list, tuple)) else [str(value)]
        selected_people = submitted('recipients') if self.is_bound else [str(u.pk) for u in self.instance.recipients.all()] if self.instance.pk else []
        selected_groups = submitted('recipient_groups') if self.is_bound else [str(g.pk) for g in self.instance.recipient_groups.all()] if self.instance.pk else []
        self.recipient_page = inline_snapshot(snapshot, selected_people=selected_people, selected_groups=selected_groups)
        for field, key in [('recipients', 'members'), ('recipient_groups', 'groups')]:
            rows = {row['id']: row for row in self.recipient_page['selected'][key] + self.recipient_page[key]}
            choices = [(row['id'], (f"{row['name']} ({row['email']})" if key == 'members' and row['name'] != row['email'] else row['name'])) for row in rows.values()]
            for value in selected_ids(selected_people if key == 'members' else selected_groups) - rows.keys():
                choices.append((value, 'Selected person is unavailable' if key == 'members' else 'Selected group is unavailable'))
            self.fields[field].choices = choices
        if not snapshot['groups'] and not selected_ids(selected_groups):
            del self.fields['recipient_groups']
        from apps.core.choice_requests import inline_choices
        from apps.core.report_access import visible_reports
        reports = visible_reports(presentation.get('actor'), self.fields['report'].queryset) if presentation.get('actor') is not None else self.fields['report'].queryset
        self.fields['report'].queryset = reports
        report_rows, report_searchable = inline_choices(reports.order_by('name', 'slug', 'pk'))
        selected_report = self.data.get(self.add_prefix('report')) if self.is_bound else self.instance.report_id
        selected = None
        if str(selected_report or '').isascii() and str(selected_report or '').isdigit() and len(str(selected_report)) < 19:
            selected = reports.filter(pk=selected_report).first()
        rows = {row.pk: row for row in report_rows + ([selected] if selected else [])}
        choices = [(row.pk, row.name or row.slug) for row in rows.values()]
        if selected_report and selected is None:
            choices.append((str(selected_report), 'Selected report is unavailable'))
        self.fields['report'].choices = choices
        if report_searchable:
            self.fields['report'].widget.attrs['data-choice-source'] = f'/s/{studio.org.slug}/{studio.slug}/alerts/report-choices'
        self.recipient_source = f'/s/{studio.org.slug}/{studio.slug}/alerts/recipient-choices' if self.recipient_page['searchable'] else ''
        self.presentation = presentation

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
