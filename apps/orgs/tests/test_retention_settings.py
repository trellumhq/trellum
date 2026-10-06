"""Org settings -> Data retention (apps.orgs.views.retention_settings): the
two per-org data windows (internal planning ticket #110) made visible and editable, with the
built-data ceiling shown up front and the zero-trap kept out of the UI --
apps.core.tests.test_retention.TestWindowResolution covers the resolver
itself, this covers the page built on top of it.
"""
import os
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import retention
from apps.core.models import AuditLog
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


def _url(org):
    return f"/orgs/{org.slug}/settings/retention"


@pytest.fixture
def built(studio_tree):
    """A report with output on disk, aged to taste (mirrors the fixture of
    the same name in apps.core.tests.test_retention)."""

    def _built(slug: str, *, days_ago: int, size: int = 2048):
        report, _ = Report.objects.get_or_create(studio=studio_tree, slug=slug)
        out = studio_tree.output_dir / slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("x" * size, encoding="utf-8")
        when = timezone.now() - timedelta(days=days_ago)
        Report.objects.filter(pk=report.pk).update(last_built_at=when, name=slug.title())
        report.refresh_from_db()
        return report

    return _built


@pytest.fixture
def upload(studio_tree):
    """An uploaded file under the studio's data-sources dir, aged to taste."""

    def _upload(name: str, *, days_ago: int, body: str = "csv"):
        root = studio_tree.datasources_dir / "files"
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_text(body, encoding="utf-8")
        old = (timezone.now() - timedelta(days=days_ago)).timestamp()
        os.utime(path, (old, old))
        return path

    return _upload


class TestPermissionGate:
    def test_org_admin_can_view_the_page(self, login, org_admin, org):
        resp = login(org_admin).get(_url(org))
        assert resp.status_code == 200
        assert b"Data retention" in resp.content
        assert (
            b'href="/orgs/' + org.slug.encode()
            + b'/settings/retention" aria-current="page"'
        ) in resp.content

    def test_non_admin_member_cannot_view_the_page(self, login, member, org):
        assert login(member).get(_url(org)).status_code == 403

    def test_non_admin_member_cannot_post(self, login, member, org):
        resp = login(member).post(_url(org), {"retention_built_days": "5"})
        assert resp.status_code == 403
        org.refresh_from_db()
        assert org.retention_built_days is None

    def test_anonymous_is_redirected_to_login(self, client, org):
        resp = client.get(_url(org))
        assert resp.status_code == 302
        assert "/login" in resp["Location"]


