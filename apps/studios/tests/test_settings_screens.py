"""Studio settings screens rebuilt against the design system: repo status
badges, the data-source table (and its untouched JS contract), guarded
member removal, and the settings-hub redirect."""
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import roles
from apps.datasources.models import DataSource, RepoDataSource
from apps.reports.models import Report
from apps.studios.models import RepoPublish, StudioRepo

pytestmark = pytest.mark.django_db


def declare(studio, name, type="postgres", source_file="data-sources/config.yaml", **config):
    return RepoDataSource.objects.create(
        studio=studio, name=name, type=type, config=config, source_file=source_file,
    )


def report_using(studio, slug, *names):
    return Report.objects.create(
        studio=studio, slug=slug, config={"slug": slug, "data_sources": list(names)},
    )


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


@pytest.fixture
def html(login, org_admin):
    def _get(path):
        resp = login(org_admin).get(path)
        assert resp.status_code == 200
        return resp.content.decode()

    return _get


@pytest.fixture
def repo(studio_tree):
    return StudioRepo.objects.create(
        studio=studio_tree,
        repo_url="https://github.com/demo/reports.git",
        webhook_secret="hook-secret",
    )


class TestRepoScreen:
    def test_polling_is_wired_only_for_a_configured_queued_repo(self, html, prefix, repo, studio_tree):
        page = html(f"{prefix}/settings/repo")
        assert 'data-repo-status' in page and 'data-sync-requested="false"' in page
        assert "repo-status.js" in page and 'id="repoPollStatus"' in page
        assert 'id="repoSyncButton"' in page
        StudioRepo.objects.filter(pk=repo.pk).update(sync_requested=True)
        page = html(f"{prefix}/settings/repo")
        assert 'data-sync-requested="true"' in page
        assert re.search(r'id="repoSyncButton"[^>]*disabled', page)
        assert "Checking for changes…" in page
        repo.delete()
        page = html(f"{prefix}/settings/repo")
        assert 'data-repo-status' not in page and "repo-status.js" not in page

    def test_status_uses_badges_and_the_webhook_is_copyable(self, html, prefix, repo):
        page = html(f"{prefix}/settings/repo")
        assert 'class="repo-status"' in page
        assert '<span class="ui-badge ok">ok</span>' in page
        assert "data-ui-copy" in page and "/api/git/webhook" in page
        assert 'class="ui-btn ghost"' in page  # Sync now
        for heading in ("Repository", "Sync behavior", "Webhook"):
            assert heading in page

    def test_error_and_pending_states_get_their_own_badge(self, html, prefix, repo):
        repo.sync_requested = True
        repo.save(update_fields=["sync_requested"])
        assert '<span class="ui-badge warn">sync scheduled</span>' in html(
            f"{prefix}/settings/repo"
        )
        repo.last_error = "auth failed"
        repo.save(update_fields=["last_error"])
        page = html(f"{prefix}/settings/repo")
        assert '<span class="ui-badge error">error</span>' in page
        assert "auth failed" in page

    def test_check_row_class_replaces_the_inline_style(self, html, prefix, repo):
        page = html(f"{prefix}/settings/repo")
        assert 'class="ui-check-row"' in page
        assert "display:flex;gap:8px;align-items:center" not in page

    def test_sync_now_flags_repo_and_redirects(self, login, org_admin, prefix, repo):
        # "Sync now" posts back to the settings page and redirects — it must not
        # hit a JSON API path (which 404'd / dumped JSON in the browser).
        resp = login(org_admin).post(f"{prefix}/settings/repo", {"sync_now": "1"})
        assert resp.status_code == 302
        assert resp.url == f"{prefix}/settings/repo"
        repo.refresh_from_db()
        assert repo.sync_requested is True

    def test_sync_now_without_a_repo_redirects_not_errors(
        self, login, org_admin, prefix, studio_tree
    ):
        resp = login(org_admin).post(f"{prefix}/settings/repo", {"sync_now": "1"})
        assert resp.status_code == 302  # friendly message, not a 404/500


