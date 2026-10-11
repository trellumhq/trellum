"""Shared authorization and live delivery policy for schedules and alert mail."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger
from django import forms
from django.http import Http404
from django.utils import timezone

from apps.core.models import InstanceConfig
from apps.core.permissions import get_effective
from apps.core.report_access import can_view_report
from apps.orgs.models import Organization
from apps.reports.models import EmailSchedule


def can_manage_org(request, org):
    return request.user.is_authenticated and request.user.is_active and get_effective(request, org).is_org_admin


def can_manage_schedule(request, schedule):
    org = schedule.report.studio.org
    key = getattr(request, "api_key", None)
    if key is not None and key.org_id != org.pk:
        return False
    return bool(request.user.is_active and can_view_report(request.user, schedule.report) and (
        schedule.created_by_id == request.user.pk or can_manage_org(request, org)
    ))


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
    owner = schedule.created_by
    return owner is None or not owner.is_active or not can_view_report(owner, schedule.report)


def next_occurrence(schedule, now=None):
    now = now or timezone.now()
    if not schedule.enabled or schedule.report.disabled or delivery_pause(schedule.report.studio.org_id) or owner_repair_required(schedule):
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
        "id": schedule.pk, "report_id": schedule.report_id, "owner_id": schedule.created_by_id,
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
    schedule = EmailSchedule.objects.select_related("created_by", "report__studio__org").filter(pk=snapshot["id"]).first()
    return schedule if (schedule is not None and schedule.enabled
        and not delivery_pause(schedule.report.studio.org_id)
        and not owner_repair_required(schedule) and schedule.report.present_in_scan and not schedule.report.disabled
        and schedule_snapshot(schedule) == snapshot) else None


class DeliveryPolicyForm(forms.Form):
    deliveries_paused = forms.BooleanField(required=False, label="Pause scheduled report and AI-alert mail")
    delivery_default_enabled = forms.TypedChoiceField(
        choices=[("", "Inherit / enabled on a fresh instance"), ("true", "Enabled"), ("false", "Disabled")],
        required=False, empty_value=None, coerce=lambda value: value == "true", label="New schedules: enabled",
    )
    delivery_default_timezone = forms.CharField(required=False, max_length=64, label="New schedules: timezone (blank inherits)")

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
    recipient_ids = forms.MultipleChoiceField(required=False, label="People")
    recipient_roles = forms.MultipleChoiceField(required=False, choices=[
        ("admins", "All admins"), ("developers", "All developers"), ("everyone", "Everyone in studio"),
    ], label="Studio roles")
    recipient_group_ids = forms.MultipleChoiceField(required=False, label="Permission groups")
    owner_id = forms.ChoiceField(required=False, label="Owner")

    class Meta:
        model = EmailSchedule
        fields = ["freq", "send_hour", "send_minute", "weekday", "month_day", "timezone", "attach_pdf", "enabled"]

    def __init__(self, *args, members, groups, can_reassign=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipient_ids"].choices = [(row["id"], row["email"]) for row in members]
        self.fields["recipient_group_ids"].choices = [(row["id"], row["name"]) for row in groups]
        self.fields["recipient_ids"].initial = list(self.instance.recipients.values_list("pk", flat=True))
        self.fields["recipient_roles"].initial = self.instance.recipient_roles
        self.fields["recipient_group_ids"].initial = list(self.instance.recipient_groups.values_list("pk", flat=True))
        if can_reassign:
            self.fields["owner_id"].choices = [("", "Keep current owner")] + [(row["id"], row["email"]) for row in members]
        else:
            del self.fields["owner_id"]
