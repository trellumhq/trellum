"""Studio theming (apps.core.themes.resolve_studio_theme / explicit_studio_
theme) -- theme is a per-studio property: the studio's own default falls
back to the Trellum default (the organization no longer picks a default
palette of its own -- see apps.core.themes.explicit_studio_theme's
docstring); a viewer may override it for themselves, per studio, unless the
organization has locked overrides off. One resolver feeds BOTH the studio's
management chrome (this file) and the served report content (apps/reports/tests/test_api.py)
-- see apps/core/tests/test_theme_resolution.py for the resolver's own unit
tests and apps/studios/tests/test_theme.py, apps/studios/tests/
test_appearance_settings.py + apps/orgs/tests/test_theme.py for the setter
endpoints.

Console pages always use the neutral Light/Dark/Auto surface. Named studio
themes remain report-content preferences and never emit data-studio-theme on
the catalog or management templates.
"""
import pytest

from apps.core import roles
from apps.studios.models import StudioPreference

pytestmark = pytest.mark.django_db


class TestPageSplit:
    @pytest.mark.parametrize("path, theme", [
        ("/", "monokai"),
        ("{org_settings}", "dracula"),
        ("/account", "nord"),
    ])
    def test_pages_outside_the_studio_never_carry_the_studio_theme(
        self, login, org_admin, org, studio, path, theme
    ):
        studio.theme = theme
        studio.save(update_fields=["theme"])
        html = login(org_admin).get(path.format(org_settings=f"/orgs/{org.slug}/settings/members")).content.decode()
        assert "data-studio-theme" not in html

    @pytest.mark.parametrize("studio_theme, viewer_theme", [
        ("", ""),
        ("money", ""),
        ("not-a-real-theme", ""),  # a deleted theme falls back instead of breaking
        ("money", "dracula"),      # a viewer's report override never rethemes the console
    ])
    def test_studio_management_page_does_not_carry_report_theme(
        self, login, org_admin, member, org, studio_tree, report_row, grant_studio, studio_theme, viewer_theme
    ):
        if studio_theme:
            studio_tree.theme = studio_theme
            studio_tree.save(update_fields=["theme"])
        user = org_admin
        if viewer_theme:
            user = member
            grant_studio(member, studio_tree, roles.VIEWER)
            StudioPreference.objects.create(user=member, studio=studio_tree, theme=viewer_theme)
        html = login(user).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "data-studio-theme" not in html

    @pytest.mark.parametrize("studio_theme", ["", "blossom", "sunset"])
    def test_studio_dashboard_boots_the_neutral_console_whatever_the_report_theme(
        self, login, org_admin, org, studio_tree, report_row, studio_theme
    ):
        if studio_theme:
            studio_tree.theme = studio_theme
            studio_tree.save(update_fields=["theme"])
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "mgmt-theme" in html
        assert "PORTAL_THEME_IS_DEFAULT" not in html
        if studio_theme:
            assert f'data-theme="{studio_theme}"' not in html


class TestMgmtBootUnchanged:
    def test_management_pages_use_mgmt_boot(self, login, org_admin, org):
        html = login(org_admin).get("/").content.decode()
        assert "mgmt-theme" in html

    def test_mgmt_boot_clamps_to_light_or_dark(self, client, member):
        html = client.get("/login").content.decode()
        assert "pref === 'light' || pref === 'dark'" in html


class TestMgmtBootOrgDefaultMode:
    """The pre-paint "auto" default used to resolve the OS preference
    directly; it now resolves Organization.default_mode instead (a real
    Light/Dark/Auto setting on Org settings -> Appearance) -- the org no
    longer picks a default *palette* at all, so this no longer derives a
    mode from one (contrast the old theme_mode_for-based behaviour). OS
    preference is still the final fallback when no org context exists at
    all, and also when the org's own setting is itself "auto"."""

    def test_defaults_dark_out_of_the_box(self, login, org_admin, org):
        # Organization.default_mode's own model default.
        html = login(org_admin).get("/").content.decode()
        assert 'data-console-default-mode="dark"' in html

    def test_follows_an_explicit_light_org_default(self, login, org_admin, org):
        org.default_mode = "light"
        org.save(update_fields=["default_mode"])
        html = login(org_admin).get("/").content.decode()
        assert 'data-console-default-mode="light"' in html

    def test_an_explicit_auto_org_default_falls_through_to_the_OS_clamp(self, login, org_admin, org):
        org.default_mode = "auto"
        org.save(update_fields=["default_mode"])
        html = login(org_admin).get("/").content.decode()
        # The boot script's own `if (t !== 'light' && t !== 'dark')` catches
        # this and resolves prefers-color-scheme instead -- see
        # templates/_theme_boot_mgmt.html.
        assert 'data-console-default-mode="auto"' in html


class TestAppearanceCluster:
    def test_mode_row_is_unconditional_on_the_dashboard(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "data-console-theme-choice" in html
        assert "tl-console-theme" in html

    def test_mode_row_is_unconditional_on_mgmt_pages(self, login, org_admin, org):
        html = login(org_admin).get("/").content.decode()
        assert "data-console-theme-choice" in html
        assert "tl-console-theme" in html

    def test_theme_picker_is_absent_outside_a_studio(self, login, org_admin, org):
        # Theme is per-studio now -- there is nothing to pick on the hub.
        html = login(org_admin).get("/").content.decode()
        assert 'name="theme"' not in html

    def test_theme_picker_does_not_reflect_a_studio_default_nobody_picked(
        self, login, member, org, studio_tree, report_row, grant_studio
    ):
        # studio_tree.theme resolves the PAGE to "money", but this viewer
        # personally chose nothing -- the picker must show "Studio default"
        # selected, not falsely claim they picked "money" themselves.
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        grant_studio(member, studio_tree, roles.VIEWER)
        html = login(member).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert '<option value="money" selected>' not in html


class TestAccountAppearanceSection:
    """The org-admin block ("Lock it" + the org default) moved off this
    page entirely, to Org settings -> Appearance -- live owner testing
    flagged an org-admin control sitting on a page titled "Your account" as
    nonsense (.lavish/theming-explained.html "The fix"). Account ->
    Appearance now holds only the viewer's own personal mode."""

    def test_everyone_sees_the_mode_row(self, login, member, org):
        html = login(member).get("/account").content.decode()
        assert "setMgmtTheme" in html

    def test_org_admin_sees_a_pointer_to_org_appearance_settings(self, login, org_admin, org):
        html = login(org_admin).get("/account").content.decode()
        assert f"/orgs/{org.slug}/settings/appearance" in html

    def test_member_does_not_see_the_org_appearance_pointer(self, login, member, org):
        html = login(member).get("/account").content.decode()
        assert "settings/appearance" not in html
