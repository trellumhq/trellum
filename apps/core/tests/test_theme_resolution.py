"""Studio theming resolution (apps.core.themes.resolve_studio_theme /
explicit_studio_theme) -- theme is a per-studio property now: a viewer's
personal per-studio override, falling back to the studio's own default,
then the Trellum default. (There used to be a third rung, the organization's
own default -- retired, .lavish/theming-controls-design.html decision (2),
once nothing in the UI could set it any more; see TestRetiredOrgRung below.)
This is the single resolver two consumers must never disagree on:
apps.core.context_processors.shell (studio management chrome) and
apps.reports.views._inject_report_chrome (served report content) -- see
apps/core/tests/test_mgmt_theme.py for the page-level behavior and
apps/reports/tests/test_api.py for the report-content injection.
"""
import pytest

from apps.core.themes import (
    explicit_studio_theme,
    has_custom_repo_theme,
    resolve_studio_theme,
    theme_mode_for,
    viewer_theme_override,
)
from apps.studios.models import StudioPreference

pytestmark = pytest.mark.django_db


class TestResolutionChain:
    def test_nothing_set_anywhere_resolves_to_trellum_dark(self, member, studio):
        assert resolve_studio_theme(member, studio) == "trellum dark"
        # The bare-default case must read as "nothing was picked" so chrome
        # stays mode-governed (Light/Dark/Auto) rather than locking dark.
        assert explicit_studio_theme(member, studio) == ""

    def test_studio_theme_is_rung_two(self, member, studio):
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(member, studio) == "money"
        assert explicit_studio_theme(member, studio) == "money"

    def test_viewer_override_is_rung_one_and_wins_over_the_studio_default(self, member, studio):
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        StudioPreference.objects.create(user=member, studio=studio, theme="dracula")
        assert resolve_studio_theme(member, studio) == "dracula"

    def test_an_explicit_pick_of_the_trellum_pair_still_counts_as_explicit(
        self, member, studio
    ):
        # Distinguishing "nothing picked" from "explicitly picked the
        # trellum pair" matters: the latter must still emit
        # data-studio-theme (it is a pin like any other), not fall back to
        # mode-governed behavior.
        studio.theme = "trellum dark"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(member, studio) == "trellum dark"
        assert explicit_studio_theme(member, studio) == "trellum dark"

    def test_anonymous_viewer_skips_rung_one(self, studio):
        studio.theme = "sunset"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(None, studio) == "sunset"

    def test_a_deleted_themes_name_falls_through_every_rung(self, member, studio):
        StudioPreference.objects.create(user=member, studio=studio, theme="not-a-real-theme")
        studio.theme = "also-not-real"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(member, studio) == "trellum dark"
        assert explicit_studio_theme(member, studio) == ""

    def test_a_deleted_viewer_override_falls_through_to_studio_not_default(
        self, member, org, studio
    ):
        StudioPreference.objects.create(user=member, studio=studio, theme="not-a-real-theme")
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(member, studio) == "money"


class TestOrgLock:
    def test_lock_ignores_the_viewer_override(self, member, org, studio):
        StudioPreference.objects.create(user=member, studio=studio, theme="dracula")
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        assert resolve_studio_theme(member, studio) == "trellum dark"

    def test_lock_ignores_an_override_stored_before_the_lock_was_turned_on(
        self, member, org, studio
    ):
        preference = StudioPreference.objects.create(user=member, studio=studio, theme="dracula")
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        preference.refresh_from_db()
        assert preference.theme == "dracula"  # not cleared -- just ignored
        assert resolve_studio_theme(member, studio) == "trellum dark"

    def test_lock_does_not_affect_the_studio_rung(self, member, org, studio):
        studio.theme = "midnight"
        studio.save(update_fields=["theme"])
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        assert resolve_studio_theme(member, studio) == "midnight"


class TestRetiredOrgRung:
    """Organization.default_theme used to be rung 3 -- retired
    (.lavish/theming-controls-design.html decision (2)) once the org-admin
    palette control moved off the account page and nothing in the UI wrote
    it any more, orphaning it as a rung nothing could aim at. The column
    stays on the model (apps/orgs/models.py), but the resolver never reads
    it again, whatever value is stored -- a studio with no palette of its
    own now falls straight through to the Trellum default."""

    def test_org_default_theme_is_never_consulted(self, member, org, studio):
        org.default_theme = "ocean"
        org.save(update_fields=["default_theme"])
        assert resolve_studio_theme(member, studio) == "trellum dark"
        assert explicit_studio_theme(member, studio) == ""

    def test_studio_default_wins_over_a_stored_org_default_regardless(self, member, org, studio):
        org.default_theme = "ocean"
        org.save(update_fields=["default_theme"])
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(member, studio) == "money"


