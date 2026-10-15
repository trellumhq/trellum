"""Idle refresh suspension (docs/idle-refresh-suspension.md): the org policy,
the activity clock, scheduler gating, and the manual resume API."""
import json
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.alerts.models import AlertRule
from apps.core import roles
from apps.core.models import AuditLog
from apps.reports.models import (
    DEFAULT_REFRESH_SUSPEND_DAYS,
    EmailSchedule,
    OrgRefreshPolicy,
    Report,
    record_view,
    refresh_suspend_days,
    should_suspend_refresh,
)
from apps.runner.management.commands.runworker import _enqueue_scheduled, _SchedulerManager

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def idle(report_row):
    """A report first seen long before the default window, never viewed."""
    report_row.first_seen_at = timezone.now() - timedelta(days=200)
    report_row.save(update_fields=["first_seen_at"])
    return report_row


def _ago(days):
    return timezone.now() - timedelta(days=days)


class TestPolicyHelper:
    def test_default(self, org):
        assert DEFAULT_REFRESH_SUSPEND_DAYS == 60
        assert refresh_suspend_days(org) == 60

    def test_null_row_is_default(self, org):
        OrgRefreshPolicy.objects.create(org=org)
        assert refresh_suspend_days(org) == 60

    def test_override_and_zero(self, org):
        OrgRefreshPolicy.objects.create(org=org, suspend_after_days=14)
        assert refresh_suspend_days(org) == 14
        OrgRefreshPolicy.objects.filter(org=org).update(suspend_after_days=0)
        assert refresh_suspend_days(org) == 0


class TestShouldSuspend:
    def test_idle_report_suspends(self, idle):
        assert should_suspend_refresh(idle) is True

    def test_zero_disables(self, idle, org):
        OrgRefreshPolicy.objects.create(org=org, suspend_after_days=0)
        assert should_suspend_refresh(idle) is False

    def test_policy_override_changes_threshold(self, idle, org):
        idle.last_viewed_at = _ago(30)
        assert should_suspend_refresh(idle) is False
        OrgRefreshPolicy.objects.create(org=org, suspend_after_days=14)
        assert should_suspend_refresh(idle) is True

    def test_recent_view_keeps_active(self, idle):
        idle.last_viewed_at = _ago(5)
        assert should_suspend_refresh(idle) is False

    def test_new_report_gets_grace_via_first_seen(self, report_row):
        report_row.first_seen_at = _ago(10)
        assert should_suspend_refresh(report_row) is False

    def test_resume_restarts_clock(self, idle):
        idle.refresh_resumed_at = _ago(1)
        assert should_suspend_refresh(idle) is False
        idle.refresh_resumed_at = _ago(90)
        assert should_suspend_refresh(idle) is True

    def test_enabled_email_schedule_exempts(self, idle, developer):
        sched = EmailSchedule.objects.create(report=idle, created_by=developer, enabled=True)
        assert should_suspend_refresh(idle) is False
        EmailSchedule.objects.filter(pk=sched.pk).update(enabled=False)
        assert should_suspend_refresh(idle) is True

    def test_schedule_alert_exempts_but_other_triggers_do_not(self, idle, org, studio_tree, developer):
        rule = AlertRule.objects.create(
            org=org, studio=studio_tree, report=idle, name="a", created_by=developer,
            trigger=AlertRule.TRIGGER_AFTER_BUILD,
        )
        assert should_suspend_refresh(idle) is True
        AlertRule.objects.filter(pk=rule.pk).update(trigger=AlertRule.TRIGGER_SCHEDULE)
        assert should_suspend_refresh(idle) is False
        AlertRule.objects.filter(pk=rule.pk).update(enabled=False)
        assert should_suspend_refresh(idle) is True


class TestRecordView:
    def test_bumps_last_viewed_at(self, report_row, viewer):
        assert report_row.last_viewed_at is None
        record_view(report_row, user=viewer)
        report_row.refresh_from_db()
        assert report_row.last_viewed_at is not None
        assert timezone.now() - report_row.last_viewed_at < timedelta(minutes=1)

    def test_view_does_not_resume(self, idle, viewer):
        Report.objects.filter(pk=idle.pk).update(refresh_suspended_at=_ago(1))
        record_view(idle, user=viewer)
        idle.refresh_from_db()
        assert idle.refresh_suspended_at is not None


@pytest.fixture(autouse=False)
def keep_connection():
    """_enqueue_scheduled runs on a scheduler thread and calls
    close_old_connections(); in-test that would drop the test transaction."""
    with mock.patch("apps.runner.management.commands.runworker.close_old_connections"):
        yield


