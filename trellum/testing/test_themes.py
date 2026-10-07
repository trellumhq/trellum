"""trellum.themes.resolve_theme / effective_theme_name -- the project's own
config.yaml `theme:` becomes the per-build DEFAULT (a studio/project declares
a look once, every report in it picks it up), while an explicit name --
typically a report's own report.yaml `theme:` -- still overrides. See
trellum/rendering/html_builder.py's `default_theme_name` for the second
consumer (the baked data-theme / generated CSS), which resolves the same way.
"""
from __future__ import annotations

import pytest

from trellum.project import get_project_root, set_project_root
from trellum.themes import (
    DEFAULT_THEME,
    DEFAULT_THEME_NAME,
    THEME_REGISTRY,
    effective_theme_name,
    resolve_theme,
)
from trellum.themes.theme import Theme


@pytest.fixture
def project_root(tmp_path):
    """Pin the global project root at tmp_path -- effective_theme_name reads
    config.yaml off it via trellum.project.load_project_config()."""
    original = get_project_root()
    set_project_root(str(tmp_path))
    yield tmp_path
    set_project_root(original)


def _write_config(root, text: str) -> None:
    (root / "config.yaml").write_text(text, encoding="utf-8")


class TestEffectiveThemeName:
    def test_explicit_name_wins_over_project_config(self, project_root):
        _write_config(project_root, "theme: money\n")
        assert effective_theme_name("nord") == "nord"

    def test_falls_back_to_project_config_theme(self, project_root):
        _write_config(project_root, "theme: money\n")
        assert effective_theme_name(None) == "money"

    def test_falls_back_to_default_when_no_project_config(self, project_root):
        assert effective_theme_name(None) == DEFAULT_THEME_NAME

    def test_falls_back_to_default_when_project_config_has_no_theme_key(self, project_root):
        _write_config(project_root, "extensions:\n  nav_html: ''\n")
        assert effective_theme_name(None) == DEFAULT_THEME_NAME

    def test_unvalidated_name_passes_through_unchanged(self, project_root):
        # Not the framework's job here -- resolve_theme / THEME_REGISTRY
        # membership is checked by the caller (or is a repo-custom name a
        # project registers itself, invisible to this function).
        _write_config(project_root, "theme: a-repo-custom-theme\n")
        assert effective_theme_name(None) == "a-repo-custom-theme"


class TestResolveThemeProjectDefault:
    def test_no_name_no_project_config_is_the_bare_default(self, project_root):
        assert resolve_theme(None) is DEFAULT_THEME

    def test_no_name_uses_the_project_configs_theme(self, project_root):
        _write_config(project_root, "theme: nord\n")
        assert resolve_theme(None) is THEME_REGISTRY["nord"]

    def test_an_explicit_name_still_overrides_the_project_default(self, project_root):
        _write_config(project_root, "theme: nord\n")
        assert resolve_theme("dracula") is THEME_REGISTRY["dracula"]

    def test_an_explicit_unknown_name_still_raises(self, project_root):
        _write_config(project_root, "theme: nord\n")
        with pytest.raises(ValueError):
            resolve_theme("not-a-real-theme")

    def test_an_invalid_project_default_falls_back_quietly_instead_of_raising(self, project_root):
        # A project-level typo must not fail every report in the project --
        # same tolerance trellum.runner.execute.run_report already applies
        # to a bad report.yaml value (warn + fall back, never raise there).
        _write_config(project_root, "theme: not-a-real-theme\n")
        assert resolve_theme(None) is DEFAULT_THEME


class TestThemeComponentTokens:
    def test_component_tokens_keep_legacy_aliases_and_explicit_overrides(self):
        theme = Theme(font_size_base="16px", font_size_small="0.875rem",
                      primary_fill="#123456", on_accent="#fefefe")
        css = theme.to_css_vars()
        assert "--font-size-base: 16px;" in css
        assert "--font-size-small: 0.875rem;" in css
        assert "--font-size-interface: var(--font-size-base);" in css
        assert "--font-size-table: 13px;" in css
        assert "--font-size-axis: var(--font-size-small);" in css
        assert "--radius-card: var(--border-radius);" in css
        assert "--primary-fill: #123456;" in css
        assert "--on-accent: #fefefe;" in css

    def test_primary_fill_follows_header_and_explicit_values_still_win(self):
        theme = Theme(bg_header="#abcdef")
        assert theme.primary_fill == "var(--bg-header)"
        assert "--primary-fill: var(--bg-header);" in theme.to_css_vars()

        override = Theme(bg_header="#abcdef", primary_fill="#123456")
        assert "--primary-fill: #123456;" in override.to_css_vars()

    @pytest.mark.parametrize("name", THEME_REGISTRY)
    def test_builtin_primary_text_contrast(self, name):
        theme = THEME_REGISTRY[name]
        fill = theme.bg_header if theme.primary_fill == "var(--bg-header)" else theme.primary_fill

        def luminance(hex_color):
            channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                      for c in channels]
            return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]

        lighter, darker = sorted((luminance(theme.on_accent), luminance(fill)), reverse=True)
        assert (lighter + .05) / (darker + .05) >= 4.5, name

    def test_default_component_scale(self):
        theme = Theme()
        assert theme.font_size_base == "14px"
        assert theme.font_size_table == "13px"
        assert theme.font_size_axis == theme.font_size_label == "var(--font-size-small)"
        assert theme.font_size_section == "16px"
        assert theme.font_size_heading == theme.font_size_kpi == "24px"

        assert theme.primary_fill == "var(--bg-header)"
