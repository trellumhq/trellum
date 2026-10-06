from django.conf import settings


def _read(path: str) -> str:
    return (settings.BASE_DIR / path).read_text(encoding="utf-8")


def test_shell_is_reusable_and_report_palette_independent():
    shell = _read("templates/_shell.html")
    css = _read("static/console-shell.css")
    js = _read("static/console-shell.js")

    assert "{% include \"_console_nav.html\" %}" in shell
    assert "data-console-css=" in shell and "data-console-script=" in shell
    assert "data-console-default-mode=" in shell
    assert "data-html2canvas-ignore=" in shell
    assert "document.documentElement.getAttribute('data-theme')" not in js
    assert '.tl-console-shell[data-console-theme="dark"]' in css
    assert 'html[data-theme="dark"] .tl-console-shell:not([data-console-theme])' in css


def test_mobile_drawer_accessibility_contract():
    shell = _read("templates/_shell.html")
    js = _read("static/console-shell.js")
    css = _read("static/console-shell.css")

    for hook in ("data-console-toggle", "data-console-close", "data-console-backdrop"):
        assert hook in shell
    for behavior in ("aria-hidden", "aria-expanded", "inert", "Escape", "returnFocus"):
        assert behavior in js
    assert ".tl-console-shell :focus-visible" in css
    assert "@media (max-width: 1023px)" in css
    assert "transition: opacity .2s ease, visibility 0s linear .2s" in css
    assert "preventScroll" in js
    assert "closeMenus" in js


def test_console_dimensions_and_neutral_tokens():
    css = _read("static/console-shell.css")
    for declaration in (
        "--tl-console-width: 240px",
        "--tl-console-collapsed-width: 64px",
        "--tl-console-header-height: 56px",
        "--tl-console-bg: #f6f7f8",
        "--tl-console-card: #fff",
        "--tl-console-hover: #eef1f3",
        "--tl-console-text: #172126",
        "--tl-console-border: #e2e6ea",
        "--tl-console-primary: #0f766e",
    ):
        assert declaration in css


def test_mobile_utility_bar_and_known_title_contract():
    base = _read("templates/base.html")
    shell = _read("templates/_shell.html")
    css = _read("static/console-shell.css")
    js = _read("static/console-shell.js")

    assert "tl-console-title-known" in base
    assert "data-console-page-title" in shell
    assert "data-console-search-toggle" in shell
    assert "data-console-search" in shell
    assert "@media (max-width: 767px)" in css
    assert "height: 44px" in css and "font-size: 16px" in css
    assert "setPageTitle" in js and "prepare" in js and "setTheme" in js
    assert "document.title" not in js
    assert '/api/assistant/widget.js' in base


def test_theme_and_sidebar_state_are_available_before_styles():
    base = _read("templates/base.html")
    boot = _read("templates/_theme_boot_mgmt.html")
    assert base.index('{% include "_theme_boot_mgmt.html" %}') < base.index("console-shell.css")
    assert 'data-theme="light"' not in base
    assert "tl-console-collapsed" in boot
    assert "data-console-ready" in boot


def test_dashboard_report_links_keep_console_display():
    js = _read("static/portal.js")
    assert "function consoleReportUrl" in js
    assert "url.searchParams.set('display', 'console')" in js
    assert js.count("consoleReportUrl(apiUrl('/r/'") == 3


def test_report_modes_keep_console_assistant_but_hide_it_in_monitor_mode():
    host_css = _read("static/console-report-host.css")
    menu_js = _read("static/report_menu.js")
    for selector in (
        "#assistantAsk",
        "#assistantPill",
        "#assistantPanel",
        ".assistant-setup",
    ):
        assert f"body.tl-console-report-hosting {selector}" not in host_css
        assert selector in menu_js
    assert ".assistant-section-ask" in menu_js
    assert "var(--assistant-w, 0px)" in host_css
