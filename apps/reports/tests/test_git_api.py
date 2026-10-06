"""Git sync web API: status, sync-now flag, HMAC webhook."""
import hashlib
import hmac

import pytest

from apps.core import roles
from apps.studios.models import StudioRepo

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


@pytest.fixture
def repo(studio_tree):
    return StudioRepo.objects.create(
        studio=studio_tree,
        repo_url="https://github.com/demo/reports.git",
        webhook_secret="hook-secret",
    )


class TestStatusAndSyncNow:
    def test_status_configured(self, login, developer, prefix, repo, report_row):
        body = login(developer).get(f"{prefix}/api/system/git/status").json()
        assert body["configured"] is True
        assert body["repo"] == repo.repo_url

    def test_sync_now_sets_flag(self, login, developer, prefix, repo, report_row):
        body = login(developer).post(f"{prefix}/api/system/git/sync").json()
        assert body["ok"] is True
        repo.refresh_from_db()
        assert repo.sync_requested is True

    def test_sync_now_unconfigured_400(self, login, developer, prefix, report_row):
        resp = login(developer).post(f"{prefix}/api/system/git/sync")
        assert resp.status_code == 400

    def test_viewer_cannot_trigger_sync(
        self, login, make_user, org, studio_tree, grant_studio, prefix, repo, report_row
    ):
        viewer = make_user("v@demo.example", org=org)
        grant_studio(viewer, studio_tree, roles.VIEWER)
        assert login(viewer).post(f"{prefix}/api/system/git/sync").status_code == 403


class TestWebhook:
    def _sign(self, body: bytes, secret: str = "hook-secret") -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_valid_signature_schedules_sync(self, client, prefix, repo):
        body = b'{"ref": "refs/heads/main"}'
        resp = client.post(
            f"{prefix}/api/git/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": self._sign(body)},
        )
        assert resp.status_code == 200
        repo.refresh_from_db()
        assert repo.sync_requested is True
        assert repo.sync_reason == "webhook"  # fetch-only in manual mode

    def test_bad_signature_403(self, client, prefix, repo):
        resp = client.post(
            f"{prefix}/api/git/webhook",
            data=b"{}",
            content_type="application/json",
            headers={"X-Hub-Signature-256": self._sign(b"other-body")},
        )
        assert resp.status_code == 403
        repo.refresh_from_db()
        assert repo.sync_requested is False

    def test_missing_secret_404(self, client, prefix, studio_tree):
        StudioRepo.objects.create(studio=studio_tree, repo_url="https://x.test/r.git")
        resp = client.post(
            f"{prefix}/api/git/webhook",
            data=b"{}",
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=00"},
        )
        assert resp.status_code == 404

    def test_get_rejected(self, client, prefix, repo):
        assert client.get(f"{prefix}/api/git/webhook").status_code == 405


class TestRepoSettingsPage:
    def test_admin_saves_config_and_secret_kept_on_blank(
        self, login, org_admin, org, studio_tree, repo
    ):
        c = login(org_admin)
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/repo"
        assert c.get(url).status_code == 200
        resp = c.post(
            url,
            {
                "repo_url": "https://github.com/demo/new.git",
                "branch": "main",
                "path": "reports",
                "auth_method": "https_token",
                "token": "",  # blank -> keep stored
                "sync_interval_minutes": 10,
                "publish_mode": "auto",
                "webhook_secret": "",
            },
        )
        assert resp.status_code == 302
        repo.refresh_from_db()
        assert repo.repo_url == "https://github.com/demo/new.git"
        assert repo.webhook_secret == "hook-secret"  # preserved
        assert repo.sync_requested is True  # saving schedules a sync
        assert repo.sync_reason == "manual"
        assert repo.publish_requested is False

    def test_switching_manual_to_auto_requests_a_publish(
        self, login, org_admin, org, studio_tree, repo
    ):
        StudioRepo.objects.filter(pk=repo.pk).update(publish_mode="manual")
        resp = login(org_admin).post(
            f"/s/{org.slug}/{studio_tree.slug}/settings/repo",
            {
                "repo_url": repo.repo_url, "branch": "main", "path": "reports",
                "auth_method": "none", "token": "", "sync_interval_minutes": 0,
                "publish_mode": "auto", "webhook_secret": "",
            },
        )
        assert resp.status_code == 302
        repo.refresh_from_db()
        assert repo.publish_mode == "auto"
        assert repo.publish_requested is True
        assert repo.publish_requested_by == org_admin

    def test_developer_cannot_open_repo_settings(
        self, login, make_user, org, studio_tree, grant_studio
    ):
        dev = make_user("d2@demo.example", org=org)
        grant_studio(dev, studio_tree, roles.DEVELOPER)
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/repo"
        assert login(dev).get(url).status_code == 403


