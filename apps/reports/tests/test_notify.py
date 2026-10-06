"""Alert-on-transition mail, scheduled delivery, and the endpoints that
manage schedules (apps.reports.notify + the schedule/sample views).

Failure/recovery alerts have no per-user configuration any more: every
studio member with role developer or admin hears about a report of theirs
breaking automatically (see apps.reports.notify.alert_recipients), and
viewers never do. There is nothing to toggle, subscribe to, or unsubscribe
from on that mail -- see TestRoleBasedRecipients and
TestNoUnsubscribeOnAlertMail below. Only EmailSchedule (a personal delivery
cadence) still carries an unsubscribe link."""
import json
import sys
import types

import pytest
from django.core import mail
from django.core.exceptions import ValidationError

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import EmailSchedule, ReportPermissionGrant
from apps.reports.notify import (
    alert_recipients,
    flush_broken_buffers,
    read_unsubscribe_token,
    resolve_recipients,
    send_sample,
    send_scheduled,
    sign_unsubscribe_token,
)
from apps.reports.notify import run_finished as notify_run_finished
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def other_developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev2@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def studio_admin(make_user, org, studio_tree, grant_studio):
    user = make_user("studio-admin@demo.example", org=org)
    grant_studio(user, studio_tree, roles.ADMIN)
    return user


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def other_viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view2@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


def _make_run(report_row, status, **kwargs):
    return Run.objects.create(
        report=report_row, studio=report_row.studio, slug=report_row.slug, status=status, **kwargs
    )


class FakeRenderResult:
    def __init__(self, png=None, pdf=None, error=""):
        self.png = png
        self.pdf = pdf
        self.error = error


def _inject_fake_snapshot(monkeypatch, **render_kwargs):
    """Stand in for apps.reports.snapshot, which is built by a different
    workstream and may not exist in this checkout — inserting a fake module
    into sys.modules exercises the happy path without depending on it."""
    fake = types.ModuleType("apps.reports.snapshot")
    fake.render_report = lambda output_dir: FakeRenderResult(**render_kwargs)  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "apps.reports.snapshot", fake)


def _remove_fake_snapshot(monkeypatch):
    monkeypatch.delitem(sys.modules, "apps.reports.snapshot", raising=False)


# ── role-based alert audience ────────────────────────────────────────────────

class TestRoleBasedRecipients:
    """apps.reports.notify.alert_recipients: every studio member with role
    developer or admin, with zero configuration. Viewers never hear
    anything. No per-report override, no per-user toggle, no studio-wide
    watch level -- this is the entire resolution rule."""

    def test_developer_gets_recipients_with_zero_setup(self, report_row, developer):
        assert alert_recipients(report_row, "failure") == [developer]

    def test_admin_gets_recipients_with_zero_setup(self, report_row, studio_admin):
        assert alert_recipients(report_row, "failure") == [studio_admin]

    def test_viewer_is_never_a_recipient(self, report_row, viewer):
        assert alert_recipients(report_row, "failure") == []

    def test_developer_and_admin_both_included_viewer_excluded(
        self, report_row, developer, studio_admin, viewer
    ):
        recipients = alert_recipients(report_row, "failure")
        assert set(recipients) == {developer, studio_admin}

    def test_no_members_no_recipients(self, report_row):
        assert alert_recipients(report_row, "failure") == []

    def test_kind_does_not_change_the_audience(self, report_row, developer):
        # "kind" used to select an audience under the old watch-level
        # system; it's accepted but ignored now.
        assert alert_recipients(report_row, "recovery") == [developer]

    def test_permission_group_granted_developer_is_included(
        self, report_row, studio_tree, org, make_user, make_group, attach_group
    ):
        """A developer role reached only through a permission-group grant
        (no explicit StudioMembership row) must hear about a broken build
        the same as one granted the role directly -- alert_recipients used
        to read StudioMembership rows only and miss this entirely."""
        user = make_user("group-dev@demo.example", org=org)
        group = make_group("Dev Group", grants=[(studio_tree, roles.DEVELOPER)])
        attach_group(user, group)

        assert alert_recipients(report_row, "failure") == [user]

    def test_org_admin_is_included_without_a_studio_membership_row(
        self, report_row, org, make_user
    ):
        """An org admin has implicit admin on every studio in the org --
        same effective-access rule as everywhere else (see
        apps.core.permissions.effective_roles) -- even with zero explicit
        StudioMembership rows."""
        admin = make_user("org-admin@demo.example", org=org, org_role=roles.ORG_ADMIN)

        assert alert_recipients(report_row, "failure") == [admin]

    def test_deactivated_developer_is_excluded(self, report_row, developer):
        developer.is_active = False
        developer.save(update_fields=["is_active"])

        assert alert_recipients(report_row, "failure") == []


# ── build-status transitions ─────────────────────────────────────────────────

class TestRunFinishedTransitions:
    def test_first_failure_after_success_sends_one_broken_mail(self, report_row, developer):
        # No configuration needed: every developer/admin studio member
        # (developer, via the fixture's grant_studio) hears about a broken
        # report by default. Broken mail is buffered for the coalescing
        # window (see flush_broken_buffers) rather than sent inline, so the
        # test flushes it with force=True to avoid faking the clock.
        _make_run(report_row, Run.SUCCESS)
        failing = _make_run(report_row, Run.ERROR, stderr_tail="boom")

        notify_run_finished(failing)
        assert mail.outbox == []  # buffered, not sent yet
        flush_broken_buffers(force=True)

        assert len(mail.outbox) == 1
        assert "Broken" in mail.outbox[0].subject
        assert mail.outbox[0].to == [developer.email]

    def test_repeated_failure_sends_nothing_more(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR))
        flush_broken_buffers(force=True)
        mail.outbox.clear()

        notify_run_finished(_make_run(report_row, Run.ERROR))
        flush_broken_buffers(force=True)

        assert mail.outbox == []

    def test_recovery_sends_one_recovered_mail(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR))
        mail.outbox.clear()

        notify_run_finished(_make_run(report_row, Run.SUCCESS))

        assert len(mail.outbox) == 1
        assert "Recovered" in mail.outbox[0].subject
        assert mail.outbox[0].to == [developer.email]

    def test_no_developer_or_admin_sends_nothing(self, report_row):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR))
        flush_broken_buffers(force=True)
        assert mail.outbox == []

    def test_viewer_only_gets_no_broken_mail(self, report_row, viewer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR, stderr_tail="boom"))
        flush_broken_buffers(force=True)
        assert mail.outbox == []

    def test_first_ever_build_has_no_transition_to_report(self, report_row, developer):
        notify_run_finished(_make_run(report_row, Run.ERROR))
        assert mail.outbox == []

    def test_user_stop_is_not_treated_as_a_failure_transition(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.STOPPED))
        assert mail.outbox == []

    def test_never_raises_on_a_malformed_run(self):
        class Bogus:
            pk = None

        notify_run_finished(Bogus())  # must not raise


