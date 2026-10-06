"""Dashboard convergence guards.

The dashboard consumes design-system tokens (docs/design-system.md). These
are cheap textual asserts on the shipped assets: they cost nothing to run and
they fail loudly the moment a hardcoded color or a resurrected dead branch
sneaks back into portal.css / portal.js.
"""
from django.conf import settings

STATIC = settings.BASE_DIR / "static"


def _portal_css() -> str:
    return (STATIC / "portal.css").read_text(encoding="utf-8")


def _portal_js() -> str:
    return (STATIC / "portal.js").read_text(encoding="utf-8")


# ── Tokens, not literals ─────────────────────────────────────────────────

def test_portal_css_has_no_hardcoded_status_colors():
    css = _portal_css()
    for literal in ("#d1d5db", "#eff6ff", "#e17055", "#b07000", "#8a4f00"):
        assert literal not in css, f"portal.css still hardcodes {literal}"


def test_portal_css_has_no_divergent_rgba_tints():
    css = _portal_css()
    for literal in (
        "rgba(0,184,148",   # legacy green
        "rgba(16,185,129",  # a second legacy green
        "rgba(214,48,49",   # legacy red
        "rgba(239,68,68",   # a second legacy red
        "rgba(253,203,110",  # legacy warn (also spelled with spaces)
        "rgba(253, 203, 110",
    ):
        assert literal not in css, f"portal.css still hardcodes {literal}"


def test_portal_css_uses_color_mix_for_tints():
    assert "color-mix" in _portal_css()


def test_portal_css_does_not_redefine_theme_variables():
    # The theme table lives in ui.css only (portal-theme.css was deleted).
    for token in ("--bg:", "--bg-card:", "--text:", "--accent:", "--warn:"):
        assert token not in _portal_css(), f"portal.css redefines {token}"


def test_terminal_exception_is_documented_where_it_lives():
    css = _portal_css()
    note = "Deliberately theme-independent: terminals are dark."
    assert css.count(note) == 2, "both terminal blocks need the exception note"
    doc = (settings.BASE_DIR / "docs" / "design-system.md").read_text(encoding="utf-8")
    assert "Deliberate exceptions" in doc


# ── Dead code stays dead ─────────────────────────────────────────────────

def test_portal_css_dead_rules_removed():
    css = _portal_css()
    # Both spellings: these rules died under the old name, and must not come
    # back under the new one either.
    for dead in (".run-btn", ".recent-icon",
                 "bi-portal-footer", "bi-portal-version",
                 "trellum-portal-footer", "trellum-portal-version"):
        assert dead not in css, f"portal.css still ships dead rule {dead}"


def test_portal_js_dead_code_removed():
    js = _portal_js()
    for dead in ("SPINNER_SVG", "_opsSearch", "_opsTestResults"):
        assert dead not in js, f"portal.js still ships dead symbol {dead}"


# ── Feedback + accessibility ─────────────────────────────────────────────

def test_portal_js_uses_toasts_not_alerts():
    assert "alert(" not in _portal_js()


def test_toast_stack_is_a_live_region():
    assert "aria-live" in _portal_js()


def test_toast_has_warning_and_info_variants():
    css = _portal_css()
    assert ".toast.warning" in css
    assert ".toast.info" in css


def test_drawers_are_labelled_dialogs():
    html = (settings.BASE_DIR / "templates" / "portal" / "index.html").read_text(
        encoding="utf-8"
    )
    assert html.count('role="dialog"') == 3
    for label in ("Error log", "Report details", "Server log"):
        assert f'aria-label="{label}"' in html
