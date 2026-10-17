"""Durable registry-refresh actions: admission, status route, worker, leases.

Real PostgreSQL only: admission relies on advisory locks and partial unique
indexes, and the race test uses separate connections.
"""
import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connections
from django.test import Client
from django.utils import timezone

from apps.core import roles
from apps.core.models import AuditLog
from apps.runner import maintenance_actions as maint
from apps.runner.models import StudioMaintenanceAction as Action

pytestmark = pytest.mark.django_db(transaction=True)


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


def post(client, prefix, **headers):
    return client.post(f"{prefix}/api/system/registry/refresh", **headers)


class TestAdmission:
    def test_returns_202_with_operation_and_status_url(self, login, developer, prefix):
        resp = post(login(developer), prefix)
        body = resp.json()
        assert resp.status_code == 202
        assert body["state"] == "accepted" and body["scheduled"] is True
        assert Action.objects.get(pk=body["operation_id"]).state == "accepted"
        assert body["status_url"] == f"{prefix}/api/system/actions/{body['operation_id']}"
        assert "reports" not in body  # never claims a result on 202
        assert resp["Cache-Control"] == "no-store"

    def test_concurrent_posts_create_one_action(self, make_user, org, studio_tree, grant_studio):
        users = []
        for i in range(2):
            u = make_user(f"racer{i}@demo.example", org=org)
            grant_studio(u, studio_tree, roles.DEVELOPER)
            users.append(u)
        prefix = f"/s/{org.slug}/{studio_tree.slug}"
        barrier = threading.Barrier(2)
        results, errors = [], []

        def go(user):
            try:
                c = Client()
                assert c.login(username=user.email, password="pw-Str0ng-pw")
                barrier.wait(timeout=10)
                r = post(c, prefix)
                results.append((r.status_code, r.json()["operation_id"]))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=go, args=(u,)) for u in users]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        assert not errors
        assert [c for c, _ in results] == [202, 202]
        assert len({i for _, i in results}) == 1
        assert Action.objects.count() == 1

    def test_duplicate_returns_existing_without_new_row(self, login, developer, prefix):
        first = post(login(developer), prefix).json()
        again = post(login(developer), prefix)
        assert again.status_code == 202
        assert again.json()["operation_id"] == first["operation_id"]
        assert again.json()["duplicate"] is True
        assert Action.objects.count() == 1

    def test_idempotency_key_scoped_to_actor(self, login, make_user, org, studio_tree, grant_studio, developer, prefix):
        other = make_user("dev2@demo.example", org=org)
        grant_studio(other, studio_tree, roles.DEVELOPER)
        first = post(login(developer), prefix, HTTP_IDEMPOTENCY_KEY="k1").json()
        maint.run_once("w")  # first reaches a terminal state
        same = post(login(developer), prefix, HTTP_IDEMPOTENCY_KEY="k1").json()
        assert same["operation_id"] == first["operation_id"]
        assert same["state"] == "succeeded"
        c2 = Client()
        c2.login(username=other.email, password="pw-Str0ng-pw")
        new = post(c2, prefix, HTTP_IDEMPOTENCY_KEY="k1").json()
        assert new["operation_id"] != first["operation_id"]

    def test_invalid_key_rejected(self, login, developer, prefix):
        assert post(login(developer), prefix, HTTP_IDEMPOTENCY_KEY="bad key!").status_code == 400

    def test_pending_limit_returns_429(self, settings, login, org_admin, studio_tree, studio2, org):
        settings.TRELLUM_MAINT_ORG_PENDING_LIMIT = 1
        c = login(org_admin)
        assert post(c, f"/s/{org.slug}/{studio_tree.slug}").status_code == 202
        resp = post(c, f"/s/{org.slug}/{studio2.slug}")
        assert resp.status_code == 429
        assert resp["Retry-After"] and resp.json()["ok"] is False
        assert Action.objects.count() == 1

    def test_fleet_limit_returns_429(self, settings, login, org_admin, studio_tree, studio2, org):
        settings.TRELLUM_MAINT_FLEET_PENDING_LIMIT = 1
        c = login(org_admin)
        assert post(c, f"/s/{org.slug}/{studio_tree.slug}").status_code == 202
        assert post(c, f"/s/{org.slug}/{studio2.slug}").status_code == 429

    def test_viewer_denied_and_csrf_enforced(self, login, viewer, developer, prefix):
        assert post(login(viewer), prefix).status_code == 403
        strict = Client(enforce_csrf_checks=True)
        strict.login(username=developer.email, password="pw-Str0ng-pw")
        assert post(strict, prefix).status_code == 403
        assert Action.objects.count() == 0

    def test_admission_is_audited_without_paths(self, login, developer, prefix):
        post(login(developer), prefix)
        row = AuditLog.objects.get(action="registry.refresh_requested")
        assert row.metadata["duplicate"] is False


