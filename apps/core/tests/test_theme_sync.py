"""The portal's theme table is generated from the framework's registry.

The framework owns the 13 themes so a project using the framework alone can
select one (that is what `consumer test repository` protects). The portal
renders them for its dashboard chrome via `manage.py sync_themes`. These tests
are what stop the two from silently drifting apart again — they did before,
as three independent copies of the same 13 names.
"""
import re

import pytest
from django.conf import settings

from apps.core.theme_overlay import ALLOWED_TOKENS, OVERLAY
from apps.core.themes import GENERATED_PATH, ThemeContractError, render, validate
from trellum.themes import THEME_REGISTRY


def _generated() -> str:
    return (settings.BASE_DIR / GENERATED_PATH).read_text(encoding="utf-8")


def test_generated_file_is_not_stale():
    # The one that matters: edit a framework theme without regenerating and
    # this fails instead of the portal quietly showing last week's colours.
    assert _generated() == render(THEME_REGISTRY), (
        f"{GENERATED_PATH} is stale — run `manage.py sync_themes`"
    )


def test_theme_names_match_the_framework_registry():
    names = re.findall(r'\[data-theme="([\w -]+)"\]', _generated())
    assert names == list(THEME_REGISTRY)


def test_appearance_select_renders_from_the_registry():
    # The dashboard-only #portalThemeSelect (which hardcoded neither, but
    # only existed on dashboard pages) is gone. The viewer's own per-studio
    # picker moved off the shell's top-right user menu onto the studio's own
    # tab rail after live owner testing (.lavish/theming-explained.html "The
    # fix") and lives in _studio_nav.html now; the studio admin's own
    # default-palette control moved a second time, off that same tab rail
    # into templates/studios/appearance.html
    # (.lavish/theming-controls-design.html) -- both must render from the
    # registry context var, not a hardcoded list. The shell itself carries
    # neither any more.
    shell_html = (settings.BASE_DIR / "templates" / "_shell.html").read_text(encoding="utf-8")
    assert "{% for name in theme_registry %}" not in shell_html
    assert "portalThemeSelect" not in shell_html, "the superseded dashboard-only picker is back"
    assert '<option value="monokai">' not in shell_html, "picker hardcodes theme names again"

    # The viewer's per-studio picker was removed from the tab rail on owner
    # request (it read badly beside the report page's own theme picker, which
    # already sets the same per-viewer override); the rail now carries no theme
    # control at all. The studio admin's default-palette control lives in
    # templates/studios/appearance.html.
    studio_nav_html = (settings.BASE_DIR / "templates" / "_studio_nav.html").read_text(encoding="utf-8")
    assert studio_nav_html.count("{% for name in theme_registry %}") == 0
    assert "portalThemeSelect" not in studio_nav_html

    appearance_html = (settings.BASE_DIR / "templates" / "studios" / "appearance.html").read_text(
        encoding="utf-8"
    )
    assert "{% for name in theme_registry %}" in appearance_html
    assert '<option value="monokai">' not in appearance_html, "picker hardcodes theme names again"


def test_overlay_only_sets_tokens_the_framework_does_not_own():
    for theme, tokens in OVERLAY.items():
        assert theme in THEME_REGISTRY, f"overlay names unknown theme '{theme}'"
        unknown = set(tokens) - ALLOWED_TOKENS
        assert not unknown, f"theme '{theme}': overlay must not set {sorted(unknown)}"


def test_brand_and_danger_stay_apart_in_every_generated_theme():
    blocks = re.findall(
        r'\[data-theme="([\w -]+)"\]\s*\{([^}]*)\}', _generated()
    )
    for theme, body in blocks:
        accent = re.search(r"--accent:\s*(#[0-9a-fA-F]{3,6})", body).group(1)
        red = re.search(r"--red:\s*(#[0-9a-fA-F]{3,6})", body).group(1)
        rgb = lambda v: tuple(int(v.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))  # noqa: E731
        distance = sum((x - y) ** 2 for x, y in zip(rgb(accent), rgb(red))) ** 0.5
        assert distance > 40, f"theme '{theme}': --accent {accent} vs --red {red}"


def test_validate_rejects_a_theme_whose_danger_is_the_brand():
    # The generator must fail loudly rather than emit a theme where "error"
    # and "brand" look identical — the collision this pass removed.
    tokens = {
        "--accent": "#E84855", "--red": "#ef4444", "--on-accent": "#ffffff",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5)",
    }
    with pytest.raises(ThemeContractError, match="too close"):
        validate("hypothetical", tokens)


def test_validate_rejects_unreadable_text_on_the_accent():
    tokens = {
        "--accent": "#a6e22e", "--red": "#f92672", "--on-accent": "#ffffff",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5)",
    }
    with pytest.raises(ThemeContractError, match="on --accent"):
        validate("hypothetical", tokens)


def test_management_surface_has_its_own_palette():
    # "Portal Neutral is the always-on default; a per-viewer studio theme is
    # opt-in." The neutral palette must be defined in ui.css against
    # body.mgmt, not inherited from the generated table — otherwise a
    # framework theme bump restyles the org/product surface, which never
    # carries data-studio-theme at all.
    css = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
    assert "body.mgmt {" in css or "body.mgmt," in css
    assert ':where(html[data-theme="dark"]) body.mgmt' in css
