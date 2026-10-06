"""Studio settings -> Appearance (apps.studios.views.appearance_settings) --
the studio admin's own default-palette control (apps.core.themes
.resolve_studio_theme rung 2), moved off the tab bar's admin-only chip into
the studio's own settings after live owner testing found it sitting beside
the viewer's personal picker as two identical unlabeled dropdowns
(.lavish/theming-controls-design.html). This is a GET-only view; the page's
own form posts straight to the existing `theme_set_default` endpoint --
see apps/studios/tests/test_theme.py for that endpoint's own setter tests
(POST validation, audit, gating) and apps/core/tests/test_mgmt_theme.py /
apps/core/tests/test_theme_resolution.py for the resolver this feeds."""
import re

import pytest

from apps.core import roles
from trellum.themes import THEME_REGISTRY

pytestmark = pytest.mark.django_db


@pytest.fixture
def prefix(org, studio):
    return f"/s/{org.slug}/{studio.slug}"


class TestAppearanceSettingsPage:
    def test_studio_admin_can_view_the_page(self, login, org_admin, studio, prefix):
        resp = login(org_admin).get(f"{prefix}/settings/appearance")
        assert resp.status_code == 200
        assert b"Report theme" in resp.content

    def test_non_admin_member_is_forbidden(self, login, member, studio, prefix, grant_studio):
        grant_studio(member, studio, roles.VIEWER)
        assert login(member).get(f"{prefix}/settings/appearance").status_code == 403

    def test_member_without_any_grant_gets_404(self, login, member, studio, prefix):
        # Same "hide its existence" rule as every other studio settings route.
        assert login(member).get(f"{prefix}/settings/appearance").status_code == 404

    def test_anonymous_is_redirected_to_login(self, client, studio, prefix):
        resp = client.get(f"{prefix}/settings/appearance")
        assert resp.status_code == 302
        assert "/login" in resp["Location"]

    def test_renders_a_swatch_per_registered_theme(self, login, org_admin, studio, prefix):
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        for name in THEME_REGISTRY:
            assert f'data-theme="{name}"' in html
            assert f'value="{name}"' in html

    def test_empty_option_reads_default_trellum(self, login, org_admin, studio, prefix):
        # Retired org rung (.lavish/theming-controls-design.html decision
        # (2)): the empty pick used to be labeled "Org default", pointing at
        # a control nothing can set any more.
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "Default" in html and "Trellum" in html
        assert "Org default" not in html

    def test_current_studio_theme_is_preselected(self, login, org_admin, studio, prefix):
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert 'value="money" checked' in html

    def test_form_posts_to_the_existing_setter_endpoint(self, login, org_admin, studio, prefix):
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert f'action="{prefix}/theme/default"' in html

    def test_saving_redirects_back_to_the_appearance_page_not_the_dashboard(
        self, login, org_admin, studio, prefix
    ):
        resp = login(org_admin).post(
            f"{prefix}/theme/default",
            {"theme": "nord", "next": f"{prefix}/settings/appearance"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == f"{prefix}/settings/appearance"
        studio.refresh_from_db()
        assert studio.theme == "nord"

    def test_locked_org_shows_the_locked_lockline(self, login, org_admin, org, studio, prefix):
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "Member overrides: locked." in html
        assert "Member overrides: allowed." not in html

    def test_unlocked_org_shows_the_allowed_lockline(self, login, org_admin, studio, prefix):
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "Member overrides: allowed." in html
        assert "Member overrides: locked." not in html

    def test_appearance_appears_in_the_settings_nav_after_members(self, login, org_admin, studio, prefix):
        html = login(org_admin).get(f"{prefix}/settings/repo").content.decode()
        members = f'href="{prefix}/settings/members"'
        theme = f'href="{prefix}/settings/appearance"'
        assert html.index(members) < html.index(theme)

    def test_appearance_nav_link_is_active_on_its_own_page(self, login, org_admin, studio, prefix):
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        link = re.search(
            rf'<a\b(?P<attrs>[^>]*href="{re.escape(prefix)}/settings/appearance"[^>]*)>'
            r'(?P<body>.*?)</a>',
            html,
            re.DOTALL,
        )
        assert link is not None
        classes = re.search(r'class="([^"]*)"', link.group("attrs"))
        assert classes is not None and "active" in classes.group(1).split()
        assert 'aria-current="page"' in link.group("attrs")
        assert "Report theme" in link.group("body")


class TestRepoControlledAppearance:
    """Studio.repo_theme set -- the picker is replaced by a read-only pinned
    row (apps.reports.scan.sync_studio_registry stamps this from the
    studio's own repository; nothing on THIS page writes it). See
    apps/core/tests/test_theme_resolution.py::TestRepoTheme for the
    resolver precedence this reflects."""

    def test_picker_form_is_replaced_by_a_pinned_row(self, login, org_admin, studio, prefix):
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "set in repository" in html
        assert 'name="theme"' not in html
        assert f'action="{prefix}/theme/default"' not in html

    def test_pinned_row_names_the_repo_theme(self, login, org_admin, studio, prefix):
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "nord" in html
        assert 'data-theme="nord"' in html

    def test_registry_name_note_mentions_both_surfaces(self, login, org_admin, studio, prefix):
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "built-in theme" in html

    def test_custom_name_note_explains_chrome_stays_neutral(self, login, org_admin, studio, prefix):
        studio.repo_theme = "a-repo-custom-theme"
        studio.save(update_fields=["repo_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "custom theme" in html
        assert "stays Trellum" in html

    def test_lockline_says_repository_controlled_not_member_overrides(
        self, login, org_admin, studio, prefix
    ):
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "Repository-controlled." in html
        assert "Member overrides: locked." not in html
        assert "Member overrides: allowed." not in html

    def test_org_lock_does_not_bring_back_the_member_overrides_lockline(
        self, login, org_admin, org, studio, prefix
    ):
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "Repository-controlled." in html
        assert "Member overrides: locked." not in html

    def test_unset_repo_theme_still_shows_the_normal_picker(self, login, org_admin, studio, prefix):
        html = login(org_admin).get(f"{prefix}/settings/appearance").content.decode()
        assert "set in repository" not in html
        assert 'name="theme"' in html