class TestStatusRoute:
    def action_url(self, client, prefix):
        return post(client, prefix).json()["status_url"]

    def test_developer_reads_status_no_store(self, login, developer, prefix):
        c = login(developer)
        url = self.action_url(c, prefix)
        resp = c.get(url)
        assert resp.status_code == 200 and resp["Cache-Control"] == "no-store"
        assert resp.json()["state"] == "accepted" and resp.json()["terminal"] is False

    def test_viewer_forbidden(self, login, developer, viewer, prefix):
        url = self.action_url(login(developer), prefix)
        assert login(viewer).get(url).status_code == 403

    def test_anonymous_cannot_read(self, login, developer, prefix):
        url = self.action_url(login(developer), prefix)
        assert Client().get(url).status_code in (302, 401, 403)

    def test_unknown_uuid_404(self, login, developer, prefix):
        assert login(developer).get(f"{prefix}/api/system/actions/{uuid.uuid4()}").status_code == 404

    def test_cross_studio_same_org_denied(self, login, org_admin, studio_tree, studio2, org, prefix):
        # org admin is developer+ on both studios, yet an id never resolves
        # under a different studio's URL.
        c = login(org_admin)
        url = self.action_url(c, prefix)
        action_id = url.rsplit("/", 1)[1]
        assert c.get(f"/s/{org.slug}/{studio2.slug}/api/system/actions/{action_id}").status_code == 404

    def test_cross_org_denied(self, login, developer, make_user, other_org, other_studio, prefix, grant_studio):
        url = self.action_url(login(developer), prefix)
        action_id = url.rsplit("/", 1)[1]
        outsider = make_user("out@rival.example", org=other_org)
        grant_studio(outsider, other_studio, roles.DEVELOPER)
        c = login(outsider)
        assert c.get(url).status_code == 404
        assert c.get(f"/s/{other_org.slug}/{other_studio.slug}/api/system/actions/{action_id}").status_code == 404


def age(action, by=maint.ACCEPTED_TTL + timedelta(seconds=1)):
    """Make an admitted action look older, as if no runner had served its pool."""
    Action.objects.filter(pk=action.pk).update(accepted_at=timezone.now() - by)