# ── broken-mail coalescing ────────────────────────────────────────────────────

class TestCoalescing:
    """A broken-build transition is buffered (BrokenReportPending), not
    mailed inline — flush_broken_buffers (called from the coordinator's
    periodic scheduler sync in production; force=True here to avoid faking
    the clock) delivers it as the normal single-report mail when it's the
    only one pending for the studio, or as one summary mail per recipient
    when several reports broke together."""

    def test_one_report_breaking_is_one_normal_broken_mail(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        failing = _make_run(report_row, Run.ERROR, stderr_tail="boom")
        notify_run_finished(failing)

        flush_broken_buffers(force=True)

        assert len(mail.outbox) == 1
        assert "Broken" in mail.outbox[0].subject
        assert "summary" not in mail.outbox[0].subject.lower()

    def test_three_reports_breaking_together_send_one_summary_mail(
        self, studio_tree, write_report, developer
    ):
        from apps.reports.models import Report
        from apps.reports.scan import sync_studio_registry

        for slug in ("report-a", "report-b", "report-c"):
            write_report(slug)
        sync_studio_registry(studio_tree)
        reports = [
            Report.objects.get(studio=studio_tree, slug=slug)
            for slug in ("report-a", "report-b", "report-c")
        ]

        for report in reports:
            _make_run(report, Run.SUCCESS)
        for report in reports:
            notify_run_finished(_make_run(report, Run.ERROR, stderr_tail="boom"))

        assert mail.outbox == []  # still buffered

        flush_broken_buffers(force=True)

        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == [developer.email]
        assert "3" in msg.subject
        for report in reports:
            assert (report.name or report.slug) in msg.body

    def test_not_yet_due_is_left_pending(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        failing = _make_run(report_row, Run.ERROR, stderr_tail="boom")
        notify_run_finished(failing)

        flush_broken_buffers()  # no force: the coalescing window hasn't elapsed

        assert mail.outbox == []
        from apps.reports.models import BrokenReportPending

        assert BrokenReportPending.objects.filter(report=report_row).exists()

    def test_recovered_mail_is_never_buffered(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR))
        mail.outbox.clear()

        notify_run_finished(_make_run(report_row, Run.SUCCESS))

        assert len(mail.outbox) == 1  # sent immediately, no flush needed
        assert "Recovered" in mail.outbox[0].subject

    def test_recovery_within_the_window_clears_the_pending_broken_row(self, report_row, developer):
        """A fail -> recover within the coalescing window must not leave a
        stale BrokenReportPending row behind -- otherwise the next flush
        mails a confusing "Broken" notice for a report that already
        recovered (on top of the Recovered mail already sent)."""
        from apps.reports.models import BrokenReportPending

        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR, stderr_tail="boom"))
        assert BrokenReportPending.objects.filter(report=report_row).exists()
        mail.outbox.clear()

        notify_run_finished(_make_run(report_row, Run.SUCCESS))  # recovers, still inside the window

        assert len(mail.outbox) == 1
        assert "Recovered" in mail.outbox[0].subject
        assert not BrokenReportPending.objects.filter(report=report_row).exists()

        flush_broken_buffers(force=True)  # nothing left to flush

        assert len(mail.outbox) == 1  # unchanged: no stale Broken mail

    def test_same_report_breaking_twice_in_window_is_still_a_single_report_mail(
        self, report_row, developer
    ):
        """A report can rack up more than one buffered transition within
        one coalescing window: break, get cancelled by a user (STOPPED
        counts as neither broken nor recovered), then break again before
        the window elapses. That must still read as ONE broken report, not
        trip the multi-report summary path on itself."""
        from apps.reports.models import BrokenReportPending

        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR, stderr_tail="boom"))
        notify_run_finished(_make_run(report_row, Run.STOPPED))
        notify_run_finished(_make_run(report_row, Run.ERROR, stderr_tail="boom again"))
        assert BrokenReportPending.objects.filter(report=report_row).count() == 2

        flush_broken_buffers(force=True)

        assert len(mail.outbox) == 1
        assert "Broken" in mail.outbox[0].subject
        assert "reports broken" not in mail.outbox[0].subject.lower()

    def test_summary_subject_counts_only_that_recipients_own_reports(
        self, studio_tree, studio2, data_dir, write_report, org, make_user, grant_studio
    ):
        """Each recipient's summary subject counts only the reports THEY
        hear about, not a studio-wide total -- the old code used the whole
        batch's distinct-report count for every recipient's subject even
        though a recipient's own body only ever listed their reports."""
        import yaml

        from apps.reports.models import BrokenReportPending, Report
        from apps.reports.notify import _send_broken_summary
        from apps.reports.scan import sync_studio_registry

        write_report("report-a")
        sync_studio_registry(studio_tree)
        report_a = Report.objects.get(studio=studio_tree, slug="report-a")

        studio2.ensure_dirs()
        report_dir = studio2.reports_dir / "report-b"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "report.yaml").write_text(
            yaml.safe_dump({"slug": "report-b", "name": "Report B"}), encoding="utf-8"
        )
        (report_dir / "generator.py").write_text("# test stub\n", encoding="utf-8")
        sync_studio_registry(studio2)
        report_b = Report.objects.get(studio=studio2, slug="report-b")

        dev_a = make_user("dev-a@demo.example", org=org)
        grant_studio(dev_a, studio_tree, roles.DEVELOPER)
        dev_b = make_user("dev-b@demo.example", org=org)
        grant_studio(dev_b, studio2, roles.DEVELOPER)

        run_a = _make_run(report_a, Run.ERROR, stderr_tail="boom-a")
        run_b = _make_run(report_b, Run.ERROR, stderr_tail="boom-b")
        pending = [
            BrokenReportPending.objects.create(report=report_a, run=run_a),
            BrokenReportPending.objects.create(report=report_b, run=run_b),
        ]

        _send_broken_summary(pending)

        assert len(mail.outbox) == 2
        by_recipient = {m.to[0]: m for m in mail.outbox}
        assert "1 broken" in by_recipient[dev_a.email].subject.lower()
        assert "1 broken" in by_recipient[dev_b.email].subject.lower()


