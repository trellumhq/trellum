"""Studio-scoped JSON API: legacy shapes, role gating, run/stop flow,
report content serving (traversal guard + widget injection)."""
import json

import pytest

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant
from apps.runner.models import Run
from apps.studios.models import StudioMembership

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
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


class TestRegistryApi:
    def test_registry_shape(self, login, viewer, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/registry").json()
        assert "reports" in body and "generated_at" in body
        assert body["reports"][0]["slug"] == "player-overview"
        assert body["reports"][0]["id"] == report_row.pk

    def test_registry_requires_studio_access(self, login, member, prefix, report_row):
        assert login(member).get(f"{prefix}/api/registry").status_code == 404

    def test_registry_requires_auth_json_401(self, client, prefix, report_row):
        assert client.get(f"{prefix}/api/registry").status_code == 401


class TestSystemStatus:
    def test_status_shape(self, login, viewer, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/system/status").json()
        for key in (
            "running", "queue", "max_concurrent", "scheduled_jobs", "production",
            "worker_alive", "server_uptime_seconds", "reports_total",
            "reports_success", "reports_error", "reports_not_run",
        ):
            assert key in body, key
        assert body["reports_total"] == 1
        assert body["worker_alive"] is False  # no worker heartbeat in tests

    def test_scheduled_jobs_carry_next_run(self, login, viewer, prefix, report_row):
        report_row.schedule_cron = "0 7 * * *"
        report_row.save(update_fields=["schedule_cron"])
        body = login(viewer).get(f"{prefix}/api/system/status").json()
        assert body["scheduled_jobs"][0]["slug"] == "player-overview"
        assert body["scheduled_jobs"][0]["next_run"]  # ISO string


class TestSelectedReportAccess:
    @pytest.fixture(autouse=True)
    def _enable_selected_access(self, settings):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True

    @pytest.fixture
    def selected_viewer(self, member, studio_tree, report_row, make_group, attach_group):
        group = make_group("Selected reports", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        return member

    def test_catalog_and_status_only_include_assigned_reports(
        self, login, selected_viewer, prefix, studio_tree, report_row
    ):
        hidden = Report.objects.create(studio=studio_tree, slug="hidden", name="Hidden")
        Run.objects.create(
            report=report_row, studio=studio_tree, slug=report_row.slug, status=Run.STARTING
        )
        Run.objects.create(report=hidden, studio=studio_tree, slug=hidden.slug, status=Run.QUEUED)
        registry = login(selected_viewer).get(f"{prefix}/api/registry").json()
        assert [row["id"] for row in registry["reports"]] == [report_row.pk]
        assert registry["source_states"] == []

        status = login(selected_viewer).get(f"{prefix}/api/system/status").json()
        assert status["reports_total"] == 1
        assert set(status["running"]) == {report_row.slug}
        assert status["queue"] == []

    @pytest.mark.parametrize(
        "path",
        ["operations", "metrics", "annotations", "experiments", "api/datasources", "api/members"],
    )
    def test_studio_wide_surfaces_reject_partial_viewers(
        self, login, selected_viewer, prefix, report_row, path
    ):
        assert login(selected_viewer).get(f"{prefix}/{path}").status_code == 403

    def test_hidden_report_and_asset_are_indistinguishable_from_missing(
        self, login, selected_viewer, prefix, studio_tree, report_row
    ):
        Report.objects.create(studio=studio_tree, slug="hidden", name="Hidden")
        client = login(selected_viewer)
        assert client.get(f"{prefix}/r/hidden/").status_code == 404
        assert client.get(f"{prefix}/r/hidden/data.json").status_code == 404

    def test_access_model_change_to_external_fails_closed(
        self, login, selected_viewer, prefix, report_row, settings
    ):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        client = login(selected_viewer)
        assert client.get(f"{prefix}/r/{report_row.slug}/").status_code == 503
        assert client.get(f"{prefix}/r/{report_row.slug}/data.json").status_code == 503

    def test_disabled_readiness_blocks_live_query_and_recipient_api(
        self, login, selected_viewer, prefix, org, studio_tree, report_row, settings, rf
    ):
        from apps.reports.views import api_report_recipients

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        client = login(selected_viewer)
        live = client.post(
            f"{prefix}/api/reports/{report_row.slug}/live-query",
            data="{}",
            content_type="application/json",
        )
        assert live.status_code == 503
        assert live.json()["error"] == "selected_report_access_unavailable"

        request = rf.get(f"{prefix}/api/reports/{report_row.slug}/recipients")
        request.user = selected_viewer
        recipients = api_report_recipients(
            request,
            org_slug=org.slug,
            studio_slug=studio_tree.slug,
            slug=report_row.slug,
        )
        assert recipients.status_code == 503

    def test_report_recipient_payload_excludes_unassigned_members(
        self,
        selected_viewer,
        member,
        org,
        studio_tree,
        report_row,
        make_user,
        make_group,
        attach_group,
        rf,
    ):
        from apps.reports.views import api_report_recipients

        hidden = make_user("hidden@demo.example", org=org)
        group = make_group("Hidden viewers", grants=[(studio_tree, roles.VIEWER)])
        hidden_grant = group.grants.get()
        hidden_grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        hidden_grant.save(update_fields=["viewer_scope"])
        attach_group(hidden, group)

        request = rf.get("/")
        request.user = selected_viewer
        response = api_report_recipients(
            request,
            org_slug=org.slug,
            studio_slug=studio_tree.slug,
            slug=report_row.slug,
        )
        payload = json.loads(response.content)
        assert [row["id"] for row in payload["members"]] == [member.pk]
        assert all(row["id"] != group.pk for row in payload["groups"])


class TestRunStop:
    def test_run_creates_queued_run(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/run",
            data=json.dumps({"cache_mode": "fresh"}),
            content_type="application/json",
        )
        assert resp.json() == {"ok": True, "status": "queued"}
        run = Run.objects.get(report=report_row)
        assert run.cache_mode == "fresh"
        assert run.requested_by == developer

    def test_run_dedupe(self, login, developer, prefix, report_row):
        c = login(developer)
        c.post(f"{prefix}/api/reports/player-overview/run")
        resp = c.post(f"{prefix}/api/reports/player-overview/run")
        assert resp.json()["status"] == "queued"
        assert Run.objects.count() == 1

    def test_viewer_cannot_run(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(f"{prefix}/api/reports/player-overview/run")
        assert resp.status_code == 403
        assert Run.objects.count() == 0

    def test_stop(self, login, developer, prefix, report_row):
        c = login(developer)
        c.post(f"{prefix}/api/reports/player-overview/run")
        resp = c.post(f"{prefix}/api/reports/player-overview/stop")
        assert resp.json() == {"stopped": True}
        assert Run.objects.get(report=report_row).status == Run.STOPPED

    def test_unknown_report_404(self, login, developer, prefix, report_row):
        assert (
            login(developer).post(f"{prefix}/api/reports/ghost/run").status_code == 404
        )

    def test_run_all(self, login, developer, prefix, studio_tree, write_report, report_row):
        write_report("second")
        from apps.reports.scan import sync_studio_registry

        sync_studio_registry(studio_tree)
        body = login(developer).post(f"{prefix}/api/system/run-all").json()
        assert body["ok"] is True and body["enqueued"] == 2
        assert Run.objects.count() == 2

    def test_stop_all(self, login, developer, prefix, report_row):
        c = login(developer)
        c.post(f"{prefix}/api/system/run-all")
        assert c.post(f"{prefix}/api/system/stop-all").json() == {"ok": True}
        assert Run.objects.filter(status=Run.STOPPED).count() == 1


class TestStatusAndLogs:
    def test_report_status_shape(self, login, viewer, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/reports/player-overview/status").json()
        assert body == {
            "slug": "player-overview",
            "state": "idle",
            "elapsed_seconds": None,
            "history": [],
            "aggregates": {
                "runs": 0,
                "success": 0,
                "success_rate": None,
                "avg_duration_s": None,
                "p50_duration_s": None,
                "p95_duration_s": None,
                "avg_queue_wait_s": None,
                "p95_queue_wait_s": None,
                "avg_peak_memory_mb": None,
                "max_peak_memory_mb": None,
                "by_status": {},
                "window_days": 90,
            },
        }

    def test_history_after_completed_run(self, login, viewer, prefix, report_row):
        from django.utils import timezone

        Run.objects.create(
            report=report_row,
            studio=report_row.studio,
            slug=report_row.slug,
            status=Run.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            exit_code=0,
            stdout_tail="all good",
            peak_memory_mb=123,
        )
        body = login(viewer).get(f"{prefix}/api/reports/player-overview/status").json()
        assert len(body["history"]) == 1
        rec = body["history"][0]
        assert rec["status"] == "success"
        assert "stdout" not in rec  # heavy fields stripped from list view
        assert rec["peak_memory_mb"] == 123
        assert rec["queue_wait_seconds"] is not None
        assert body["aggregates"]["runs"] == 1
        assert body["aggregates"]["success_rate"] == 1.0
        assert body["aggregates"]["by_status"] == {"success": 1}

    def test_run_stats_endpoint(self, login, viewer, member, prefix, report_row):
        from django.utils import timezone

        now = timezone.now()
        Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug,
            status=Run.SUCCESS, created_at=now - timezone.timedelta(seconds=70),
            started_at=now - timezone.timedelta(seconds=60), finished_at=now,
            peak_memory_mb=256,
        )
        body = login(viewer).get(f"{prefix}/api/system/run-stats").json()
        entry = body["stats"]["player-overview"]
        assert entry["agg"]["runs"] == 1
        assert entry["agg"]["success_rate"] == 1.0
        assert entry["last"]["status"] == "success"
        assert entry["last"]["duration_s"] == 60.0
        assert entry["last"]["peak_memory_mb"] == 256
        # org member without a studio grant: existence not leaked
        assert login(member).get(f"{prefix}/api/system/run-stats").status_code == 404

    def test_log_endpoint_includes_output(self, login, developer, prefix, report_row):
        from django.utils import timezone

        Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug,
            status=Run.ERROR, started_at=timezone.now(), finished_at=timezone.now(),
            exit_code=1, stderr_tail="trace...",
        )
        body = login(developer).get(f"{prefix}/api/reports/player-overview/log").json()
        assert body["stderr"] == "trace..."

    def test_log_404_when_empty(self, login, developer, prefix, report_row):
        resp = login(developer).get(f"{prefix}/api/reports/player-overview/log")
        assert resp.status_code == 404

    def test_log_ignores_non_integer_run_param(self, login, developer, prefix, report_row):
        resp = login(developer).get(f"{prefix}/api/reports/player-overview/log?run=abc")
        # falls back to run 0; no runs exist -> 404, not 500
        assert resp.status_code == 404

    def test_system_log_ignores_non_integer_lines_param(
        self, login, developer, prefix, report_row
    ):
        resp = login(developer).get(f"{prefix}/api/system/log?lines=abc")
        assert resp.status_code == 200
        assert resp.json()["lines"] == 500

    def test_live_log_empty_when_idle(self, login, developer, prefix, report_row):
        body = login(developer).get(f"{prefix}/api/reports/player-overview/log/live").json()
        assert body == {"slug": "player-overview", "stdout_tail": ""}

    def test_viewer_cannot_read_logs(self, login, viewer, prefix, report_row):
        assert login(viewer).get(f"{prefix}/api/reports/player-overview/log").status_code == 403


class TestValidation:
    def test_validation_with_details_fallback(
        self, login, viewer, prefix, report_row, write_meta
    ):
        write_meta("player-overview", validation={"status": "warn"}, health={"total": 3})
        body = login(viewer).get(f"{prefix}/api/reports/player-overview/validation").json()
        assert body["status"] == "warn"
        assert body["details"] == {"total": 3}


class TestMaintenance:
    def test_cache_clear(self, login, developer, prefix, studio_tree, report_row):
        cache_dir = studio_tree.output_dir / ".query_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "x.pkl").write_bytes(b"1")
        body = login(developer).post(f"{prefix}/api/system/cache/clear").json()
        assert body["ok"] is True
        assert not cache_dir.exists()

    def test_registry_refresh(self, login, developer, prefix, studio_tree, write_report, report_row):
        write_report("newone")
        body = login(developer).post(f"{prefix}/api/system/registry/refresh").json()
        assert body == {"ok": True, "reports": 2, "warning": ""}

    def test_registry_refresh_refused_while_sync_holds_the_lock(self, login, developer, prefix, report_row):
        import psycopg
        from django.db import connections

        from apps.runner.gitsync import GITSYNC_LOCK_NAMESPACE

        params = connections["default"].get_connection_params()
        for k in ("cursor_factory", "context"):
            params.pop(k, None)
        with psycopg.connect(**params) as other:
            with other.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)",
                    [GITSYNC_LOCK_NAMESPACE, report_row.studio.pk],
                )
                assert cur.fetchone()[0] is True
                resp = login(developer).post(f"{prefix}/api/system/registry/refresh")
        assert resp.status_code == 409
        body = resp.json()
        assert body["ok"] is False
        assert "sync" in body["message"].lower()

    def test_git_status_unconfigured(self, login, developer, prefix, report_row):
        body = login(developer).get(f"{prefix}/api/system/git/status").json()
        assert body == {"configured": False}

    def test_datasources_stub(self, login, viewer, prefix, report_row):
        assert login(viewer).get(f"{prefix}/api/datasources").json() == {"sources": []}


class TestReportServing:
    @pytest.fixture
    def built_report(self, studio_tree, write_report, report_row):
        out = studio_tree.output_dir / "player-overview"
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
        (out / "data.json").write_text('{"a": 1}', encoding="utf-8")
        return out

    def test_page_redirects_to_entry(self, login, viewer, prefix, built_report):
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 302
        assert resp.url.endswith("/r/player-overview/index.html")

    def test_page_redirect_preserves_display_and_other_query_params(
        self, login, viewer, prefix, built_report
    ):
        resp = login(viewer).get(
            f"{prefix}/r/player-overview/?display=console&range=30d"
        )
        assert resp.status_code == 302
        assert resp.url.endswith(
            "/r/player-overview/index.html?display=console&range=30d"
        )

    def test_unbuilt_report_shows_status_not_404(self, login, viewer, prefix, report_row):
        # The report exists in the registry but has never produced output. It
        # must not 404 as if the page doesn't exist — that's misleading.
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 200
        assert b"hasn't been built yet" in resp.content
        # Distinct from the expired-data page below, not just a superset of it.
        assert b"data has been deleted" not in resp.content

    def test_expired_report_explains_deletion_not_unbuilt(
        self, login, viewer, prefix, report_row, org
    ):
        # Both timestamps set (apps.core.retention._sweep_built_data's
        # contract) is the expired state -- distinct from "never built"
        # (data_expired_at NULL), which must keep rendering report_unbuilt.html.
        from django.utils import timezone

        report_row.last_built_at = timezone.now() - timezone.timedelta(days=10)
        report_row.data_expired_at = timezone.now() - timezone.timedelta(days=1)
        report_row.save(update_fields=["last_built_at", "data_expired_at"])

        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 200
        assert b"data has been deleted" in resp.content
        assert b"hasn't been built yet" not in resp.content
        assert org.name.encode() in resp.content
        assert report_row.data_expired_at.strftime("%Y-%m-%d %H:%M").encode() in resp.content
        # Nothing was lost but the bytes.
        assert b"history" in resp.content.lower()
        assert b"favourites" in resp.content.lower()
        assert b"share links" in resp.content.lower()

    def test_expired_report_viewer_gets_no_rebuild_button(
        self, login, viewer, prefix, report_row
    ):
        from django.utils import timezone

        report_row.last_built_at = timezone.now() - timezone.timedelta(days=10)
        report_row.data_expired_at = timezone.now() - timezone.timedelta(days=1)
        report_row.save(update_fields=["last_built_at", "data_expired_at"])

        html = login(viewer).get(f"{prefix}/r/player-overview/").content.decode()
        assert "reportRebuildNow" not in html
        assert "ask an editor" in html.lower()

    def test_expired_report_developer_gets_working_rebuild_button(
        self, login, developer, prefix, report_row
    ):
        from django.utils import timezone

        report_row.last_built_at = timezone.now() - timezone.timedelta(days=10)
        report_row.data_expired_at = timezone.now() - timezone.timedelta(days=1)
        report_row.save(update_fields=["last_built_at", "data_expired_at"])

        html = login(developer).get(f"{prefix}/r/player-overview/").content.decode()
        assert "reportRebuildNow" in html
        # Wired to the real run endpoint -- api_report_run below, not a new one.
        assert f'{prefix}/api/reports/player-overview/run' in html
        assert "ask an editor" not in html.lower()

        # The control is only as real as the endpoint it posts to.
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/run",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.json() == {"ok": True, "status": "queued"}

    def test_expired_report_with_schedule_shows_next_build(
        self, login, viewer, prefix, report_row
    ):
        from django.utils import timezone

        report_row.last_built_at = timezone.now() - timezone.timedelta(days=10)
        report_row.data_expired_at = timezone.now() - timezone.timedelta(days=1)
        report_row.schedule_cron = "0 7 * * *"
        report_row.save(
            update_fields=["last_built_at", "data_expired_at", "schedule_cron"]
        )

        html = login(viewer).get(f"{prefix}/r/player-overview/").content.decode()
        # Whitespace-normalized: the template wraps this sentence across
        # source lines (harmless once HTML-rendered), so compare on meaning.
        assert "next due to build automatically" in " ".join(html.split())

    def test_errored_report_surfaces_the_build_error_to_admins(
        self, login, org_admin, viewer, prefix, report_row, write_meta
    ):
        write_meta(
            "player-overview",
            last_status="error",
            last_error="ValueError: No credentials could be resolved.",
        )
        resp = login(org_admin).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 200
        assert b"failed to build" in resp.content
        assert b"No credentials could be resolved" in resp.content
        # Build output can name hosts and users: a viewer gets the fact, not the text.
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert b"failed to build" in resp.content
        assert b"No credentials could be resolved" not in resp.content

    def test_html_gets_widget_injected(self, login, viewer, prefix, built_report):
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.status_code == 200
        assert b'/api/assistant/widget.js' in resp.content

    def test_legacy_html_gets_report_display_host_once(
        self, login, viewer, prefix, built_report
    ):
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.content.count(b"/api/reports/menu-widget.js") == 1

        (built_report / "index.html").write_text(
            '<html><body><script src="/api/reports/menu-widget.js" defer></script>'
            "</body></html>",
            encoding="utf-8",
        )
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.content.count(b"/api/reports/menu-widget.js") == 1

    def test_legacy_html_gets_share_widget_after_menu_once(
        self, login, viewer, prefix, built_report
    ):
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.content.count(b"/api/reports/share-widget.js") == 1
        assert resp.content.index(b"/api/reports/menu-widget.js") < resp.content.index(
            b"/api/reports/share-widget.js"
        )

        (built_report / "index.html").write_text(
            '<html><body><script src="/api/reports/share-widget.js" defer></script>'
            "</body></html>",
            encoding="utf-8",
        )
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.content.count(b"/api/reports/share-widget.js") == 1
        assert resp.content.index(b"/api/reports/menu-widget.js") < resp.content.index(
            b"/api/reports/share-widget.js"
        )

    def test_legacy_back_link_upgraded_to_breadcrumb(
        self, login, viewer, prefix, built_report, org, studio_tree
    ):
        # Reports built before the trellum header carry the lone Portal pill;
        # serving upgrades it in place, keeping the fw-back-link canary class.
        (built_report / "index.html").write_text(
            '<html><body><a class="fw-back-link" href="/s/o/s/" '
            'title="Back to Portal"><svg></svg>Portal</a></body></html>',
            encoding="utf-8",
        )
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert "fw-crumb" in html
        assert 'class="fw-back-link"' in html
        assert f"/s/{org.slug}/{studio_tree.slug}/" in html
        assert ">Portal</a>" not in html
        # The upgraded crumb's org half carries ?org=<slug> — a bare "/"
        # would open whatever org the session last remembered, not this one.
        assert f'href="/?org={org.slug}"' in html

    def test_org_crumb_hardcoded_root_fixed_at_serve_time(
        self, login, viewer, prefix, built_report, org
    ):
        # Builds from the window where the crumb existed but its org half
        # hard-coded "/": already carrying fw-crumb, so the legacy upgrade
        # above skips them — the href itself is patched in place instead.
        (built_report / "index.html").write_text(
            '<html><body><a class="fw-crumb" href="/" style="x">Demo Org</a>'
            '<a class="fw-back-link" href="/s/o/s/">Demo</a></body></html>',
            encoding="utf-8",
        )
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert f'class="fw-crumb" href="/?org={org.slug}"' in html
        assert 'class="fw-crumb" href="/"' not in html

    def test_new_breadcrumb_not_double_injected(
        self, login, viewer, prefix, built_report
    ):
        (built_report / "index.html").write_text(
            '<html><body><span class="fw-crumb-sep">▸</span>'
            '<a class="fw-back-link" href="/s/o/s/">Demo</a></body></html>',
            encoding="utf-8",
        )
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert html.count("fw-crumb-sep") == 1

    def test_existing_widget_not_duplicated(self, login, viewer, prefix, built_report):
        (built_report / "index.html").write_text(
            '<html><body><script src="/api/assistant/widget.js"></script></body></html>',
            encoding="utf-8",
        )
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.content.count(b"/api/assistant/widget.js") == 1

    # ── Theming redesign: the served report carries the SAME resolved
    # theme the studio's management chrome does (apps.core.themes.
    # resolve_studio_theme) -- see apps/core/tests/test_theme_resolution.py
    # for the resolver's own chain/lock/independence tests and
    # apps/core/tests/test_mgmt_theme.py for the chrome side.

    def test_html_carries_the_resolved_theme_as_data_theme(self, login, viewer, prefix, built_report):
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert 'data-theme="trellum dark"' in html

    def test_html_omits_data_studio_theme_when_nothing_was_picked(
        self, login, viewer, prefix, built_report
    ):
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert "data-studio-theme" not in html

    def test_html_carries_data_studio_theme_when_the_studio_picked_one(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert 'data-theme="money"' in html
        assert 'data-studio-theme="money"' in html

    def test_viewers_override_wins_in_served_report(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        StudioMembership.objects.filter(user=viewer, studio=studio_tree).update(theme="dracula")
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert 'data-theme="dracula"' in html

    def test_a_stale_build_time_data_theme_is_overridden_not_duplicated(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        studio_tree.theme = "ocean"
        studio_tree.save(update_fields=["theme"])
        (built_report / "index.html").write_text(
            '<html lang="en" data-theme="money"><body>hi</body></html>', encoding="utf-8"
        )
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert html.count("data-theme=") == 1
        assert 'data-theme="ocean"' in html
        assert 'lang="en"' in html  # untouched attribute survives the rewrite

    def test_theme_stays_correct_and_single_across_repeat_requests(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        # Idempotency: a second request under a DIFFERENT resolved theme
        # must not leave the first request's attributes behind.
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        login(viewer).get(f"{prefix}/r/player-overview/index.html")
        studio_tree.theme = "ocean"
        studio_tree.save(update_fields=["theme"])
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert html.count("data-theme=") == 1
        assert html.count("data-studio-theme=") == 1
        assert 'data-theme="ocean"' in html

    def test_theme_widget_script_injected(self, login, viewer, prefix, built_report):
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert b"/api/reports/theme-widget.js" in resp.content

    def test_theme_widget_script_not_duplicated(self, login, viewer, prefix, built_report):
        (built_report / "index.html").write_text(
            '<html><body><script src="/api/reports/theme-widget.js" defer></script>'
            "</body></html>",
            encoding="utf-8",
        )
        resp = login(viewer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.content.count(b"/api/reports/theme-widget.js") == 1

    def test_theme_select_option_resynced_to_the_resolved_theme(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        (built_report / "index.html").write_text(
            '<html><body><select class="fw-theme-select" id="fwThemeSelect">'
            '<option value="trellum dark" selected>Trellum Dark</option>'
            '<option value="money">Money</option>'
            "</select></body></html>",
            encoding="utf-8",
        )
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert '<option value="money" selected>' in html
        assert '<option value="trellum dark" selected>' not in html

    # ── Repo-declared themes (Studio.repo_theme) -- decision ①: a
    # REGISTERED name behaves exactly like any other resolved theme (this
    # class's tests above already cover that path via studio_tree.theme,
    # and repo_theme resolves through the same resolve_studio_theme); a
    # CUSTOM name must leave the report's own baked look alone instead of
    # overwriting it with the chrome's Trellum fallback. See
    # apps/core/tests/test_theme_resolution.py::TestRepoTheme for the
    # resolver-level unit tests this serve-time behavior builds on.

    def test_registry_repo_theme_overwrites_data_theme_like_any_other_pick(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        studio_tree.repo_theme = "nord"
        studio_tree.save(update_fields=["repo_theme"])
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert 'data-theme="nord"' in html
        assert 'data-studio-theme="nord"' in html

    def test_custom_repo_theme_leaves_the_baked_data_theme_untouched(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        (built_report / "index.html").write_text(
            '<html lang="en" data-theme="a-repo-custom-theme"><body>hi</body></html>',
            encoding="utf-8",
        )
        studio_tree.repo_theme = "a-repo-custom-theme"
        studio_tree.save(update_fields=["repo_theme"])
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        # The chrome resolves to Trellum for this studio (no CSS for a
        # custom name), but the report was BUILT with real CSS for it --
        # overwriting would repaint working variables onto rules that don't
        # exist for "trellum dark" is not the point; leaving it alone is.
        assert 'data-theme="a-repo-custom-theme"' in html
        assert 'data-theme="trellum dark"' not in html
        assert 'lang="en"' in html  # untouched attribute confirms no rewrite ran

    def test_custom_repo_theme_leaves_the_theme_select_untouched_too(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        (built_report / "index.html").write_text(
            '<html data-theme="a-repo-custom-theme"><body>'
            '<select class="fw-theme-select" id="fwThemeSelect">'
            '<option value="a-repo-custom-theme" selected>Custom</option>'
            '<option value="trellum dark">Trellum Dark</option>'
            "</select></body></html>",
            encoding="utf-8",
        )
        studio_tree.repo_theme = "a-repo-custom-theme"
        studio_tree.save(update_fields=["repo_theme"])
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert '<option value="a-repo-custom-theme" selected>' in html
        assert '<option value="trellum dark" selected>' not in html

    def test_custom_repo_theme_ignores_a_viewer_override_too(
        self, login, viewer, prefix, built_report, studio_tree
    ):
        (built_report / "index.html").write_text(
            '<html data-theme="a-repo-custom-theme"><body>hi</body></html>',
            encoding="utf-8",
        )
        studio_tree.repo_theme = "a-repo-custom-theme"
        studio_tree.save(update_fields=["repo_theme"])
        StudioMembership.objects.filter(user=viewer, studio=studio_tree).update(theme="dracula")
        html = login(viewer).get(f"{prefix}/r/player-overview/index.html").content.decode()
        assert 'data-theme="a-repo-custom-theme"' in html
        assert 'data-theme="dracula"' not in html

    def test_asset_served(self, login, viewer, prefix, built_report):
        resp = login(viewer).get(f"{prefix}/r/player-overview/data.json")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/json")

    def test_traversal_blocked(self, login, viewer, prefix, built_report, studio_tree):
        secret = studio_tree.project_root / "config.yaml"
        secret.write_text("secret: yes", encoding="utf-8")
        resp = login(viewer).get(f"{prefix}/r/player-overview/..%2f..%2fconfig.yaml")
        assert resp.status_code == 404

    def test_unauthenticated_html_redirects_to_login(self, client, prefix, built_report):
        resp = client.get(f"{prefix}/r/player-overview/index.html")
        assert resp.status_code == 302
        assert resp.url.startswith("/login")

    def test_member_without_grant_cannot_view(self, login, member, prefix, built_report):
        assert (
            login(member).get(f"{prefix}/r/player-overview/index.html").status_code == 404
        )


class TestVendorRoute:
    def test_vendor_serves_bundled_asset(self, client, db):
        resp = client.get("/_vendor/chart.umd.min.js")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/javascript"
        assert "immutable" in resp.get("Cache-Control", "")

    def test_vendor_traversal_blocked(self, client, db):
        assert client.get("/_vendor/..%2fproject.py").status_code in (400, 404)

    def test_vendor_missing_404(self, client, db):
        assert client.get("/_vendor/nope.js").status_code == 404


class TestAssistantStub:
    def test_widget_bundle_served(self, client, db):
        resp = client.get("/api/assistant/widget.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        assert b"document.head.appendChild" in resp.content

    def test_available_false(self, client, db):
        body = client.get("/api/assistant/available").json()
        assert body["available"] is False


class TestEdgeReadPath:
    """In an edge posture, opening a report hands the viewer to the edge: a
    redirect to the current build's immutable /content/ URL plus (edge-signed)
    an Ed25519 grant cookie scoped to exactly this studio's prefix. Permission
    stays the same DB check as ever — it just runs before the grant instead of
    before each proxied byte."""

    @pytest.fixture
    def edge_on(self, settings, monkeypatch, report_row):
        from apps.core import cdn, storage
        from apps.core.tests.test_cdn import TEST_PEM
        from apps.core.tests.test_storage import FakeClient, FakeS3

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"
        settings.TRELLUM_CDN_SIGNING_KEY = TEST_PEM
        settings.TRELLUM_CDN_SIGNING_KEY_FILE = ""
        settings.TRELLUM_CDN_COOKIE_TTL_SECONDS = 600
        cdn._reset_key_cache()
        cdn._reset_probe_cache()
        # The guard would otherwise make an outbound request; assert protected.
        monkeypatch.setattr(cdn, "exposure_ok", lambda: (True, "protected (test)"))
        fake = FakeS3()
        monkeypatch.setattr(storage, "_s3", lambda: fake)
        monkeypatch.setattr(storage, "_client", lambda: FakeClient(fake))
        monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 0.0)
        storage._pointer_memo.clear()
        yield fake
        cdn._reset_key_cache()
        cdn._reset_probe_cache()

    def _publish(self, fake, build="b1"):
        from apps.core.tests.test_storage import install_build

        install_build(
            fake, slug="player-overview", build=build,
            files={"index.html": b"<h1>r</h1>"},
        )

    def test_report_page_redirects_to_the_current_build(
        self, login, viewer, prefix, edge_on
    ):
        self._publish(edge_on, build="b7")
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 302
        assert resp["Location"] == (
            "/content/demo/casino/player-overview/builds/b7/index.html"
        )

    def test_report_page_forwards_query_to_the_current_build(
        self, login, viewer, prefix, edge_on
    ):
        self._publish(edge_on, build="b7")
        resp = login(viewer).get(
            f"{prefix}/r/player-overview/?display=monitor&range=30d"
        )
        assert resp["Location"] == (
            "/content/demo/casino/player-overview/builds/b7/index.html"
            "?display=monitor&range=30d"
        )

    def test_the_grant_cookie_is_scoped_to_the_report(
        self, login, viewer, prefix, edge_on
    ):
        from apps.core import cdn

        self._publish(edge_on)
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        morsel = resp.cookies[cdn.GRANT_COOKIE]
        assert morsel["path"] == "/content/demo/casino/player-overview"
        assert morsel["max-age"] == 600
        assert morsel["httponly"]

    def test_not_one_content_byte_flows_through_this_process(
        self, login, viewer, prefix, edge_on, settings
    ):
        """The whole point: no pull, no materialised cache."""
        self._publish(edge_on)
        login(viewer).get(f"{prefix}/r/player-overview/")
        assert edge_on.pulls == []
        assert not (settings.DATA_DIR / "cache").exists()

    def test_an_unpublished_report_still_gets_the_unbuilt_page(
        self, login, viewer, prefix, edge_on
    ):
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 200
        assert b"hasn't been built" in resp.content

    def test_permission_still_decides(self, login, member, prefix, edge_on):
        """A user outside the studio gets nothing to take to the edge."""
        from apps.core import cdn

        self._publish(edge_on)
        resp = login(member).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 404
        assert cdn.GRANT_COOKIE not in resp.cookies

    def test_the_guard_refuses_to_serve_when_content_is_exposed(
        self, login, viewer, prefix, edge_on, monkeypatch
    ):
        """If the guard cannot prove a stranger is denied, the portal 503s
        rather than emitting a URL to an open bucket — and never falls back to
        proxying the bytes itself."""
        from apps.core import cdn

        self._publish(edge_on)
        monkeypatch.setattr(cdn, "exposure_ok", lambda: (False, "publicly readable"))
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 503
        assert cdn.GRANT_COOKIE not in resp.cookies

    def test_edge_external_redirects_without_minting_a_grant(
        self, login, viewer, prefix, edge_on, settings
    ):
        from apps.core import cdn

        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        self._publish(edge_on)
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 302
        assert resp["Location"].startswith("/content/demo/casino/")
        assert cdn.GRANT_COOKIE not in resp.cookies  # external layer authenticates

    def test_proxy_posture_stays_on_the_proxy_path(
        self, login, viewer, prefix, edge_on, settings
    ):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
        self._publish(edge_on)
        resp = login(viewer).get(f"{prefix}/r/player-overview/")
        assert resp.status_code == 302
        assert resp["Location"] == f"{prefix}/r/player-overview/index.html"


class TestDeliveryWidgetStub:
    """The Email & Alerts widget's frozen-URL bundle — same shape as
    /api/assistant/widget.js above, no auth (the calls it makes are gated)."""

    def test_widget_served(self, client, db):
        resp = client.get("/api/reports/delivery-widget.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        assert b"window.ReportDelivery" in resp.content


class TestMenuWidgetStub:
    """The report-page Options menu host's frozen-URL bundle -- same shape
    as the delivery widget above, no auth (the button only ever appears
    once something registers, and each registration is itself gated)."""

    def test_widget_served(self, client, db):
        resp = client.get("/api/reports/menu-widget.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        assert b"window.__reportMenu" in resp.content
        assert "max-age=300" in resp["Cache-Control"]


class TestReportShell:
    def test_viewer_gets_scoped_shell_without_cache(
        self, login, viewer, prefix, report_row
    ):
        resp = login(viewer).get(f"{prefix}/api/report-shell?report={report_row.slug}")
        assert resp.status_code == 200
        assert resp["Cache-Control"] == "no-store"
        assert b'data-console-shell' in resp.content
        assert b'data-report-display="focus"' in resp.content
        assert f'{prefix}/analytics'.encode() not in resp.content
        assert f'{prefix}/settings/repo'.encode() not in resp.content

    def test_developer_role_is_allowed(
        self, login, developer, prefix, report_row
    ):
        resp = login(developer).get(f"{prefix}/api/report-shell?report={report_row.slug}")
        assert resp.status_code == 200
        assert f'{prefix}/analytics'.encode() in resp.content
        assert f'{prefix}/settings/repo'.encode() not in resp.content

    def test_org_admin_role_is_allowed(
        self, login, org_admin, prefix, report_row
    ):
        resp = login(org_admin).get(f"{prefix}/api/report-shell?report={report_row.slug}")
        assert resp.status_code == 200
        assert f'{prefix}/analytics'.encode() in resp.content
        assert f'{prefix}/settings/repo'.encode() in resp.content

    def test_shell_context_is_html_escaped(
        self, login, viewer, prefix, report_row, org
    ):
        org.name = '<img src=x onerror="alert(1)">'
        org.save(update_fields=["name"])
        resp = login(viewer).get(f"{prefix}/api/report-shell?report={report_row.slug}")
        assert b"&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in resp.content
        assert b'<img src=x onerror="alert(1)">' not in resp.content

    def test_unauthenticated_request_is_rejected(self, client, prefix, report_row):
        resp = client.get(f"{prefix}/api/report-shell?report={report_row.slug}")
        assert resp.status_code == 401
        assert resp.json() == {"error": "authentication required"}

    def test_member_without_studio_grant_cannot_fetch_shell(
        self, login, member, prefix, report_row
    ):
        assert (
            login(member).get(
                f"{prefix}/api/report-shell?report={report_row.slug}"
            ).status_code
            == 404
        )

    def test_report_must_exist_in_the_resolved_studio(
        self, login, viewer, prefix, report_row
    ):
        assert (
            login(viewer).get(f"{prefix}/api/report-shell?report=missing").status_code
            == 404
        )

    def test_shell_is_read_only(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(
            f"{prefix}/api/report-shell?report={report_row.slug}"
        )
        assert resp.status_code == 405


class TestStudioMembersApi:
    """The Email & Alerts drawer's recipient picker — no JSON endpoint for
    "who can see this studio" existed before (the server-rendered members
    settings page is ADMIN-gated); this one is VIEWER-gated like the
    schedule endpoints it feeds, since any member can pick recipients for
    their own personal delivery schedule."""

    def test_studio_member_listed(self, login, viewer, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/members").json()
        assert {"id": viewer.pk, "email": viewer.email, "name": viewer.display_name or viewer.email} in body["members"]

    def test_org_admin_listed_without_explicit_studio_membership(
        self, login, viewer, org_admin, prefix, report_row
    ):
        """org_admin has no StudioMembership row at all -- effective_roles
        grants it implicitly, and the picker must honor the same rule
        _valid_recipients uses server-side, or a schedule created with an
        org admin checked would 404 refuse to save."""
        body = login(viewer).get(f"{prefix}/api/members").json()
        assert any(m["id"] == org_admin.pk for m in body["members"])

    def test_member_without_studio_grant_excluded(self, login, viewer, member, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/members").json()
        assert all(m["id"] != member.pk for m in body["members"])

    def test_requires_auth(self, client, prefix, report_row):
        assert client.get(f"{prefix}/api/members").status_code == 401


class TestDataSourceStatusSurfaces:
    """The Operations JSON and the unbuilt page carry a report's data source
    state: what it waits for, and the source a failure was attributed to
    (internal planning ticket #128)."""

    @pytest.fixture
    def declared(self, studio_tree, report_row):
        from apps.datasources.models import RepoDataSource
        from apps.reports.scan import invalidate_registry_cache

        RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "db.internal"}, source_file="data-sources/config.yaml",
        )
        report_row.config = {**report_row.config, "data_sources": ["warehouse"]}
        report_row.save(update_fields=["config"])
        invalidate_registry_cache(studio_tree)
        return report_row

    @pytest.fixture
    def admin(self, make_user, org, studio_tree, grant_studio):
        user = make_user("adm@demo.example", org=org)
        grant_studio(user, studio_tree, roles.ADMIN)
        return user

    def test_status_json_lists_source_states_and_what_they_block(
        self, login, viewer, prefix, declared, write_meta
    ):
        write_meta(
            "player-overview", last_status="error", blocked_by=["warehouse"],
            last_error="Waiting for data source 'warehouse': missing: user, password",
        )
        body = login(viewer).get(f"{prefix}/api/system/status").json()
        [src] = body["source_states"]
        assert src["name"] == "warehouse" and src["type"] == "postgres"
        assert src["state"] == "needs_credentials"
        assert src["used_by"] == ["player-overview"] and src["blocks"] == ["player-overview"]
        assert src["binding_scope"] is None
        assert body["reports_waiting"] == 1
        reg = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert reg["blocked_by"] == ["warehouse"] and reg["waiting"] is True
        assert reg["failing_source"] is None

    def test_a_built_report_names_its_failing_source(self, login, viewer, prefix, declared, write_meta):
        from django.utils import timezone

        from apps.datasources.models import DataSource
        from apps.reports.scan import invalidate_registry_cache

        DataSource.objects.create(
            studio=declared.studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
            last_check_at=timezone.now(), last_check_ok=False, last_check_error="refused",
        )
        write_meta("player-overview", last_status="success", blocked_by=["warehouse"])
        invalidate_registry_cache(declared.studio)
        reg = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert reg["waiting"] is False  # a stale blocked_by after a good build is not waiting
        assert reg["failing_source"]["name"] == "warehouse"
        assert reg["failing_source"]["since"]

    def test_the_run_tail_wins_when_only_it_carries_attribution(
        self, login, developer, prefix, declared, write_meta
    ):
        """Attribution rewrites the runner's local _meta.json only; a web node
        reading a remote store still has the raw error."""
        from django.utils import timezone

        from apps.reports.scan import invalidate_registry_cache

        write_meta("player-overview", last_status="error", last_error="Traceback ... refused")
        Run.objects.create(
            report=declared, studio=declared.studio, slug=declared.slug, status=Run.ERROR,
            finished_at=timezone.now(),
            stderr_tail="Data source 'warehouse': OperationalError: refused\n\nTraceback ... refused",
        )
        invalidate_registry_cache(declared.studio)
        reg = login(developer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert reg["last_error"].startswith("Data source 'warehouse': OperationalError: refused")
        assert reg["waiting"] is False

    def test_a_blocked_run_is_read_off_the_run_when_meta_was_not_published(
        self, login, viewer, prefix, declared
    ):
        from django.utils import timezone

        from apps.reports.scan import invalidate_registry_cache

        Run.objects.create(
            report=declared, studio=declared.studio, slug=declared.slug, status=Run.ERROR,
            finished_at=timezone.now(),
            stderr_tail="Waiting for data source 'warehouse': missing: user, password",
        )
        invalidate_registry_cache(declared.studio)
        reg = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert reg["blocked_by"] == ["warehouse"] and reg["waiting"] is True

    def test_waiting_page_for_an_admin_links_to_configure(self, login, admin, prefix, declared, write_meta):
        write_meta("player-overview", last_status="error", blocked_by=["warehouse"])
        page = login(admin).get(f"{prefix}/r/player-overview/").content.decode()
        assert "<code>player-overview</code> is waiting for a data source" in page
        assert "your repository declares but this studio hasn't configured yet." in page
        assert f'href="{prefix}/settings/datasources?configure=warehouse#configure">Configure warehouse' in page
        assert f'href="{prefix}/operations">View operations' in page
        assert "Ask a studio admin" not in page

    def test_waiting_page_for_a_viewer_hides_the_fix_and_the_host(
        self, login, viewer, prefix, declared, write_meta
    ):
        write_meta("player-overview", last_status="error", blocked_by=["warehouse"])
        page = login(viewer).get(f"{prefix}/r/player-overview/").content.decode()
        assert "is waiting for a data source" in page
        assert "Ask a studio admin to configure it." in page
        assert "Last successful build: none." in page
        assert "settings/datasources" not in page and "View operations" not in page
        assert "db.internal" not in page

    def test_waiting_page_tells_an_undeclared_name_apart(self, login, admin, prefix, report_row, write_meta):
        report_row.config = {**report_row.config, "data_sources": ["legacy"]}
        report_row.save(update_fields=["config"])
        write_meta("player-overview", last_status="error", blocked_by=["legacy"])
        page = login(admin).get(f"{prefix}/r/player-overview/").content.decode()
        assert "is referenced by the report but not declared in <code>data-sources/config.yaml</code>" in page
        assert "Configure legacy" in page

    def test_failing_page_names_the_source_and_shows_raw_error_to_admins_only(
        self, login, admin, viewer, prefix, declared, write_meta
    ):
        write_meta(
            "player-overview", last_status="error",
            last_error="Data source 'warehouse': OperationalError: refused\nTraceback ...",
        )
        page = login(admin).get(f"{prefix}/r/player-overview/").content.decode()
        assert "can't build: data source <code>warehouse</code> is failing" in page
        assert "OperationalError: refused" in page
        assert 'class="ui-code-block"' in page
        assert "Configure warehouse" in page
        page = login(viewer).get(f"{prefix}/r/player-overview/").content.decode()
        assert "is failing" in page and "Ask a studio admin to check its credentials." in page
        assert 'class="ui-code-block"' not in page and "Traceback" not in page
        assert "OperationalError" not in page  # the check's message stays with admins


class TestRoleSafeStatusJson:
    """Check messages and build output can carry hosts and user names: below
    developer, the JSON says which source, not what it said."""

    @pytest.fixture
    def failing(self, studio_tree, report_row, write_meta):
        from django.utils import timezone

        from apps.datasources.models import DataSource, RepoDataSource
        from apps.reports.scan import invalidate_registry_cache

        RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "db.internal"}, source_file="data-sources/config.yaml",
        )
        DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            credentials={"user": "svc", "password": "p"},
            last_check_at=timezone.now(), last_check_ok=False,
            last_check_error="OperationalError: could not connect to db.internal as svc",
        )
        report_row.config = {**report_row.config, "data_sources": ["warehouse"]}
        report_row.save(update_fields=["config"])
        write_meta(
            "player-overview", last_status="error",
            last_error="Data source 'warehouse': OperationalError: could not connect to db.internal as svc",
        )
        invalidate_registry_cache(studio_tree)
        return report_row

    def test_viewer_sees_the_source_not_the_message(self, login, viewer, prefix, failing):
        client = login(viewer)
        status = client.get(f"{prefix}/api/system/status")
        registry = client.get(f"{prefix}/api/registry")
        assert status.json()["source_states"][0]["detail"] == ""
        assert registry.json()["reports"][0]["last_error"] == (
            "Data source 'warehouse': connection check failed"
        )
        for body in (status.content, registry.content):
            assert b"db.internal" not in body and b"svc" not in body

    def test_developer_sees_the_message(self, login, developer, prefix, failing):
        client = login(developer)
        assert "db.internal" in client.get(f"{prefix}/api/system/status").json()["source_states"][0]["detail"]
        assert "db.internal" in client.get(f"{prefix}/api/registry").json()["reports"][0]["last_error"]

    def test_a_held_report_reads_as_waiting_and_a_plain_failure_as_failed(
        self, login, viewer, prefix, report_row, write_meta
    ):
        from apps.datasources.models import RepoDataSource
        from apps.reports.scan import invalidate_registry_cache

        RepoDataSource.objects.create(
            studio=report_row.studio, name="warehouse", type="postgres",
            config={"host": "db.internal"}, source_file="data-sources/config.yaml",
        )
        report_row.config = {**report_row.config, "data_sources": ["warehouse"]}
        report_row.save(update_fields=["config"])
        write_meta("player-overview", last_status="error", blocked_by=["warehouse"],
                   last_error="Waiting for data source 'warehouse': missing: user, password")
        invalidate_registry_cache(report_row.studio)
        entry = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert entry["last_error"] == "Waiting for data source 'warehouse'"

        write_meta("player-overview", last_status="error", blocked_by=[],
                   last_error="Traceback: KeyError 'revenue' at db.internal")
        invalidate_registry_cache(report_row.studio)
        entry = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert entry["last_error"] == "Build failed"

    def test_last_error_is_capped_for_everyone(self, login, developer, prefix, report_row, write_meta):
        from django.utils import timezone

        from apps.reports.scan import invalidate_registry_cache

        write_meta("player-overview", last_status="error", last_error="x")
        Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug, status=Run.ERROR,
            finished_at=timezone.now(), stderr_tail="Data source 'w': boom\n" + "y" * 5000,
        )
        invalidate_registry_cache(report_row.studio)
        entry = login(developer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert len(entry["last_error"]) == 500


class TestWaitingReconciliation:
    """A wait recorded by the last run ends as soon as the reason does."""

    @pytest.fixture
    def held(self, studio_tree, report_row, write_meta):
        from apps.datasources.models import RepoDataSource
        from apps.reports.scan import invalidate_registry_cache

        RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "db"}, source_file="data-sources/config.yaml",
        )
        report_row.config = {**report_row.config, "data_sources": ["warehouse"]}
        report_row.save(update_fields=["config"])
        write_meta("player-overview", last_status="error", blocked_by=["warehouse"])
        invalidate_registry_cache(studio_tree)
        return report_row

    def test_configuring_the_source_clears_the_wait_before_a_build(self, login, viewer, prefix, held):
        from apps.datasources.models import DataSource
        from apps.reports.scan import invalidate_registry_cache

        assert login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]["waiting"] is True
        DataSource.objects.create(
            studio=held.studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        invalidate_registry_cache(held.studio)
        entry = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert entry["waiting"] is False and entry["blocked_by"] == []

    def test_dropping_the_reference_clears_the_wait(self, login, viewer, prefix, held):
        from apps.reports.scan import invalidate_registry_cache

        held.config = {k: v for k, v in held.config.items() if k != "data_sources"}
        held.save(update_fields=["config"])
        invalidate_registry_cache(held.studio)
        entry = login(viewer).get(f"{prefix}/api/registry").json()["reports"][0]
        assert entry["waiting"] is False and entry["blocked_by"] == []

    def test_a_disabled_report_waits_for_nothing(self, login, viewer, prefix, held):
        from apps.reports.scan import invalidate_registry_cache

        held.disabled = True
        held.save(update_fields=["disabled"])
        invalidate_registry_cache(held.studio)
        body = login(viewer).get(f"{prefix}/api/system/status").json()
        assert body["reports_waiting"] == 0
        assert body["source_states"][0]["blocks"] == []


class TestStatusEndpointCost:
    def test_source_states_add_no_queries_to_the_status_poll(
        self, login, developer, prefix, studio_tree, report_row, write_report
    ):
        """The 2-second poll reads source states from the cached registry
        payload: adding sources and reports must not add queries to it."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.datasources.models import DataSource, RepoDataSource
        from apps.reports.scan import (
            build_registry_payload,
            invalidate_registry_cache,
            sync_studio_registry,
        )

        client = login(developer)
        client.get(f"{prefix}/api/system/status")  # settle session/auth queries
        build_registry_payload(studio_tree)
        with CaptureQueriesContext(connection) as few:
            assert client.get(f"{prefix}/api/system/status").status_code == 200

        for i in range(4):
            RepoDataSource.objects.create(
                studio=studio_tree, name=f"src-{i}", type="file",
                config={"path": f"data-sources/files/{i}.csv", "upload": True},
                source_file="data-sources/config.yaml",
            )
            DataSource.objects.create(
                studio=studio_tree, name=f"src-{i}", type="file",
                config={"path": f"data-sources/files/{i}.csv", "upload": True},
            )
            write_report(f"many-{i}", data_sources=[f"src-{i}"])
        sync_studio_registry(studio_tree)
        invalidate_registry_cache(studio_tree)
        build_registry_payload(studio_tree)
        with CaptureQueriesContext(connection) as many:
            body = client.get(f"{prefix}/api/system/status").json()
        assert len(body["source_states"]) == 4
        assert len(many.captured_queries) == len(few.captured_queries), (
            f"status poll grew with sources: {len(few.captured_queries)} -> "
            f"{len(many.captured_queries)}"
        )