class TestPublishApi:
    def test_check_schedules_a_fetch(self, login, developer, prefix, repo, report_row):
        resp = login(developer).post(f"{prefix}/api/system/git/check")
        assert resp.status_code == 202 and resp.json()["scheduled"] is True
        repo.refresh_from_db()
        assert repo.sync_requested is True and repo.sync_reason == "manual"
        assert repo.publish_requested is False

    def test_publish_requests_a_publish_with_a_rebuild_override(
        self, login, developer, prefix, repo, report_row
    ):
        resp = login(developer).post(
            f"{prefix}/api/system/git/publish", data='{"rebuild": true, "to": "%s"}' % ("b" * 40),
            content_type="application/json",
        )
        assert resp.status_code == 202 and resp.json()["scheduled"] is True
        repo.refresh_from_db()
        assert repo.publish_requested is True and repo.publish_requested_by == developer
        assert repo.sync_requested is True and repo.sync_reason == "manual"
        assert repo.publish_rebuild_override is True
        assert repo.publish_requested_to == "b" * 40

    def test_publish_without_a_body_leaves_the_override_alone(
        self, login, developer, prefix, repo, report_row
    ):
        assert login(developer).post(f"{prefix}/api/system/git/publish").status_code == 202
        repo.refresh_from_db()
        assert repo.publish_requested is True and repo.publish_rebuild_override is None
        assert repo.publish_requested_to == ""  # unpinned: publishes whatever is pending

    def test_publish_unconfigured_400(self, login, developer, prefix, report_row):
        assert login(developer).post(f"{prefix}/api/system/git/publish").status_code == 400

    def test_viewer_is_refused(
        self, login, make_user, org, studio_tree, grant_studio, prefix, repo, report_row
    ):
        viewer = make_user("v@demo.example", org=org)
        grant_studio(viewer, studio_tree, roles.VIEWER)
        c = login(viewer)
        assert c.post(f"{prefix}/api/system/git/check").status_code == 403
        assert c.post(f"{prefix}/api/system/git/publish").status_code == 403
        assert c.get(f"{prefix}/api/system/git/history").status_code == 403

    def test_status_carries_the_publish_fields(self, login, developer, prefix, repo, report_row):
        pending = {"from": "a" * 40, "to": "b" * 40, "reports": {"added": ["x"]}}
        StudioRepo.objects.filter(pk=repo.pk).update(
            publish_mode="manual", remote_sha="b" * 40, pending_changes=pending,
            publish_requested=True,
        )
        body = login(developer).get(f"{prefix}/api/system/git/status").json()
        assert body["publish_mode"] == "manual" and body["remote_sha"] == "b" * 40
        assert body["pending"] == pending and body["publishing"] is True
        assert body["remote_checked_at"] is None

    def test_status_pending_is_null_when_up_to_date(self, login, developer, prefix, repo, report_row):
        body = login(developer).get(f"{prefix}/api/system/git/status").json()
        assert body["pending"] is None and body["publishing"] is False

    def test_history_shape(self, login, developer, prefix, repo, studio_tree, report_row):
        from apps.studios.models import RepoPublish

        summary = {
            "commits": [{"sha": "c" * 40}], "reports": {"added": ["a", "b"], "removed": ["z"]},
            "datasources": {"added": ["w"]}, "root_files": ["events.yaml"], "warnings": [],
        }
        for i in range(6):
            RepoPublish.objects.create(
                studio=studio_tree, from_sha="a" * 40, to_sha=f"{i}" * 40, trigger="auto",
                summary=summary, published_by=developer if i == 5 else None,
            )
        RepoPublish.objects.create(
            studio=studio_tree, to_sha="e" * 40, trigger="manual", status="error",
            error="report.yaml invalid: bad", summary=summary,
        )
        rows = login(developer).get(f"{prefix}/api/system/git/history?limit=3").json()["publishes"]
        assert len(rows) == 3
        rows = login(developer).get(f"{prefix}/api/system/git/history").json()["publishes"]
        assert len(rows) == 7
        newest = rows[0]
        assert newest["status"] == "error" and newest["error"] == "report.yaml invalid: bad"
        assert newest["trigger"] == "manual" and newest["from_sha"] == ""
        assert newest["counts"] == {
            "commits": 1, "reports": {"added": 2, "removed": 1}, "datasources": {"added": 1},
            "root_files": 1, "warnings": 0,
        }
        assert newest["summary"] == summary and newest["published_at"]
        assert rows[1]["published_by"] == developer.email and rows[2]["published_by"] is None
        assert all(r["summary"] == summary for r in rows[:5])
        assert all(r["summary"] is None for r in rows[5:])
