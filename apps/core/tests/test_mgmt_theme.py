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
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


class TestPageSplit:
    def test_hub_never_carries_the_studio_theme(self, login, org_admin, org, studio):
        studio.theme = "monokai"
        studio.save(update_fields=["theme"])
        html = login(org_admin).get("/").content.decode()
        assert "data-studio-theme" not in html

    def test_org_settings_never_carries_the_studio_theme(self, login, org_admin, org, studio):
        studio.theme = "dracula"
        studio.save(update_fields=["theme"])
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        assert "data-studio-theme" not in html

    def test_account_page_never_carries_the_studio_theme(self, login, org_admin, org, studio):
        studio.theme = "nord"
        studio.save(update_fields=["theme"])
        html = login(org_admin).get("/account").content.decode()
        assert "data-studio-theme" not in html

    def test_studio_management_page_does_not_carry_report_theme(
        self, login, org_admin, org, studio_tree, report_row
    ):
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "data-studio-theme" not in html

    def test_studio_management_page_ignores_the_retired_org_default(
        self, login, org_admin, org, studio_tree, report_row
    ):
        # Organization.default_theme used to be rung 3; retired
        # (.lavish/theming-controls-design.html decision (2)) -- a stored
        # value must no longer surface anywhere, whatever the studio itself
        # picked (nothing, here).
        org.default_theme = "ocean"
        org.save(update_fields=["default_theme"])
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "data-studio-theme" not in html

    def test_a_viewers_report_override_does_not_retheme_console(
        self, login, member, org, studio_tree, report_row, grant_studio
    ):
        studio_tree.theme = "money"
        studio_tree.save(update_fields=["theme"])
        grant_studio(member, studio_tree, roles.VIEWER)
        StudioMembership.objects.filter(user=member, studio=studio_tree).update(theme="dracula")
        html = login(member).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "data-studio-theme" not in html

    def test_studio_dashboard_uses_neutral_console_theme(
        self, login, org_admin, org, studio_tree, report_row
    ):
        studio_tree.theme = "blossom"
        studio_tree.save(update_fields=["theme"])
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert 'data-theme="blossom"' not in html
        assert "mgmt-theme" in html

    def test_default_theme_studio_page_carries_no_studio_theme_attribute(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "data-studio-theme" not in html

    def test_default_theme_dashboard_boots_console_mode(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "mgmt-theme" in html
        assert "PORTAL_THEME_IS_DEFAULT" not in html

    def test_named_report_theme_does_not_skip_console_boot(
        self, login, org_admin, org, studio_tree, report_row
    ):
        studio_tree.theme = "sunset"
        studio_tree.save(update_fields=["theme"])
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "mgmt-theme" in html
        assert "PORTAL_THEME_IS_DEFAULT" not in html

    def test_a_deleted_theme_falls_back_to_default_instead_of_breaking(
        self, login, org_admin, org, studio_tree, report_row
    ):
        studio_tree.theme = "not-a-real-theme"
        studio_tree.save(update_fields=["theme"])
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "data-studio-theme" not in html


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

    def test_no_viewer_theme_picker_in_the_studio_nav(
        self, login, org_admin, org, studio_tree, report_row
    ):
        # The per-studio viewer picker was removed from the tab rail on owner
        # request; viewers set their own theme from the report page's picker
        # (static/report_theme_widget.js), not the studio chrome.
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "studio-tab-theme-mine" not in html
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

    def test_dashboard_picker_is_gone(self, login, org_admin, org, studio_tree, report_row):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "portalThemeSelect" not in html


class TestStudioAdminThemeChipMovedToSettings:
    """The studio ADMIN's own default-palette control (rung 2) used to be
    `studio-tab-theme-default`, a second unlabeled dropdown on the tab rail
    right beside `studio-tab-theme-mine` (TestViewerPickerOnTheStudio
    below) -- two identical controls with opposite blast radius. It now
    lives at Studio settings -> Appearance
    (apps/studios/tests/test_appearance_settings.py); the rail itself keeps
    only the viewer's own picker, on every studio page, not just the
    dashboard."""

    def test_admin_chip_no_longer_on_the_dashboard(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "studio-tab-theme-default" not in html
        assert f"/s/{org.slug}/{studio_tree.slug}/theme/default" not in html

    def test_admin_chip_no_longer_on_a_studio_management_page(
        self, login, org_admin, org, studio_tree, report_row
    ):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert "studio-tab-theme-default" not in html


class TestViewerPickerNotInTheChrome:
    """The viewer's per-studio theme override once sat in the shell user menu,
    then briefly on the studio tab rail; the owner had it removed from the
    chrome entirely — a viewer now sets their own theme from the report page's
    own picker (static/report_theme_widget.js). Neither the user menu nor the
    tab rail carries a theme control any more."""

    def test_not_in_the_shell_user_menu(self, login, org_admin, org, studio_tree, report_row):
        html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
        assert "shell-menu-select-row" not in html
        assert "studio-tab-theme-mine" not in html


class TestAccountAppearanceSection:
    """The org-admin block ("Lock it" + the org default) moved off this
    page entirely, to Org settings -> Appearance -- live owner testing
    flagged an org-admin control sitting on a page titled "Your account" as
    nonsense (.lavish/theming-explained.html "The fix"). Account ->
    Appearance now holds only the viewer's own personal mode."""

    def test_org_admin_no_longer_sees_an_org_default_block_here(self, login, org_admin, org):
        html = login(org_admin).get("/account").content.decode()
        assert "Organization default theme" not in html
        assert "lock_studio_theme" not in html

    def test_member_does_not_see_an_org_default_block_either(self, login, member, org):
        html = login(member).get("/account").content.decode()
        assert "Organization default theme" not in html
        assert "lock_studio_theme" not in html

    def test_everyone_sees_the_mode_row(self, login, member, org):
        html = login(member).get("/account").content.decode()
        assert "setMgmtTheme" in html

    def test_org_admin_sees_a_pointer_to_org_appearance_settings(self, login, org_admin, org):
        html = login(org_admin).get("/account").content.decode()
        assert f"/orgs/{org.slug}/settings/appearance" in html

    def test_member_does_not_see_the_org_appearance_pointer(self, login, member, org):
        html = login(member).get("/account").content.decode()
        assert "settings/appearance" not in html


class TestFwThemeAdoption:
    """The retired global per-viewer theme (and its one-time adoption of
    localStorage['fw-theme'] into it) is gone -- theme is a per-studio
    property persisted directly from the studio header / report picker
    now, so there is nothing left to adopt INTO."""

    def test_portal_js_no_longer_adopts_fw_theme(self):
        from django.conf import settings

        js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
        assert "localStorage.removeItem('fw-theme')" not in js
        assert "apiFetch('/account/theme'" not in js

    def test_theme_boot_still_does_not_read_fw_theme(self):
        from django.conf import settings

        html = (settings.BASE_DIR / "templates" / "_theme_boot.html").read_text(encoding="utf-8")
        assert "localStorage.getItem('fw-theme')" not in html