# ── no unsubscribe on alert mail ─────────────────────────────────────────────

class TestNoUnsubscribeOnAlertMail:
    """Failure/recovery/summary mail is operational duty mail, not a
    subscription -- it never carries an unsubscribe link. Only EmailSchedule
    delivery mail does (see TestUnsubscribeView below)."""

    def test_broken_mail_has_no_unsubscribe_link(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR, stderr_tail="boom"))
        flush_broken_buffers(force=True)

        assert len(mail.outbox) == 1
        assert "unsubscribe" not in mail.outbox[0].body.lower()

    def test_recovered_mail_has_no_unsubscribe_link(self, report_row, developer):
        _make_run(report_row, Run.SUCCESS)
        notify_run_finished(_make_run(report_row, Run.ERROR))
        mail.outbox.clear()

        notify_run_finished(_make_run(report_row, Run.SUCCESS))

        assert len(mail.outbox) == 1
        assert "unsubscribe" not in mail.outbox[0].body.lower()

    def test_broken_summary_mail_has_no_unsubscribe_link(self, studio_tree, write_report, developer):
        from apps.reports.models import Report
        from apps.reports.scan import sync_studio_registry

        for slug in ("report-a", "report-b", "report-c"):
            write_report(slug)
        sync_studio_registry(studio_tree)
        reports = [
            Report.objects.get(studio=studio_tree, slug=slug)
            for slug in ("report-a", "report-b", "report-c")
        ]
        for report in reports:
            _make_run(report, Run.SUCCESS)
        for report in reports:
            notify_run_finished(_make_run(report, Run.ERROR, stderr_tail="boom"))
        flush_broken_buffers(force=True)

        assert len(mail.outbox) == 1
        assert "unsubscribe" not in mail.outbox[0].body.lower()


# ── recipient-group resolution ───────────────────────────────────────────────

