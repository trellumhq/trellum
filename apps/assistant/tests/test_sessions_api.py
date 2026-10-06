"""Session CRUD, privacy and studio scoping."""
import json

import pytest
from django.core.exceptions import ValidationError

from apps.assistant.models import AssistantSession
from apps.core import roles

pytestmark = pytest.mark.django_db


@pytest.fixture
def session(viewer, org, studio_tree):
    return AssistantSession.objects.create(
        user=viewer, org=org, studio=studio_tree, title="Payer churn"
    )


class TestCreate:
    def test_create_returns_full_shape(self, login, viewer, prefix):
        resp = login(viewer).post(f"{prefix}/sessions")
        assert resp.status_code == 201
        body = resp.json()
        assert body["transcript"] == []
        assert body["message_count"] == 0
        assert body["cost_usd"] == 0
        row = AssistantSession.objects.get(pk=body["id"])
        assert row.user == viewer
        assert row.studio_id is not None
        assert row.scope == AssistantSession.SCOPE_FULL

    def test_non_object_json_body_is_treated_as_empty(self, login, viewer, prefix):
        resp = login(viewer).post(
            f"{prefix}/sessions", data="[]", content_type="application/json"
        )
        assert resp.status_code == 201

    def test_create_links_report_when_given(self, login, viewer, prefix, report_row):
        resp = login(viewer).post(
            f"{prefix}/sessions",
            data=json.dumps({"report": report_row.slug}),
            content_type="application/json",
        )
        assert AssistantSession.objects.get(pk=resp.json()["id"]).report == report_row
        assert resp.json()["scope"] == AssistantSession.SCOPE_FULL

    def test_unknown_report_slug_is_ignored(self, login, viewer, prefix):
        resp = login(viewer).post(
            f"{prefix}/sessions",
            data=json.dumps({"report": "ghost"}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert AssistantSession.objects.get(pk=resp.json()["id"]).report is None


class TestListGetDelete:
    def test_list_only_own_sessions(self, login, viewer, other_viewer, org, studio_tree, prefix, session):
        AssistantSession.objects.create(user=other_viewer, org=org, studio=studio_tree)
        body = login(viewer).get(f"{prefix}/sessions").json()
        assert [s["id"] for s in body["sessions"]] == [session.pk]
        assert body["sessions"][0]["title"] == "Payer churn"

    def test_list_newest_first(self, login, viewer, org, studio_tree, prefix, session):
        newer = AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree)
        ids = [s["id"] for s in login(viewer).get(f"{prefix}/sessions").json()["sessions"]]
        assert ids == [newer.pk, session.pk]

    def test_get_returns_transcript(self, login, viewer, prefix, session):
        session.state["transcript"] = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        session.save()
        body = login(viewer).get(f"{prefix}/sessions/{session.pk}").json()
        assert len(body["transcript"]) == 2
        assert body["message_count"] == 2

    def test_delete(self, login, viewer, prefix, session):
        from apps.core.models import AuditLog

        assert login(viewer).delete(f"{prefix}/sessions/{session.pk}").json() == {"ok": True}
        assert not AssistantSession.objects.filter(pk=session.pk).exists()
        entry = AuditLog.objects.get(action="assistant.session.delete")
        assert entry.actor == viewer
        assert entry.metadata["title"] == "Payer churn"

    def test_unknown_id_404(self, login, viewer, prefix):
        assert login(viewer).get(f"{prefix}/sessions/424242").status_code == 404

    def test_put_not_allowed(self, login, viewer, prefix, session):
        assert login(viewer).put(f"{prefix}/sessions/{session.pk}").status_code == 405


class TestPrivacyAndScoping:
    def test_other_users_session_is_404(self, login, other_viewer, prefix, session):
        assert login(other_viewer).get(f"{prefix}/sessions/{session.pk}").status_code == 404

    def test_other_users_session_cannot_be_deleted(self, login, other_viewer, prefix, session):
        assert login(other_viewer).delete(f"{prefix}/sessions/{session.pk}").status_code == 404
        assert AssistantSession.objects.filter(pk=session.pk).exists()

    def test_session_of_studio_a_invisible_from_studio_b(
        self, login, viewer, org, studio2, grant_studio, prefix, session
    ):
        grant_studio(viewer, studio2, roles.VIEWER)
        other_prefix = f"/s/{org.slug}/{studio2.slug}/api/assistant"
        assert login(viewer).get(f"{other_prefix}/sessions/{session.pk}").status_code == 404
        assert login(viewer).get(f"{other_prefix}/sessions").json()["sessions"] == []

    def test_unauthenticated_401(self, client, prefix, session):
        assert client.get(f"{prefix}/sessions").status_code == 401
        assert client.post(f"{prefix}/sessions").status_code == 401
        assert client.get(f"{prefix}/sessions/{session.pk}").status_code == 401

    def test_org_member_without_studio_grant_404(self, login, member, prefix):
        assert login(member).get(f"{prefix}/sessions").status_code == 404

    def test_viewer_role_is_enough(self, login, viewer, prefix):
        assert login(viewer).get(f"{prefix}/sessions").status_code == 200

    def test_foreign_org_user_404(self, login, make_user, other_org, prefix):
        outsider = make_user("spy@rival.com", org=other_org)
        assert login(outsider).get(f"{prefix}/sessions").status_code == 404


class TestReportBoundSessions:
    def test_selected_viewer_must_bind_an_allowed_report(
        self, login, selected_viewer, prefix, report_row
    ):
        client = login(selected_viewer)
        assert client.post(f"{prefix}/sessions").status_code == 400
        assert client.post(
            f"{prefix}/sessions",
            data=json.dumps({"report": "unknown"}),
            content_type="application/json",
        ).status_code == 404

        response = client.post(
            f"{prefix}/sessions",
            data=json.dumps({"report": report_row.slug}),
            content_type="application/json",
        )
        assert response.status_code == 201
        assert response.json()["scope"] == AssistantSession.SCOPE_REPORT
        assert response.json()["report"] == report_row.slug

    def test_selected_sessions_are_fail_closed_until_scoped_access_is_ready(
        self, login, selected_viewer, org, studio_tree, prefix, report_row, settings
    ):
        session = AssistantSession.objects.create(
            user=selected_viewer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        client = login(selected_viewer)
        listing = client.get(f"{prefix}/sessions")
        assert listing.status_code == 503
        assert listing.json()["error"] == "selected_report_access_unavailable"
        assert client.get(f"{prefix}/sessions/{session.pk}").status_code == 503
        response = client.post(
            f"{prefix}/sessions",
            data=json.dumps({"report": report_row.slug}),
            content_type="application/json",
        )
        assert response.status_code == 503

    def test_full_session_titles_are_hidden_after_downgrade(
        self, login, viewer, org, studio_tree, prefix, report_row, settings
    ):
        from apps.orgs.models import PermissionGroup, PermissionGroupGrant, PermissionGroupMembership
        from apps.reports.models import ReportPermissionGrant

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        secret = AssistantSession.objects.create(
            user=viewer, org=org, studio=studio_tree, title="Secret studio analysis"
        )
        group = PermissionGroup.objects.create(org=org, name="Downgraded viewers")
        grant = PermissionGroupGrant.objects.create(
            group=group, studio=studio_tree, role=roles.VIEWER, viewer_scope="selected"
        )
        PermissionGroupMembership.objects.create(user=viewer, group=group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        viewer.studio_memberships.all().delete()

        client = login(viewer)
        assert client.get(f"{prefix}/sessions").json()["sessions"] == []
        assert client.get(f"{prefix}/sessions/{secret.pk}").status_code == 404
        assert client.delete(f"{prefix}/sessions/{secret.pk}").status_code == 404

    def test_revocation_hides_report_session_and_promotion_does_not_widen_it(
        self, login, selected_viewer, org, studio_tree, prefix, report_row, grant_studio
    ):
        response = login(selected_viewer).post(
            f"{prefix}/sessions",
            data=json.dumps({"report": report_row.slug}),
            content_type="application/json",
        )
        session = AssistantSession.objects.get(pk=response.json()["id"])

        grant_studio(selected_viewer, studio_tree, roles.VIEWER)
        promoted = login(selected_viewer)
        assert promoted.get(f"{prefix}/sessions/{session.pk}").status_code == 200
        session.refresh_from_db()
        assert session.scope == AssistantSession.SCOPE_REPORT
        assert session.report == report_row

        selected_viewer.studio_memberships.all().delete()
        selected_viewer.selected_report_grant.report_grants.all().delete()
        assert login(selected_viewer).get(f"{prefix}/sessions/{session.pk}").status_code == 404

    def test_deleted_bound_report_makes_session_inaccessible(
        self, login, viewer, org, studio_tree, prefix, report_row
    ):
        session = AssistantSession.objects.create(
            user=viewer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        report_row.delete()
        assert login(viewer).get(f"{prefix}/sessions/{session.pk}").status_code == 404

    def test_removed_bound_report_makes_session_inaccessible(
        self, login, viewer, org, studio_tree, prefix, report_row
    ):
        session = AssistantSession.objects.create(
            user=viewer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        report_row.present_in_scan = False
        report_row.save(update_fields=["present_in_scan"])
        assert login(viewer).get(f"{prefix}/sessions/{session.pk}").status_code == 404

    def test_cross_studio_bound_report_corruption_is_inaccessible(
        self, login, viewer, org, studio_tree, studio2, prefix, report_row
    ):
        from apps.reports.models import Report

        session = AssistantSession.objects.create(
            user=viewer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
            title="Must stay hidden",
        )
        other = Report.objects.create(studio=studio2, slug="other-report", name="Other")
        AssistantSession.objects.filter(pk=session.pk).update(report=other)

        client = login(viewer)
        assert client.get(f"{prefix}/sessions/{session.pk}").status_code == 404
        assert "Must stay hidden" not in str(client.get(f"{prefix}/sessions").content)

    def test_scope_and_report_are_immutable(self, viewer, org, studio_tree, report_row):
        session = AssistantSession.objects.create(
            user=viewer, org=org, studio=studio_tree, report=report_row,
            scope=AssistantSession.SCOPE_REPORT,
        )
        session.scope = AssistantSession.SCOPE_FULL
        with pytest.raises(ValidationError):
            session.save()
        session.refresh_from_db()
        session.report = None
        with pytest.raises(ValidationError):
            session.save()

    def test_report_scope_requires_report(self, viewer, org, studio_tree):
        with pytest.raises(ValidationError):
            AssistantSession.objects.create(
                user=viewer, org=org, studio=studio_tree, scope=AssistantSession.SCOPE_REPORT
            )
