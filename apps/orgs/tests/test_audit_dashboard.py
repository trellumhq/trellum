"""Audit dashboard authorization, isolation, filters, pagination, and CSV export."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _row(org, *, action, category, outcome="success", actor=None, days_ago=0, **metadata):
    row = AuditLog.objects.create(
        org=org, actor=actor, action=action, category=category, outcome=outcome,
        metadata=metadata,
    )
    if days_ago:
        AuditLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        row.refresh_from_db()
    return row


class TestAuthorization:
    def test_non_admin_is_refused(self, login, member, org):
        assert login(member).get(f"/orgs/{org.slug}/settings/audit").status_code == 403

    def test_admin_sees_page_and_export(self, login, org_admin, org):
        client = login(org_admin)
        assert client.get(f"/orgs/{org.slug}/settings/audit").status_code == 200
        assert client.get(f"/orgs/{org.slug}/settings/audit/export.csv").status_code == 200

    def test_writes_always_work(self, rf, org, org_admin):
        from apps.core.audit import audit

        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        audit(request, "member.invite", target=org)
        assert AuditLog.objects.filter(org=org, action="member.invite").exists()


class TestOrgIsolation:
    def test_an_admin_never_sees_another_orgs_rows(self, login, org_admin, org, other_org):
        _row(org, action="member.invite", category="authz")
        _row(other_org, action="member.invite", category="authz")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        assert resp.status_code == 200
        assert resp.context["page"].paginator.count == 1

    def test_actor_and_action_dropdowns_are_scoped_to_the_org_too(self, login, org_admin, org, other_org, make_user):
        stranger = make_user("stranger@rival.example", org=other_org)
        _row(other_org, action="sso.update", category="admin", actor=stranger)
        _row(org, action="member.invite", category="authz")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        action_names = [name for name, _label in resp.context["action_choices"]]
        actor_emails = [email for _id, email in resp.context["actor_choices"]]
        assert "sso.update" not in action_names
        assert stranger.email not in actor_emails


class TestFilters:
    def test_category_pill_filters(self, login, org_admin, org):
        _row(org, action="member.invite", category="authz")
        _row(org, action="auth.login", category="auth")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?category=authz")
        assert resp.context["page"].paginator.count == 1
        assert resp.context["page"][0].action == "member.invite"

    def test_action_filter_is_exact_not_prefix(self, login, org_admin, org):
        """member.invite and member.invite_revoke must not bleed into each
        other -- the old view used action__startswith."""
        _row(org, action="member.invite", category="authz")
        _row(org, action="member.invite_revoke", category="authz")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?action=member.invite")
        assert resp.context["page"].paginator.count == 1

    def test_actor_filter(self, login, org_admin, org, member):
        _row(org, action="member.invite", category="authz", actor=member)
        _row(org, action="member.invite", category="authz", actor=org_admin)

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?actor={member.pk}")
        assert resp.context["page"].paginator.count == 1
        assert resp.context["page"][0].actor_id == member.pk

    def test_outcome_filter(self, login, org_admin, org):
        _row(org, action="auth.login_failed", category="auth", outcome="failure")
        _row(org, action="auth.login", category="auth", outcome="success")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?outcome=failure")
        assert resp.context["page"].paginator.count == 1

    def test_date_range_filter(self, login, org_admin, org):
        _row(org, action="member.invite", category="authz", days_ago=10)
        recent = _row(org, action="member.invite", category="authz", days_ago=0)

        today = timezone.now().date().isoformat()
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?from={today}")
        assert resp.context["page"].paginator.count == 1
        assert resp.context["page"][0].pk == recent.pk

    def test_target_filter_reached_by_clicking_a_target_cell(self, login, org_admin, org):
        row = AuditLog.objects.create(
            org=org, action="studio.rename", category="admin",
            target_type="studios.studio", target_id="7",
        )
        AuditLog.objects.create(org=org, action="studio.rename", category="admin", target_id="8", target_type="studios.studio")

        resp = login(org_admin).get(
            f"/orgs/{org.slug}/settings/audit?target_type=studios.studio&target_id=7"
        )
        assert resp.context["page"].paginator.count == 1
        assert resp.context["page"][0].pk == row.pk

    def test_filters_compose(self, login, org_admin, org, member):
        _row(org, action="member.invite", category="authz", actor=member, outcome="success")
        _row(org, action="member.invite", category="authz", actor=org_admin, outcome="success")
        _row(org, action="auth.login_failed", category="auth", actor=member, outcome="failure")

        resp = login(org_admin).get(
            f"/orgs/{org.slug}/settings/audit?category=authz&actor={member.pk}"
        )
        assert resp.context["page"].paginator.count == 1

    def test_search_looks_at_action_target_and_metadata(self, login, org_admin, org):
        _row(org, action="member.invite", category="authz", email="findme@example.com")
        _row(org, action="member.invite", category="authz", email="other@example.com")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?q=findme")
        assert resp.context["page"].paginator.count == 1

    def test_pagination_preserves_filters(self, login, org_admin, org):
        for i in range(60):
            _row(org, action="member.invite", category="authz", n=i)
        AuditLog.objects.create(org=org, action="auth.login", category="auth")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?category=authz&page=2")
        assert resp.status_code == 200
        assert resp.context["page"].paginator.count == 60
        html = resp.content.decode()
        assert "category=authz" in html  # the pager link carries the filter forward

    def test_clear_filters_link_appears_only_when_a_filter_is_active(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        assert resp.context["any_filter_active"] is False
        resp2 = login(org_admin).get(f"/orgs/{org.slug}/settings/audit?outcome=failure")
        assert resp2.context["any_filter_active"] is True


class TestTilesAndDenials:
    def test_tiles_arithmetic(self, login, org_admin, org, member):
        _row(org, action="auth.login", category="auth", outcome="success", actor=org_admin)
        _row(org, action="auth.login", category="auth", outcome="success", actor=member)
        _row(org, action="auth.login_failed", category="auth", outcome="failure")
        _row(org, action="auth.sso_denied", category="auth", outcome="denied")
        # Outside the 30-day window -- must not count.
        _row(org, action="auth.login", category="auth", outcome="success", days_ago=45)

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        tiles = resp.context["tiles"]
        assert tiles["events"] == 4
        assert tiles["denials"] == 2  # failure + denied
        assert tiles["failed_logins"] == 1
        assert tiles["actors"] == 2  # org_admin + member, distinct

    def test_pills_sum_to_the_events_tile(self, login, org_admin, org):
        _row(org, action="member.invite", category="authz")
        _row(org, action="auth.login", category="auth")
        _row(org, action="report.view", category="access")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        pills = resp.context["pills"]
        assert sum(p["count"] for p in pills) == resp.context["tiles"]["events"]

    def test_recent_denials_panel_shows_only_when_present(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        assert resp.context["recent_denials"] == []
        assert "Recent denials" not in resp.content.decode()

        _row(org, action="auth.sso_denied", category="auth", outcome="denied", reason_code="outside_verified_domains")
        resp2 = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
        assert len(resp2.context["recent_denials"]) == 1
        assert "Recent denials" in resp2.content.decode()


class TestCsvExport:
    def test_contents_and_column_order(self, login, org_admin, org, member):
        _row(org, action="member.invite", category="authz", actor=member, email="a@b.com")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit/export.csv")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        body = b"".join(resp.streaming_content).decode()
        reader = csv.reader(io.StringIO(body))
        header, *rows = list(reader)
        assert header == [
            "timestamp", "actor", "impersonator", "action", "category",
            "outcome", "target_type", "target_id", "ip", "user_agent", "metadata",
        ]
        data_row = next(r for r in rows if r[3] == "member.invite")
        assert data_row[1] == member.email
        assert '"a@b.com"' in data_row[10] or "a@b.com" in data_row[10]

    def test_filters_apply_to_the_export_too(self, login, org_admin, org):
        _row(org, action="member.invite", category="authz")
        _row(org, action="auth.login", category="auth")

        resp = login(org_admin).get(
            f"/orgs/{org.slug}/settings/audit/export.csv?category=authz"
        )
        body = b"".join(resp.streaming_content).decode()
        reader = csv.reader(io.StringIO(body))
        rows = list(reader)[1:]
        assert len(rows) == 1
        assert rows[0][3] == "member.invite"

    def test_export_writes_its_own_audit_row(self, login, org_admin, org):
        _row(org, action="member.invite", category="authz")

        resp = login(org_admin).get(
            f"/orgs/{org.slug}/settings/audit/export.csv?category=authz"
        )
        list(resp.streaming_content)  # force the generator to run

        export_row = AuditLog.objects.get(action="audit.export")
        assert export_row.actor_id == org_admin.pk
        assert export_row.org_id == org.pk
        assert export_row.metadata["row_count"] == 1
        assert export_row.metadata["filters"]["category"] == "authz"
        assert export_row.category == "system"

    def test_row_cap_is_enforced(self, login, org_admin, org, monkeypatch):
        from apps.orgs.audit import views as audit_views

        monkeypatch.setattr(audit_views, "CSV_ROW_CAP", 3)
        for i in range(5):
            _row(org, action="member.invite", category="authz", n=i)

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit/export.csv")
        body = b"".join(resp.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(body)))[1:]
        assert len(rows) == 3

    def test_org_isolation_holds_for_export_too(self, login, org_admin, org, other_org):
        _row(other_org, action="member.invite", category="authz")

        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/audit/export.csv")
        body = b"".join(resp.streaming_content).decode()
        rows = list(csv.reader(io.StringIO(body)))[1:]
        assert rows == []
