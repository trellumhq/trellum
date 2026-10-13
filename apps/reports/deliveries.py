"""Shared authorization and live delivery policy for schedules and alert mail."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger
from django import forms
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.models import InstanceConfig
from apps.core.audit import audit
from apps.core.permissions import get_effective
from apps.core import roles
from apps.core.report_access import can_view_report
from apps.orgs.models import Organization
from apps.reports.models import EmailSchedule


def can_manage_org(request, org):
    return request.user.is_authenticated and request.user.is_active and get_effective(request, org).is_org_admin


def can_manage_schedule(request, schedule):
    org = schedule.report.studio.org
    key = getattr(request, "api_key", None)
    if key is not None and (key.org_id != org.pk or not getattr(key, "can_write", False)):
        return False
    return bool(request.user.is_authenticated and request.user.is_active
        and can_view_report(request.user, schedule.report, request=request)
        and roles.at_least(get_effective(request, org).role_for(schedule.report.studio), roles.DEVELOPER))


def schedule_for_request(request, schedule_id, *, report=None):
    rows = EmailSchedule.objects.select_related("report__studio__org", "created_by")
    if report is not None:
        rows = rows.filter(report=report)
    schedule = rows.filter(pk=schedule_id).first()
    if schedule is None or not can_manage_schedule(request, schedule):
        raise Http404
    return schedule


def managed_orgs(request):
    rows = Organization.objects.filter(is_active=True)
    key = getattr(request, "api_key", None)
    if key is not None:
        rows = rows.filter(pk=key.org_id)
    return [org for org in rows.order_by("name") if can_manage_org(request, org)]


def can_manage_instance_policy(request):
    return bool(request.user.is_authenticated and request.user.is_active
        and request.user.is_superuser and getattr(request, "api_key", None) is None)


def save_policy(request, target, form):
    """Persist a validated policy under a lock, invalidating sends on pause changes."""
    fields = tuple(form.cleaned_data)
    with transaction.atomic():
        target = type(target).objects.select_for_update().get(pk=target.pk)
        before = {name: getattr(target, name) for name in fields}
        for name, value in form.cleaned_data.items():
            setattr(target, name, value)
        if bool(before["deliveries_paused"]) != bool(target.deliveries_paused):
            target.delivery_generation = (target.delivery_generation or 0) + 1
        target.save(update_fields=(*fields, "delivery_generation"))
        audit(request, "notify.delivery_policy_update", target=target,
              org=target if isinstance(target, Organization) else None,
              before=before, after=form.cleaned_data)
    enabled = form.cleaned_data["delivery_default_enabled"]
    return {
        "delivery_default_enabled": "" if enabled is None else str(enabled).lower(),
        "delivery_default_timezone": form.cleaned_data["delivery_default_timezone"] or "",
    }


def delivery_pause(org_id):
    """Always read current settings; instance pause takes precedence."""
    if InstanceConfig.objects.filter(pk=1, deliveries_paused=True).exists():
        return "Paused for delivery by instance"
    if Organization.objects.filter(pk=org_id, deliveries_paused=True).exists():
        return "Paused for delivery by organization"
    return ""


def schedule_defaults(org):
    instance = InstanceConfig.load()
    enabled = org.delivery_default_enabled
    zone = org.delivery_default_timezone
    return {
        "enabled": enabled if enabled is not None else (
            instance.delivery_default_enabled if instance.delivery_default_enabled is not None else True
        ),
        "timezone": zone or instance.delivery_default_timezone or "UTC",
    }


def delivery_generation(org_id):
    return (
        InstanceConfig.objects.filter(pk=1).values_list("delivery_generation", flat=True).first(),
        Organization.objects.filter(pk=org_id).values_list("delivery_generation", flat=True).first(),
    )


def owner_repair_required(schedule):
    """AI alerts retain their billing owner's access requirements."""
    owner = schedule.created_by
    return owner is None or not owner.is_active or not can_view_report(owner, schedule.report)


def next_occurrence(schedule, now=None, *, pause=None):
    now = now or timezone.now()
    if not schedule.enabled or not schedule.report.present_in_scan or schedule.report.disabled or (delivery_pause(schedule.report.studio.org_id) if pause is None else pause):
        return None
    kwargs = {"hour": schedule.send_hour, "minute": schedule.send_minute, "timezone": schedule.timezone}
    if schedule.freq == EmailSchedule.FREQ_WEEKDAYS:
        kwargs["day_of_week"] = "mon-fri"
    elif schedule.freq == EmailSchedule.FREQ_WEEKLY:
        kwargs["day_of_week"] = schedule.weekday
    elif schedule.freq == EmailSchedule.FREQ_MONTHLY:
        kwargs["day"] = schedule.month_day
    return CronTrigger(**kwargs).get_next_fire_time(None, now + timedelta(microseconds=1))


def schedule_snapshot(schedule):
    return {
        "id": schedule.pk, "report_id": schedule.report_id,
        "studio_id": schedule.report.studio_id, "org_id": schedule.report.studio.org_id,
        "revision": schedule.delivery_revision,
        "policy_generation": delivery_generation(schedule.report.studio.org_id),
        **{name: getattr(schedule, name) for name in (
            "freq", "send_hour", "send_minute", "weekday", "month_day", "timezone", "attach_pdf", "enabled",
        )},
        "recipient_roles": list(schedule.recipient_roles),
        "recipient_ids": sorted(schedule.recipients.values_list("pk", flat=True)),
        "recipient_group_ids": sorted(schedule.recipient_groups.values_list("pk", flat=True)),
    }