PENDING = {
    "from": "a" * 40, "to": "b" * 40, "branch": "main",
    "rewritten": False, "initial": False, "full_listing": False,
    "commits": [
        {"sha": "b" * 40, "author": "Ada", "date": "2026-09-01T10:00:00+02:00",
         "message": "Tighten churn cohorts", "files": ["reports/churn/report.yaml", "reports/churn/q.sql"]},
        {"sha": "c" * 40, "author": "Ada", "date": "2026-09-01T09:00:00+02:00",
         "message": "Drop the legacy funnel", "files": ["reports/funnel/report.yaml"]},
    ],
    "reports": {"added": ["retention"], "modified": ["churn"], "removed": ["funnel"]},
    "root_files": ["metrics.yaml"],
    "datasources": {"added": ["warehouse"], "removed": [], "changed": ["events"]},
    "warnings": ["2 report(s) will not be registered: this organization is limited to 5 reports."],
}


class TestRepoPublishing:
    """Phase 3b of the repository page: publish mode, pending changes, the
    Publish dialog and the publish history (internal planning ticket #129)."""

    @pytest.fixture
    def manual(self, repo):
        StudioRepo.objects.filter(pk=repo.pk).update(
            publish_mode="manual", remote_sha="b" * 40, remote_checked_at=timezone.now(),
            last_synced_sha="a" * 40, pending_changes=PENDING,
        )
        repo.refresh_from_db()
        return repo

    def test_status_card_shows_published_and_remote(self, html, prefix, repo, org_admin):
        assert "Nothing published yet" in html(f"{prefix}/settings/repo")
        RepoPublish.objects.create(
            studio=repo.studio, to_sha="a" * 40, trigger="manual", published_by=org_admin,
        )
        StudioRepo.objects.filter(pk=repo.pk).update(
            remote_sha="a" * 40, remote_checked_at=timezone.now(), last_synced_sha="a" * 40,
        )
        page = html(f"{prefix}/settings/repo")
        assert "<code>aaaaaaa</code>" in page
        assert f"by {org_admin.email}" in page and "(manual)" in page
        assert "Remote <code>main</code>" in page
        assert '<span class="ui-badge ok">Up to date</span>' in page
        assert "commits ahead" not in page

    def test_commits_ahead_and_a_stale_check(self, html, prefix, manual):
        StudioRepo.objects.filter(pk=manual.pk).update(
            remote_checked_at=timezone.now() - timedelta(minutes=30)
        )
        page = html(f"{prefix}/settings/repo")
        assert '<b class="text-warn">2 commits ahead</b>' in page
        assert 'class="text-warn" title="The runner has not checked recently."' in page
        StudioRepo.objects.filter(pk=manual.pk).update(remote_checked_at=timezone.now())
        assert "has not checked recently" not in html(f"{prefix}/settings/repo")

    def test_mode_control_and_buttons_follow_the_mode(self, html, prefix, repo):
        page = html(f"{prefix}/settings/repo")
        assert 'name="publish_mode" value="auto" checked' in page
        assert "goes live as soon as the runner sees it" in page
        assert ">Sync now</button>" in page and "Review &amp; publish" not in page
        StudioRepo.objects.filter(pk=repo.pk).update(publish_mode="manual")
        page = html(f"{prefix}/settings/repo")
        assert 'name="publish_mode" value="manual" checked' in page
        assert "nothing goes live until you press Publish" in page
        assert ">Check for changes</button>" in page
        assert 'disabled title="Up to date">Up to date</button>' in page
        assert "<dialog" not in page

    def test_pending_chips_warnings_and_commits(self, html, prefix, manual):
        page = html(f"{prefix}/settings/repo")
        assert "<h2>Pending changes (2 commits)</h2>" in page
        for chip in (
            "1 report modified", "1 report added", "1 report removed",
            "1 data source added \u26a0", "1 data source changed", "metrics.yaml changed",
        ):
            assert chip in page, chip
        assert "data sources removed" not in page  # zero chips are omitted
        assert "Adds data source <code>warehouse</code>" in page
        assert f'href="{prefix}/settings/datasources?configure=warehouse">Configure now</a>' in page
        assert "limited to 5 reports" in page
        assert "Removes <code>funnel</code>" in page
        assert "<code>bbbbbbb</code>" in page and "Tighten churn cohorts" in page
        assert "<td>Ada</td>" in page and "ago</td>" in page
        assert 'title="reports/churn/report.yaml&#10;reports/churn/q.sql">2 files</td>' in page
        assert 'id="reviewBtn" data-remote="' + "b" * 40 + '"' in page

    def test_ahead_counts_the_total_not_the_capped_table(self, html, prefix, manual):
        commits = [dict(PENDING["commits"][0], sha=f"{i:040x}") for i in range(100)]
        StudioRepo.objects.filter(pk=manual.pk).update(
            pending_changes={**PENDING, "commits": commits, "commits_total": 150}
        )
        page = html(f"{prefix}/settings/repo")
        assert '<b class="text-warn">150 commits ahead</b>' in page
        assert "<h2>Pending changes (150 commits)</h2>" in page
        assert f"<h2>Publish 150 commits to {manual.studio.name}?</h2>" in page
        assert "Showing the latest 100 of 150 commits." in page
        StudioRepo.objects.filter(pk=manual.pk).update(pending_changes=PENDING)
        assert "Showing the latest" not in html(f"{prefix}/settings/repo")

    def test_malformed_commit_entries_still_render(self, html, prefix, manual):
        commits = [dict(PENDING["commits"][0], date=None), "not-a-commit", {"sha": "d" * 40}]
        StudioRepo.objects.filter(pk=manual.pk).update(
            pending_changes={**PENDING, "commits": commits}
        )
        page = html(f"{prefix}/settings/repo")
        assert "<h2>Pending changes (3 commits)</h2>" in page
        assert "<code>ddddddd</code>" in page

    def test_initial_and_rewritten_headings(self, html, prefix, manual):
        StudioRepo.objects.filter(pk=manual.pk).update(pending_changes={**PENDING, "initial": True})
        assert "<h2>Initial import</h2>" in html(f"{prefix}/settings/repo")
        StudioRepo.objects.filter(pk=manual.pk).update(pending_changes={**PENDING, "rewritten": True})
        assert "History was rewritten \u2014 showing the full contents of <code>main</code>" in html(
            f"{prefix}/settings/repo"
        )

    def test_empty_state_in_manual_mode(self, html, prefix, manual):
        StudioRepo.objects.filter(pk=manual.pk).update(pending_changes={})
        page = html(f"{prefix}/settings/repo")
        assert 'class="ui-empty"' in page
        assert "Nothing pending. <code>main</code> is published at" in page
        assert "<code>aaaaaaa</code>" in page

    def test_publish_dialog_and_publishing_state(self, html, prefix, manual):
        page = html(f"{prefix}/settings/repo")
        assert '<dialog class="ui-dialog" id="publishDialog">' in page
        assert f"<h2>Publish 2 commits to {manual.studio.name}?</h2>" in page
        assert "2 reports will be updated" in page
        assert "1 data source will need credentials" in page and "1 report removed" in page
        assert '<input type="checkbox" id="publishRebuild">' in page  # auto_run_changed off
        assert "Rebuild the 2 changed reports after publishing" in page
        assert 'id="modeConfirm"' in page and "publishes the 2 pending commits now" in page
        StudioRepo.objects.filter(pk=manual.pk).update(auto_run_changed=True, publish_requested=True)
        page = html(f"{prefix}/settings/repo")
        assert "disabled>Publishing\u2026</button>" in page
        assert "Publishing <code>bbbbbbb</code>\u2026" in page
        assert "<dialog" not in page

    def test_history_rows_statuses_and_expandable_commits(self, html, prefix, repo, org_admin):
        summary = {"commits": PENDING["commits"], "reports": PENDING["reports"]}
        RepoPublish.objects.create(studio=repo.studio, to_sha="1" * 40, trigger="initial")
        RepoPublish.objects.create(
            studio=repo.studio, from_sha="1" * 40, to_sha="2" * 40, trigger="webhook", summary=summary,
        )
        RepoPublish.objects.create(
            studio=repo.studio, from_sha="2" * 40, to_sha="3" * 40, trigger="auto", summary=summary,
        )
        RepoPublish.objects.create(
            studio=repo.studio, from_sha="3" * 40, to_sha="4" * 40, trigger="manual",
            published_by=org_admin, status="error", summary=summary,
            error="report.yaml invalid in churn \u2014 studio kept on 3333333",
        )
        page = html(f"{prefix}/settings/repo")
        assert "<h2>History</h2>" in page
        assert "\u2014 \u2192 1111111" in page and "3333333 \u2192 4444444" in page
        assert f"{org_admin.email} \u00b7 manual" in page
        assert "webhook \u00b7 auto" in page and "schedule \u00b7 auto" in page
        assert "initial</td>" in page
        assert '<span class="ui-badge ok">Published</span>' in page
        assert '<span class="ui-badge fail">Failed</span>' in page
        assert "report.yaml invalid in churn" in page
        assert "<details><summary>2 commits</summary>" in page
        assert "<code>ccccccc</code> Drop the legacy funnel" in page
        assert '<td class="num">3</td>' in page
        assert "Load more" not in page
        page = html(f"{prefix}/settings/repo?history=2")
        assert page.count("<details>") == 2  # the two newest rows: error + auto
        assert 'href="?history=32">Load more</a>' in page

    def test_load_more_stops_at_the_cap(self, html, prefix, repo):
        RepoPublish.objects.bulk_create(
            RepoPublish(studio=repo.studio, to_sha=f"{i:040x}", trigger="auto") for i in range(201)
        )
        assert 'href="?history=200">Load more</a>' in html(f"{prefix}/settings/repo?history=170")
        page = html(f"{prefix}/settings/repo?history=200")
        assert page.count("<tr>") == 201 and "Load more" not in page  # header row + 200
        assert "Load more" not in html(f"{prefix}/settings/repo?history=230")

    def test_mode_change_manual_to_auto_requests_a_publish(self, login, org_admin, prefix, manual):
        resp = login(org_admin).post(
            f"{prefix}/settings/repo", {"set_mode": "1", "publish_mode": "auto"}
        )
        assert resp.status_code == 302 and resp.url == f"{prefix}/settings/repo"
        manual.refresh_from_db()
        assert manual.publish_mode == "auto"
        assert manual.publish_requested is True and manual.publish_requested_by == org_admin
        assert manual.sync_requested is True and manual.sync_reason == "manual"

    def test_mode_change_saves_only_its_own_columns(
        self, login, org_admin, prefix, manual, monkeypatch
    ):
        original = StudioRepo.save

        def racing_save(self, *args, **kwargs):
            # The runner writes between the view's read and its save.
            StudioRepo.objects.filter(pk=self.pk).update(remote_sha="c" * 40, pending_changes={})
            return original(self, *args, **kwargs)

        monkeypatch.setattr(StudioRepo, "save", racing_save)
        resp = login(org_admin).post(
            f"{prefix}/settings/repo", {"set_mode": "1", "publish_mode": "auto"}
        )
        assert resp.status_code == 302
        manual.refresh_from_db()
        assert manual.publish_mode == "auto" and manual.publish_requested is True
        assert manual.remote_sha == "c" * 40 and manual.pending_changes == {}

    def test_mode_change_to_manual_and_bad_values(self, login, org_admin, prefix, repo):
        c = login(org_admin)
        url = f"{prefix}/settings/repo"
        assert c.post(url, {"set_mode": "1", "publish_mode": "manual"}).status_code == 302
        repo.refresh_from_db()
        assert repo.publish_mode == "manual" and repo.publish_requested is False
        assert c.post(url, {"set_mode": "1", "publish_mode": "yolo"}).status_code == 302
        repo.refresh_from_db()
        assert repo.publish_mode == "manual"

    def test_developer_cannot_change_the_mode(
        self, login, make_user, org, studio_tree, grant_studio, prefix, manual
    ):
        dev = make_user("dev@demo.example", org=org)
        grant_studio(dev, studio_tree, roles.DEVELOPER)
        resp = login(dev).post(f"{prefix}/settings/repo", {"set_mode": "1", "publish_mode": "auto"})
        assert resp.status_code == 403
        manual.refresh_from_db()
        assert manual.publish_mode == "manual"


