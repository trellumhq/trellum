"""The availability probe: studio-scoped route and the legacy global one."""
import pytest

pytestmark = pytest.mark.django_db


class TestStudioScoped:
    def test_false_when_unconfigured(self, login, viewer, prefix):
        body = login(viewer).get(f"{prefix}/available").json()
        assert body["available"] is False
        assert "not configured" in body["reason"]

    def test_false_when_disabled(self, login, viewer, org, prefix, make_assistant_config):
        make_assistant_config(org, enabled=False)
        body = login(viewer).get(f"{prefix}/available").json()
        assert body["available"] is False
        assert "disabled" in body["reason"]

    def test_true_when_configured(self, login, viewer, prefix, assistant_config):
        assert login(viewer).get(f"{prefix}/available").json() == {
            "available": True,
            "reason": "",
            "can_configure": False,
            "settings_url": None,
            "suggestions": ["Which reports cover revenue?"],
            "deadline_s": 120,
        }

    def test_org_admin_is_pointed_at_the_settings(
        self, login, org_admin, org, studio_tree, grant_studio, prefix, make_assistant_config
    ):
        from apps.core import roles

        make_assistant_config(org, enabled=False)
        grant_studio(org_admin, studio_tree, roles.VIEWER)
        body = login(org_admin).get(f"{prefix}/available").json()
        assert body["available"] is False
        assert body["can_configure"] is True
        assert body["settings_url"] == f"/orgs/{org.slug}/settings/assistant"
        assert body["suggestions"] == []  # nothing to suggest while it is off

    def test_suggestions_for_a_report_page(
        self, login, viewer, prefix, assistant_config, studio_tree, report_row
    ):
        import json

        out = studio_tree.output_dir / report_row.slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "_meta.json").write_text(json.dumps({"details": {
            "datasets": [{"id": "revenue_daily"}, {"id": "payers"}],
            "filter_matrix": [{"chart_title": "Revenue by platform"}, {"chart_title": "Payers"}],
        }}), encoding="utf-8")
        body = login(viewer).get(
            f"{prefix}/available", {"path": f"/s/demo/casino/r/{report_row.slug}/", "title": "x"}
        ).json()
        assert body["suggestions"] == [
            "Summarise this report",
            "Explain Revenue by platform",
            "What does revenue_daily show over the last 4 weeks?",
        ]

    def test_suggestions_for_the_dashboard_name_the_biggest_categories(
        self, login, viewer, prefix, assistant_config, studio_tree, write_report
    ):
        from apps.reports.scan import sync_studio_registry

        for slug, cat in [("a", "Finance"), ("b", "Finance"), ("c", "Growth"), ("d", None)]:
            write_report(slug, **({"category": cat} if cat else {}))
        sync_studio_registry(studio_tree)
        body = login(viewer).get(f"{prefix}/available", {"path": "/s/demo/casino/"}).json()
        assert body["suggestions"] == ["What's in Finance?", "What's in Growth?"]

    def test_unauthenticated_401(self, client, prefix):
        assert client.get(f"{prefix}/available").status_code == 401

    def test_mints_the_csrf_cookie_for_the_widget(self, login, viewer, prefix, assistant_config):
        # A built report page renders no form, so this probe is the widget's
        # only chance to obtain a CSRF token before it POSTs.
        resp = login(viewer).get(f"{prefix}/available")
        assert "csrftoken" in resp.cookies

    def test_member_without_studio_access_404(self, login, member, prefix, assistant_config):
        assert login(member).get(f"{prefix}/available").status_code == 404

    def test_selected_viewer_needs_an_allowed_report_context(
        self, login, selected_viewer, prefix, assistant_config, report_row, monkeypatch
    ):
        from apps.core import storage
        from apps.reports.models import Report

        client = login(selected_viewer)
        dashboard = client.get(f"{prefix}/available", {"path": "/s/demo/casino/"}).json()
        assert dashboard["available"] is False
        assert "Open a report" in dashboard["reason"]

        forbidden = Report.objects.create(
            studio=report_row.studio, slug="private-report", name="Private report"
        )
        monkeypatch.setattr(
            storage, "read_meta",
            lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("forbidden report was read")),
        )
        denied = client.get(
            f"{prefix}/available",
            {"path": f"/s/demo/casino/r/{forbidden.slug}/"},
        ).json()
        assert denied["available"] is False

    def test_selected_viewer_gets_suggestions_for_the_allowed_report(
        self, login, selected_viewer, prefix, assistant_config, studio_tree, report_row
    ):
        import json

        out = studio_tree.output_dir / report_row.slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "_meta.json").write_text(json.dumps({"details": {
            "datasets": [{"id": "allowed_data"}],
            "filter_matrix": [{"chart_title": "Allowed chart"}],
        }}), encoding="utf-8")
        body = login(selected_viewer).get(
            f"{prefix}/available",
            {"path": f"/s/demo/casino/r/{report_row.slug}/"},
        ).json()
        assert body["available"] is True
        assert body["suggestions"] == [
            "Summarise this report", "Explain Allowed chart",
            "What does allowed_data show over the last 4 weeks?",
        ]

    def test_selected_viewer_is_fail_closed_until_scoped_access_is_ready(
        self, login, selected_viewer, prefix, assistant_config, report_row, settings
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        response = login(selected_viewer).get(
            f"{prefix}/available",
            {"path": f"/s/demo/casino/r/{report_row.slug}/"},
        )
        assert response.status_code == 503
        assert response.json()["error"] == "selected_report_access_unavailable"


class TestGlobalRoute:
    """Kept for report pages built before the studio-scoped route existed."""

    URL = "/api/assistant/available"

    def test_anonymous_is_unavailable_not_an_error(self, client, db):
        body = client.get(self.URL).json()
        assert body["available"] is False
        assert "Log in" in body["reason"]

    def test_org_query_param(self, login, viewer, org, assistant_config):
        body = login(viewer).get(f"{self.URL}?org={org.slug}").json()
        assert body["available"] is True

    def test_org_resolved_from_referring_studio_path(
        self, login, viewer, org, studio_tree, assistant_config
    ):
        resp = login(viewer).get(
            self.URL,
            HTTP_REFERER=f"http://testserver/s/{org.slug}/{studio_tree.slug}/r/x/index.html",
        )
        assert resp.json()["available"] is True

    def test_single_org_membership_is_inferred(self, login, viewer, assistant_config):
        assert login(viewer).get(self.URL).json()["available"] is True

    def test_non_member_org_is_not_leaked(self, login, make_user, other_org, org, assistant_config):
        outsider = make_user("spy@rival.com", org=other_org)
        body = login(outsider).get(f"{self.URL}?org={org.slug}").json()
        assert body["available"] is False
        assert "No organization context" in body["reason"]

    def test_unknown_org_slug(self, login, viewer, assistant_config):
        assert login(viewer).get(f"{self.URL}?org=nope").json()["available"] is False

    def test_false_when_org_has_no_config(self, login, viewer, org):
        body = login(viewer).get(f"{self.URL}?org={org.slug}").json()
        assert body["available"] is False


class TestWidgetBundle:
    def test_bundle_still_served_at_frozen_url(self, client, db):
        resp = client.get("/api/assistant/widget.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        # CSS is injected by the bundle prologue, then assistant.js follows.
        assert b"document.head.appendChild" in resp.content
        assert b"assistantPanel" in resp.content

    def test_bundle_needs_no_auth(self, client, db):
        # Report pages load it before any session check; the APIs it calls
        # are the gated part.
        assert client.get("/api/assistant/widget.js").status_code == 200
