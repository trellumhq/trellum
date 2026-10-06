"""Per-user favorites: toggle, visibility filtering, localStorage import."""
import json

import pytest

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report, ReportFavorite
from apps.reports.models import ReportPermissionGrant

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


class TestToggle:
    def test_put_then_get(self, login, viewer, report_row):
        c = login(viewer)
        resp = c.put(f"/api/me/favorites/{report_row.pk}")
        assert resp.json() == {"ok": True, "favorited": True}
        favs = c.get("/api/me/favorites").json()["favorites"]
        assert favs == [
            {
                "report_id": report_row.pk,
                "org_slug": report_row.studio.org.slug,
                "studio_slug": report_row.studio.slug,
                "slug": "player-overview",
                "name": "Player-Overview",
            }
        ]

    def test_put_idempotent(self, login, viewer, report_row):
        c = login(viewer)
        c.put(f"/api/me/favorites/{report_row.pk}")
        c.put(f"/api/me/favorites/{report_row.pk}")
        assert ReportFavorite.objects.count() == 1

    def test_delete(self, login, viewer, report_row):
        c = login(viewer)
        c.put(f"/api/me/favorites/{report_row.pk}")
        resp = c.delete(f"/api/me/favorites/{report_row.pk}")
        assert resp.json() == {"ok": True, "favorited": False}
        assert ReportFavorite.objects.count() == 0

    def test_cannot_favorite_invisible_report(self, login, make_user, org, report_row):
        outsider = make_user("plain@demo.example", org=org)  # member, no studio grant
        resp = login(outsider).put(f"/api/me/favorites/{report_row.pk}")
        assert resp.status_code == 404
        assert ReportFavorite.objects.count() == 0

    def test_requires_auth(self, client, report_row):
        assert client.put(f"/api/me/favorites/{report_row.pk}").status_code == 401


class TestVisibilityFiltering:
    def test_favorite_hidden_after_access_revoked(
        self, login, viewer, report_row, studio_tree
    ):
        c = login(viewer)
        c.put(f"/api/me/favorites/{report_row.pk}")
        from apps.studios.models import StudioMembership

        StudioMembership.objects.filter(user=viewer, studio=studio_tree).delete()
        assert c.get("/api/me/favorites").json()["favorites"] == []
        # The row survives: regaining access restores the favorite.
        assert ReportFavorite.objects.count() == 1

    def test_favorite_hidden_when_report_leaves_scan(self, login, viewer, report_row):
        c = login(viewer)
        c.put(f"/api/me/favorites/{report_row.pk}")
        Report.objects.filter(pk=report_row.pk).update(present_in_scan=False)
        assert c.get("/api/me/favorites").json()["favorites"] == []

    def test_selected_viewer_only_sees_favorites_for_assigned_reports(
        self, login, member, studio_tree, report_row, make_group, attach_group, settings
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        hidden = Report.objects.create(studio=studio_tree, slug="hidden")
        group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        ReportFavorite.objects.bulk_create(
            [
                ReportFavorite(user=member, report=report_row),
                ReportFavorite(user=member, report=hidden),
            ]
        )

        favorites = login(member).get("/api/me/favorites").json()["favorites"]
        assert [row["report_id"] for row in favorites] == [report_row.pk]


class TestImport:
    def test_import_matches_visible_studio(self, login, viewer, report_row):
        c = login(viewer)
        resp = c.post(
            "/api/me/favorites/import",
            data=json.dumps({"slugs": ["player-overview", "ghost-report"]}),
            content_type="application/json",
        )
        assert resp.json() == {"ok": True, "imported": 1}
        assert ReportFavorite.objects.filter(user=viewer, report=report_row).exists()

    def test_import_idempotent(self, login, viewer, report_row):
        c = login(viewer)
        for _ in range(2):
            c.post(
                "/api/me/favorites/import",
                data=json.dumps({"slugs": ["player-overview"]}),
                content_type="application/json",
            )
        assert ReportFavorite.objects.count() == 1

    def test_import_rejects_non_list(self, login, viewer):
        resp = login(viewer).post(
            "/api/me/favorites/import",
            data=json.dumps({"slugs": "player-overview"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