class TestUnclaimedExpiry:
    """An action no runner claims ends instead of holding its studio forever."""

    def admit(self, studio, user):
        return maint.admit(studio=studio, user=user)[0]

    def test_a_young_action_is_kept_and_answers_a_repeat_request(self, login, developer, prefix):
        first = post(login(developer), prefix).json()
        age(Action.objects.get(pk=first["operation_id"]), maint.ACCEPTED_TTL - timedelta(seconds=30))
        again = post(login(developer), prefix).json()
        assert again["operation_id"] == first["operation_id"] and again["duplicate"] is True
        assert again["state"] == "accepted" and again["age_seconds"] >= maint.ACCEPTED_TTL.total_seconds() - 30
        assert maint.recover_expired() == 0

    def test_admission_expires_a_stuck_action_and_admits_a_new_one(self, login, developer, prefix):
        first = post(login(developer), prefix).json()
        age(Action.objects.get(pk=first["operation_id"]))
        again = post(login(developer), prefix).json()
        assert again["operation_id"] != first["operation_id"] and again["duplicate"] is False
        assert again["state"] == "accepted" and again["age_seconds"] < 5
        old = Action.objects.get(pk=first["operation_id"])
        assert (old.state, old.message) == ("failed", maint.MSG_EXPIRED) and old.finished_at
        assert old.is_terminal and old.started_at is None and old.worker_id == ""
        out = AuditLog.objects.get(action="registry.refresh_finished")
        assert out.outcome == "failure" and out.metadata["reason"] == "unclaimed"
        assert out.metadata["action_id"] == first["operation_id"] and out.org_id == old.org_id

    def test_recovery_expires_it_and_it_is_not_claimable(self, developer, studio_tree):
        a = self.admit(studio_tree, developer)
        age(a)
        assert maint.claim_one("w") is None  # even before recovery has run
        assert maint.recover_expired() == 1
        a.refresh_from_db()
        assert (a.state, a.message) == ("failed", maint.MSG_EXPIRED)
        assert maint.recover_expired() == 0  # once
        assert AuditLog.objects.filter(action="registry.refresh_finished").count() == 1
        assert maint.run_once("w") is False

    def test_the_tick_expires_it_when_this_runner_does_not_serve_the_pool(self, developer, studio_tree):
        a = self.admit(studio_tree, developer)
        age(a)
        assert maint.run_once("w", pools=["elsewhere"]) is False
        a.refresh_from_db()
        assert a.state == "failed"

    def test_a_running_action_is_never_expired_by_age(self, developer, studio_tree):
        a = self.admit(studio_tree, developer)
        claimed = maint.claim_one("w")
        age(claimed, timedelta(hours=1))
        assert maint.recover_expired() == 0
        a.refresh_from_db()
        assert a.state == "running"

    def test_expired_actions_no_longer_count_toward_the_pending_limits(
            self, settings, login, org_admin, studio_tree, studio2, org):
        settings.TRELLUM_MAINT_ORG_PENDING_LIMIT = 1
        c = login(org_admin)
        first = post(c, f"/s/{org.slug}/{studio_tree.slug}").json()
        assert post(c, f"/s/{org.slug}/{studio2.slug}").status_code == 429
        age(Action.objects.get(pk=first["operation_id"]))
        assert post(c, f"/s/{org.slug}/{studio2.slug}").status_code == 202

    def test_a_status_read_reports_the_expiry_without_any_runner(self, login, developer, prefix):
        c = login(developer)
        first = post(c, prefix).json()
        age(Action.objects.get(pk=first["operation_id"]))
        body = c.get(first["status_url"]).json()
        assert (body["state"], body["terminal"], body["ok"], body["message"]) == (
            "failed", True, False, maint.MSG_EXPIRED)
        assert AuditLog.objects.filter(action="registry.refresh_finished").count() == 1
        assert c.get(first["status_url"]).json()["state"] == "failed"
        assert AuditLog.objects.filter(action="registry.refresh_finished").count() == 1

    def test_an_expired_action_still_answers_its_idempotency_key(self, login, developer, prefix):
        c = login(developer)
        first = post(c, prefix, HTTP_IDEMPOTENCY_KEY="k1").json()
        age(Action.objects.get(pk=first["operation_id"]))
        retry = post(c, prefix, HTTP_IDEMPOTENCY_KEY="k1").json()
        assert retry["operation_id"] == first["operation_id"] and retry["state"] == "failed"
        fresh = post(c, prefix, HTTP_IDEMPOTENCY_KEY="k2").json()
        assert fresh["state"] == "accepted" and fresh["operation_id"] != first["operation_id"]


