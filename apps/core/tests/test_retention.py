"""Retention: what gets deleted, and — more important — what does not.

Nothing in this product deleted anything before, so the risk here is not that
cleanup fails to run. It is that cleanup removes something someone needed. Most
of these tests are about the exceptions.
"""
import os
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.core import retention
from apps.core.models import AuditLog, OpsState
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


def _run(studio, report, *, days_ago, status=Run.SUCCESS, slug="daily", tails=True):
    created = timezone.now() - timedelta(days=days_ago)
    run = Run.objects.create(
        report=report, studio=studio, slug=slug, status=status,
        stdout_tail="out" * 100 if tails else "",
        stderr_tail="err" * 100 if tails else "",
        started_at=created, finished_at=created + timedelta(seconds=30),
    )
    # created_at has a default rather than auto_now_add, but it is set on
    # insert, so move it explicitly.
    Run.objects.filter(pk=run.pk).update(created_at=created)
    run.refresh_from_db()
    return run


def _age_tree(path, *, days: int) -> None:
    """Backdate every file in a tree — the mtime floor the orphan sweep uses
    when no audit row explains what orphaned it."""
    old = (timezone.now() - timedelta(days=days)).timestamp()
    for entry in path.rglob("*"):
        if entry.is_file():
            os.utime(entry, (old, old))


@pytest.fixture
def report(studio):
    from apps.reports.models import Report

    return Report.objects.create(studio=studio, slug="daily", name="Daily")


class TestRunRetention:
    def test_old_terminal_runs_are_removed(self, settings, studio, report):
        settings.RETENTION = {**settings.RETENTION, "run_days": 30,
                              "run_keep_per_report": 0}
        _run(studio, report, days_ago=200)
        _run(studio, report, days_ago=1)
        label, count = retention.purge_runs()
        assert (label, count) == ("runs", 1)
        assert Run.objects.count() == 1

    @pytest.mark.parametrize("status", [Run.QUEUED, Run.STARTING, Run.RUNNING])
    def test_active_runs_are_never_touched(self, settings, studio, report, status):
        """Whatever their age. An in-flight build is not history."""
        settings.RETENTION = {**settings.RETENTION, "run_days": 1,
                              "run_keep_per_report": 0}
        _run(studio, report, days_ago=500, status=status)
        assert retention.purge_runs()[1] == 0
        assert Run.objects.count() == 1

    def test_newest_n_per_report_survive_any_age(self, settings, studio, report):
        """A quarterly report must not lose its entire history to a 90-day
        window just because it runs rarely."""
        settings.RETENTION = {**settings.RETENTION, "run_days": 30,
                              "run_keep_per_report": 3}
        for age in (400, 300, 200, 100, 50):
            _run(studio, report, days_ago=age)
        retention.purge_runs()
        assert Run.objects.count() == 3
        # The three kept are the newest three.
        ages = sorted(
            (timezone.now() - r.created_at).days for r in Run.objects.all()
        )
        assert ages == [50, 100, 200]

    def test_the_current_billing_month_is_never_purged(
        self, settings, studio, report, monkeypatch
    ):
        """apps/orgs/quotas.py derives monthly build minutes from Run rather
        than a ledger, so deleting a run inside this month hands quota back.

        The month boundary is synthetic: against the real calendar, a run
        "2 days old but inside this month" cannot exist on the 1st or 2nd,
        and this test failed on main every month-start (first bitten
        2026-09-01). A boundary pinned 10 days back exercises the exact same
        ``min(cutoff, month_start())`` guard on every calendar day.
        """
        monkeypatch.setattr(
            "apps.orgs.quotas.month_start",
            lambda: timezone.now() - timedelta(days=10),
        )
        settings.RETENTION = {**settings.RETENTION, "run_days": 0 or 1,
                              "run_keep_per_report": 0}
        _run(studio, report, days_ago=2)     # old by the window, but this month
        retention.purge_runs()
        assert Run.objects.count() == 1, "purged a run inside the billing month"

    def test_zero_means_keep_forever(self, settings, studio, report):
        settings.RETENTION = {**settings.RETENTION, "run_days": 0}
        _run(studio, report, days_ago=5000)
        assert retention.purge_runs()[1] == 0
        assert Run.objects.count() == 1