@pytest.mark.usefixtures("keep_connection")
class TestScheduler:
    def test_enqueue_scheduled_marks_and_skips(self, idle):
        with mock.patch("apps.runner.services.enqueue") as enq:
            _enqueue_scheduled(idle.pk)
        enq.assert_not_called()
        idle.refresh_from_db()
        assert idle.refresh_suspended_at is not None
        assert AuditLog.objects.filter(action="report.refresh_suspend").exists()

    def test_enqueue_scheduled_runs_active_report(self, report_row):
        with mock.patch("apps.runner.services.enqueue") as enq:
            _enqueue_scheduled(report_row.pk)
        enq.assert_called_once()
        report_row.refresh_from_db()
        assert report_row.refresh_suspended_at is None

    def test_already_suspended_is_skipped(self, report_row):
        Report.objects.filter(pk=report_row.pk).update(refresh_suspended_at=_ago(1))
        with mock.patch("apps.runner.services.enqueue") as enq:
            _enqueue_scheduled(report_row.pk)
        enq.assert_not_called()

    def test_refresh_excludes_suspended(self, report_row):
        report_row.schedule_cron = "0 7 * * *"
        report_row.save(update_fields=["schedule_cron"])
        mgr = _SchedulerManager()
        mgr.start()
        try:
            assert f"report-{report_row.pk}" in {j.id for j in mgr._scheduler.get_jobs()}
            Report.objects.filter(pk=report_row.pk).update(refresh_suspended_at=timezone.now())
            mgr.refresh()
            assert not mgr._scheduler.get_jobs()
        finally:
            mgr.shutdown()


class TestResumeApi:
    def test_resume_clears_and_enqueues(self, login, developer, report_row, studio_tree, org):
        Report.objects.filter(pk=report_row.pk).update(refresh_suspended_at=_ago(2))
        url = f"/s/{org.slug}/{studio_tree.slug}/api/reports/{report_row.slug}/resume_refresh"
        with mock.patch("apps.reports.views.enqueue", return_value="queued") as enq:
            resp = login(developer).post(url)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "status": "queued"}
        enq.assert_called_once()
        assert enq.call_args.kwargs["trigger"] == "manual"
        report_row.refresh_from_db()
        assert report_row.refresh_suspended_at is None
        assert report_row.refresh_resumed_at is not None
        assert AuditLog.objects.filter(action="report.refresh_resume").exists()

    def test_viewer_forbidden(self, login, viewer, report_row, studio_tree, org):
        Report.objects.filter(pk=report_row.pk).update(refresh_suspended_at=_ago(2))
        url = f"/s/{org.slug}/{studio_tree.slug}/api/reports/{report_row.slug}/resume_refresh"
        with mock.patch("apps.reports.views.enqueue") as enq:
            resp = login(viewer).post(url)
        assert resp.status_code in (401, 403, 404)
        enq.assert_not_called()
        report_row.refresh_from_db()
        assert report_row.refresh_suspended_at is not None


class TestApiRefreshPolicy:
    def _post(self, client, org, body):
        return client.post(
            f"/orgs/{org.slug}/api/refresh_policy", data=json.dumps(body), content_type="application/json"
        )

    def test_set_zero_and_reset(self, login, org_admin, org):
        client = login(org_admin)
        resp = self._post(client, org, {"suspend_after_days": 0})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "suspend_after_days": 0, "effective_suspend_after_days": 0}
        assert AuditLog.objects.filter(action="refresh_policy.update").exists()
        resp = self._post(client, org, {"suspend_after_days": None})
        assert resp.json()["effective_suspend_after_days"] == 60
        assert OrgRefreshPolicy.objects.get(org=org).suspend_after_days is None

    def test_set_value(self, login, org_admin, org):
        resp = self._post(login(org_admin), org, {"suspend_after_days": 30})
        assert resp.status_code == 200
        assert OrgRefreshPolicy.objects.get(org=org).updated_by == org_admin

    @pytest.mark.parametrize("body", [{"suspend_after_days": -1}, {"suspend_after_days": "x"}, {"nope": 1}])
    def test_rejects_invalid_without_row(self, login, org_admin, org, body):
        resp = self._post(login(org_admin), org, body)
        assert resp.status_code == 400
        assert not OrgRefreshPolicy.objects.filter(org=org).exists()

    def test_non_admin_forbidden(self, login, member, org):
        resp = self._post(login(member), org, {"suspend_after_days": 5})
        assert resp.status_code in (403, 404)
        assert not OrgRefreshPolicy.objects.filter(org=org).exists()

    def test_settings_page_renders(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/auto-refresh")
        assert resp.status_code == 200
        assert b"Auto-refresh" in resp.content