class TestWorker:
    def admit(self, studio, user):
        return maint.admit(studio=studio, user=user)[0]

    def run(self):
        action = maint.claim_one("w1")
        assert action is not None
        return action, maint.execute(action, renew_every=0.05)

    def test_success(self, developer, studio_tree, write_report):
        write_report("alpha")
        write_report("beta")
        a = self.admit(studio_tree, developer)
        claimed, state = self.run()
        a.refresh_from_db()
        assert state == "succeeded" == a.state
        assert (a.reports, a.warning, a.lease_token) == (2, "", None)
        assert a.started_at and a.finished_at and a.worker_id == "w1"
        out = AuditLog.objects.get(action="registry.refresh_finished")
        assert out.outcome == "success" and out.metadata["reports"] == 2

    def test_quota_overage_is_success_with_warning(self, developer, studio_tree, write_report, monkeypatch):
        from apps.reports import scan

        write_report("alpha")
        scan.sync_studio_registry(studio_tree)

        def over(studio):
            raise scan.ReportQuotaExceeded("1 report(s) were not registered: limited to 1 reports.")

        monkeypatch.setattr(scan, "sync_studio_registry", over)
        a = self.admit(studio_tree, developer)
        _, state = self.run()
        a.refresh_from_db()
        assert state == "succeeded" and a.reports == 1
        assert "not registered" in a.warning and a.message == maint.MSG_WARNING

    def test_failure_hides_exception_text_and_paths(self, developer, studio_tree, monkeypatch):
        from apps.reports import scan

        def boom(studio):
            raise RuntimeError("/srv/secret/path exploded")

        monkeypatch.setattr(scan, "sync_studio_registry", boom)
        a = self.admit(studio_tree, developer)
        _, state = self.run()
        a.refresh_from_db()
        assert state == "failed" and a.message == maint.MSG_FAILED
        out = AuditLog.objects.get(action="registry.refresh_finished")
        assert out.outcome == "failure"
        assert "secret" not in str(out.metadata) and "secret" not in a.message

    def test_lock_conflict_fails_without_scanning(self, developer, studio_tree, monkeypatch):
        import psycopg

        from apps.reports import scan
        from apps.runner.gitsync import GITSYNC_LOCK_NAMESPACE

        calls = []
        monkeypatch.setattr(scan, "sync_studio_registry", lambda s: calls.append(s) or 0)
        a = self.admit(studio_tree, developer)
        params = connections["default"].get_connection_params()
        for k in ("cursor_factory", "context"):
            params.pop(k, None)
        with psycopg.connect(**params) as other, other.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", [GITSYNC_LOCK_NAMESPACE, studio_tree.pk])
            assert cur.fetchone()[0] is True
            _, state = self.run()
        a.refresh_from_db()
        assert state == "failed" and a.message == maint.MSG_LOCK and calls == []
        # a fresh admission is allowed afterwards
        assert self.admit(studio_tree, developer).pk != a.pk

    def test_expired_lease_becomes_unconfirmed_and_is_never_rerun(self, developer, studio_tree, monkeypatch):
        from apps.reports import scan

        calls = []
        monkeypatch.setattr(scan, "sync_studio_registry", lambda s: calls.append(s) or 0)
        a = self.admit(studio_tree, developer)
        claimed = maint.claim_one("crashed-worker")
        assert claimed.pk == a.pk
        Action.objects.filter(pk=a.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        assert maint.recover_expired() == 1
        a.refresh_from_db()
        assert a.state == "unconfirmed" and a.message == maint.MSG_UNCONFIRMED
        assert maint.claim_one("w2") is None  # not re-claimable
        assert maint.run_once("w2") is False and calls == []
        # the stale executor's late result is fenced out
        assert maint._finish(claimed, Action.SUCCEEDED, "x", reports=9) is False
        a.refresh_from_db()
        assert a.state == "unconfirmed" and a.reports is None
        assert AuditLog.objects.filter(action="registry.refresh_finished", outcome="failure").count() == 1
        # unconfirmed does not block a new, visible action
        assert self.admit(studio_tree, developer).pk != a.pk

    def test_live_lease_is_renewed_and_not_recovered(self, developer, studio_tree):
        a = self.admit(studio_tree, developer)
        claimed = maint.claim_one("w")
        before = Action.objects.get(pk=a.pk).lease_expires_at
        assert maint._renew(claimed)
        assert Action.objects.get(pk=a.pk).lease_expires_at >= before
        assert maint.recover_expired() == 0

    def test_renewer_survives_transient_database_errors(self, developer, studio_tree, monkeypatch):
        from apps.reports import scan

        real_renew = maint._renew
        attempts = {"n": 0}

        def flaky(action):
            attempts["n"] += 1
            if attempts["n"] in (2, 3):  # first call is the pre-scan renewal
                raise RuntimeError("connection reset")
            return real_renew(action)

        def slow_scan(studio):
            threading.Event().wait(0.6)
            return 0

        monkeypatch.setattr(maint, "_renew", flaky)
        monkeypatch.setattr(scan, "sync_studio_registry", slow_scan)
        a = self.admit(studio_tree, developer)
        claimed = maint.claim_one("w1")
        assert maint.execute(claimed, renew_every=0.05) == "succeeded"
        a.refresh_from_db()
        assert a.state == "succeeded"
        assert attempts["n"] > 3  # kept renewing after both failures

    def test_unclaimed_action_survives_and_pool_filter(self, developer, studio_tree):
        a = self.admit(studio_tree, developer)
        assert maint.claim_one("w", pools=["large"]) is None
        assert maint.claim_one("w", pools=[studio_tree.pool]).pk == a.pk

    def test_coordinator_only_process_starts_no_thread(self):
        assert maint.start_for_worker(is_runner=False, pools=[], worker_id="c") is None
