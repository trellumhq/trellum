"""Organization appearance: default MODE + the per-studio-override lock
(apps.orgs.views.appearance_settings) -- Org settings -> Appearance, an
org-admin-only settings page (moved off the personal Account page after
live owner testing, see .lavish/theming-explained.html "The fix"). The org
no longer picks a default *palette* -- Organization.default_theme stays on
the model, unused: apps.core.themes.explicit_studio_theme dropped it as a
resolver rung entirely (.lavish/theming-controls-design.html decision (2)),
and nothing in the UI reads or writes it either. See
apps/core/tests/test_theme_resolution.py (TestRetiredOrgRung) for the
resolver-side confirmation and apps/core/tests/test_mgmt_theme.py for the
page-level rendering."""
import pytest

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


class TestOrgAppearanceSettings:
    def test_org_admin_can_set_the_default_mode(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "light"}
        )
        assert resp.status_code == 302
        org.refresh_from_db()
        assert org.default_mode == "light"

    def test_accepts_auto(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "auto"}
        )
        org.refresh_from_db()
        assert org.default_mode == "auto"

    def test_rejects_an_unrecognised_mode(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "not-a-mode"}
        )
        org.refresh_from_db()
        assert org.default_mode == "dark"  # unchanged from the model default

    def test_never_writes_the_retired_palette_field(self, login, org_admin, org):
        # The org no longer picks a palette anywhere in the UI -- confirm
        # this endpoint (the only org-admin appearance writer left) leaves
        # default_theme alone even though the POST has no `theme` field to
        # begin with.
        org.default_theme = "ocean"
        org.save(update_fields=["default_theme"])
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "light"}
        )
        org.refresh_from_db()
        assert org.default_theme == "ocean"  # untouched

    def test_sets_the_lock(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance",
            {"default_mode": "dark", "lock_studio_theme": "1"},
        )
        org.refresh_from_db()
        assert org.lock_studio_theme is True

    def test_omitting_the_checkbox_clears_the_lock(self, login, org_admin, org):
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "dark"}
        )
        org.refresh_from_db()
        assert org.lock_studio_theme is False

    def test_non_admin_member_is_forbidden(self, login, member, org):
        resp = login(member).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "light"}
        )
        assert resp.status_code == 403
        org.refresh_from_db()
        assert org.default_mode == "dark"

    def test_anonymous_is_redirected_to_login(self, client, org):
        resp = client.post(f"/orgs/{org.slug}/settings/appearance", {"default_mode": "light"})
        assert resp.status_code == 302
        assert "/login" in resp["Location"]

    def test_org_admin_can_view_the_page(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/appearance")
        assert resp.status_code == 200
        assert b"Default mode" in resp.content

    def test_non_admin_member_cannot_view_the_page(self, login, member, org):
        assert login(member).get(f"/orgs/{org.slug}/settings/appearance").status_code == 403

    def test_is_audited(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/appearance", {"default_mode": "light"}
        )
        assert AuditLog.objects.filter(action="org.appearance_set").exists()