def schedule_send_current(snapshot):
    schedule = EmailSchedule.objects.select_related("report__studio__org").filter(pk=snapshot["id"]).first()
    return schedule if (schedule is not None and schedule.enabled
        and not delivery_pause(schedule.report.studio.org_id)
        and schedule.report.present_in_scan and not schedule.report.disabled
        and schedule_snapshot(schedule) == snapshot) else None


class DeliveryPolicyForm(forms.Form):
    deliveries_paused = forms.BooleanField(required=False, label="Pause scheduled report and AI-alert mail")
    delivery_default_enabled = forms.TypedChoiceField(
        choices=[("", "Inherit / enabled on a fresh instance"), ("true", "Enabled"), ("false", "Disabled")],
        required=False, empty_value=None, coerce=lambda value: value == "true", label="New schedules: enabled",
    )
    delivery_default_timezone = forms.ChoiceField(
        choices=[("", "Inherit")]+[(zone, zone.replace("_", " ")) for zone in sorted(available_timezones())],
        required=False, label="New schedules: timezone (blank inherits)",
        widget=forms.Select(attrs={"data-choice-picker": ""}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        value = self.data.get(self.add_prefix("delivery_default_timezone")) if self.is_bound else None
        if not value:
            value = self.initial.get("delivery_default_timezone")
        if value and value not in dict(self.fields["delivery_default_timezone"].choices):
            self.fields["delivery_default_timezone"].choices = [*self.fields["delivery_default_timezone"].choices, (value, value)]

    def clean_delivery_default_timezone(self):
        value = self.cleaned_data["delivery_default_timezone"]
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                raise forms.ValidationError("Enter a valid IANA timezone.")
        return value or None

    @classmethod
    def for_target(cls, target, data=None):
        value = target.delivery_default_enabled
        return cls(data, auto_id=f"id_policy_{target._meta.model_name}_{target.pk}_%s", initial={
            "deliveries_paused": target.deliveries_paused,
            "delivery_default_enabled": "" if value is None else str(value).lower(),
            "delivery_default_timezone": target.delivery_default_timezone or "",
        })


class ScheduleSettingsForm(forms.ModelForm):
    send_time = forms.TimeField(required=False, label="Local send time", input_formats=["%H:%M"],
                                widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "step": "60"}))
    weekday = forms.ChoiceField(choices=[(i, day) for i, day in enumerate(
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))])
    month_day = forms.ChoiceField(choices=[(i, str(i)) for i in range(1, 29)])
    recipient_ids = forms.MultipleChoiceField(required=False, label="People", widget=forms.CheckboxSelectMultiple)
    recipient_roles = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple, choices=[
        ("admins", "All admins"), ("developers", "All developers"), ("everyone", "Everyone in studio"),
    ], label="Studio roles")
    recipient_group_ids = forms.MultipleChoiceField(required=False, label="Permission groups", widget=forms.CheckboxSelectMultiple)
    timezone = forms.ChoiceField(
        choices=[(zone, zone.replace("_", " ")) for zone in sorted(available_timezones())],
        label="Timezone", widget=forms.Select(attrs={"data-choice-picker": ""}),
    )

    class Meta:
        model = EmailSchedule
        fields = ["freq", "send_hour", "send_minute", "weekday", "month_day", "timezone", "attach_pdf", "enabled"]

    def __init__(self, *args, members, groups, **kwargs):
        super().__init__(*args, **kwargs)
        person_choices = []
        for row in members:
            name, email = row.get("name") or row["email"], row["email"]
            person_choices.append((row["id"], f"{name} · {email}" if name != email else email))
        self.fields["recipient_ids"].choices = person_choices
        self.fields["recipient_group_ids"].choices = [(row["id"], row["name"]) for row in groups]
        saved_timezone = getattr(self.instance, "timezone", None)
        if saved_timezone and saved_timezone not in dict(self.fields["timezone"].choices):
            self.fields["timezone"].choices = [*self.fields["timezone"].choices, (saved_timezone, saved_timezone)]
        self.fields["recipient_ids"].widget.attrs.update({"data-recipient-type": "people"})
        self.fields["recipient_roles"].widget.attrs.update({"data-recipient-type": "roles"})
        self.fields["recipient_group_ids"].widget.attrs.update({"data-recipient-type": "groups"})
        self.fields["freq"].widget.attrs["class"] = "ui-input"
        self.fields["timezone"].widget.attrs.update({"class": "ui-input", "data-choice-picker": ""})
        self.fields["weekday"].widget.attrs["class"] = "ui-input"
        self.fields["month_day"].widget.attrs["class"] = "ui-input"
        for name in ("send_hour", "send_minute"):
            self.fields[name].required = False
            self.fields[name].widget = forms.HiddenInput()
        self.fields["enabled"].widget.attrs["class"] = "ui-check-row"
        self.fields["attach_pdf"].widget.attrs["class"] = "ui-check-row"
        if not groups:
            self.fields["recipient_group_ids"].help_text = "No permission groups are available."
        self.fields["recipient_ids"].initial = list(self.instance.recipients.values_list("pk", flat=True)) if self.instance.pk else []
        self.fields["recipient_roles"].initial = self.instance.recipient_roles
        self.fields["recipient_group_ids"].initial = list(self.instance.recipient_groups.values_list("pk", flat=True)) if self.instance.pk else []
