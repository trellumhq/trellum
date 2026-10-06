from django import forms
from django.contrib import admin

from apps.alerts.models import AlertRule, AlertRun
from apps.orgs.models import OrgMembership


class AlertRuleAdminForm(forms.ModelForm):
    class Meta:
        model = AlertRule
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        org = cleaned.get("org")
        if org is None:
            return cleaned

        groups = cleaned.get("recipient_groups")
        if groups is not None and groups.exclude(org=org).exists():
            self.add_error(
                "recipient_groups",
                "Every recipient group must belong to the rule's organization.",
            )

        recipients = cleaned.get("recipients")
        if recipients is not None:
            member_ids = OrgMembership.objects.filter(
                org=org, user_id__in=recipients.values_list("pk", flat=True)
            ).values_list("user_id", flat=True)
            if recipients.exclude(pk__in=member_ids).exists():
                self.add_error(
                    "recipients",
                    "Every recipient must belong to the rule's organization.",
                )
        return cleaned


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    form = AlertRuleAdminForm

# Plain registrations so rules can be created before the studio Alerts page
# (internal planning ticket #147) exists.
admin.site.register(AlertRun)