class TestDataSourceScreen:
    """One row per source state, each with its badge and the actions that
    state calls for (internal planning ticket #128)."""

    def test_every_state_has_its_badge_and_actions(self, html, prefix, studio_tree, repo):
        declare(studio_tree, "warehouse", host="db")                       # needs credentials
        declare(studio_tree, "budget", type="file", upload=True)            # needs upload
        declare(studio_tree, "crm", host="crm")                             # connected
        DataSource.objects.create(
            studio=studio_tree, name="crm", type="postgres",
            credentials={"user": "u", "password": "p"},
            last_check_at=timezone.now() - timedelta(minutes=4), last_check_ok=True,
        )
        declare(studio_tree, "events", host="ev")                           # failing
        DataSource.objects.create(
            studio=studio_tree, name="events", type="postgres",
            credentials={"user": "u", "password": "p"},
            last_check_at=timezone.now() - timedelta(hours=2), last_check_ok=False,
            last_check_error="OperationalError: connection refused by db.internal",
        )
        portal_only = DataSource.objects.create(                            # not in repo
            studio=studio_tree, name="scratch", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
        )
        page = html(f"{prefix}/settings/datasources")

        assert '<span class="ui-badge fail">Needs credentials</span>' in page
        assert "user, password" in page
        assert '<span class="ui-badge warn">Needs upload</span>' in page
        assert "upload allowed" in page
        assert '<span class="ui-badge ok">Connected</span>' in page
        assert "checked 4" in page and "minutes ago" in page
        assert '<span class="ui-badge fail">Failing</span>' in page
        assert "connection refused" in page and "since 2" in page
        assert '<span class="ui-badge ">Not in repository</span>' in page
        assert "portal-only" in page
        assert 'class="ui-chip acc">from repository' in page

        assert 'class="ui-btn ghost" href="?configure=warehouse#configure">Configure' in page
        assert 'href="?configure=crm#configure">Edit credentials' in page
        # The upload row's primary action is the file picker itself.
        assert f'data-url="{prefix}/api/datasources/budget/upload"' in page
        # Only the portal-only source can be deleted; declared ones cannot.
        assert page.count('class="ui-btn danger"') == 1
        assert f'href="?edit={portal_only.pk}"' in page
        assert "Repo-declared fields (type, host, port, database, path) are read-only" in page
        assert "Declared by your repository" in page

    def test_banner_counts_sources_and_waiting_reports(self, html, prefix, studio_tree):
        declare(studio_tree, "warehouse", host="db")
        declare(studio_tree, "budget", type="file", upload=True)
        report_using(studio_tree, "sales", "warehouse")
        report_using(studio_tree, "costs", "warehouse", "budget")
        report_using(studio_tree, "static")
        page = html(f"{prefix}/settings/datasources")
        assert "2 data sources still\n      need credentials" in page
        assert "2 reports\n    are waiting for them" in page
        assert "Configure warehouse" in page and "Upload budget" in page

    def test_banner_says_file_when_only_uploads_are_missing(self, html, prefix, studio_tree):
        declare(studio_tree, "budget", type="file", upload=True)
        page = html(f"{prefix}/settings/datasources")
        assert "1 data source still\n      needs a file" in page

    def test_configure_form_shows_the_declaration_read_only(self, html, prefix, studio_tree):
        declare(studio_tree, "warehouse", host="db.internal", port=5432, database="core")
        report_using(studio_tree, "sales", "warehouse")
        page = html(f"{prefix}/settings/datasources?configure=warehouse")
        assert "Configure <code>warehouse</code>" in page
        assert "db.internal" in page and "5432" in page and "core" in page
        assert "<code>data-sources/config.yaml</code>" in page
        assert "sales" in page
        form = page.split('id="ds-configure"', 1)[1].split("</form>", 1)[0]
        assert 'name="user"' in form and 'name="password"' in form
        assert 'name="host"' not in form and 'name="database"' not in form
        assert "Save and test" in form
        assert 'name="org_level"' in form  # org admin
        assert 'class="ui-help org"' in form

    def test_configure_form_for_a_sheet_asks_only_for_the_key(
        self, html, login, org_admin, prefix, studio_tree, monkeypatch
    ):
        """A declared spreadsheet connects on its own (ADC or a key file on
        the worker), and Configure adds the key inline; the declaration's
        path and credentials_path stay read-only."""
        from apps.datasources import testing

        declare(studio_tree, "sheets", type="google_sheets", path="https://docs.google.com/spreadsheets/d/x")
        page = html(f"{prefix}/settings/datasources?configure=sheets")
        assert "declared in repository" in page
        form = page.split('id="ds-configure"', 1)[1].split("</form>", 1)[0]
        assert 'name="credentials_json"' in form
        assert 'name="credentials_path"' not in form and 'name="path"' not in form
        monkeypatch.setattr(testing, "test_datasource", lambda ds: (True, "Opened the spreadsheet."))
        login(org_admin).post(
            f"{prefix}/settings/datasources",
            {"action": "configure", "name": "sheets", "credentials_json": '{"type": "service_account"}'},
        )
        ds = DataSource.objects.get(studio=studio_tree, name="sheets")
        assert ds.type == "google_sheets" and ds.config == {}
        assert ds.credentials == {"credentials_json": '{"type": "service_account"}'}

    def test_configure_saves_studio_credentials_and_tests_them(
        self, login, org_admin, prefix, studio_tree, monkeypatch
    ):
        from apps.datasources import testing

        declare(studio_tree, "warehouse", host="db.internal")
        report = report_using(studio_tree, "sales", "warehouse")
        monkeypatch.setattr(testing, "test_datasource", lambda ds: (True, "Connected successfully."))
        client = login(org_admin)
        resp = client.post(
            f"{prefix}/settings/datasources",
            {"action": "configure", "name": "warehouse", "user": "svc", "password": "pw"},
        )
        assert resp.status_code == 302
        ds = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert ds.type == "postgres" and ds.config == {}
        assert ds.credentials == {"user": "svc", "password": "pw"}
        assert ds.last_check_ok is True
        page = client.get(f"{prefix}/settings/datasources").content.decode()
        assert "connected. 1 waiting report has been queued to build." in page
        assert report.runs.filter(status="queued").exists()

    def test_configure_reports_a_failed_test(self, login, org_admin, prefix, studio_tree, monkeypatch):
        from apps.datasources import testing

        declare(studio_tree, "warehouse", host="db.internal")
        monkeypatch.setattr(testing, "test_datasource", lambda ds: (False, "refused"))
        client = login(org_admin)
        client.post(
            f"{prefix}/settings/datasources",
            {"action": "configure", "name": "warehouse", "user": "svc", "password": "pw"},
        )
        page = client.get(f"{prefix}/settings/datasources").content.decode()
        assert "saved but the connection test failed: refused" in page
        assert '<span class="ui-badge fail">Failing</span>' in page

    def test_org_level_checkbox_saves_a_shared_row(self, login, org_admin, org, prefix, studio_tree, monkeypatch):
        from apps.datasources import testing

        declare(studio_tree, "warehouse", host="db.internal")
        monkeypatch.setattr(testing, "test_datasource", lambda ds: (True, "ok"))
        login(org_admin).post(
            f"{prefix}/settings/datasources",
            {"action": "configure", "name": "warehouse", "user": "svc", "password": "pw", "org_level": "1"},
        )
        ds = DataSource.objects.get(name="warehouse")
        assert ds.org == org and ds.studio is None

    def test_blank_password_keeps_the_stored_one(self, login, org_admin, prefix, studio_tree, monkeypatch):
        from apps.datasources import testing

        declare(studio_tree, "warehouse", host="db.internal")
        DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            credentials={"user": "old", "password": "keep"},
        )
        monkeypatch.setattr(testing, "test_datasource", lambda ds: (True, "ok"))
        login(org_admin).post(
            f"{prefix}/settings/datasources",
            {"action": "configure", "name": "warehouse", "user": "new", "password": ""},
        )
        ds = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert ds.credentials == {"user": "new", "password": "keep"}

    def test_remove_credentials_deletes_the_studio_binding(self, login, org_admin, prefix, studio_tree):
        declare(studio_tree, "warehouse", host="db.internal")
        DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        login(org_admin).post(
            f"{prefix}/settings/datasources",
            {"action": "remove_credentials", "name": "warehouse"},
        )
        assert not DataSource.objects.filter(name="warehouse").exists()

    def test_configure_of_an_undeclared_name_opens_the_portal_only_form(self, html, prefix, studio_tree):
        report_using(studio_tree, "sales", "legacy")
        page = html(f"{prefix}/settings/datasources?configure=legacy")
        assert "Configure <code>" not in page
        assert '<details class="ui-panel" open>' in page
        assert 'value="legacy"' in page

    def test_referenced_but_not_declared_lists_the_reports(self, html, prefix, studio_tree):
        report_using(studio_tree, "sales", "legacy")
        report_using(studio_tree, "costs", "legacy")
        page = html(f"{prefix}/settings/datasources")
        assert "Referenced but not declared" in page
        assert "<code>legacy</code> — referenced by costs, sales" in page
        assert "Declare these in <code>data-sources/config.yaml</code>" in page
        # Not a row in the main table.
        assert 'id="ds-row-legacy"' not in page

    def test_portal_only_form_is_collapsed_unless_there_is_no_repo(self, html, prefix, studio_tree, repo):
        page = html(f"{prefix}/settings/datasources")
        assert '<details class="ui-panel" >' in page
        assert "Add a portal-only source (advanced)" in page
        repo.delete()
        page = html(f"{prefix}/settings/datasources")
        assert '<details class="ui-panel" open>' in page
        assert "Connect a" in page and "repository</a> to declare" in page

    def test_inline_declaration_names_its_report_file(self, html, prefix, studio_tree):
        declare(studio_tree, "extract", source_file="reports/sales/report.yaml", host="h")
        page = html(f"{prefix}/settings/datasources")
        assert 'class="ui-chip">declared in sales/report.yaml' in page

    def test_org_page_counts_declaring_studios(self, html, org, studio_tree, studio2):
        DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        DataSource.objects.create(org=org, name="unused", type="postgres")
        declare(studio_tree, "warehouse", host="db")
        declare(studio2, "warehouse", host="db")
        page = html(f"/orgs/{org.slug}/settings/datasources")
        assert "Declared by 2 studios" in page
        assert "Not declared by any studio" in page
        assert "Add shared credentials" in page
        assert "Studios that declare a source with this name will use these credentials." in page
        assert '<details class="ui-panel"' not in page

    def test_js_contract_survives_the_restyle(self, html, prefix, studio_tree):
        ds = DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres", config={"host": "h"},
        )
        page = html(f"{prefix}/settings/datasources")
        assert f'id="test-result-{ds.pk}"' in page
        assert f"testSource({ds.pk}, 'warehouse', this)" in page
        assert 'data-f="host"' in page and 'id="ds-form"' in page
        assert f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources/" in page

    def test_empty_state(self, html, prefix):
        assert "ui-empty" in html(f"{prefix}/settings/datasources")