class TestRunOutputBlanking:
    def test_tails_are_blanked_but_rows_kept(self, settings, studio, report):
        """The cheapest large win: the tails are most of the bytes, the row is
        what history and quota accounting need."""
        settings.RETENTION = {**settings.RETENTION, "run_output_days": 7}
        old = _run(studio, report, days_ago=30)
        recent = _run(studio, report, days_ago=1)
        label, count = retention.blank_run_output()
        assert (label, count) == ("run log tails", 1)
        old.refresh_from_db()
        recent.refresh_from_db()
        assert old.stdout_tail == "" and old.stderr_tail == ""
        assert recent.stdout_tail != ""
        assert Run.objects.count() == 2


class TestOtherTargets:
    def test_old_audit_rows_go(self, settings, org):
        settings.RETENTION = {**settings.RETENTION, "audit_days": 30}
        row = AuditLog.objects.create(org=org, action="member.invite")
        AuditLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=90)
        )
        AuditLog.objects.create(org=org, action="member.invite")
        assert retention.purge_audit()[1] == 1
        assert AuditLog.objects.count() == 1


def _aged_audit_row(org, *, action, category, days_ago):
    row = AuditLog.objects.create(org=org, action=action, category=category)
    AuditLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    row.refresh_from_db()
    return row


class TestAuditPerCategoryRetention:
    """access/auth events age out on the shorter RETENTION_AUDIT_ACCESS_DAYS
    window; authz/admin/system stay under RETENTION_AUDIT_DAYS -- that split
    is the whole point of the two windows (see apps.core.retention.purge_audit
    and apps.core.audit_actions for what a category is)."""

    def test_an_old_access_row_dies_while_a_same_age_authz_row_survives(self, settings, org):
        settings.RETENTION = {
            **settings.RETENTION, "audit_access_days": 30, "audit_days": 365,
        }
        access_row = _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        authz_row = _aged_audit_row(org, action="member.invite", category="authz", days_ago=60)

        label, count = retention.purge_audit()
        assert label == "audit rows"
        assert count == 1
        assert not AuditLog.objects.filter(pk=access_row.pk).exists()
        assert AuditLog.objects.filter(pk=authz_row.pk).exists()

    def test_auth_category_shares_the_access_window(self, settings, org):
        settings.RETENTION = {**settings.RETENTION, "audit_access_days": 30, "audit_days": 365}
        old_login = _aged_audit_row(org, action="auth.login", category="auth", days_ago=60)
        retention.purge_audit()
        assert not AuditLog.objects.filter(pk=old_login.pk).exists()

    def test_admin_and_system_use_the_longer_window(self, settings, org):
        settings.RETENTION = {**settings.RETENTION, "audit_access_days": 30, "audit_days": 365}
        admin_row = _aged_audit_row(org, action="org.create", category="admin", days_ago=60)
        system_row = _aged_audit_row(org, action="cache.clear", category="system", days_ago=60)
        retention.purge_audit()
        assert AuditLog.objects.filter(pk=admin_row.pk).exists()
        assert AuditLog.objects.filter(pk=system_row.pk).exists()

    def test_zero_on_either_window_means_keep_that_bucket_forever(self, settings, org):
        settings.RETENTION = {**settings.RETENTION, "audit_access_days": 0, "audit_days": 365}
        access_row = _aged_audit_row(org, action="report.view", category="access", days_ago=5000)
        retention.purge_audit()
        assert AuditLog.objects.filter(pk=access_row.pk).exists()

    def test_dry_run_changes_nothing(self, settings, org):
        settings.RETENTION = {**settings.RETENTION, "audit_access_days": 30, "audit_days": 365}
        access_row = _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        label, count = retention.purge_audit(dry_run=True)
        assert count == 1
        assert AuditLog.objects.filter(pk=access_row.pk).exists()


