from pathlib import Path

ROOT = Path(__file__).parents[3]


def read_template(name):
    return (ROOT / "templates" / name).read_text()


def test_organization_navigation_has_single_scope_heading_and_studios_management_target():
    nav = read_template("_org_nav.html")

    assert nav.count('>Organization<') == 1
    assert "Manage studios" not in nav
    assert "or request.resolver_match.url_name == 'org-studios'" in nav


def test_shell_docs_link_is_permanent_and_accessible_on_mobile():
    shell = read_template("_shell.html")

    assert "{% load static docs %}" in shell
    assert 'class="tl-console-docs-link"' in shell
    assert 'target="_blank" rel="noopener"' in shell
    assert 'aria-label="Documentation"' in shell


def test_scope_styles_keep_security_at_bottom_and_mobile_docs_compact():
    css = (ROOT / "static" / "console-shell.css").read_text()

    assert ".tl-console-nav-scope-heading" in css
    assert '[data-console-nav-group="org-security"] { margin-top: auto; }' in css
    assert ".tl-console-docs-label { position: absolute" in css


def test_long_scope_switchers_keep_authorized_links_and_filter_in_place():
    shell = read_template("_shell.html")
    js = (ROOT / "static" / "console-shell.js").read_text()
    assert shell.count("if shell_orgs|length > 8") == 2
    assert shell.count("if shell_studios|length > 8") == 2
    assert "data-scope-menu" in shell
    assert "data-scope-menu] .tl-console-scope-filter" in js
    assert "link.hidden = query &&" in js


def test_scope_filter_shows_links_before_search_and_preserves_link_targets():
    from playwright.sync_api import sync_playwright

    js = (ROOT / "static" / "console-shell.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "console-shell.css").read_text(encoding="utf-8")
    output = ROOT / ".artifacts" / "ux-workspace-selectors"
    output.mkdir(parents=True, exist_ok=True)
    links = "".join(
        f'<a href="/s/org/studio-{number}/">Studio {number:02d}</a>'
        for number in range(20)
    )
    html = f'''<!doctype html><html data-theme="light"><head><style>{css}
      *,*::before,*::after {{ animation:none!important; transition:none!important; }}
    </style></head><body class="mgmt"><div class="tl-console-shell" data-console-shell data-console-default-mode="light">
      <aside class="tl-console-sidebar"><div class="tl-console-context"><details class="tl-console-switcher">
        <summary>Studio</summary><div class="tl-console-popover" data-scope-menu>
          <input class="tl-console-scope-filter" type="search" placeholder="Search studios" aria-label="Search studios">
          <p class="tl-console-scope-empty" role="status" hidden>No studios match.</p>{links}
        </div></details></div><nav class="tl-console-nav"></nav></aside>
    </div><script>{js}</script></body></html>'''

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_content(html)
        all_links = page.locator("[data-scope-menu] a")
        assert all_links.count() == 20
        assert all_links.first.get_attribute("href") == "/s/org/studio-0/"
        page.locator("summary").click()
        page.screenshot(path=output / "scope-list-1440-light.png", full_page=True)
        page.evaluate("document.documentElement.dataset.theme='dark'; document.querySelector('.tl-console-shell').dataset.consoleTheme='dark'")
        page.screenshot(path=output / "scope-list-1440-dark.png", full_page=True)
        page.evaluate("document.documentElement.dataset.theme='light'; document.querySelector('.tl-console-shell').dataset.consoleTheme='light'")
        page.locator(".tl-console-scope-filter").fill("Studio 17")
        assert all_links.count() == 20
        assert page.locator("[data-scope-menu] a:visible").count() == 1
        assert page.locator("[data-scope-menu] a:visible").inner_text() == "Studio 17"
        page.locator(".tl-console-scope-filter").fill("no result")
        assert page.locator("[data-scope-menu] a:visible").count() == 0
        assert page.locator(".tl-console-scope-empty").is_visible()
        page.keyboard.press("Escape")
        assert page.locator("details").evaluate("el => !el.open")
        browser.close()