class TestStudioMembersScreen:
    def test_remove_is_guarded_and_role_is_set_explicitly(
        self, html, prefix, member, studio_tree, grant_studio
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        page = html(f"{prefix}/settings/members")
        assert re.search(r'<table\b[^>]*\bclass="[^"]*\bui-table\b[^"]*"', page)
        assert "return confirm(" in page
        # Scoped to the page content: the shell's own Appearance theme select
        # (unconditional on every page, studio-theming-design.md §8) submits
        # inline on change by design -- a different, deliberate control.
        main = re.search(r"<main\b[^>]*>(.*?)</main>", page, re.DOTALL)
        assert main is not None
        main = main.group(1)
        assert "this.form.submit()" not in main  # explicit Set button, like org members


class TestSettingsHub:
    def test_studio_settings_redirects_to_repo(self, login, org_admin, prefix):
        resp = login(org_admin).get(f"{prefix}/settings/")
        assert resp.status_code == 302
        assert resp.url == f"{prefix}/settings/repo"


class TestConfigureAgainstSharedCredentials:
    """A studio row shadows the organization's: creating one must not lose
    what the studio has been connecting with, and overwriting the shared
    row must be a knowing choice."""

    def test_blank_fields_keep_the_shared_credentials(
        self, login, org_admin, org, prefix, studio_tree, monkeypatch
    ):
        from apps.datasources import testing

        declare(studio_tree, "warehouse", host="db.internal")
        DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            credentials={"user": "shared", "password": "sp"},
        )
        monkeypatch.setattr(testing, "test_datasource", lambda ds: (True, "ok"))
        login(org_admin).post(
            f"{prefix}/settings/datasources",
            {"action": "configure", "name": "warehouse", "user": "", "password": ""},
        )
        studio_row = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert studio_row.credentials == {"user": "shared", "password": "sp"}
        assert DataSource.objects.get(org=org, name="warehouse").credentials == {
            "user": "shared", "password": "sp"
        }

    def test_warns_before_overwriting_shared_credentials_the_form_does_not_show(
        self, html, org, prefix, studio_tree
    ):
        declare(studio_tree, "warehouse", host="db.internal")
        DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            credentials={"user": "shared", "password": "sp"},
        )
        warning = "Replaces the shared credentials used by other studios."
        # The org row is what the form shows: nothing hidden to warn about.
        assert warning not in html(f"{prefix}/settings/datasources?configure=warehouse")
        DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            credentials={"user": "mine", "password": "mp"},
        )
        page = html(f"{prefix}/settings/datasources?configure=warehouse")
        assert warning in page
        assert 'class="ui-help warn"' in page