class TestInheritedVsSetDisplay:
    def test_never_chosen_shows_as_inherited(self, settings, login, org_admin, org):
        settings.RETENTION = {
            **settings.RETENTION, "built_data_days": 30, "abandoned_upload_days": 90,
        }
        resp = login(org_admin).get(_url(org))
        assert resp.content.count(b"using the instance default") == 2
        assert b"<strong>30 day" in resp.content
        assert b"<strong>90 day" in resp.content

    def test_an_explicit_built_choice_shows_as_set(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        org.retention_built_days = 7
        org.save(update_fields=["retention_built_days"])
        resp = login(org_admin).get(_url(org))
        assert b"set for this organization" in resp.content
        assert b"<strong>7 day" in resp.content

    def test_an_explicit_upload_choice_shows_as_set(self, login, org_admin, org):
        org.retention_abandoned_upload_days = 400
        org.save(update_fields=["retention_abandoned_upload_days"])
        resp = login(org_admin).get(_url(org))
        assert b"set for this organization" in resp.content
        assert b"<strong>400 day" in resp.content


class TestNoExpiryIsTheDefault:
    """``built_data_days`` ships at 0: built output is kept until someone
    deletes the report. The panel must say that in words, because the number
    it would otherwise render is "0 days" — which reads as "deleted
    immediately", the opposite of what is happening."""

    def test_the_default_install_says_data_is_kept(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 0}
        resp = login(org_admin).get(_url(org))
        assert b"kept until the report is deleted" in resp.content
        assert b"<strong>0 day" not in resp.content

    def test_an_org_can_still_opt_in_to_a_window(self, settings, login, org_admin, org):
        """Turning the cap off instance-wide must not take the control away
        from an organization with a retention obligation of its own."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 0}
        login(org_admin).post(_url(org), {"retention_built_days": "45"}, follow=True)
        org.refresh_from_db()
        assert org.retention_built_days == 45
        assert retention.built_window(org) == 45


class TestCeiling:
    def test_the_ceiling_is_shown_before_any_submission(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 21}
        resp = login(org_admin).get(_url(org))
        assert b"at most <strong>21 days</strong>" in resp.content

    def test_an_over_ceiling_submission_is_rejected_and_names_the_ceiling(
        self, settings, login, org_admin, org
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        resp = login(org_admin).post(
            _url(org), {"retention_built_days": "90"}, follow=True
        )
        assert resp.status_code == 200
        assert b"30 days" in resp.content  # the ceiling, named in the message
        org.refresh_from_db()
        assert org.retention_built_days is None  # rejected, not saved

    def test_a_value_at_the_ceiling_is_accepted(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        login(org_admin).post(_url(org), {"retention_built_days": "30"})
        org.refresh_from_db()
        assert org.retention_built_days == 30

    def test_the_upload_window_has_no_ceiling_to_reject(self, login, org_admin, org):
        login(org_admin).post(_url(org), {"retention_abandoned_upload_days": "9000"})
        org.refresh_from_db()
        assert org.retention_abandoned_upload_days == 9000


class TestZeroTrap:
    def test_zero_is_normalized_to_inherit_while_a_ceiling_is_on(
        self, settings, login, org_admin, org
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        org.retention_built_days = 10
        org.save(update_fields=["retention_built_days"])

        resp = login(org_admin).post(
            _url(org), {"retention_built_days": "0"}, follow=True
        )

        org.refresh_from_db()
        assert org.retention_built_days is None  # not stored as a literal 0
        assert b"not" in resp.content and b"keep forever" in resp.content

    def test_zero_really_means_forever_once_the_ceiling_is_off(
        self, settings, login, org_admin, org
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 0}
        login(org_admin).post(_url(org), {"retention_built_days": "0"})
        org.refresh_from_db()
        assert org.retention_built_days == 0

    def test_zero_on_the_upload_window_is_stored_as_typed(self, login, org_admin, org):
        """No ceiling on uploads, so 0 there is an honest "keep forever" --
        it must never be silently rewritten the way the built window's is."""
        login(org_admin).post(_url(org), {"retention_abandoned_upload_days": "0"})
        org.refresh_from_db()
        assert org.retention_abandoned_upload_days == 0

    def test_the_display_never_reads_as_forever_while_a_ceiling_is_on(
        self, settings, login, org_admin, org
    ):
        """A stored literal 0 (however it got there) resolves and displays
        exactly like "never chosen" -- never as "kept forever"."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        org.retention_built_days = 0
        org.save(update_fields=["retention_built_days"])
        resp = login(org_admin).get(_url(org))
        assert b"<strong>30 day" in resp.content
        assert b"using the instance default" in resp.content
        assert b"instance has no cap" not in resp.content


class TestExpiringSoon:
    def test_a_report_close_to_its_window_is_listed(
        self, settings, login, org_admin, org, studio_tree, built
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=25)  # a few days left, comfortably inside 7

        resp = login(org_admin).get(_url(org))

        assert b"Daily" in resp.content
        assert b"Nothing is expiring in the next 7 days." not in resp.content

    def test_a_report_far_from_its_window_is_not_listed(
        self, settings, login, org_admin, org, studio_tree, built
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=1)

        resp = login(org_admin).get(_url(org))
        assert b"Nothing is expiring in the next 7 days." in resp.content

    def test_an_already_expired_report_is_not_listed_as_soon(
        self, settings, login, org_admin, org, studio_tree, built
    ):
        """Expiring-soon is a warning before the fact, not a duplicate of the
        preview -- something already past its window belongs there instead."""
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=40)

        resp = login(org_admin).get(_url(org))
        assert b"Nothing is expiring in the next 7 days." in resp.content

    def test_an_abandoned_upload_close_to_its_window_is_listed(
        self, settings, login, org_admin, org, studio_tree, upload
    ):
        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        upload("stale.csv", days_ago=87)  # a few days left, comfortably inside 7

        resp = login(org_admin).get(_url(org))
        assert b"stale.csv" in resp.content

    def test_a_connected_uploads_never_counts_as_expiring(
        self, settings, login, org_admin, org, studio_tree, upload
    ):
        from apps.datasources.models import DataSource

        settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
        upload("budget.csv", days_ago=89)
        DataSource.objects.create(
            studio=studio_tree, name="budget", type="file",
            config={"path": "data-sources/files/budget.csv", "upload": True},
        )

        resp = login(org_admin).get(_url(org))
        assert b"budget.csv" not in resp.content
        assert b"Nothing is expiring in the next 7 days." in resp.content

    def test_no_data_at_all_shows_the_plain_empty_state(self, login, org_admin, org):
        resp = login(org_admin).get(_url(org))
        assert b"Nothing is expiring in the next 7 days." in resp.content


class TestSaveAndAudit:
    def test_saving_updates_the_resolved_windows(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        resp = login(org_admin).post(
            _url(org),
            {"retention_built_days": "10", "retention_abandoned_upload_days": "200"},
        )
        assert resp.status_code == 302
        org.refresh_from_db()
        assert org.retention_built_days == 10
        assert org.retention_abandoned_upload_days == 200

    def test_a_non_numeric_value_is_rejected_without_saving(self, login, org_admin, org):
        resp = login(org_admin).post(_url(org), {"retention_built_days": "soon"})
        assert resp.status_code == 302
        org.refresh_from_db()
        assert org.retention_built_days is None

    def test_is_audited(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        login(org_admin).post(_url(org), {"retention_built_days": "10"})
        row = AuditLog.objects.get(action="org.retention_set")
        assert row.org_id == org.pk
        assert row.metadata["retention_built_days"] == 10


class TestPreview:
    def test_nothing_past_its_window_says_so(self, settings, login, org_admin, org):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        resp = login(org_admin).get(_url(org) + "?preview=1")
        assert b"would remove nothing" in resp.content

    def test_an_expired_report_shows_up_and_nothing_is_removed(
        self, settings, login, org_admin, org, studio_tree, built
    ):
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 30}
        built("daily", days_ago=40)

        resp = login(org_admin).get(_url(org) + "?preview=1")

        assert b"1 built report output" in resp.content
        assert (studio_tree.output_dir / "daily" / "index.html").is_file()  # untouched
        report = Report.objects.get(studio=studio_tree, slug="daily")
        assert report.data_expired_at is None  # the preview changed nothing


class TestDormancyPanel:
    """The page is also where an org admin finds out that a colleague's
    account is about to be closed -- an instance policy this form cannot
    edit, shown here because this is the page that answers "what is about to
    be deleted, and when".
    """

    @pytest.fixture
    def dormant_member(self, org, member):
        from django.contrib.auth import get_user_model

        get_user_model().objects.filter(pk=member.pk).update(
            last_login=timezone.now() - timedelta(days=800)
        )
        return member

    def test_the_section_is_absent_while_dormancy_is_off(
        self, login, org_admin, org, dormant_member
    ):
        """The shipped default: no section, and nobody named."""
        resp = login(org_admin).get(_url(org))
        assert b"about to lose their account" not in resp.content
        assert dormant_member.email.encode() not in resp.content

    def test_a_dormant_member_is_named_with_the_stage_and_the_fix(
        self, settings, login, org_admin, org, dormant_member
    ):
        settings.RETENTION = {
            **settings.RETENTION, "dormant_days": 730,
            "dormant_disable_days": 30, "dormant_erase_days": 60,
        }

        resp = login(org_admin).get(_url(org))

        assert dormant_member.email.encode() in resp.content
        assert b"not warned yet" in resp.content
        assert b"one sign-in cancels it" in resp.content
