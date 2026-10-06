"""The theme contract: every theme defines the full token set.

A theme is pure data — this test is what makes that promise enforceable
(and is exactly the check a future custom-theme editor must satisfy).

The table itself is generated from the framework's registry, so these run
against `static/themes.generated.css`; see `test_theme_sync.py` for the
guards on the generator.
"""
import re

from django.conf import settings

from apps.core.themes import TOKEN_ORDER

REQUIRED_VARS = set(TOKEN_ORDER)
EXPECTED_THEMES = {
    "trellum light", "trellum dark",  # the default pair
    "light", "dark",                  # the pre-trellum classics
    "money", "blossom", "midnight", "sunset",
    "nord", "dracula", "solarized", "ocean", "monokai",
}


def _theme_blocks() -> dict[str, str]:
    css = (settings.BASE_DIR / "static" / "themes.generated.css").read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r'\[data-theme="([\w -]+)"\]\s*\{([^}]*)\}', css)
    }


def test_all_themes_present():
    assert set(_theme_blocks()) == EXPECTED_THEMES


def test_ui_css_does_not_redefine_the_theme_table():
    # The 13 themes moved to the framework. ui.css owns components and the
    # neutral management palette; a [data-theme] block here means the copy
    # crept back.
    css = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
    stray = re.findall(r'^\[data-theme="([\w-]+)"\]\s*\{', css, re.MULTILINE)
    assert not stray, f"ui.css redefines themes {stray} — they belong in the generated file"


def test_every_theme_defines_every_token():
    for theme, body in _theme_blocks().items():
        defined = set(re.findall(r"(--[\w-]+)\s*:", body))
        missing = REQUIRED_VARS - defined
        assert not missing, f"theme '{theme}' is missing {sorted(missing)}"


def test_dark_themes_hover_shadow_is_not_the_light_value():
    # The legacy table copy-pasted the light hover shadow into dark themes,
    # making cards LOSE elevation on hover. Never again.
    light_hover = "0 4px 12px rgba(0,0,0,0.08)"
    for theme, body in _theme_blocks().items():
        if theme in ("trellum light", "light", "blossom"):
            continue
        hover = re.search(r"--shadow-hover:\s*([^;]+);", body).group(1)
        assert light_hover not in hover, f"theme '{theme}' still has the light hover shadow"


def _rgb(value: str) -> tuple[int, int, int]:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_brand_red_is_never_the_error_red():
    # The brand accent and the danger colour must be tellable apart at a
    # glance. They were not: light shipped --accent #E84855 next to --red
    # #ef4444, so "0 errors", a focused input and a Save button all read as
    # the same alarm. Distance is crude but it is exactly the failure mode.
    for theme, body in _theme_blocks().items():
        accent = re.search(r"--accent:\s*(#[0-9a-fA-F]{3,6})", body).group(1)
        red = re.search(r"--red:\s*(#[0-9a-fA-F]{3,6})", body).group(1)
        a, r = _rgb(accent), _rgb(red)
        distance = sum((x - y) ** 2 for x, y in zip(a, r)) ** 0.5
        assert distance > 40, (
            f"theme '{theme}': --accent {accent} and --red {red} are too close "
            f"(distance {distance:.0f})"
        )


def test_base_does_not_redefine_components():
    # base.html used to carry an inline <style> that re-implemented .panel,
    # .card, button and friends alongside ui.css's .ui-* versions. Two drifting
    # systems is what this pass removed; keep it removed.
    html = (settings.BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "<style>" not in html, "base.html defines CSS again — it belongs in ui.css"


def test_shells_load_ui_css():
    boots = {
        "templates/base.html": "_theme_boot_mgmt.html",  # clamped light/dark
        "templates/portal/index.html": "_theme_boot_mgmt.html",  # neutral console
    }
    for template, boot in boots.items():
        html = (settings.BASE_DIR / template).read_text(encoding="utf-8")
        assert "ui.css" in html, f"{template} does not load ui.css"
        assert "themes.generated.css" in html, f"{template} does not load the theme table"
        assert boot in html, f"{template} does not include {boot}"