class TestAuditArchiveBeforePrune:
    def test_on_by_default_archives_before_prune(self, settings, org, tmp_path):
        # The archive is on by default (compliance-safe): a purge exports the
        # doomed window before deleting it.
        settings.DATA_DIR = tmp_path
        settings.RETENTION = {**settings.RETENTION, "audit_access_days": 30, "audit_days": 365}
        _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        retention.purge_audit()
        assert (tmp_path / "archive" / "audit").exists()

    def test_disabled_hard_deletes_with_no_archive(self, settings, org, tmp_path):
        # Operators who explicitly opt out get a hard delete, no archive file.
        settings.DATA_DIR = tmp_path
        settings.RETENTION = {**settings.RETENTION, "audit_access_days": 30,
                              "audit_days": 365, "audit_archive": False}
        _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        retention.purge_audit()
        assert not (tmp_path / "archive" / "audit").exists()

    def test_archived_rows_round_trip_through_the_ndjson(self, settings, org, tmp_path):
        import gzip
        import json

        settings.DATA_DIR = tmp_path
        settings.RETENTION = {
            **settings.RETENTION, "audit_access_days": 30, "audit_days": 365,
            "audit_archive": True,
        }
        row = _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        row.metadata = {"via": "portal"}
        row.save(update_fields=["metadata"])

        label, count = retention.purge_audit()
        assert count == 1
        assert not AuditLog.objects.filter(pk=row.pk).exists()

        month = row.created_at.strftime("%Y%m")
        archive_path = tmp_path / "archive" / "audit" / org.slug / f"{month}.ndjson.gz"
        assert archive_path.exists()
        with gzip.open(archive_path, "rt", encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        assert len(lines) == 1
        assert lines[0]["id"] == row.pk
        assert lines[0]["action"] == "report.view"
        assert lines[0]["org"] == org.slug
        assert lines[0]["metadata"] == {"via": "portal"}

    def test_a_later_purge_the_same_month_appends_rather_than_overwrites(self, settings, org, tmp_path):
        import gzip

        settings.DATA_DIR = tmp_path
        settings.RETENTION = {
            **settings.RETENTION, "audit_access_days": 30, "audit_days": 365,
            "audit_archive": True,
        }
        first = _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        retention.purge_audit()
        _aged_audit_row(org, action="report.view", category="access", days_ago=60)
        retention.purge_audit()

        month = first.created_at.strftime("%Y%m")
        archive_path = tmp_path / "archive" / "audit" / org.slug / f"{month}.ndjson.gz"
        with gzip.open(archive_path, "rt", encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
        assert len(lines) == 2

    def test_no_org_rows_archive_under_a_placeholder_directory(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        settings.RETENTION = {
            **settings.RETENTION, "audit_access_days": 30, "audit_days": 365,
            "audit_archive": True,
        }
        row = AuditLog.objects.create(org=None, action="report.view", category="access")
        AuditLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=60))
        retention.purge_audit()
        assert (tmp_path / "archive" / "audit" / "_none").is_dir()

    def test_expired_sessions_go(self):
        from django.contrib.sessions.models import Session

        Session.objects.create(
            session_key="dead", session_data="x",
            expire_date=timezone.now() - timedelta(days=1),
        )
        Session.objects.create(
            session_key="live", session_data="x",
            expire_date=timezone.now() + timedelta(days=1),
        )
        assert retention.purge_sessions()[1] == 1
        assert Session.objects.filter(session_key="live").exists()

    def test_a_pending_invitation_is_not_expired(self, settings, org, org_admin):
        """Only accepted or expired ones go; a live invite is not litter."""
        from apps.accounts.models import Invitation

        settings.RETENTION = {**settings.RETENTION, "invitation_days": 1}
        live = Invitation.objects.create(
            org=org, email="new@demo.example", invited_by=org_admin,
            expires_at=timezone.now() + timedelta(days=7),
        )
        Invitation.objects.filter(pk=live.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        assert retention.purge_invitations()[1] == 0
        assert Invitation.objects.count() == 1


class TestCleanupCommand:
    def test_dry_run_changes_nothing(self, settings, studio, report):
        settings.RETENTION = {**settings.RETENTION, "run_days": 1,
                              "run_keep_per_report": 0}
        _run(studio, report, days_ago=400)
        call_command("cleanup", "--dry-run")
        assert Run.objects.count() == 1
        assert not OpsState.objects.filter(key="cleanup").exists()

    def test_a_real_run_records_what_it_did(self, settings, studio, report):
        """So "what went last night" survives the container logs — which on the
        default deployment are the first thing lost when the disk fills."""
        settings.RETENTION = {**settings.RETENTION, "run_days": 1,
                              "run_keep_per_report": 0}
        _run(studio, report, days_ago=400)
        call_command("cleanup")

        state = OpsState.objects.get(key="cleanup")
        assert state.ok is True
        assert state.payload["removed"]["runs"] == 1
        assert "policy" in state.payload

    def test_a_real_run_writes_one_summary_audit_row(self, settings, studio, report):
        """One row for the whole run, not one per deletion -- audit_system()
        because a nightly job has no request behind it."""
        settings.RETENTION = {**settings.RETENTION, "run_days": 1, "run_keep_per_report": 0}
        _run(studio, report, days_ago=400)
        call_command("cleanup")

        row = AuditLog.objects.get(action="retention.purge")
        assert row.actor_id is None
        assert row.category == "system"
        assert row.metadata["removed"]["runs"] == 1

    def test_dry_run_writes_no_audit_row(self, settings, studio, report):
        settings.RETENTION = {**settings.RETENTION, "run_days": 1, "run_keep_per_report": 0}
        _run(studio, report, days_ago=400)
        call_command("cleanup", "--dry-run")
        assert not AuditLog.objects.filter(action="retention.purge").exists()

    def test_one_failing_target_does_not_stop_the_rest(
        self, settings, studio, report, monkeypatch
    ):
        """A locked table should not mean the disk keeps filling for a day."""
        settings.RETENTION = {**settings.RETENTION, "run_days": 1,
                              "run_keep_per_report": 0}
        _run(studio, report, days_ago=400)

        def boom(dry_run=False):
            raise RuntimeError("table is locked")

        boom.__name__ = "purge_audit"   # reported under the target's own name
        monkeypatch.setattr(retention, "TARGETS", (boom, retention.purge_runs))
        call_command("cleanup")

        assert Run.objects.count() == 0, "the healthy target still ran"
        state = OpsState.objects.get(key="cleanup")
        assert state.ok is False
        assert state.payload["failures"]["purge_audit"] == "table is locked"


class TestRetentionHealthCheck:
    def _row(self):
        from apps.core.health import run_checks

        return next(c for c in run_checks() if c["label"] == "retention")

    def test_never_run_fails(self, settings):
        settings.CLEANUP_ENABLED = True
        row = self._row()
        assert row["ok"] is False
        assert "never run" in row["detail"]

    def test_recent_run_passes(self, settings):
        settings.CLEANUP_ENABLED = True
        OpsState.record("cleanup", ok=True, removed={"runs": 5}, failures={})
        row = self._row()
        assert row["ok"] is True
        assert "removed 5" in row["detail"]

    def test_stale_run_fails(self, settings):
        settings.CLEANUP_ENABLED = True
        settings.CLEANUP_MAX_AGE_HOURS = 48
        OpsState.record("cleanup", ok=True, removed={}, failures={})
        OpsState.objects.filter(key="cleanup").update(
            ran_at=timezone.now() - timedelta(days=10)
        )
        row = self._row()
        assert row["ok"] is False
        assert "has stopped" in row["detail"]

    def test_disabled_is_not_a_failure(self, settings):
        """Turning retention off is a choice a compliance hold may require."""
        settings.CLEANUP_ENABLED = False
        row = self._row()
        assert row["ok"] is True
        assert "disabled" in row["detail"]

    def test_it_surfaces_what_is_still_sitting_there(self, settings, studio_tree):
        """"Cleanup ran" and "nothing is overdue" are different questions, and
        the second is the one an operator acts on."""
        from apps.reports.models import Report

        settings.CLEANUP_ENABLED = True
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        out = studio_tree.output_dir / "daily"
        out.mkdir(parents=True)
        (out / "index.html").write_text("x" * 4096, encoding="utf-8")
        report = Report.objects.create(studio=studio_tree, slug="daily")
        Report.objects.filter(pk=report.pk).update(
            last_built_at=timezone.now() - timedelta(days=90)
        )
        OpsState.record("cleanup", ok=True, removed={}, failures={})

        row = self._row()
        assert row["ok"] is True
        assert "reclaimable" in row["detail"]
        assert "0 B reclaimable" not in row["detail"]

    def test_an_unmeasurable_volume_does_not_fail_the_check(
        self, settings, monkeypatch
    ):
        """A walk of the data volume must not be able to fail the check it only
        decorates — whether cleanup is still running is the actual question."""
        settings.CLEANUP_ENABLED = True
        OpsState.record("cleanup", ok=True, removed={}, failures={})
        monkeypatch.setattr(
            retention, "reclaimable_bytes",
            lambda: (_ for _ in ()).throw(OSError("volume gone")),
        )
        row = self._row()
        assert row["ok"] is True
        assert "unmeasurable" in row["detail"]


# ── The two data windows ───────────────────────────────────────────────────
#
# internal planning ticket #110. These are the only retention rules with a public claim attached,
# so the tests below are less about "does it delete" than about the four ways
# it must refuse to.


class TestWindowResolution:
    @pytest.mark.parametrize(
        "ceiling,chosen,expected",
        [
            (30, None, 30),   # never chose: inherit
            (30, 7, 7),       # tightened
            (30, 90, 30),     # cannot be loosened past the ceiling
            (30, 0, 30),      # 0 does NOT mean forever while a ceiling exists
            (0, None, 0),     # cap off instance-wide (legal hold)
            (0, 0, 0),
            (0, 14, 14),      # an org may still keep its own shorter window
        ],
    )
    def test_built_window(self, settings, org, ceiling, chosen, expected):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": ceiling}
        org.retention_built_days = chosen
        assert retention.built_window(org) == expected

    def test_zero_never_lifts_the_built_ceiling(self, settings, org):
        """The named trap. An org storing 0 means "the shortest I am allowed",
        never "keep forever" — otherwise one org could opt out of the promise
        the instance makes on everyone's behalf."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        org.retention_built_days = 0
        assert retention.built_window(org) == 30

    @pytest.mark.parametrize(
        "chosen,expected", [(None, 90), (30, 30), (365, 365), (0, 0)]
    )
    def test_upload_window_has_no_ceiling(self, settings, org, chosen, expected):
        """No public claim rides on it, so an org may lengthen it, and 0 really
        does mean forever."""
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        org.retention_abandoned_upload_days = chosen
        assert retention.upload_window(org) == expected

    def test_a_rowless_org_falls_back_to_the_instance_defaults(self, settings):
        """Both windows also govern trees whose org row is gone."""
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 30, "abandoned_upload_days": 90
        }
        assert retention.built_window(None) == 30
        assert retention.upload_window(None) == 90


@pytest.fixture
def built(studio_tree):
    """Give a report built output on disk and a last_built_at to age."""
    from apps.reports.models import Report

    def _built(slug: str, *, days_ago: int | None, size: int = 2048):
        report, _ = Report.objects.get_or_create(studio=studio_tree, slug=slug)
        out = studio_tree.output_dir / slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("x" * size, encoding="utf-8")
        when = None if days_ago is None else timezone.now() - timedelta(days=days_ago)
        Report.objects.filter(pk=report.pk).update(last_built_at=when)
        report.refresh_from_db()
        return report

    return _built


class TestBuiltDataRetention:
    def test_output_past_the_window_goes_and_the_row_stays(self, settings, studio_tree, built):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        report = built("daily", days_ago=40)

        label, count = retention.purge_built_data()

        assert (label, count) == ("built report output", 1)
        assert not (studio_tree.output_dir / "daily").exists()
        # The row itself survives — refresh_from_db would raise if it had not.
        report.refresh_from_db()
        assert report.data_expired_at is not None
        assert report.last_built_at is not None  # what it was built from, still

    def test_output_inside_the_window_is_untouched(self, settings, studio_tree, built):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=5)
        assert retention.purge_built_data()[1] == 0
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()

    def test_a_null_clock_is_never_expired(self, settings, studio_tree, built):
        """Unknown age must not authorise deletion — a report built before the
        column existed, or never built at all, costs one window of storage
        rather than being guessed at."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 1}
        built("daily", days_ago=None)
        assert retention.purge_built_data()[1] == 0
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()

    def test_a_report_failing_nightly_still_expires(self, settings, studio_tree, built):
        """The clock reads the last SUCCESSFUL build, not the last activity.

        A report that has failed every night for six months has a fresh
        `_meta.json` and fresh Run rows, and would look freshly built to either
        of them — exempting exactly the reports most likely to be serving stale
        data. Only last_built_at, which only success writes, gets this right.
        """
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        report = built("daily", days_ago=200)
        # Everything else about this report says "yesterday".
        (studio_tree.output_dir / "daily" / "_meta.json").write_text(
            f'{{"last_run": "{timezone.now().isoformat()}", "last_status": "error"}}',
            encoding="utf-8",
        )
        for days in range(1, 6):
            _run(studio_tree, report, days_ago=days, status=Run.ERROR)

        assert retention.purge_built_data()[1] == 1
        assert not (studio_tree.output_dir / "daily").exists()

    def test_the_org_window_wins_over_the_instance_default(
        self, settings, studio_tree, built
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=20)
        assert retention.purge_built_data()[1] == 0
        studio_tree.org.retention_built_days = 7
        studio_tree.org.save(update_fields=["retention_built_days"])
        assert retention.purge_built_data()[1] == 1

    def test_a_disabled_cap_expires_nothing(self, settings, studio_tree, built):
        """The documented legal-hold escape."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 0}
        built("daily", days_ago=9000)
        assert retention.purge_built_data()[1] == 0
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()

    def test_dry_run_reports_without_removing(self, settings, studio_tree, built):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        report = built("daily", days_ago=40)
        assert retention.purge_built_data(dry_run=True)[1] == 1
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()
        report.refresh_from_db()
        assert report.data_expired_at is None

    def test_expiry_is_recorded_rather_than_the_build_erased(
        self, settings, studio_tree, built
    ):
        """A purged report has to stay tellable-apart from one that never built.

        Both dates survive because the expired-report page needs to say "built
        on X, deleted on Y", and "not built yet" is a different page from "we
        deleted this" — one nulled clock would collapse the two states.
        """
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        report = built("daily", days_ago=40)
        built_at = report.last_built_at

        retention.purge_built_data()

        report.refresh_from_db()
        assert report.last_built_at == built_at
        assert report.data_expired_at is not None
        assert report.data_expired_at > built_at

    def test_a_purged_report_is_not_swept_again(self, settings, studio_tree, built):
        """last_built_at survives the sweep, so without the data_expired_at
        exclusion this report stays a candidate and is re-walked every night."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=40)
        assert retention.purge_built_data()[1] == 1

        assert retention.purge_built_data()[1] == 0
        assert retention.purge_built_data(dry_run=True)[1] == 0
        assert retention.reclaimable_bytes() == 0

    def test_a_successful_build_clears_the_expiry(self, settings, studio_tree, built):
        """Rebuilding is the way back, and it takes one write on the path that
        already stamps the clock."""
        from apps.reports.models import Report

        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        report = built("daily", days_ago=40)
        retention.purge_built_data()

        # What apps.runner.executor does when a build succeeds.
        Report.objects.filter(pk=report.pk).update(
            last_built_at=timezone.now(), data_expired_at=None
        )

        report.refresh_from_db()
        assert report.data_expired_at is None
        assert retention.purge_built_data()[1] == 0

    def test_the_org_gets_an_audit_row_it_can_actually_see(
        self, settings, studio_tree, built
    ):
        """cleanup.py's instance-wide row carries org=None, so without this an
        org admin never learns their data was the data that went."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=40)
        retention.purge_built_data()
        row = AuditLog.objects.get(action="retention.built_data_purged")
        assert row.org_id == studio_tree.org.pk
        assert row.metadata["reports"] == 1
        assert row.metadata["freed_bytes"] > 0


@pytest.fixture
def upload(studio_tree):
    """Put a file where the portal puts uploads, aged to taste."""

    def _upload(name: str, *, days_ago: int, studio=None, org=None, body: str = "csv"):
        if org is not None:
            root = org.datasources_dir / "files"
        else:
            root = (studio or studio_tree).datasources_dir / "files"
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_text(body, encoding="utf-8")
        old = (timezone.now() - timedelta(days=days_ago)).timestamp()
        os.utime(path, (old, old))
        return path

    return _upload


class TestOrphanedUploads:
    def test_a_connected_file_is_never_swept_at_any_age(
        self, settings, studio_tree, upload
    ):
        """The invariant the whole design rests on. An upload is not built
        data: the clock starts when nothing references the file, so a file a
        live data source still points at does not age at all.
        """
        from apps.datasources.models import DataSource

        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        path = upload("budget.csv", days_ago=9000)
        DataSource.objects.create(
            studio=studio_tree, name="budget", type="file",
            config={"path": "data-sources/files/budget.csv", "upload": True},
        )

        assert retention.purge_orphaned_data()[1] == 0
        assert path.is_file()

    def test_an_unreferenced_file_past_the_window_goes(
        self, settings, studio_tree, upload
    ):
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        path = upload("stale.csv", days_ago=200)
        assert retention.purge_orphaned_data()[1] == 1
        assert not path.exists()

    def test_an_unreferenced_file_inside_the_window_stays(
        self, settings, studio_tree, upload
    ):
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        path = upload("recent.csv", days_ago=10)
        assert retention.purge_orphaned_data()[1] == 0
        assert path.is_file()

    def test_the_deletion_audit_row_dates_the_file_not_its_mtime(
        self, settings, studio_tree, upload
    ):
        """A file uploaded a year ago and disconnected yesterday has a year of
        mtime behind it and one day of abandonment. Only the second counts."""
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        path = upload("budget.csv", days_ago=400)
        row = AuditLog.objects.create(
            org=studio_tree.org, action="datasource.delete", category="access",
            metadata={"name": "budget", "scope": "studio"},
        )
        AuditLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )

        assert retention.purge_orphaned_data()[1] == 0
        assert path.is_file()

    def test_org_level_shared_uploads_are_swept_too(self, settings, org, upload):
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        path = upload("shared.csv", days_ago=200, org=org)
        assert retention.purge_orphaned_data()[1] == 1
        assert not path.exists()

    def test_a_zero_upload_window_keeps_forever(self, settings, studio_tree, upload):
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 0}
        path = upload("stale.csv", days_ago=9000)
        assert retention.purge_orphaned_data()[1] == 0
        assert path.is_file()

    def test_dry_run_reports_without_removing(self, settings, studio_tree, upload):
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        path = upload("stale.csv", days_ago=200)
        assert retention.purge_orphaned_data(dry_run=True)[1] == 1
        assert path.is_file()


class TestOrphanedTrees:
    """Deleting a studio deletes rows, not files. Nothing walking Report rows
    can see what is left, so the built cap would quietly stop applying to
    exactly the data whose owner is gone."""

    def test_a_deleted_studios_output_expires_on_the_abandoned_window(
        self, settings, studio_tree, built
    ):
        """And specifically NOT on the built window, which is off by default.

        Dating this tree by ``built_data_days`` would mean a deleted studio's
        bytes never went at all on a default install -- the exact leak this
        target exists to close. What makes them sweepable is that nothing owns
        them, not that they were once a build.
        """
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 0, "abandoned_upload_days": 90
        }
        built("daily", days_ago=100)
        output = studio_tree.output_dir
        _age_tree(output, days=100)
        studio_tree.delete()  # rows only; the files stay, as production does

        assert retention.purge_orphaned_data()[1] == 1
        assert not output.exists()

    def test_a_deleted_studios_output_survives_the_built_cap_being_off(
        self, settings, studio_tree, built
    ):
        """The default install still sweeps it: built_data_days=0 must not
        propagate into "keep abandoned bytes for ever"."""
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 0, "abandoned_upload_days": 90
        }
        built("daily", days_ago=30)
        output = studio_tree.output_dir
        _age_tree(output, days=30)
        studio_tree.delete()

        # Inside the abandoned window — still here, and not swept by the
        # (disabled) built cap either.
        assert retention.purge_orphaned_data()[1] == 0
        assert output.exists()

    def test_a_deleted_studios_output_inside_the_built_window_stays(
        self, settings, studio_tree, built
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=5)
        output = studio_tree.output_dir
        _age_tree(output, days=5)
        studio_tree.delete()
        assert retention.purge_orphaned_data()[1] == 0
        assert (output / "daily" / "index.html").is_file()

    def test_a_live_studios_output_is_never_treated_as_orphaned(
        self, settings, studio_tree, built
    ):
        """purge_built_data owns live reports; this sweep must not race it."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=9000)
        _age_tree(studio_tree.output_dir, days=9000)
        assert retention.purge_orphaned_data()[1] == 0
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()

    def test_the_studio_delete_audit_row_dates_the_tree(
        self, settings, studio_tree, built
    ):
        """Output written a year ago and orphaned yesterday has one day of
        orphanhood, not a year of it."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=400)
        output = studio_tree.output_dir
        _age_tree(output, days=400)
        AuditLog.objects.create(
            org=studio_tree.org, action="studio.delete", category="admin",
            metadata={"slug": studio_tree.slug},
        )
        studio_tree.delete()

        assert retention.purge_orphaned_data()[1] == 0
        assert (output / "daily" / "index.html").is_file()

    def test_a_rowless_organizations_tree_falls_back_to_the_instance_window(
        self, settings, studio_tree, built
    ):
        """Nothing is left to ask about the window, so the instance answers.
        The two slugs the sweep needs are still legible from the path."""
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        built("daily", days_ago=100)
        output = studio_tree.output_dir
        _age_tree(output, days=100)
        studio_tree.org.delete()

        assert retention.purge_orphaned_data()[1] == 1
        assert not output.exists()

    def test_the_cleanup_command_runs_both_data_targets(
        self, settings, studio_tree, built, upload
    ):
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 30, "abandoned_upload_days": 90
        }
        built("daily", days_ago=40)
        path = upload("stale.csv", days_ago=200)

        call_command("cleanup")

        state = OpsState.objects.get(key="cleanup")
        assert state.payload["removed"]["built report output"] == 1
        assert state.payload["removed"]["orphaned data"] == 1
        assert not (studio_tree.output_dir / "daily").exists()
        assert not path.exists()

    def test_a_deleted_studios_uploads_keep_the_upload_window(
        self, settings, studio_tree, upload
    ):
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 30, "abandoned_upload_days": 90
        }
        path = upload("budget.csv", days_ago=40)
        studio_tree.delete()
        # Past the built window, inside the upload one: uploads are not built
        # data even when their studio is gone.
        assert retention.purge_orphaned_data()[1] == 0
        assert path.is_file()


class TestReclaimableBytes:
    def test_it_counts_both_windows_and_changes_nothing(
        self, settings, studio_tree, built, upload
    ):
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 30, "abandoned_upload_days": 90
        }
        built("daily", days_ago=40, size=4096)
        path = upload("stale.csv", days_ago=200, body="0123456789")

        total = retention.reclaimable_bytes()

        assert total >= 4096 + 10
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()
        assert path.is_file()

    def test_nothing_expired_is_zero(self, settings, studio_tree, built):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=1)
        assert retention.reclaimable_bytes() == 0


class TestLastPurgeEvent:
    """The retention settings page's footer -- apps.orgs.views
    .retention_settings reads this to say when cleanup last touched THIS
    organization's data, distinct from cleanup.py's instance-wide OpsState
    row, which carries no org at all.
    """

    def test_none_when_nothing_has_ever_been_purged(self, org):
        assert retention.last_purge_event(org) is None

    def test_reads_the_built_purge_audit_row(self, settings, studio_tree, built):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=40, size=4096)
        retention.purge_built_data()

        event = retention.last_purge_event(studio_tree.org)

        assert event is not None
        assert event["freed_bytes"] >= 4096

    def test_reflects_the_most_recent_of_the_two_purge_kinds(
        self, settings, studio_tree, built, upload
    ):
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 30, "abandoned_upload_days": 90
        }
        built("daily", days_ago=40)
        retention.purge_built_data()
        AuditLog.objects.filter(action="retention.built_data_purged").update(
            created_at=timezone.now() - timedelta(days=1)
        )

        upload("stale.csv", days_ago=200, body="0123456789")
        retention.purge_orphaned_data()

        event = retention.last_purge_event(studio_tree.org)
        assert event["freed_bytes"] == 10  # the later (orphaned-data) row, not the built one

    def test_another_orgs_purge_is_invisible(self, settings, studio_tree, built, other_org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=40)
        retention.purge_built_data()
        assert retention.last_purge_event(other_org) is None
