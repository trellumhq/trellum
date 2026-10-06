"""Report view analytics (internal planning#4): who actually looks at which
report, surfaced where authors live.

Covers record_view()'s dedup/race behavior, the two capture points
(authenticated entry HTML and the public share entry), the dashboard's
per-report view stats (and that computing them costs a fixed number of
queries, not one per report), the Activity panel's API, and retention of
the raw event log.
"""
import json
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.core.models import AuditLog
from apps.reports.models import (
    OrgSharePolicy,
    ReportViewDaily,
    ReportViewEvent,
    ShareLink,
    record_view,
)
from apps.reports.scan import build_registry_payload, invalidate_registry_cache

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
def admin_user(make_user, org, studio_tree, grant_studio):
    user = make_user("admin@demo.example", org=org)
    grant_studio(user, studio_tree, roles.ADMIN)
    return user


@pytest.fixture
def second_viewer(make_user, org, studio_tree, grant_studio):
    """A second distinct authenticated viewer -- for unique-viewer math
    (record_view dedups per user, so one user viewing twice must not be
    mistaken for two people)."""
    user = make_user("second@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


@pytest.fixture
def built_report(studio_tree, report_row):
    out = studio_tree.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text("<html><head></head><body>hi</body></html>", encoding="utf-8")
    (out / "data.json").write_text('{"a": 1}', encoding="utf-8")
    return out


@pytest.fixture
def sharing_on(org):
    return OrgSharePolicy.objects.create(org=org, share_links_enabled=True)


@pytest.fixture
def make_link(report_row):
    def _make(**kwargs):
        link = ShareLink(report=report_row, **kwargs)
        link.save()
        return link

    return _make


def _bucket_now():
    return timezone.now().replace(minute=0, second=0, microsecond=0)


class TestRecordView:
    def test_dedups_within_the_hour_same_user(self, report_row, developer):
        record_view(report_row, user=developer)
        record_view(report_row, user=developer)
        assert ReportViewEvent.objects.filter(report=report_row, user=developer).count() == 1

    def test_dedups_within_the_hour_same_share_link(self, report_row, make_link):
        link = make_link()
        record_view(report_row, share_link=link)
        record_view(report_row, share_link=link)
        assert ReportViewEvent.objects.filter(report=report_row, share_link=link).count() == 1

    def test_separate_hours_count_separately(self, report_row, developer):
        record_view(report_row, user=developer)
        # Move the existing event's bucket back two hours, then record again
        # "now" -- two distinct hours must both survive as separate rows.
        ReportViewEvent.objects.filter(report=report_row, user=developer).update(
            bucket=_bucket_now() - timedelta(hours=2)
        )
        record_view(report_row, user=developer)
        assert ReportViewEvent.objects.filter(report=report_row, user=developer).count() == 2

    def test_separate_users_count_separately(self, report_row, developer, viewer):
        record_view(report_row, user=developer)
        record_view(report_row, user=viewer)
        assert ReportViewEvent.objects.filter(report=report_row).count() == 2

    def test_separate_links_count_separately(self, report_row, make_link):
        link1 = make_link()
        link2 = make_link()
        record_view(report_row, share_link=link1)
        record_view(report_row, share_link=link2)
        assert ReportViewEvent.objects.filter(report=report_row).count() == 2

    def test_daily_row_increments_only_on_new_events(self, report_row, developer):
        record_view(report_row, user=developer)
        record_view(report_row, user=developer)  # deduped -- same hour, no new event
        daily = ReportViewDaily.objects.get(report=report_row, date=timezone.now().date())
        assert daily.views == 1

    def test_daily_row_increments_across_distinct_events(self, report_row, developer, viewer):
        record_view(report_row, user=developer)
        record_view(report_row, user=viewer)
        daily = ReportViewDaily.objects.get(report=report_row, date=timezone.now().date())
        assert daily.views == 2

    def test_pre_existing_event_never_raises_and_is_not_double_counted(self, report_row, developer):
        """Simulates the race the unique constraint exists for: another
        request already won and inserted this exact (report, user, bucket)
        row. record_view must treat that as "already counted" -- no raise,
        no daily bump."""
        ReportViewEvent.objects.create(report=report_row, user=developer, bucket=_bucket_now())
        record_view(report_row, user=developer)  # must not raise
        assert ReportViewEvent.objects.filter(report=report_row, user=developer).count() == 1
        assert not ReportViewDaily.objects.filter(report=report_row).exists()

    def test_integrity_error_from_the_insert_itself_never_raises(self, report_row, developer, monkeypatch):
        """A more direct race than the pre-existing-row case above: the
        manager's get_or_create raises IntegrityError outright (as it would
        if two concurrent requests both missed the SELECT and both tried to
        INSERT). record_view's own except IntegrityError branch must catch
        it -- this must not propagate into the serving path."""

        class _BoomManager:
            def get_or_create(self, **kwargs):  # noqa: ARG002
                raise IntegrityError("simulated race")

        monkeypatch.setattr(ReportViewEvent, "objects", _BoomManager())
        record_view(report_row, user=developer)  # must not raise

    def test_never_raises_on_unexpected_error(self, report_row, developer, monkeypatch):
        class _BoomManager:
            def get_or_create(self, **kwargs):  # noqa: ARG002
                raise RuntimeError("something else broke")

        monkeypatch.setattr(ReportViewEvent, "objects", _BoomManager())
        record_view(report_row, user=developer)  # must not raise


class TestCapture:
    def test_authenticated_entry_get_records_one_event_with_user(
        self, login, developer, prefix, report_row, built_report
    ):
        login(developer).get(f"{prefix}/r/{report_row.slug}/index.html")
        events = list(ReportViewEvent.objects.filter(report=report_row))
        assert len(events) == 1
        assert events[0].user_id == developer.pk
        assert events[0].share_link_id is None

    def test_second_get_same_hour_still_one_event(self, login, developer, prefix, report_row, built_report):
        client = login(developer)
        client.get(f"{prefix}/r/{report_row.slug}/index.html")
        client.get(f"{prefix}/r/{report_row.slug}/index.html")
        assert ReportViewEvent.objects.filter(report=report_row).count() == 1

    def test_share_entry_get_records_event_with_share_link_and_no_user(
        self, client, sharing_on, report_row, built_report, make_link
    ):
        link = make_link()
        client.get(f"/share/{link.token}/")
        events = list(ReportViewEvent.objects.filter(report=report_row))
        assert len(events) == 1
        assert events[0].share_link_id == link.pk
        assert events[0].user_id is None

    def test_non_entry_asset_gets_record_no_event(self, login, developer, prefix, report_row, built_report):
        client = login(developer)
        client.get(f"{prefix}/r/{report_row.slug}/data.json")
        client.get(f"{prefix}/r/{report_row.slug}/data.json")
        assert ReportViewEvent.objects.filter(report=report_row).count() == 0

    def test_share_asset_gets_record_no_event(self, client, sharing_on, report_row, built_report, make_link):
        link = make_link()
        client.get(f"/share/{link.token}/")  # the one entry view
        client.get(f"/share/{link.token}/data.json")
        client.get(f"/share/{link.token}/data.json")
        assert ReportViewEvent.objects.filter(report=report_row).count() == 1


class TestAuditMirror:
    """report.view: record_view()'s audit mirror, fired only when the hourly
    dedup actually creates a row -- so the audit trail's volume is bounded
    the same way ReportViewEvent's is, and a bug in the mirror can never
    take report serving down with it.
    """

    def test_authenticated_view_writes_one_audit_row(self, login, developer, prefix, report_row, built_report):
        login(developer).get(f"{prefix}/r/{report_row.slug}/index.html")
        row = AuditLog.objects.get(action="report.view")
        assert row.actor_id == developer.pk
        assert row.target_id == str(report_row.pk)
        assert row.metadata["via"] == "portal"
        assert row.category == "access"
        assert row.outcome == "success"

    def test_dedup_bounds_the_audit_row_too(self, login, developer, prefix, report_row, built_report):
        client = login(developer)
        client.get(f"{prefix}/r/{report_row.slug}/index.html")
        client.get(f"{prefix}/r/{report_row.slug}/index.html")
        assert AuditLog.objects.filter(action="report.view").count() == 1

    def test_share_view_writes_an_anonymous_audit_row(self, client, sharing_on, report_row, built_report, make_link):
        link = make_link()
        client.get(f"/share/{link.token}/")
        row = AuditLog.objects.get(action="report.view")
        assert row.actor_id is None
        assert row.metadata["via"] == "share"
        assert row.metadata["token_suffix"] == link.token[-6:]
        assert link.token not in json.dumps(row.metadata)
        assert row.org_id == report_row.studio.org_id

    def test_a_non_entry_asset_writes_no_audit_row(self, login, developer, prefix, report_row, built_report):
        login(developer).get(f"{prefix}/r/{report_row.slug}/data.json")
        assert not AuditLog.objects.filter(action="report.view").exists()

    def test_record_view_never_raises_when_the_audit_mirror_blows_up(
        self, report_row, developer, rf, monkeypatch
    ):
        """The try/except record_view already has for its own dedup logic
        covers the audit mirror too -- a bug here must never cost a report
        page its response."""
        import apps.core.audit as audit_module

        def boom(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError("audit blew up")

        monkeypatch.setattr(audit_module, "audit", boom)
        request = rf.get("/anything")
        request.user = developer
        record_view(report_row, user=developer, request=request)  # must not raise
        assert ReportViewEvent.objects.filter(report=report_row, user=developer).exists()

    def test_no_request_means_no_audit_mirror_but_dedup_still_works(self, report_row, developer):
        """record_view() stays callable from anywhere with no request in
        hand (its unit tests above, for instance) -- it just skips the
        mirror rather than requiring one."""
        record_view(report_row, user=developer)  # request=None by default
        assert ReportViewEvent.objects.filter(report=report_row, user=developer).exists()
        assert not AuditLog.objects.filter(action="report.view").exists()


class TestExportDownloadAudit:
    """report.export_download / share_link.export_download: the prebuilt
    export files (.csv/.xlsx/.parquet/.zip) are what actually carries data
    out of the portal server-side; the framework's in-browser PNG/PDF
    rendering never reaches this server and is not observable."""

    @pytest.fixture
    def built_report_with_export(self, studio_tree, report_row):
        out = studio_tree.output_dir / report_row.slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html><head></head><body>hi</body></html>", encoding="utf-8")
        (out / "export.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        return out

    def test_report_asset_export_download_is_audited(
        self, login, developer, prefix, report_row, built_report_with_export
    ):
        resp = login(developer).get(f"{prefix}/r/{report_row.slug}/export.csv")
        assert resp.status_code == 200
        row = AuditLog.objects.get(action="report.export_download")
        assert row.actor_id == developer.pk
        assert row.target_id == str(report_row.pk)
        assert row.metadata["asset"] == "export.csv"
        assert row.metadata["ext"] == ".csv"
        assert row.category == "access"

    def test_non_export_asset_is_not_audited_as_a_download(
        self, login, developer, prefix, report_row, built_report_with_export
    ):
        login(developer).get(f"{prefix}/r/{report_row.slug}/index.html")
        assert not AuditLog.objects.filter(action="report.export_download").exists()

    def test_share_link_export_download_is_audited_when_allowed(
        self, client, sharing_on, report_row, built_report_with_export, make_link
    ):
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/export.csv")
        assert resp.status_code == 200
        row = AuditLog.objects.get(action="share_link.export_download")
        assert row.actor_id is None
        assert row.metadata["token_suffix"] == link.token[-6:]
        assert row.metadata["asset"] == "export.csv"
        assert row.org_id == report_row.studio.org_id

    def test_share_link_export_download_refused_is_not_audited(
        self, client, sharing_on, report_row, built_report_with_export, make_link
    ):
        link = make_link(allow_export=False)
        resp = client.get(f"/share/{link.token}/export.csv")
        assert resp.status_code == 403
        assert not AuditLog.objects.filter(action="share_link.export_download").exists()


class TestDashboardMetaAndBadge:
    def test_zero_views_and_recent_success_is_stale(self, studio_tree, report_row, write_meta):
        write_meta(report_row.slug, last_status="success", last_run=timezone.now().isoformat())
        invalidate_registry_cache(studio_tree)
        row = build_registry_payload(studio_tree)["reports"][0]
        assert row["views_30d"] == 0
        assert row["last_viewed"] is None
        assert row["stale"] is True

    def test_a_view_clears_the_stale_badge(self, studio_tree, report_row, developer, write_meta):
        write_meta(report_row.slug, last_status="success", last_run=timezone.now().isoformat())
        record_view(report_row, user=developer)
        invalidate_registry_cache(studio_tree)
        row = build_registry_payload(studio_tree)["reports"][0]
        assert row["views_30d"] == 1
        assert row["last_viewed"] is not None
        assert row["stale"] is False

    def test_old_build_is_never_stale_even_with_zero_views(self, studio_tree, report_row, write_meta):
        old = (timezone.now() - timedelta(days=30)).isoformat()
        write_meta(report_row.slug, last_status="success", last_run=old)
        invalidate_registry_cache(studio_tree)
        row = build_registry_payload(studio_tree)["reports"][0]
        assert row["stale"] is False

    def test_failed_build_is_never_stale(self, studio_tree, report_row, write_meta):
        write_meta(report_row.slug, last_status="error", last_run=timezone.now().isoformat())
        invalidate_registry_cache(studio_tree)
        row = build_registry_payload(studio_tree)["reports"][0]
        assert row["stale"] is False

    def test_views_older_than_30d_do_not_count(self, studio_tree, report_row):
        old_day = (timezone.now() - timedelta(days=45)).date()
        ReportViewDaily.objects.create(report=report_row, date=old_day, views=7)
        invalidate_registry_cache(studio_tree)
        row = build_registry_payload(studio_tree)["reports"][0]
        assert row["views_30d"] == 0

    def test_meta_line_and_stale_badge_markup_in_portal_js(self):
        """cardHtml() renders the meta line every card gets, and the
        info-toned "unseen" badge only when r.stale is set -- see
        static/ui.css's .ui-badge.info for the class this must match. "stale"
        is reserved for the metrics-catalog definition-drift badge."""
        from django.conf import settings

        js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
        assert "card-views" in js
        assert "not viewed yet" in js
        assert 'ui-badge info' in js
        assert "unseen</span>" in js
        assert "no one has viewed it yet" in js

    def test_registry_query_count_does_not_scale_with_report_count(
        self, login, developer, prefix, studio_tree, write_report
    ):
        """No N+1: build_registry_payload's view-stats stitching is two
        aggregate queries for the WHOLE studio (see apps.reports.scan.
        _view_stats_for), not one pair per report -- so the query count for
        this endpoint must be identical whether the studio has one report or
        several."""
        from apps.reports.scan import sync_studio_registry

        write_report("solo")
        sync_studio_registry(studio_tree)
        invalidate_registry_cache(studio_tree)
        client = login(developer)
        with CaptureQueriesContext(connection) as few:
            resp = client.get(f"{prefix}/api/registry")
        assert resp.status_code == 200

        for i in range(8):
            write_report(f"many-{i}")
        sync_studio_registry(studio_tree)
        invalidate_registry_cache(studio_tree)
        with CaptureQueriesContext(connection) as many:
            resp = client.get(f"{prefix}/api/registry")
        assert resp.status_code == 200
        assert len(resp.json()["reports"]) == 9

        assert len(many.captured_queries) == len(few.captured_queries), (
            f"query count grew with report count: {len(few.captured_queries)} "
            f"-> {len(many.captured_queries)}"
        )


class TestViewsApi:
    def test_developer_gets_summary_and_recent_with_share_hits_marked(
        self, login, developer, prefix, report_row, make_link
    ):
        link = make_link()
        record_view(report_row, user=developer)
        record_view(report_row, share_link=link)

        resp = login(developer).get(f"{prefix}/api/reports/{report_row.slug}/views")
        assert resp.status_code == 200
        body = resp.json()

        assert body["summary"]["views_30d"] == 2
        assert body["summary"]["last_viewed"] is not None
        assert len(body["recent"]) == 2

        share_rows = [r for r in body["recent"] if r["via_share"]]
        user_rows = [r for r in body["recent"] if not r["via_share"]]
        assert len(share_rows) == 1 and len(user_rows) == 1
        assert share_rows[0]["who"] is None  # never names which link
        assert user_rows[0]["who"] in (developer.display_name, developer.email)

        # The token must never leak into this payload.
        assert link.token not in json.dumps(body)

    def test_recent_is_capped_at_20_newest_first(self, login, developer, prefix, report_row):
        for i in range(25):
            bucket = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=i)
            ReportViewEvent.objects.create(report=report_row, user=developer, bucket=bucket)
        resp = login(developer).get(f"{prefix}/api/reports/{report_row.slug}/views")
        body = resp.json()
        assert len(body["recent"]) == 20

    def test_viewer_forbidden(self, login, viewer, prefix, report_row):
        resp = login(viewer).get(f"{prefix}/api/reports/{report_row.slug}/views")
        assert resp.status_code == 403

    def test_anonymous_requires_auth(self, client, prefix, report_row):
        resp = client.get(f"{prefix}/api/reports/{report_row.slug}/views")
        assert resp.status_code == 401


class TestRetention:
    def test_purge_removes_old_events_keeps_new_and_daily_untouched(self, settings, report_row, developer):
        from apps.core import retention

        settings.RETENTION = {**settings.RETENTION, "report_view_event_days": 90}

        old_event = ReportViewEvent.objects.create(
            report=report_row, user=developer, bucket=_bucket_now() - timedelta(days=100)
        )
        ReportViewEvent.objects.filter(pk=old_event.pk).update(
            created_at=timezone.now() - timedelta(days=100)
        )
        new_event = ReportViewEvent.objects.create(report=report_row, user=developer, bucket=_bucket_now())
        old_daily = ReportViewDaily.objects.create(
            report=report_row, date=(timezone.now() - timedelta(days=100)).date(), views=5
        )

        label, count = retention.purge_report_view_events()
        assert (label, count) == ("report view events", 1)
        assert not ReportViewEvent.objects.filter(pk=old_event.pk).exists()
        assert ReportViewEvent.objects.filter(pk=new_event.pk).exists()
        # Daily rollups are kept forever -- only the raw event log is purged.
        assert ReportViewDaily.objects.filter(pk=old_daily.pk).exists()

    def test_zero_means_keep_forever(self, settings, report_row, developer):
        from apps.core import retention

        settings.RETENTION = {**settings.RETENTION, "report_view_event_days": 0}
        old_event = ReportViewEvent.objects.create(
            report=report_row, user=developer, bucket=_bucket_now() - timedelta(days=5000)
        )
        ReportViewEvent.objects.filter(pk=old_event.pk).update(
            created_at=timezone.now() - timedelta(days=5000)
        )
        assert retention.purge_report_view_events()[1] == 0
        assert ReportViewEvent.objects.filter(pk=old_event.pk).exists()

    def test_registered_in_cleanup_targets(self):
        from apps.core import retention

        assert retention.purge_report_view_events in retention.TARGETS


class TestAnalyticsPage:
    """The studio-wide /analytics table (internal planning#4 follow-up):
    developer/admin-gated, one row per report (including reports with no
    views at all), computed with a handful of aggregate queries rather than
    one per report, and windowed by ``?days=``."""

    def test_developer_sees_correct_per_report_numbers(
        self, login, developer, second_viewer, prefix, studio_tree, report_row,
        write_report, make_link,
    ):
        from apps.reports.models import Report
        from apps.reports.scan import sync_studio_registry

        # A second report that has never been viewed -- must still appear
        # as its own row (the "left join", not an inner one).
        write_report("zero-views")
        sync_studio_registry(studio_tree)
        zero_report = Report.objects.get(studio=studio_tree, slug="zero-views")

        link = make_link()
        record_view(report_row, user=developer)
        record_view(report_row, user=second_viewer)
        record_view(report_row, share_link=link)

        resp = login(developer).get(f"{prefix}/analytics")
        assert resp.status_code == 200
        rows_by_slug = {row["report"].slug: row for row in resp.context["rows"]}

        viewed = rows_by_slug[report_row.slug]
        assert viewed["views"] == 3
        # 2 distinct users + 1 distinct share link, counted separately then
        # summed for the one "Unique viewers" column.
        assert viewed["unique_users"] == 2
        assert viewed["unique_links"] == 1
        assert viewed["unique_total"] == 3
        assert viewed["share_views"] == 1
        assert viewed["last_viewed"] is not None

        untouched = rows_by_slug[zero_report.slug]
        assert untouched["views"] == 0
        assert untouched["unique_total"] == 0
        assert untouched["share_views"] == 0
        assert untouched["last_viewed"] is None

        html = resp.content.decode()
        assert report_row.name in html
        assert zero_report.name in html
        assert (
            f'{prefix}/r/{report_row.slug}/?display=console' in html
        )
        assert "No views recorded yet" not in html  # real rows present -- not the empty state

    def test_admin_also_allowed(self, login, admin_user, prefix, report_row):
        assert login(admin_user).get(f"{prefix}/analytics").status_code == 200

    def test_stale_flag_reuses_the_dashboard_convention(self, login, developer, prefix, studio_tree, report_row, write_meta):
        write_meta(report_row.slug, last_status="success", last_run=timezone.now().isoformat())
        resp = login(developer).get(f"{prefix}/analytics")
        row = resp.context["rows"][0]
        assert row["stale"] is True
        # Rendered as the info-toned "unseen" badge (relabelled from "stale",
        # which is now reserved for the metrics-catalog definition-drift badge).
        body = resp.content.decode()
        assert "ui-badge info" in body and "unseen" in body

    def test_empty_studio_shows_the_empty_state(self, login, developer, prefix, studio_tree):
        # No report_row fixture pulled in here -- present_in_scan reports:
        # zero.
        resp = login(developer).get(f"{prefix}/analytics")
        assert resp.status_code == 200
        assert resp.context["rows"] == []
        assert "No views recorded yet" in resp.content.decode()

    def test_days_param_narrows_the_window(self, login, developer, prefix, report_row):
        ReportViewDaily.objects.create(
            report=report_row, date=timezone.now().date() - timedelta(days=20), views=5
        )
        ReportViewDaily.objects.create(report=report_row, date=timezone.now().date(), views=2)

        resp7 = login(developer).get(f"{prefix}/analytics?days=7")
        assert resp7.context["rows"][0]["views"] == 2

        resp30 = login(developer).get(f"{prefix}/analytics?days=30")
        assert resp30.context["rows"][0]["views"] == 7

    def test_days_clamped_to_90(self, login, developer, prefix, report_row):
        resp = login(developer).get(f"{prefix}/analytics?days=500")
        assert resp.status_code == 200
        assert resp.context["days"] == 90

    def test_days_defaults_to_30(self, login, developer, prefix, report_row):
        resp = login(developer).get(f"{prefix}/analytics")
        assert resp.context["days"] == 30

    def test_viewer_forbidden(self, login, viewer, prefix, report_row):
        assert login(viewer).get(f"{prefix}/analytics").status_code == 403

    def test_anonymous_redirected_to_login(self, client, prefix, report_row):
        resp = client.get(f"{prefix}/analytics")
        assert resp.status_code == 302
        assert "/login" in resp["Location"]

    def test_no_n_plus_one_across_report_count(self, login, developer, prefix, studio_tree, write_report):
        """The four aggregate queries the view runs must not scale with the
        number of reports -- same guard as the dashboard's own registry
        payload (test_dashboard_meta_and_badge's equivalent test)."""
        from apps.reports.scan import sync_studio_registry

        write_report("solo")
        sync_studio_registry(studio_tree)
        client = login(developer)
        # Warm-up, uncaptured: the shell context processor
        # (apps.core.context_processors.shell) writes session["active_org"]
        # on the first request that renders _shell.html and skips the write
        # on every later one -- a one-time side effect of this test reusing
        # one client/session across both captures, unrelated to report
        # count. Settling it here first keeps the comparison apples-to-apples.
        client.get(f"{prefix}/analytics")
        with CaptureQueriesContext(connection) as few:
            resp = client.get(f"{prefix}/analytics")
        assert resp.status_code == 200

        for i in range(8):
            write_report(f"many-{i}")
        sync_studio_registry(studio_tree)
        with CaptureQueriesContext(connection) as many:
            resp = client.get(f"{prefix}/analytics")
        assert resp.status_code == 200
        assert len(resp.context["rows"]) == 9

        assert len(many.captured_queries) == len(few.captured_queries), (
            f"query count grew with report count: {len(few.captured_queries)} "
            f"-> {len(many.captured_queries)}"
        )


class TestDashboardAnalyticsLink:
    """The Analytics entry point is a tab in the studio tab bar
    (templates/_studio_nav.html), gated server-side on the same
    developer/admin role the view itself requires — portal.js no longer
    mounts its own client-side link (or its own copy of the gate)."""

    def test_portal_js_no_longer_mounts_its_own_link(self):
        from django.conf import settings

        js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
        assert "summary-analytics-link" not in js
        assert "canSeeAnalytics" not in js

    def test_developer_and_admin_see_the_tab(
        self, login, developer, admin_user, org, studio_tree, report_row, client
    ):
        for user in (developer, admin_user):
            html = login(user).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
            assert f"/s/{org.slug}/{studio_tree.slug}/analytics" in html, user.email
            client.logout()

    def test_viewer_does_not_see_the_tab(self, login, viewer, org, studio_tree, report_row):
        html = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert f"/s/{org.slug}/{studio_tree.slug}/analytics" not in html

    def test_dashboard_ctx_carries_admin_role(self, login, admin_user, org, studio_tree, report_row):
        html = login(admin_user).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        start = html.index('id="portal-ctx"')
        blob = html[html.index(">", start) + 1 : html.index("</script>", start)]
        ctx = json.loads(blob)
        assert ctx["user"]["role"] == "admin"

    def test_dashboard_ctx_carries_viewer_role(self, login, viewer, org, studio_tree, report_row):
        html = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        start = html.index('id="portal-ctx"')
        blob = html[html.index(">", start) + 1 : html.index("</script>", start)]
        ctx = json.loads(blob)
        # The ops surface still reads this field for its role-gated system
        # actions (_studioRoleSet) -- a viewer's role must stay "viewer".
        assert ctx["user"]["role"] == "viewer"
