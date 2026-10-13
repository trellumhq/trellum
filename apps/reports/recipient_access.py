"""Request-local delivery presentation eligibility and policy snapshots."""
from apps.core.models import InstanceConfig
from apps.core.report_access import ReportAccessBatch
from apps.reports import notify


class DeliveryPresentationBatch:
    def __init__(self, schedules):
        self.schedules = list(schedules)
        users_by_org = {}
        orgs = {}
        for schedule in self.schedules:
            org = schedule.report.studio.org
            orgs[org.pk] = org
            users = users_by_org.setdefault(org.pk, {})
            for user in schedule.recipients.all():
                users.setdefault(user.pk, user)
        self.users = users_by_org
        self.access = {oid: ReportAccessBatch(users.values(), orgs[oid])
                       for oid, users in users_by_org.items()}
        instance_paused = bool(orgs) and InstanceConfig.objects.filter(pk=1, deliveries_paused=True).exists()
        self.pauses = {oid: "Paused for delivery by instance" if instance_paused else
                       "Paused for delivery by organization" if org.deliveries_paused else ""
                       for oid, org in orgs.items()}

    def eligible(self, schedule):
        oid = schedule.report.studio.org_id
        batch = self.access[oid]
        return [user for original in schedule.recipients.all()
                if (user := self.users[oid][original.pk]).is_active
                and batch.can_view(user, schedule.report)]

    def summary(self, schedule):
        parts = [f"Everyone in {schedule.report.studio.name}" if role == "everyone"
                 else notify.RECIPIENT_ROLE_LABELS.get(role, "")
                 for role in schedule.recipient_roles]
        parts = [part for part in parts if part]
        parts.extend(group.name for group in schedule.recipient_groups.all())
        count = len(self.eligible(schedule))
        if count:
            parts.append(f"{count} {'person' if count == 1 else 'people'}")
        return " + ".join(parts) if parts else "No recipients"