class TestPerStudioIndependence:
    def test_same_user_two_studios_two_overrides(self, member, studio, studio2):
        StudioPreference.objects.create(user=member, studio=studio, theme="ocean")
        StudioPreference.objects.create(user=member, studio=studio2, theme="money")
        assert resolve_studio_theme(member, studio) == "ocean"
        assert resolve_studio_theme(member, studio2) == "money"

    def test_studio_defaults_are_independent_too(self, member, studio, studio2):
        studio.theme = "nord"
        studio.save(update_fields=["theme"])
        assert resolve_studio_theme(member, studio) == "nord"
        assert resolve_studio_theme(member, studio2) == "trellum dark"


class TestRepoTheme:
    """Studio.repo_theme -- rung 0, above everything else. Set by
    apps.reports.scan.sync_studio_registry from the studio repo's own
    config.yaml `theme:` (apps/reports/tests/test_scan.py); NOT validated
    against THEME_REGISTRY there, so it may name a theme the repo registers
    itself at build time (trellum.themes.register_theme) -- see
    has_custom_repo_theme and apps/reports/tests/test_api.py for what that
    means for the served report's own data-theme."""

    def test_registry_name_wins_over_everything(self, member, org, studio):
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        StudioPreference.objects.create(user=member, studio=studio, theme="dracula")
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        assert resolve_studio_theme(member, studio) == "nord"
        assert explicit_studio_theme(member, studio) == "nord"

    def test_custom_name_falls_through_to_trellum_for_chrome(self, member, studio):
        studio.repo_theme = "a-repo-custom-theme"
        studio.save(update_fields=["repo_theme"])
        assert resolve_studio_theme(member, studio) == "trellum dark"
        assert explicit_studio_theme(member, studio) == ""

    def test_custom_name_still_ignores_viewer_and_studio_picks(self, member, org, studio):
        # "Moot" means moot even for the fallback direction -- a custom
        # repo_theme must not let a viewer/studio pick leak through just
        # because rung 0 itself answered "".
        studio.theme = "money"
        studio.save(update_fields=["theme"])
        StudioPreference.objects.create(user=member, studio=studio, theme="dracula")
        studio.repo_theme = "a-repo-custom-theme"
        studio.save(update_fields=["repo_theme"])
        assert resolve_studio_theme(member, studio) == "trellum dark"

    def test_repo_theme_ignores_the_org_lock(self, member, org, studio):
        studio.repo_theme = "nord"
        studio.save(update_fields=["repo_theme"])
        org.lock_studio_theme = True
        org.save(update_fields=["lock_studio_theme"])
        assert resolve_studio_theme(member, studio) == "nord"

    def test_empty_repo_theme_falls_through_to_the_normal_chain(self, member, studio):
        studio.theme = "sunset"
        studio.save(update_fields=["theme"])
        assert studio.repo_theme == ""
        assert resolve_studio_theme(member, studio) == "sunset"


class TestHasCustomRepoTheme:
    def test_false_when_unset(self, studio):
        assert has_custom_repo_theme(studio) is False

    def test_false_for_a_registered_name(self, studio):
        studio.repo_theme = "nord"
        assert has_custom_repo_theme(studio) is False

    def test_true_for_an_unregistered_name(self, studio):
        studio.repo_theme = "a-repo-custom-theme"
        assert has_custom_repo_theme(studio) is True


class TestViewerThemeOverride:
    def test_empty_when_nothing_stored(self, member, studio):
        # Not "trellum dark" -- the raw stored value, for the picker's
        # `selected` state, not the resolved chain (see the function's
        # docstring: a studio/org default nobody at this row actually
        # picked must not show as this viewer's personal choice).
        assert viewer_theme_override(member, studio) == ""

    def test_reflects_an_explicit_override(self, member, studio):
        StudioPreference.objects.create(user=member, studio=studio, theme="blossom")
        assert viewer_theme_override(member, studio) == "blossom"

    def test_anonymous_is_empty(self, studio):
        assert viewer_theme_override(None, studio) == ""


class TestThemeModeFor:
    def test_trellum_dark_is_dark(self):
        assert theme_mode_for("trellum dark") == "dark"

    def test_trellum_light_is_light(self):
        assert theme_mode_for("trellum light") == "light"

    def test_unknown_name_defaults_dark(self):
        # Same direction the bare, nothing-picked-anywhere case resolves to.
        assert theme_mode_for("not-a-real-theme") == "dark"