class TestResolveRecipients:
    """apps.reports.notify.resolve_recipients: the union of individually-
    picked recipients, role chips (expanded from this studio's *current*
    StudioMembership rows) and permission-group chips (expanded from
    PermissionGroupMembership) -- deduped and always intersected with the
    studio's current membership, so a permission-group member with no access
    to this particular studio never receives the mail."""

    def test_individuals_only(self, report_row, viewer):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        assert resolve_recipients(schedule) == [viewer]

    def test_admins_role_expands_to_studio_admins_only(self, report_row, developer, studio_admin):
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=studio_admin, recipient_roles=["admins"]
        )
        assert resolve_recipients(schedule) == [studio_admin]

    def test_developers_role_expands_to_studio_developers_only(
        self, report_row, developer, studio_admin
    ):
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=developer, recipient_roles=["developers"]
        )
        assert resolve_recipients(schedule) == [developer]

    def test_everyone_role_includes_viewers(self, report_row, developer, viewer):
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=developer, recipient_roles=["everyone"]
        )
        assert set(resolve_recipients(schedule)) == {developer, viewer}

    def test_group_expands_to_its_members(self, report_row, viewer, make_group, attach_group):
        group = make_group("Leadership")
        attach_group(viewer, group)  # viewer already has studio access
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipient_groups.add(group)
        assert resolve_recipients(schedule) == [viewer]

    def test_dedupes_a_user_reached_multiple_ways(self, report_row, developer, make_group, attach_group):
        group = make_group("Leadership")
        attach_group(developer, group)
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=developer, recipient_roles=["developers"]
        )
        schedule.recipients.add(developer)
        schedule.recipient_groups.add(group)
        assert resolve_recipients(schedule) == [developer]  # not listed three times

    def test_group_member_outside_studio_is_excluded(self, report_row, org, make_user, make_group, attach_group):
        # An org member with no studio access at all -- the group grants
        # nothing studio-specific (no default_studio_role, no grants).
        outsider = make_user("outsider@demo.example", org=org)
        group = make_group("Leadership")
        attach_group(outsider, group)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=outsider)
        schedule.recipient_groups.add(group)
        assert resolve_recipients(schedule) == []

    def test_group_from_another_org_grants_nothing_even_if_referenced(
        self, report_row, other_org, make_user, make_group, attach_group
    ):
        member = make_user("rival@rival.com", org=other_org)
        group = make_group("Rival Leadership", org_=other_org)
        attach_group(member, group)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=member)
        schedule.recipient_groups.add(group)
        # The member has no membership at all in the studio's org, so even
        # though the schedule (hypothetically) references a foreign group,
        # resolution finds nobody eligible.
        assert resolve_recipients(schedule) == []

    def test_individually_picked_user_who_left_the_studio_is_excluded(self, report_row, studio_tree, viewer):
        from apps.studios.models import StudioMembership

        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        StudioMembership.objects.filter(user=viewer, studio=studio_tree).delete()
        assert resolve_recipients(schedule) == []

    def test_role_expansion_tracks_membership_changes_between_sends(
        self, report_row, studio_tree, developer, make_user, grant_studio, org
    ):
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=developer, recipient_roles=["developers"]
        )
        assert resolve_recipients(schedule) == [developer]  # joiner not yet a member

        joiner = make_user("joiner@demo.example", org=org)
        grant_studio(joiner, studio_tree, roles.DEVELOPER)
        assert set(resolve_recipients(schedule)) == {developer, joiner}  # picked up with no edit

        from apps.studios.models import StudioMembership

        StudioMembership.objects.filter(user=developer, studio=studio_tree).delete()
        assert resolve_recipients(schedule) == [joiner]  # leaver drops with no edit

    def test_inactive_user_is_excluded(self, report_row, viewer):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        viewer.is_active = False
        viewer.save(update_fields=["is_active"])
        assert resolve_recipients(schedule) == []

    def test_no_recipients_of_any_kind_returns_empty(self, report_row, viewer):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        assert resolve_recipients(schedule) == []

    def test_group_recipient_is_rechecked_against_report_assignment(
        self, report_row, viewer, org, make_user, make_group, attach_group, settings
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        allowed = make_user("selected@demo.example", org=org)
        denied = make_user("unselected@demo.example", org=org)
        group = make_group("Selected delivery", grants=[(report_row.studio, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(allowed, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)

        empty_group = make_group("No report", grants=[(report_row.studio, roles.VIEWER)])
        empty_grant = empty_group.grants.get()
        empty_grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        empty_grant.save(update_fields=["viewer_scope"])
        attach_group(denied, empty_group)

        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipient_groups.add(group, empty_group)
        assert resolve_recipients(schedule) == [allowed]


# ── scheduled delivery ───────────────────────────────────────────────────────

class TestSendScheduled:
    def test_owner_access_revocation_skips_delivery(self, report_row, viewer, write_meta):
        from apps.studios.models import StudioMembership

        write_meta(report_row.slug, last_status="success")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        StudioMembership.objects.filter(user=viewer, studio=report_row.studio).delete()

        send_scheduled(schedule.pk)

        assert mail.outbox == []
        schedule.refresh_from_db()
        assert schedule.last_sent_at is None

    def test_disabled_readiness_skips_selected_owner_delivery(
        self, report_row, member, make_group, attach_group, settings
    ):
        from apps.orgs.models import PermissionGroupGrant

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        group = make_group("Selected owner", grants=[(report_row.studio, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=member)
        schedule.recipients.add(member)

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        send_scheduled(schedule.pk)

        assert mail.outbox == []
        schedule.refresh_from_db()
        assert schedule.last_sent_at is None

    def test_happy_path_embeds_snapshot_and_updates_last_sent(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, png=b"PNGDATA", pdf=b"PDFDATA")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        assert schedule.last_sent_at is None

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.subject == f"Fresh · {report_row.name}"
        # inline image, no other attachment (attach_pdf defaults False) --
        # the whole message is multipart/related (see
        # TestDeliveryMailMimeStructure for the tree with a PDF alongside).
        assert msg.message().get_content_type() == "multipart/related"
        schedule.refresh_from_db()
        assert schedule.last_sent_at is not None

    def test_pdf_attached_only_when_requested(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, png=b"PNGDATA", pdf=b"PDFDATA")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, attach_pdf=True)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        # The inline image lives in the MIME tree itself, not django's
        # .attachments list (see TestDeliveryMailMimeStructure) -- only the
        # PDF is a standalone attachment.
        assert len(mail.outbox[0].attachments) == 1
        root = mail.outbox[0].message()
        assert root.get_content_type() == "multipart/mixed"
        related, pdf_part = root.get_payload()
        assert related.get_content_type() == "multipart/related"
        assert pdf_part.get_content_type() == "application/pdf"

    def test_missing_snapshot_module_falls_back_to_text_html(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        assert "snapshot unavailable" in mail.outbox[0].body

    def test_render_error_falls_back_to_text_html(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        fake = types.ModuleType("apps.reports.snapshot")

        def _boom(output_dir):  # noqa: ARG001
            raise RuntimeError("rendering exploded")

        fake.render_report = _boom
        monkeypatch.setitem(sys.modules, "apps.reports.snapshot", fake)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)  # must not raise

        assert len(mail.outbox) == 1
        assert "snapshot unavailable" in mail.outbox[0].body

    def test_real_snapshot_module_error_result_falls_back_to_text_html(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        """Integration with the real apps.reports.snapshot (from
        feat/report-snapshot, merged in): unlike the sys.modules-stub tests
        above, this imports the actual module — proving _try_render's
        ``from apps.reports.snapshot import render_report`` resolves to it —
        and only fakes the one function, to confirm the fallback path also
        covers render_report's own documented contract: it never raises, it
        returns RenderResult(png=None, pdf=None, error=...) instead."""
        write_meta(report_row.slug, last_status="success")
        from apps.reports import snapshot as snapshot_mod

        def _fake_render(output_dir):  # noqa: ARG001
            return snapshot_mod.RenderResult(png=None, pdf=None, error="chromium unavailable: boom")

        monkeypatch.setattr(snapshot_mod, "render_report", _fake_render)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == f"Fresh · {report_row.name}"  # still "sent", just textual
        assert "snapshot unavailable" in mail.outbox[0].body

    def test_not_sent_when_last_build_failed(self, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="error", last_error="boom")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        assert "not sent" in mail.outbox[0].subject.lower()

    def test_not_sent_when_report_never_built(self, report_row, viewer):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        assert "not sent" in mail.outbox[0].subject.lower()

    def test_disabled_schedule_sends_nothing(self, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, enabled=False)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        assert mail.outbox == []

    def test_no_recipients_sends_nothing(self, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)

        send_scheduled(schedule.pk)

        assert mail.outbox == []

    def test_never_raises_on_unknown_schedule(self):
        send_scheduled(999999)  # must not raise

    def test_delivers_to_role_expanded_recipients(self, monkeypatch, report_row, developer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=developer, recipient_roles=["developers"]
        )

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [developer.email]

    def test_delivers_to_group_expanded_recipients_intersected_with_studio(
        self, monkeypatch, report_row, viewer, make_group, attach_group, write_meta
    ):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        group = make_group("Leadership")
        attach_group(viewer, group)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipient_groups.add(group)

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [viewer.email]

    def test_role_only_schedule_with_no_matching_members_sends_nothing(
        self, report_row, viewer, write_meta
    ):
        write_meta(report_row.slug, last_status="success")
        # viewer has no admin/developer role -- "admins" resolves to nobody.
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=viewer, recipient_roles=["admins"]
        )

        send_scheduled(schedule.pk)

        assert mail.outbox == []


class TestDeliveryMailMetadata:
    """The trellum-banded delivery mail's meta line: a cadence phrase (see
    notify._cadence_phrase) drawn from the schedule, a built_at timestamp
    (see notify._format_dt) drawn from _meta.json's last_run, and a hidden
    inbox preheader carrying the preview text -- all three optional in the
    template, present only when the underlying data is."""

    def test_cadence_and_built_at_appear_in_html_body(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        write_meta(
            report_row.slug, last_status="success", last_run="2026-08-19T07:58:00+00:00"
        )
        _remove_fake_snapshot(monkeypatch)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        assert len(mail.outbox) == 1
        html_body = mail.outbox[0].alternatives[0][0]
        # default schedule: freq=daily, send_hour=8, send_minute=0, tz=UTC
        assert "daily at 08:00 (UTC)" in html_body
        assert "19 Aug 2026, 07:58" in html_body

    def test_no_last_run_omits_built_at_without_raising(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        write_meta(report_row.slug, last_status="success")  # no last_run key at all
        _remove_fake_snapshot(monkeypatch)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)  # must not raise

        assert len(mail.outbox) == 1
        assert "built " not in mail.outbox[0].alternatives[0][0]

    def test_preheader_present_with_preview_text(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        send_scheduled(schedule.pk)

        html_body = mail.outbox[0].alternatives[0][0]
        assert "display:none" in html_body
        assert report_row.studio.name in html_body

    def test_sample_subject_uses_sample_prefix(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)

        send_sample(report_row, viewer)

        assert mail.outbox[0].subject == f"Sample · {report_row.name}"


class TestDeliveryMailFluidLayout:
    """The card table must shrink on narrow viewports instead of forcing
    horizontal scroll -- a hard width="600" attribute on the card table
    beats its own max-width:600px style (see templates/email/_base_top.html),
    so the fix drops the attribute-forced width in favour of width="100%"
    with max-width in style, adds the usual mobile-mail head (doctype,
    charset, viewport meta), and lets the inline snapshot scale with its
    container instead of pinning a device-pixel width="552" attribute."""

    def test_card_table_is_fluid_not_fixed_600(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)

        send_sample(report_row, viewer)

        html_body = mail.outbox[0].alternatives[0][0]
        assert 'width="100%"' in html_body
        assert 'width="600"' not in html_body

    def test_viewport_meta_and_doctype_present(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)

        send_sample(report_row, viewer)

        html_body = mail.outbox[0].alternatives[0][0]
        assert "<!doctype html>" in html_body.lower()
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in html_body

    def test_snapshot_image_has_no_fixed_width_attribute(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, png=b"PNGDATA")

        send_sample(report_row, viewer)

        html_body = mail.outbox[0].alternatives[0][0]
        assert 'width="552"' not in html_body
        assert "max-width:552px" in html_body


class TestDeliveryMailMimeStructure:
    """apps.reports.notify._send_delivery_mail's MIME tree. An inline CID
    image needs a multipart/related wrapper to render at all; a PDF needs
    to be a plain sibling attachment for mail clients (Outlook among them)
    to show it as a downloadable attachment rather than swallowing it as
    "related" content. The two attachments can't share the same wrapper --
    see notify._DeliveryEmail's docstring for the tree this produces:

        png only:    multipart/related > [alternative(text,html), image]
        pdf only:    multipart/mixed   > [alternative(text,html), pdf]
        png + pdf:   multipart/mixed   > [related > [alternative, image], pdf]
    """

    def test_png_only_is_multipart_related(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, png=b"PNGDATA")

        send_sample(report_row, viewer)

        root = mail.outbox[0].message()
        assert root.get_content_type() == "multipart/related"
        alt, image = root.get_payload()
        assert alt.get_content_type() == "multipart/alternative"
        assert image.get_content_type() == "image/png"
        assert image["Content-ID"] == "<report-snapshot>"

    def test_pdf_only_is_multipart_mixed(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, pdf=b"PDFDATA")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, attach_pdf=True)

        send_sample(schedule, viewer)

        root = mail.outbox[0].message()
        assert root.get_content_type() == "multipart/mixed"
        alt, pdf_part = root.get_payload()
        assert alt.get_content_type() == "multipart/alternative"
        assert pdf_part.get_content_type() == "application/pdf"
        assert "attachment" in pdf_part["Content-Disposition"]

    def test_png_and_pdf_nests_related_inside_mixed(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, png=b"PNGDATA", pdf=b"PDFDATA")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, attach_pdf=True)

        send_sample(schedule, viewer)

        root = mail.outbox[0].message()
        assert root.get_content_type() == "multipart/mixed"
        related, pdf_part = root.get_payload()
        # The PDF is a sibling of the whole related blob, not swept inside
        # it -- this is exactly the bug: some clients never surface an
        # attachment nested inside multipart/related as downloadable.
        assert related.get_content_type() == "multipart/related"
        assert pdf_part.get_content_type() == "application/pdf"
        alt, image = related.get_payload()
        assert alt.get_content_type() == "multipart/alternative"
        assert image.get_content_type() == "image/png"


class TestSendSample:
    def test_sample_from_bare_report_carries_no_unsubscribe_link(
        self, monkeypatch, report_row, viewer, write_meta
    ):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)

        send_sample(report_row, viewer)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [viewer.email]
        assert "unsubscribe" not in mail.outbox[0].body.lower()

    def test_sample_from_schedule_honours_attach_pdf(self, monkeypatch, report_row, viewer, write_meta):
        write_meta(report_row.slug, last_status="success")
        _inject_fake_snapshot(monkeypatch, png=b"x", pdf=b"y")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, attach_pdf=True)

        send_sample(schedule, viewer)

        # The inline snapshot travels inside the MIME tree itself (see
        # TestDeliveryMailMimeStructure), not django's .attachments list --
        # only the PDF, a genuine standalone attachment, shows up here.
        assert len(mail.outbox[0].attachments) == 1
        root = mail.outbox[0].message()
        assert root.get_content_type() == "multipart/mixed"

    def test_send_failure_is_logged_not_silent(self, caplog, viewer):
        """notify.send_sample (the catching wrapper other/future callers use
        for a fire-and-forget send, and the one send_sample_or_raise's
        thin raising variant sits alongside) must never let a failure --
        here, the send layer raising on a malformed argument -- vanish
        without a trace."""
        import logging

        with caplog.at_level(logging.ERROR, logger="apps.reports.notify"):
            send_sample(object(), viewer)  # not an EmailSchedule or Report -> must not raise

        assert mail.outbox == []
        assert any("send_sample failed" in r.message for r in caplog.records)


# ── unsubscribe token ─────────────────────────────────────────────────────────

class TestUnsubscribeToken:
    def test_round_trip(self):
        token = sign_unsubscribe_token("schedule", 42, 7)
        assert read_unsubscribe_token(token) == {"kind": "schedule", "id": 42, "uid": 7}

    def test_garbage_token_is_rejected(self):
        assert read_unsubscribe_token("not-a-real-token") is None


class TestUnsubscribeView:
    def test_schedule_get_then_post(self, client, report_row, viewer):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        token = sign_unsubscribe_token("schedule", schedule.pk, viewer.pk)
        url = f"/notify/unsubscribe/{token}"

        get_resp = client.get(url)
        assert get_resp.status_code == 200
        assert viewer in schedule.recipients.all()  # GET must never mutate

        post_resp = client.post(url)
        assert post_resp.status_code == 200
        schedule.refresh_from_db()
        assert viewer not in schedule.recipients.all()

    def test_alert_kind_token_is_invalid(self, client, report_row, viewer):
        """The "alert" unsubscribe kind no longer exists -- failure/recovery
        mail carries no unsubscribe link (see TestNoUnsubscribeOnAlertMail).
        A token of that shape, however it arose (an old mail, a forged
        request), resolves to nothing and is rejected like any other
        invalid token."""
        token = sign_unsubscribe_token("alert", report_row.pk, viewer.pk)
        resp = client.get(f"/notify/unsubscribe/{token}")
        assert resp.status_code == 400

    def test_invalid_token_is_400_no_login_required(self, client):
        resp = client.get("/notify/unsubscribe/not-a-real-token")
        assert resp.status_code == 400

    def test_no_login_required_to_unsubscribe(self, client, report_row, viewer):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)
        token = sign_unsubscribe_token("schedule", schedule.pk, viewer.pk)
        resp = client.post(f"/notify/unsubscribe/{token}")
        assert resp.status_code == 200


# ── model validation: EmailSchedule.timezone ─────────────────────────────────

class TestEmailScheduleTimezoneValidation:
    def test_garbage_timezone_rejected(self, report_row, viewer):
        schedule = EmailSchedule(report=report_row, created_by=viewer, timezone="22")
        with pytest.raises(ValidationError) as exc_info:
            schedule.full_clean(exclude=["report", "created_by"])
        assert "timezone" in exc_info.value.message_dict

    def test_real_iana_zone_accepted(self, report_row, viewer):
        schedule = EmailSchedule(report=report_row, created_by=viewer, timezone="Europe/Amsterdam")
        schedule.full_clean(exclude=["report", "created_by"])  # must not raise

    def test_default_utc_accepted(self, report_row, viewer):
        schedule = EmailSchedule(report=report_row, created_by=viewer)
        schedule.full_clean(exclude=["report", "created_by"])  # must not raise


# ── views: schedules, sample-now ─────────────────────────────────────────────

class TestScheduleViews:
    def test_create_then_list(self, login, viewer, prefix, report_row):
        c = login(viewer)
        resp = c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 30}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["schedule"]["freq"] == "daily"
        assert body["schedule"]["recipients"] == [{"id": viewer.pk, "email": viewer.email}]

        listed = c.get(f"{prefix}/api/reports/player-overview/schedules").json()["schedules"]
        assert len(listed) == 1

    def test_list_carries_default_timezone_and_no_alert_mode(self, login, viewer, prefix, report_row):
        """The Email drawer's first paint needs the schedule list and a sane
        timezone default from this one GET. There is no "alert_mode" any
        more -- failure/recovery alerts have no per-user setting."""
        report_row.schedule_timezone = "America/New_York"
        report_row.save(update_fields=["schedule_timezone"])
        c = login(viewer)

        body = c.get(f"{prefix}/api/reports/player-overview/schedules").json()
        assert body["default_timezone"] == "America/New_York"
        assert "alert_mode" not in body

    def test_default_timezone_falls_back_to_utc_when_blank(self, login, viewer, prefix, report_row):
        # sync_studio_registry always fills schedule_timezone (defaulting to
        # "UTC" itself), so an actually-blank value only happens if something
        # clears it out from under the registry -- the `or "UTC"` fallback in
        # the view covers that directly rather than relying on scan.py's own
        # default to exercise it.
        report_row.schedule_timezone = ""
        report_row.save(update_fields=["schedule_timezone"])
        body = login(viewer).get(f"{prefix}/api/reports/player-overview/schedules").json()
        assert body["default_timezone"] == "UTC"

    def test_invalid_weekday_rejected(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "weekly", "weekday": 9}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_timezone_rejected_on_create(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "timezone": "22"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "timezone" in resp.json()["errors"]
        assert not EmailSchedule.objects.filter(report=report_row).exists()

    def test_invalid_timezone_rejected_on_update(self, login, viewer, prefix, report_row):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}",
            data=json.dumps({"timezone": "Not/AZone"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "timezone" in resp.json()["errors"]
        schedule.refresh_from_db()
        assert schedule.timezone == "UTC"  # the model default: the bad value was never saved

    def test_valid_iana_timezone_accepted(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "timezone": "Europe/Amsterdam"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["timezone"] == "Europe/Amsterdam"

    def test_update_own_schedule(self, login, viewer, prefix, report_row):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}",
            data=json.dumps({"send_hour": 14}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        schedule.refresh_from_db()
        assert schedule.send_hour == 14

    def test_cannot_touch_someone_elses_schedule(
        self, login, viewer, other_viewer, prefix, report_row
    ):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=other_viewer)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}",
            data=json.dumps({"send_hour": 14}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_delete_own_schedule(self, login, viewer, prefix, report_row):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        resp = login(viewer).delete(f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}")
        assert resp.status_code == 200
        assert not EmailSchedule.objects.filter(pk=schedule.pk).exists()

    def test_requires_auth(self, client, prefix, report_row):
        assert client.get(f"{prefix}/api/reports/player-overview/schedules").status_code == 401

    # ── GET visibility: creator OR resolved recipient ───────────────────────

    def test_recipient_not_creator_sees_the_schedule_read_only(
        self, login, developer, studio_admin, prefix, report_row
    ):
        """A schedule addressed to "All developers" must show up in the
        drawer for a developer who didn't create it -- the same audience
        api_my_subscriptions already counts them into -- but read-only:
        they can't edit/sample/delete a schedule they don't own."""
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=studio_admin, recipient_roles=["developers"]
        )

        body = login(developer).get(f"{prefix}/api/reports/player-overview/schedules").json()

        assert len(body["schedules"]) == 1
        assert body["schedules"][0]["id"] == schedule.pk
        assert body["schedules"][0]["editable"] is False
        assert body["schedules"][0]["owner_name"] == studio_admin.display_name
        assert body["subscribed_count"] == 1

    def test_own_disabled_schedule_is_visible_but_does_not_light_the_badge(
        self, login, viewer, prefix, report_row
    ):
        """The owner can still see (and re-enable) their own disabled
        schedule, but a disabled schedule reaches nobody -- it must not
        count toward the badge the client broadcasts."""
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, enabled=False)
        schedule.recipients.add(viewer)

        body = login(viewer).get(f"{prefix}/api/reports/player-overview/schedules").json()

        assert len(body["schedules"]) == 1
        assert body["schedules"][0]["id"] == schedule.pk
        assert body["schedules"][0]["editable"] is True
        assert body["subscribed_count"] == 0

    def test_subscribed_count_matches_my_subscriptions_contract(
        self, login, developer, studio_admin, prefix, report_row
    ):
        """The count the drawer broadcasts (and my-subscriptions badge use)
        must agree exactly: enabled + resolved recipient."""
        EmailSchedule.objects.create(
            report=report_row, created_by=studio_admin, recipient_roles=["developers"]
        )
        c = login(developer)

        drawer_body = c.get(f"{prefix}/api/reports/player-overview/schedules").json()
        badge_body = c.get(f"{prefix}/api/my-subscriptions").json()

        assert drawer_body["subscribed_count"] == 1
        assert badge_body["reports"]["player-overview"]["schedules"] == 1
        assert drawer_body["subscribed_count"] == badge_body["reports"]["player-overview"]["schedules"]

    # ── recipient roles/groups: CRUD round-trip ─────────────────────────────

    def test_create_with_roles_and_groups(
        self, login, viewer, prefix, report_row, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(viewer, group)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "send_hour": 9,
                    "send_minute": 0,
                    "recipient_ids": [],
                    "recipient_roles": ["everyone", "developers"],  # deliberately out of order
                    "recipient_group_ids": [group.pk],
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()["schedule"]
        assert body["recipient_roles"] == ["developers", "everyone"]  # canonicalized order
        assert body["recipient_groups"] == [{"id": group.pk, "name": "Leadership"}]
        assert body["recipients"] == []

        schedule = EmailSchedule.objects.get(pk=body["id"])
        assert list(schedule.recipient_groups.all()) == [group]

    def test_create_with_no_recipient_fields_defaults_to_creator(self, login, viewer, prefix, report_row):
        """No recipient_ids/roles/groups key at all -- the legacy "just email
        me" default, unchanged from before roles/groups existed (see
        test_create_then_list above)."""
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()["schedule"]
        assert body["recipients"] == [{"id": viewer.pk, "email": viewer.email}]
        assert body["recipient_roles"] == []

    def test_create_with_everything_explicitly_empty_is_rejected(self, login, viewer, prefix, report_row):
        """Once the caller touches any recipient field, an empty union is a
        real validation error, not a silent "just email the creator" --
        distinct from the fully-omitted-body legacy default above."""
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "recipient_ids": [],
                    "recipient_roles": [],
                    "recipient_group_ids": [],
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert EmailSchedule.objects.count() == 0

    def test_unknown_role_is_rejected(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "recipient_roles": ["superadmin"]}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "recipient_roles" in resp.json()["errors"]

    def test_group_from_another_org_is_not_pickable(
        self, login, viewer, prefix, report_row, other_org, make_group
    ):
        foreign_group = make_group("Rival Group", org_=other_org)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "recipient_ids": [],
                    "recipient_roles": ["developers"],  # keep the union non-empty
                    "recipient_group_ids": [foreign_group.pk],
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "groups" in resp.json()["error"]

    def test_update_roles_and_groups_round_trip(
        self, login, viewer, prefix, report_row, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(viewer, group)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}",
            data=json.dumps({"recipient_ids": [], "recipient_roles": ["admins"], "recipient_group_ids": [group.pk]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        schedule.refresh_from_db()
        assert schedule.recipient_roles == ["admins"]
        assert list(schedule.recipient_groups.all()) == [group]
        assert list(schedule.recipients.all()) == []  # individuals were cleared too

    def test_create_rejects_an_individual_without_report_access(
        self, login, viewer, prefix, report_row, org, make_user
    ):
        outsider = make_user("no-report@demo.example", org=org)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"recipient_ids": [outsider.pk]}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "cannot view this report" in resp.json()["error"]

    def test_update_that_empties_everything_is_rejected_and_leaves_it_untouched(
        self, login, viewer, prefix, report_row
    ):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(viewer)

        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}",
            data=json.dumps({"recipient_ids": []}),
            content_type="application/json",
        )

        assert resp.status_code == 400
        schedule.refresh_from_db()
        assert viewer in schedule.recipients.all()  # rejected update never applied

    def test_update_unrelated_field_does_not_require_touching_recipients(
        self, login, viewer, prefix, report_row
    ):
        # A schedule with no recipients at all (as if created before this
        # feature, or directly via the ORM) -- a send_hour-only PATCH must
        # not retroactively demand recipients it was never asked to set.
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}",
            data=json.dumps({"send_hour": 14}),
            content_type="application/json",
        )
        assert resp.status_code == 200

    # ── recipient_summary ────────────────────────────────────────────────

    def test_recipient_summary_combines_roles_groups_and_people(
        self, login, viewer, other_viewer, prefix, report_row, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(other_viewer, group)
        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "recipient_ids": [viewer.pk],
                    "recipient_roles": ["developers"],
                    "recipient_group_ids": [group.pk],
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["recipient_summary"] == "All developers + Leadership + 1 person"

    def test_recipient_summary_drops_an_individual_who_left_the_studio(
        self, other_viewer, studio_tree, report_row, viewer
    ):
        """A schedule's individual pick who has since lost studio access
        drops out of the summary's people count -- it tracks resolved
        reality, same as the send path (see notify.intersect_with_studio)."""
        from apps.reports.views import _recipient_summary
        from apps.studios.models import StudioMembership

        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(other_viewer)
        assert _recipient_summary(schedule) == "1 person"

        StudioMembership.objects.filter(user=other_viewer, studio=studio_tree).delete()
        assert _recipient_summary(schedule) == "No recipients"


class TestRecipientPickerChipData:
    """apps.reports.views.api_studio_members, extended: alongside the
    existing members list, the recipient picker's quick-add data -- role
    counts straight from this studio's StudioMembership rows, and this org's
    permission groups with member counts intersected with the studio (see
    notify.intersect_with_studio), so a chip's number is "who would actually
    receive", not a raw group size."""

    def test_role_counts(self, login, viewer, developer, studio_admin, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/members").json()
        # viewer + developer + studio_admin = 3 studio members total.
        assert body["roles"] == {"admins": 1, "developers": 1, "everyone": 3}

    def test_studio_name_included(self, login, viewer, prefix, report_row, studio_tree):
        body = login(viewer).get(f"{prefix}/api/members").json()
        assert body["studio_name"] == studio_tree.name

    def test_group_count_intersected_with_studio(
        self, login, viewer, other_viewer, prefix, report_row, org, make_user, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(viewer, group)  # has studio access
        outsider = make_user("outsider@demo.example", org=org)  # org member, no studio access
        attach_group(outsider, group)

        body = login(viewer).get(f"{prefix}/api/members").json()
        entry = [g for g in body["groups"] if g["id"] == group.pk][0]
        assert entry["name"] == "Leadership"
        assert entry["count"] == 1  # outsider doesn't count

    def test_groups_scoped_to_this_org_only(
        self, login, viewer, prefix, report_row, other_org, make_group
    ):
        make_group("Rival Group", org_=other_org)
        body = login(viewer).get(f"{prefix}/api/members").json()
        assert all(g["name"] != "Rival Group" for g in body["groups"])


class TestSampleView:
    """"Send me a sample now" runs off the request thread (see
    apps.reports.views._run_sample_send) -- a full Playwright snapshot
    render plus an SMTP round trip is too slow to hold an interactive
    request open for, and the build queue is shaped for the (slow,
    resource-heavy) build pipeline, not a cheap one-off preview click. The
    `run_threads_inline` fixture makes that background thread run
    synchronously so these tests can assert on mail.outbox without a
    sleep/join."""

    def test_sample_now_returns_202_with_async_wording(
        self, monkeypatch, login, viewer, prefix, report_row, write_meta, run_threads_inline
    ):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)

        resp = login(viewer).post(f"{prefix}/api/reports/player-overview/sample")

        assert resp.status_code == 202
        assert "sending" in resp.json()["message"].lower()
        assert len(mail.outbox) == 1  # the inline-thread fixture already ran the send

    def test_schedule_sample_returns_202_with_async_wording(
        self, monkeypatch, login, viewer, prefix, report_row, write_meta, run_threads_inline
    ):
        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)

        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}/sample"
        )

        assert resp.status_code == 202
        assert "sending" in resp.json()["message"].lower()
        assert len(mail.outbox) == 1

    def test_send_failure_is_logged_with_user_and_report_context_not_silent(
        self, monkeypatch, caplog, login, viewer, prefix, report_row, write_meta, run_threads_inline
    ):
        """Before this fix, a sample-send failure was swallowed inside
        notify.send_sample with only a generic %r-of-its-arguments log line.
        The request-thread wrapper (apps.reports.views._run_sample_send)
        now calls the raising variant itself and logs with the actual
        user/report identifiers."""
        import logging

        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        monkeypatch.setattr(
            "apps.reports.notify.send_sample_or_raise",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("smtp exploded")),
        )

        with caplog.at_level(logging.ERROR, logger="apps.reports.views"):
            resp = login(viewer).post(f"{prefix}/api/reports/player-overview/sample")

        assert resp.status_code == 202  # the request itself still succeeds
        assert mail.outbox == []
        records = [r for r in caplog.records if "sample send failed" in r.message.lower()]
        assert records, "expected a logged error naming the failed sample send"
        assert viewer.email in records[0].message
        assert "player-overview" in records[0].message

    def test_sample_now_429_when_the_pool_is_full(
        self, monkeypatch, login, viewer, prefix, report_row, write_meta
    ):
        """Every studio viewer can POST this, and each accepted POST costs a
        browser render plus an SMTP round trip -- so a full slot pool is
        answered with 429 + Retry-After rather than another thread."""
        import threading

        from apps.reports import views as views_mod

        write_meta(report_row.slug, last_status="success")
        full = threading.BoundedSemaphore(1)
        assert full.acquire(blocking=False)
        monkeypatch.setattr(views_mod, "_SAMPLE_SLOTS", full)

        resp = login(viewer).post(f"{prefix}/api/reports/player-overview/sample")

        assert resp.status_code == 429
        assert resp["Retry-After"]
        assert mail.outbox == []

    def test_schedule_sample_429_when_the_pool_is_full(
        self, monkeypatch, login, viewer, prefix, report_row, write_meta
    ):
        import threading

        from apps.reports import views as views_mod

        write_meta(report_row.slug, last_status="success")
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        full = threading.BoundedSemaphore(1)
        assert full.acquire(blocking=False)
        monkeypatch.setattr(views_mod, "_SAMPLE_SLOTS", full)

        resp = login(viewer).post(
            f"{prefix}/api/reports/player-overview/schedules/{schedule.pk}/sample"
        )

        assert resp.status_code == 429
        assert resp["Retry-After"]
        assert mail.outbox == []

    def test_slot_is_released_after_the_send(
        self, monkeypatch, login, viewer, prefix, report_row, write_meta, run_threads_inline
    ):
        """A finished send gives its slot back -- a one-slot pool must still
        accept a second POST once the first has run (the inline-thread
        fixture makes the worker's finally: release() run before it)."""
        import threading

        from apps.reports import views as views_mod

        write_meta(report_row.slug, last_status="success")
        _remove_fake_snapshot(monkeypatch)
        monkeypatch.setattr(views_mod, "_SAMPLE_SLOTS", threading.BoundedSemaphore(1))
        c = login(viewer)

        assert c.post(f"{prefix}/api/reports/player-overview/sample").status_code == 202
        assert c.post(f"{prefix}/api/reports/player-overview/sample").status_code == 202
